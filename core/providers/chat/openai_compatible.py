"""OpenAI-compatible implementation of the chat provider contract."""

from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from openai import OpenAI

from core.providers.chat.base import (
    CancellationCheck,
    ChatCompletionResult,
    ChatMessage,
    ChatProvider,
    ChatProviderCapabilities,
    ChatProviderHealth,
    ChunkCallback,
    classify_chat_error,
)


MAX_COMPLETION_TEXT_BYTES = 512 * 1024
MAX_TOOL_CALLS = 64
MAX_TOOL_NAME_BYTES = 4 * 1024
MAX_TOOL_ARGUMENT_BYTES = 256 * 1024


class OpenAICompatibleChatProvider(ChatProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 45.0,
        client: Optional[Any] = None,
    ):
        self.base_url = str(base_url or "https://api.openai.com/v1").strip()
        self._validate_base_url()
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=timeout,
        )
        self._closed = False

    def _validate_base_url(self) -> None:
        try:
            parsed = urlsplit(self.base_url)
        except ValueError as exc:
            raise ValueError(
                "聊天接口地址必须使用 HTTPS（仅允许 localhost 使用 HTTP），"
                "且不得包含凭据或查询参数。"
            ) from exc
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme == "http"
                and host not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError(
                "聊天接口地址必须使用 HTTPS（仅允许 localhost 使用 HTTP），"
                "且不得包含凭据或查询参数。"
            )

    @property
    def capabilities(self) -> ChatProviderCapabilities:
        return ChatProviderCapabilities()

    def stream_completion(
        self,
        *,
        model: str,
        messages: List[ChatMessage],
        tools: List[Dict[str, Any]],
        on_chunk: ChunkCallback,
        is_cancelled: CancellationCheck,
    ) -> ChatCompletionResult:
        request: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request.update({"tools": tools, "tool_choice": "auto"})

        text_parts: List[str] = []
        text_size = 0
        pending_calls: Dict[int, Dict[str, Any]] = {}
        argument_sizes: Dict[int, int] = {}
        stream = self._client.chat.completions.create(**request)
        try:
            for chunk in stream:
                if is_cancelled():
                    return ChatCompletionResult("", [])
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content_size = len(str(delta.content).encode("utf-8"))
                    if text_size + content_size > MAX_COMPLETION_TEXT_BYTES:
                        raise ValueError("模型文本响应超过大小上限")
                    text_parts.append(delta.content)
                    text_size += content_size
                    on_chunk(delta.content)
                for call_delta in delta.tool_calls or []:
                    index = call_delta.index
                    if not isinstance(index, int) or index < 0:
                        raise ValueError("模型工具调用索引无效")
                    if index not in pending_calls and len(pending_calls) >= MAX_TOOL_CALLS:
                        raise ValueError("模型工具调用数量超过上限")
                    assembled = pending_calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if call_delta.id:
                        call_id = str(call_delta.id)
                        if len(call_id.encode("utf-8")) > MAX_TOOL_NAME_BYTES:
                            raise ValueError("模型工具调用 ID 超过大小上限")
                        assembled["id"] = call_id
                    if call_delta.type:
                        assembled["type"] = call_delta.type
                    if call_delta.function:
                        if call_delta.function.name:
                            name = str(call_delta.function.name)
                            next_name = assembled["function"]["name"] + name
                            if len(next_name.encode("utf-8")) > MAX_TOOL_NAME_BYTES:
                                raise ValueError("模型工具名称超过大小上限")
                            assembled["function"]["name"] = next_name
                        if call_delta.function.arguments:
                            arguments = str(call_delta.function.arguments)
                            argument_size = argument_sizes.get(index, 0) + len(
                                arguments.encode("utf-8")
                            )
                            if argument_size > MAX_TOOL_ARGUMENT_BYTES:
                                raise ValueError("模型工具参数超过大小上限")
                            assembled["function"]["arguments"] += arguments
                            argument_sizes[index] = argument_size
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()

        return ChatCompletionResult(
            "".join(text_parts),
            [pending_calls[index] for index in sorted(pending_calls)],
        )

    def health_check(self, model: str) -> ChatProviderHealth:
        models: tuple[str, ...] = ()
        try:
            model_api = self._client.models
            list_models = getattr(model_api, "list", None)
            if callable(list_models):
                response = list_models()
                models = tuple(
                    sorted(
                        {
                            str(getattr(item, "id", "") or "")
                            for item in getattr(response, "data", [])
                            if getattr(item, "id", "")
                        }
                    )
                )
                if models and model not in models:
                    return ChatProviderHealth(
                        False,
                        "连接成功，但配置的模型不在接口列表中。",
                        category="model_not_found",
                        recovery="从模型列表重新选择可用模型。",
                        models=models,
                    )
            else:
                model_api.retrieve(model)
        except Exception as exc:
            details = classify_chat_error(exc)
            return ChatProviderHealth(
                False,
                details.label,
                category=details.category,
                recovery=details.recovery,
                models=models,
            )
        return ChatProviderHealth(
            True,
            "连接和模型访问正常；流式与 Tool Calls 将在首次对话时确认。",
            models=models,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()
