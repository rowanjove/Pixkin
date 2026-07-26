"""Contracts shared by chat model providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List


ChatMessage = Dict[str, Any]
ToolCall = Dict[str, Any]
ChunkCallback = Callable[[str], None]
CancellationCheck = Callable[[], bool]


@dataclass(frozen=True)
class ChatProviderCapabilities:
    streaming: bool = True
    tool_calls: bool = True
    cancellation: bool = True
    health_check: bool = True


@dataclass(frozen=True)
class ChatProviderHealth:
    healthy: bool
    message: str
    category: str = "ok"
    recovery: str = ""
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatProviderErrorDetails:
    category: str
    retriable: bool
    label: str
    recovery: str
    status_code: int | None = None


@dataclass(frozen=True)
class ChatCompletionResult:
    text: str
    tool_calls: List[ToolCall]


class ChatProvider(ABC):
    """Provider-neutral interface used by the chat application service."""

    @property
    @abstractmethod
    def capabilities(self) -> ChatProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    def stream_completion(
        self,
        *,
        model: str,
        messages: List[ChatMessage],
        tools: List[Dict[str, Any]],
        on_chunk: ChunkCallback,
        is_cancelled: CancellationCheck,
    ) -> ChatCompletionResult:
        raise NotImplementedError

    @abstractmethod
    def health_check(self, model: str) -> ChatProviderHealth:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


def classify_chat_error(exc: Exception) -> ChatProviderErrorDetails:
    """Normalize SDK and compatible-endpoint errors for user recovery."""
    status = getattr(exc, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if status == 401 or "authentication" in name:
        category, retriable = "authentication", False
    elif status == 403 or "permission" in name:
        category, retriable = "permission", False
    elif status == 404 or "notfound" in name:
        category, retriable = "model_not_found", False
    elif status == 429 or "ratelimit" in name:
        category, retriable = "rate_limit", True
    elif status in {408, 504} or "timeout" in name:
        category, retriable = "timeout", True
    elif status is not None and status >= 500:
        category, retriable = "server", True
    elif "chatcapabilityerror" in name:
        category, retriable = "tool_incompatible", False
    elif "tool" in message and (
        "unsupported" in message or "not support" in message
    ):
        category, retriable = "tool_incompatible", False
    elif (
        "connection" in name
        or isinstance(exc, (ConnectionError, TimeoutError))
    ):
        category, retriable = "connection", True
    elif status in {400, 413, 422} or "badrequest" in name:
        category, retriable = "protocol", False
    else:
        category, retriable = "unknown", False
    labels = {
        "authentication": "认证失败",
        "permission": "接口无权限",
        "model_not_found": "模型不存在",
        "rate_limit": "接口限流",
        "timeout": "接口超时",
        "server": "接口服务异常",
        "tool_incompatible": "模型不支持工具调用",
        "connection": "网络连接失败",
        "protocol": "接口协议不兼容",
        "unknown": "未知错误",
    }
    recoveries = {
        "authentication": "检查 API Key 是否正确、有效且属于当前接口。",
        "permission": "检查账户权限、模型授权和组织/项目设置。",
        "model_not_found": "从接口模型列表重新选择可用模型。",
        "rate_limit": "稍后重试，或检查账户额度和并发限制。",
        "timeout": "检查网络和接口地址后重试。",
        "server": "服务端暂时异常，请稍后重试。",
        "tool_incompatible": "改用支持 Tool Calls 的模型。",
        "connection": "检查网络、代理、DNS 和接口地址。",
        "protocol": "确认接口兼容 OpenAI Chat Completions。",
        "unknown": "查看匿名诊断摘要，或联系接口提供方。",
    }
    return ChatProviderErrorDetails(
        category=category,
        retriable=retriable,
        label=labels[category],
        recovery=recoveries[category],
        status_code=status,
    )
