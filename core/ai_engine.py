from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.providers.chat.base import ChatProvider, classify_chat_error
from core.services.chat_service import ChatService
from core.tool_registry import ToolExecutionContext, ToolRegistry
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)


class AiWorkerThread(QThread):
    """只负责聊天服务的后台线程生命周期和 Qt 信号转发。"""
    chunk_received = pyqtSignal(str)       # 流式打字机输出信号
    tool_executing = pyqtSignal(str)      # 正在执行 Tool 的通知信号
    finished_response = pyqtSignal(str)   # 对话最终完成信号
    error_occurred = pyqtSignal(str)      # 异常发生信号
    emotion_detected = pyqtSignal(object) # 识别到情绪/动作标签信号 (PetState)

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tool_registry: ToolRegistry,
        provider: Optional[ChatProvider] = None,
        execution_context: ToolExecutionContext | None = None,
        permission_service: ContextPermissionService | None = None,
        permission_token: str | None = None,
    ):
        super().__init__()
        self.base_url = base_url or "https://api.deepseek.com/v1"
        self.api_key = api_key
        self.model = model or "deepseek-v4-flash"
        self.system_prompt = system_prompt
        self.messages = messages
        self.tool_registry = tool_registry
        self._provider = provider
        self._execution_context = execution_context
        # The worker is also usable outside the desktop composition root;
        # network access must remain default-deny in that embedding case.
        self._permission_service = permission_service or ContextPermissionService()
        self._permission_token = permission_token
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
            decision = self._permission_service.decide(
                PermissionResource.NETWORK,
                PermissionOperation.WRITE,
                resolve_ask=False,
                consume_pending=True,
                handoff_token=self._permission_token,
            )
            if not decision.allowed:
                raise PermissionError(
                    "网络权限未允许；请在隐私与审计中授权后重试。"
                )
            provider = self._provider
            if provider is None:
                raise RuntimeError(
                    "未注册聊天 Provider；请通过 ProviderRegistry 配置模型"
                )
            self._service = ChatService(
                provider=provider,
                model=self.model,
                system_prompt=self.system_prompt,
                messages=self.messages,
                tool_registry=self.tool_registry,
                execution_context=self._execution_context,
            )
            response = self._service.run(
                on_chunk=self.chunk_received.emit,
                on_tool_executing=lambda name: self.tool_executing.emit(
                    f"正在使用工具：{name}"
                ),
                is_cancelled=self.isInterruptionRequested,
                on_emotion=self.emotion_detected.emit,
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
