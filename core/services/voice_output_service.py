"""Text-to-Speech output service and sentence streaming splitter."""

import io
import math
import struct
import wave
from dataclasses import dataclass
from typing import List, Optional

from core.providers.tts.base import TtsProvider
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)


@dataclass(frozen=True)
class TtsSentenceChunk:
    """A segment of text ready for speech synthesis."""

    sentence: str
    is_final: bool = False


class SentenceSplitter:
    """Incrementally split incoming streaming tokens into natural speech sentences."""

    PRIMARY_PUNCTUATION = set("。！？!?\n；;")
    SECONDARY_PUNCTUATION = set("，,：:")

    def __init__(self, *, min_length: int = 4, max_length: int = 60):
        self.min_length = max(1, min_length)
        self.max_length = max(self.min_length + 1, max_length)
        self._buffer: str = ""

    def feed(self, chunk: str) -> List[str]:
        """Feed new text chunk and return any completed sentences."""
        self._buffer += str(chunk or "")
        sentences: List[str] = []

        while self._buffer:
            split_idx = -1
            buf_len = len(self._buffer)

            # Look for primary punctuation
            for i, ch in enumerate(self._buffer):
                if ch in self.PRIMARY_PUNCTUATION and i + 1 >= self.min_length:
                    split_idx = i + 1
                    break

            # If no primary punctuation and buffer is getting long, try secondary
            if split_idx == -1 and buf_len >= self.max_length:
                for i, ch in enumerate(self._buffer):
                    if ch in self.SECONDARY_PUNCTUATION and i + 1 >= self.min_length:
                        split_idx = i + 1
                        break

            # If still too long without any punctuation, force split at max_length
            if split_idx == -1 and buf_len > self.max_length * 2:
                split_idx = self.max_length

            if split_idx != -1:
                sentence = self._buffer[:split_idx].strip()
                self._buffer = self._buffer[split_idx:]
                if sentence:
                    sentences.append(sentence)
            else:
                break

        return sentences

    def flush(self) -> List[str]:
        """Flush remaining buffered text."""
        remaining = self._buffer.strip()
        self._buffer = ""
        if remaining:
            return [remaining]
        return []


def calculate_wav_rms(wav_bytes: bytes) -> float:
    """Calculate the normalized RMS amplitude (0.0 to 1.0) of a WAV byte stream."""
    if not wav_bytes or len(wav_bytes) < 44 or not wav_bytes.startswith(b"RIFF"):
        return 0.0
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()
            if n_frames == 0 or sampwidth != 2:
                return 0.0
            frames = wf.readframes(n_frames)
            total_samples = n_frames * n_channels
            fmt = f"<{total_samples}h"
            samples = struct.unpack(fmt, frames)
            sum_squares = sum(s * s for s in samples)
            mean_square = sum_squares / total_samples
            rms = math.sqrt(mean_square)
            # Normalize 16-bit signed integer (0..32767) to (0.0..1.0)
            return min(1.0, rms / 32767.0)
    except Exception:
        return 0.0


class VoiceOutputService:
    """Orchestrate TTS synthesis and sentence queuing."""

    def __init__(
        self,
        provider: TtsProvider,
        *,
        voice: Optional[str] = None,
        speed: float = 1.0,
        enabled: bool = True,
        permission_service: ContextPermissionService | None = None,
        permission_token: str | None = None,
    ):
        self.provider = provider
        self.voice = voice
        self.speed = max(0.5, min(2.0, float(speed)))
        self.enabled = bool(enabled)
        self.permission_service = permission_service or ContextPermissionService()
        self._permission_token = permission_token

    def set_permission_token(self, token: str | None) -> None:
        """Provide a one-shot GUI approval for the next cloud synthesis."""
        self._permission_token = str(token or "").strip() or None

    def synthesize_sentence(self, text: str) -> bytes:
        """Synthesize text into audio bytes if service is enabled."""
        clean = str(text or "").strip()
        if not self.enabled or not clean:
            return b""
        if not self.provider.capabilities.offline:
            decision = self.permission_service.decide(
                PermissionResource.NETWORK,
                PermissionOperation.WRITE,
                resolve_ask=False,
                consume_pending=True,
                handoff_token=self._permission_token,
            )
            self._permission_token = None
            if not decision.allowed:
                return b""
        return self.provider.synthesize(
            clean,
            voice=self.voice,
            speed=self.speed,
        )

    def close(self) -> None:
        try:
            self.provider.close()
        except Exception:
            pass

    def cancel(self) -> None:
        """Request cancellation of an in-flight provider operation."""
        cancel = getattr(self.provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass
