"""Push-to-talk transcription boundary with an independent endpoint."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

import requests

from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)


class VoiceInputError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceEndpoint:
    base_url: str
    model: str

    def transcription_url(self) -> str:
        value = str(self.base_url or "").strip().rstrip("/")
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise VoiceInputError(
                "语音转写接口必须是无凭据的 HTTPS 地址"
            ) from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise VoiceInputError("语音转写接口必须是无凭据的 HTTPS 地址")
        if not str(self.model or "").strip():
            raise VoiceInputError("语音转写模型不能为空")
        return value + "/audio/transcriptions"


class VoiceTranscriptionService:
    """Send only a user-finished push-to-talk clip for transcription."""

    MAX_AUDIO_BYTES = 25 * 1024 * 1024
    MAX_RESPONSE_BYTES = 1 * 1024 * 1024

    def __init__(
        self,
        endpoint: VoiceEndpoint,
        *,
        timeout_seconds: float = 45.0,
        post=requests.post,
        permission_service: ContextPermissionService | None = None,
        permission_token: str | None = None,
    ):
        self.endpoint = endpoint
        self.timeout_seconds = min(
            120.0, max(5.0, float(timeout_seconds))
        )
        self._post = post
        # Keep the network boundary enforced inside the service as well as in
        # the GUI.  This protects callers that start a transcription worker
        # directly and consumes the one-shot ASK grant created by the GUI.
        self.permission_service = permission_service or ContextPermissionService()
        self.permission_token = str(permission_token or "").strip() or None

    def transcribe(self, wav_bytes: bytes, *, api_key: str) -> str:
        decision = self.permission_service.decide(
            PermissionResource.NETWORK,
            PermissionOperation.WRITE,
            resolve_ask=False,
            consume_pending=True,
            handoff_token=self.permission_token,
        )
        self.permission_token = None
        if not decision.allowed:
            raise VoiceInputError("网络权限未允许")
        if not api_key:
            raise VoiceInputError("尚未设置独立的语音转写 API Key")
        if not wav_bytes or len(wav_bytes) > self.MAX_AUDIO_BYTES:
            raise VoiceInputError("录音为空或超过 25 MB 上限")
        response = self._post(
            self.endpoint.transcription_url(),
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": self.endpoint.model},
            files={
                "file": (
                    "push-to-talk.wav",
                    io.BytesIO(wav_bytes),
                    "audio/wav",
                )
            },
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )
        try:
            if 300 <= int(response.status_code) < 400:
                raise VoiceInputError("语音转写接口不允许重定向")
            response.raise_for_status()
            headers = getattr(response, "headers", {})
            declared = headers.get("Content-Length") if hasattr(headers, "get") else None
            if declared is not None:
                try:
                    if int(declared) > self.MAX_RESPONSE_BYTES:
                        raise VoiceInputError("语音转写响应超过大小上限")
                except (TypeError, ValueError) as exc:
                    raise VoiceInputError("语音转写响应大小无效") from exc
            document = self._read_json_response(response)
        except VoiceInputError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise VoiceInputError("语音转写请求失败") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        text = (
            str(document.get("text") or "").strip()
            if isinstance(document, dict)
            else ""
        )
        if not text:
            raise VoiceInputError("语音转写接口没有返回文本")
        return text[:20_000]

    @classmethod
    def _read_json_response(cls, response):
        """Read a transcription response incrementally before parsing JSON."""
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
                piece = bytes(chunk)
                total += len(piece)
                if total > cls.MAX_RESPONSE_BYTES:
                    raise VoiceInputError("语音转写响应超过大小上限")
                chunks.append(piece)
            try:
                return json.loads(b"".join(chunks).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VoiceInputError("语音转写响应格式无效") from exc

        content = getattr(response, "content", None)
        if content is not None:
            raw = bytes(content)
            if len(raw) > cls.MAX_RESPONSE_BYTES:
                raise VoiceInputError("语音转写响应超过大小上限")
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VoiceInputError("语音转写响应格式无效") from exc

        raise VoiceInputError("语音转写响应无法安全读取")
