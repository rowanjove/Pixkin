"""OpenAI-compatible reference image editing provider."""

from __future__ import annotations

import base64
import hashlib
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable, Literal, Sequence, cast
from urllib.parse import urlsplit

from openai import OpenAI

from core.providers.image.base import (
    ImageProvider,
    ImageProviderCapabilities,
    ImageProviderHealth,
    classify_image_error,
)


class OpenAICompatibleImageProvider(ImageProvider):
    MAX_IMAGE_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        quality: str,
        client=None,
        client_factory: Callable[..., Any] = OpenAI,
    ):
        self.api_key = api_key
        self._endpoint = str(base_url or "https://api.openai.com/v1").strip()
        self._model = model or "gpt-image-2"
        self.quality = quality or "medium"
        self._client = client
        self._client_factory = client_factory

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def model(self) -> str:
        return self._model

    @property
    def client(self):
        if self._client is None:
            self._client = self._client_factory(
                api_key=self.api_key,
                base_url=self.endpoint,
                timeout=180.0,
                max_retries=0,
            )
        return self._client

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(
            f"{self.endpoint.rstrip('/')}|{self.model}".encode("utf-8")
        ).hexdigest()[:16]

    @property
    def capabilities(self) -> ImageProviderCapabilities:
        return ImageProviderCapabilities(
            image_edit=callable(
                getattr(getattr(self.client, "images", None), "edit", None)
            ),
            model_lookup=callable(
                getattr(
                    getattr(self.client, "models", None),
                    "retrieve",
                    None,
                )
            ),
        )

    def validate(self):
        try:
            parsed = urlsplit(self.endpoint)
        except ValueError as exc:
            raise ValueError(
                "图像接口地址必须使用 HTTPS（仅允许 localhost 使用 HTTP），"
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
                "图像接口地址必须使用 HTTPS（仅允许 localhost 使用 HTTP），"
                "且不得包含凭据或查询参数。"
            )
        if not self.model.strip():
            raise ValueError("图像模型名不能为空。")
        if self.quality not in {"low", "medium", "high"}:
            raise ValueError("图像质量必须是 low、medium 或 high。")
        if not self.capabilities.image_edit:
            raise ValueError("当前接口客户端不支持图像编辑能力。")

    def health_check(self) -> ImageProviderHealth:
        self.validate()
        retrieve = getattr(
            getattr(self.client, "models", None),
            "retrieve",
            None,
        )
        if not callable(retrieve):
            return ImageProviderHealth(
                "unverified",
                "兼容接口未提供模型查询方法，已保留图像编辑能力检查。",
            )
        try:
            retrieve(self.model, timeout=15.0)
        except Exception as exc:
            details = classify_image_error(exc)
            hostname = urlsplit(self.endpoint).hostname
            official = hostname in {
                "api.openai.com",
                "www.api.openai.com",
            }
            if details.category in {
                "authentication",
                "permission",
            } or (
                official
                and details.category in {
                    "invalid_request",
                    "not_found",
                }
            ):
                raise RuntimeError(
                    f"图像接口预检失败（{details.label}）：{exc}"
                ) from exc
            return ImageProviderHealth(
                "unverified",
                f"模型查询无法验证（{details.label}），"
                "将由首次图像请求确认能力。",
            )
        return ImageProviderHealth(
            "verified",
            "模型查询成功，图像编辑方法可用。",
        )

    def edit(
        self,
        reference_paths: Sequence[Path],
        prompt: str,
    ) -> bytes:
        self.validate()
        quality = cast(
            Literal["standard", "low", "medium", "high", "auto"],
            self.quality,
        )
        with ExitStack() as stack:
            files = [
                stack.enter_context(Path(path).open("rb"))
                for path in reference_paths
            ]
            result = self.client.images.edit(
                model=self.model,
                image=files,
                prompt=prompt,
                size="1024x1024",
                quality=quality,
                response_format="b64_json",
            )
        data = result.data or []
        encoded = data[0].b64_json if data else None
        if not encoded:
            raise RuntimeError("图像服务没有返回可用图片。")
        if not isinstance(encoded, (str, bytes, bytearray)):
            raise RuntimeError("图像服务返回的图片数据损坏。")
        max_encoded_bytes = 4 * ((self.MAX_IMAGE_BYTES + 2) // 3)
        if len(encoded) > max_encoded_bytes:
            raise RuntimeError("图像服务返回的图片超过 20 MB 上限。")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("图像服务返回的图片数据损坏。") from exc
        if len(decoded) > self.MAX_IMAGE_BYTES:
            raise RuntimeError("图像服务返回的图片超过 20 MB 上限。")
        return decoded

    def close(self):
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        self._client = None
