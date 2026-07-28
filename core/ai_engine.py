from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.providers.chat.base import ChatProvider, classify_chat_error
from core.providers.chat.openai_compatible import OpenAICompatibleChatProvider
from core.services.chat_service import ChatService
from core.tool_registry import ToolRegistry


class AiWorkerThread(QThread):
    """只负责聊天服务的后台线程生命周期和 Qt 信号转发。"""
    chunk_received = pyqtSignal(str)       # 流式打字机输出信号
    tool_executing = pyqtSignal(str)      # 正在执行 Tool 的通知信号
    finished_response = pyqtSignal(str)   # 对话最终完成信号
    error_occurred = pyqtSignal(str)      # 异常发生信号

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tool_registry: ToolRegistry,
        provider: Optional[ChatProvider] = None,
    ):
        super().__init__()
        self.base_url = base_url or "https://api.deepseek.com/v1"
        self.api_key = api_key
        self.model = model or "deepseek-v4-flash"
        self.system_prompt = system_prompt
        self.messages = messages
        self.tool_registry = tool_registry
        self._provider = provider
        self._service: Optional[ChatService] = None

    def cancel(self):
        """请求尽快取消正在进行的网络调用。"""
        self.requestInterruption()
        if self._service is not None:
            try:
                self._service.close()
            except Exception:
                pass

    def run(self):
        if not self.api_key:
            self.error_occurred.emit("尚未设置 API Key！请右键托盘图标或在设置中配置 API Key。")
            return

        try:
            provider = self._provider or OpenAICompatibleChatProvider(
                api_key=self.api_key,
                base_url=self.base_url,
            )
            self._service = ChatService(
                provider=provider,
                model=self.model,
                system_prompt=self.system_prompt,
                messages=self.messages,
                tool_registry=self.tool_registry,
            )
            response = self._service.run(
                on_chunk=self.chunk_received.emit,
                on_tool_executing=lambda name: self.tool_executing.emit(
                    f"正在使用工具：{name}"
                ),
                is_cancelled=self.isInterruptionRequested,
            )
            if response is not None:
                self.finished_response.emit(response)

        except Exception as e:
            if not self.isInterruptionRequested():
                details = classify_chat_error(e)
                retry_hint = (
                    "可以直接重试本条消息。"
                    if details.retriable
                    else details.recovery
                )
                self.error_occurred.emit(
                    f"大模型请求失败（{details.label}）。{retry_hint}"
                )
        finally:
            if self._service is not None:
                try:
                    self._service.close()
                except Exception:
                    pass
                self._service = None
