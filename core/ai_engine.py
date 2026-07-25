import json
from typing import List, Dict, Any
from PyQt6.QtCore import QThread, pyqtSignal
from openai import OpenAI
from core.tool_registry import ToolRegistry

class AiWorkerThread(QThread):
    """用于在后台线程处理 OpenAI / DeepSeek 对话及 Tool Calls 的 QThread"""
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
        tool_registry: ToolRegistry
    ):
        super().__init__()
        self.base_url = base_url or "https://api.deepseek.com/v1"
        self.api_key = api_key
        self.model = model or "deepseek-v4-flash"
        self.system_prompt = system_prompt
        self.messages = messages
        self.tool_registry = tool_registry
        self._client = None

    def cancel(self):
        """请求尽快取消正在进行的网络调用。"""
        self.requestInterruption()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

    def run(self):
        if not self.api_key:
            self.error_occurred.emit("尚未设置 API Key！请右键托盘图标或在设置中配置 API Key。")
            return

        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=45.0)
            self._client = client

            full_messages = [{"role": "system", "content": self.system_prompt}]
            full_messages.extend(self.messages)

            tools_schema = self.tool_registry.get_tools_schema()

            # 最多允许连续 4 轮工具调用，支持组合任务，同时避免模型无限循环。
            for _ in range(4):
                if self.isInterruptionRequested():
                    return
                request = {
                    "model": self.model,
                    "messages": full_messages,
                    "stream": True,
                }
                if tools_schema:
                    request.update({"tools": tools_schema, "tool_choice": "auto"})

                reply_text, tool_calls = self._stream_completion(client, request)
                if self.isInterruptionRequested():
                    return

                if not tool_calls:
                    self.finished_response.emit(reply_text)
                    return

                full_messages.append({
                    "role": "assistant",
                    "content": reply_text or None,
                    "tool_calls": tool_calls,
                })
                for tool_call in tool_calls:
                    function = tool_call["function"]
                    function_name = function["name"]
                    try:
                        function_args = json.loads(function["arguments"])
                    except (TypeError, json.JSONDecodeError):
                        function_args = {}

                    self.tool_executing.emit(f"正在使用工具：{function_name}")
                    tool_result = self.tool_registry.execute_tool(function_name, function_args)
                    full_messages.append({
                        "tool_call_id": tool_call["id"],
                        "role": "tool",
                        "name": function_name,
                        "content": tool_result,
                    })

            raise RuntimeError("工具调用次数过多，已停止本次请求")

        except Exception as e:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(f"大模型请求失败: {str(e)}")
        finally:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    def _stream_completion(self, client: OpenAI, request):
        """消费一次流式响应，同时拼装可能被拆分的 Tool Call。"""
        text_parts = []
        pending_calls = {}
        stream = client.chat.completions.create(**request)
        try:
            for chunk in stream:
                if self.isInterruptionRequested():
                    return "", []
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text_parts.append(delta.content)
                    self.chunk_received.emit(delta.content)
                for call_delta in delta.tool_calls or []:
                    index = call_delta.index
                    assembled = pending_calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if call_delta.id:
                        assembled["id"] = call_delta.id
                    if call_delta.type:
                        assembled["type"] = call_delta.type
                    if call_delta.function:
                        if call_delta.function.name:
                            assembled["function"]["name"] += call_delta.function.name
                        if call_delta.function.arguments:
                            assembled["function"]["arguments"] += (
                                call_delta.function.arguments
                            )
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        tool_calls = [
            pending_calls[index] for index in sorted(pending_calls)
        ]
        return "".join(text_parts), tool_calls
