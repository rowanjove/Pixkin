"""Push-to-talk transcription boundary with an independent endpoint."""

from __future__ import annotations

import io
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests


class VoiceInputError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceEndpoint:
    base_url: str
    model: str

    def transcription_url(self) -> str:
        value = str(self.base_url or "").strip().rstrip("/")
        parsed = urlsplit(value)
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

    def __init__(
        self,
        endpoint: VoiceEndpoint,
        *,
        timeout_seconds: float = 45.0,
        post=requests.post,
    ):
        self.endpoint = endpoint
        self.timeout_seconds = min(
            120.0, max(5.0, float(timeout_seconds))
        )
        self._post = post

    def transcribe(self, wav_bytes: bytes, *, api_key: str) -> str:
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
        if 300 <= int(response.status_code) < 400:
            raise VoiceInputError("语音转写接口不允许重定向")
        try:
            response.raise_for_status()
            document = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise VoiceInputError("语音转写请求失败") from exc
        text = (
            str(document.get("text") or "").strip()
            if isinstance(document, dict)
            else ""
        )
        if not text:
            raise VoiceInputError("语音转写接口没有返回文本")
        return text
