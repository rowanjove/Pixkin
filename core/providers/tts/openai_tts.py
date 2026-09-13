"""OpenAI-compatible Text-to-Speech provider."""

import logging
from collections.abc import Callable, Iterable
from typing import Any, List, Optional, cast
from urllib.parse import urlsplit

import requests

from core.providers.tts.base import (
    TtsError,
    TtsProvider,
    TtsProviderCapabilities,
    TtsProviderHealth,
    TtsVoice,
)


MAX_AUDIO_BYTES = 20 * 1024 * 1024

LOGGER = logging.getLogger("desktop_pet.tts.openai")

DEFAULT_OPENAI_VOICES = [
    TtsVoice("alloy", "Alloy (中性)", "multi", "neutral"),
    TtsVoice("echo", "Echo (男声)", "multi", "male"),
    TtsVoice("fable", "Fable (叙事)", "multi", "neutral"),
    TtsVoice("onyx", "Onyx (沉稳男声)", "multi", "male"),
    TtsVoice("nova", "Nova (活泼女声)", "multi", "female"),
    TtsVoice("shimmer", "Shimmer (清亮女声)", "multi", "female"),
]


class OpenAiCompatibleTtsProvider(TtsProvider):
    """Cloud-based OpenAI compatible TTS provider."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "tts-1",
        timeout_seconds: float = 30.0,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "tts-1").strip()
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        self._session = session or requests.Session()
        self._capabilities = TtsProviderCapabilities(
            streaming=True,
            offline=False,
            voice_list=True,
            pitch_control=False,
            speed_control=True,
        )

    @property
    def capabilities(self) -> TtsProviderCapabilities:
        return self._capabilities

    def _speech_url(self) -> str:
        try:
            parsed = urlsplit(self.base_url)
        except ValueError as exc:
            raise TtsError(
                "TTS 接口必须使用 HTTPS（仅允许 localhost 使用 HTTP）且不得包含凭据或查询参数",
                category="invalid_url",
            ) from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme == "http"
                and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise TtsError(
                "TTS 接口必须使用 HTTPS（仅允许 localhost 使用 HTTP）且不得包含凭据或查询参数",
                category="invalid_url",
            )
        if self.base_url.endswith("/audio/speech"):
            return self.base_url
        return f"{self.base_url}/audio/speech"

    def list_voices(self) -> List[TtsVoice]:
        return list(DEFAULT_OPENAI_VOICES)

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        speed: float = 1.0,
    ) -> bytes:
        clean_text = str(text or "").strip()
        if not clean_text:
            return b""
        selected_voice = voice or "nova"
        url = self._speech_url()
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "input": clean_text,
            "voice": selected_voice,
            "speed": max(0.25, min(4.0, float(speed))),
            "response_format": "mp3",
        }

        try:
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
                stream=True,
            )
            try:
                response.raise_for_status()
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        if int(declared) > MAX_AUDIO_BYTES:
                            raise TtsError(
                                "TTS 音频响应超过大小上限",
                                category="response_too_large",
                            )
                    except (TypeError, ValueError) as exc:
                        raise TtsError(
                            "TTS 音频响应大小无效",
                            category="invalid_response",
                        ) from exc
                iterator = getattr(response, "iter_content", None)
                if callable(iterator):
                    chunks: list[bytes] = []
                    total = 0
                    bounded_iterator = cast(
                        Callable[..., Iterable[Any]],
                        iterator,
                    )
                    for chunk in bounded_iterator(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_AUDIO_BYTES:
                            raise TtsError(
                                "TTS 音频响应超过大小上限",
                                category="response_too_large",
                            )
                        chunks.append(bytes(chunk))
                    content = b"".join(chunks)
                else:
                    content = bytes(getattr(response, "content", b""))
                    if len(content) > MAX_AUDIO_BYTES:
                        raise TtsError(
                            "TTS 音频响应超过大小上限",
                            category="response_too_large",
                        )
                if not content:
                    raise TtsError("TTS 服务返回了空音频数据", category="empty_response")
                return content
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retriable = status in {408, 429, 500, 502, 503, 504}
            raise TtsError(
                f"TTS 请求失败: {exc}",
                category="network_error",
                retriable=retriable,
            ) from exc

    def health_check(self) -> TtsProviderHealth:
        if not self.base_url:
            return TtsProviderHealth(
                healthy=False,
                message="未配置 TTS 接口地址",
                category="not_configured",
            )
        try:
            self._speech_url()
            parsed = urlsplit(self.base_url)
            if not parsed.hostname:
                return TtsProviderHealth(
                    healthy=False,
                    message="TTS 接口主机名无效",
                    category="invalid_url",
                )
            return TtsProviderHealth(
                healthy=True,
                message="OpenAI 兼容 TTS 配置有效",
                voices_count=len(DEFAULT_OPENAI_VOICES),
            )
        except TtsError as exc:
            return TtsProviderHealth(
                healthy=False,
                message=str(exc),
                category=exc.category,
            )
        except Exception as exc:
            return TtsProviderHealth(
                healthy=False,
                message=f"TTS 健康检查失败: {exc}",
                category="check_error",
            )

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def cancel(self) -> None:
        """Close the transport so an in-flight request can unwind promptly."""
        self.close()
