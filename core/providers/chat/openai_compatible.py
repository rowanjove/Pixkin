"""OpenAI-compatible implementation of the chat provider contract."""

from typing import Any, Dict, List, Optional

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


class OpenAICompatibleChatProvider(ChatProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 45.0,
        client: Optional[Any] = None,
    ):
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._closed = False

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
        pending_calls: Dict[int, Dict[str, Any]] = {}
        stream = self._client.chat.completions.create(**request)
        try:
            for chunk in stream:
                if is_cancelled():
                    return ChatCompletionResult("", [])
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text_parts.append(delta.content)
                    on_chunk(delta.content)
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
                            assembled["function"]["name"] += (
                                call_delta.function.name
                            )
                        if call_delta.function.arguments:
                            assembled["function"]["arguments"] += (
                                call_delta.function.arguments
                            )
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
