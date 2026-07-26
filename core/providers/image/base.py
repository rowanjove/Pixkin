"""Provider-neutral contracts for reference-guided image generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence, TypedDict


@dataclass(frozen=True)
class ImageProviderCapabilities:
    image_edit: bool
    model_lookup: bool


@dataclass(frozen=True)
class ImageProviderHealth:
    status: Literal["verified", "unverified"]
    note: str


@dataclass(frozen=True)
class ImageProviderErrorDetails:
    category: str
    retriable: bool
    label: str
    status_code: int | None

    def as_dict(self) -> "ImageProviderErrorMap":
        return {
            "category": self.category,
            "retriable": self.retriable,
            "label": self.label,
            "status_code": self.status_code,
        }


class ImageProviderErrorMap(TypedDict):
    category: str
    retriable: bool
    label: str
    status_code: int | None


class ImageProvider(ABC):
    @property
    @abstractmethod
    def endpoint(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def cache_key(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def capabilities(self) -> ImageProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    def validate(self):
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> ImageProviderHealth:
        raise NotImplementedError

    @abstractmethod
    def edit(
        self,
        reference_paths: Sequence[Path],
        prompt: str,
    ) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def close(self):
        raise NotImplementedError


def classify_image_error(exc: Exception) -> ImageProviderErrorDetails:
    """Normalize SDK and compatible-endpoint failures for retry policy."""
    status = getattr(exc, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    name = type(exc).__name__.lower()
    if status == 401 or "authentication" in name:
        category, retriable = "authentication", False
    elif status == 403 or "permission" in name:
        category, retriable = "permission", False
    elif status in {400, 413, 415, 422} or "badrequest" in name:
        category, retriable = "invalid_request", False
    elif status == 404 or "notfound" in name:
        category, retriable = "not_found", False
    elif status == 429 or "ratelimit" in name:
        category, retriable = "rate_limit", True
    elif status in {408, 504} or "timeout" in name:
        category, retriable = "timeout", True
    elif status is not None and status >= 500:
        category, retriable = "server", True
    elif (
        "没有返回可用图片" in str(exc)
        or "图片数据损坏" in str(exc)
    ):
        category, retriable = "invalid_response", True
    elif (
        "connection" in name
        or isinstance(exc, (ConnectionError, TimeoutError))
    ):
        category, retriable = "connection", True
    else:
        category, retriable = "unknown", False
    labels = {
        "authentication": "认证失败",
        "permission": "接口无权限",
        "invalid_request": "请求参数不受支持",
        "not_found": "接口或模型不存在",
        "rate_limit": "接口限流",
        "timeout": "接口超时",
        "server": "接口服务异常",
        "invalid_response": "接口图片响应无效",
        "connection": "网络连接失败",
        "unknown": "未知错误",
    }
    return ImageProviderErrorDetails(
        category=category,
        retriable=retriable,
        label=labels[category],
        status_code=status,
    )
