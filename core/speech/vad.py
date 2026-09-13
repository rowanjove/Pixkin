"""Dependency-free PCM16 energy VAD suitable for streaming microphone frames."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import Enum


class VadEvent(str, Enum):
    SILENCE = "silence"
    SPEECH_START = "speech_start"
    SPEECH = "speech"
    SPEECH_END = "speech_end"


@dataclass(frozen=True)
class VadConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    threshold: float = 0.018
    start_frames: int = 2
    end_frames: int = 8
    max_utterance_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.frame_ms <= 0:
            raise ValueError("sample rate and frame duration must be positive")
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("VAD threshold must be between 0 and 1")
        if self.start_frames < 1 or self.end_frames < 1:
            raise ValueError("VAD frame counts must be positive")


class VoiceActivityDetector:
    """Classify raw little-endian signed 16-bit mono frames."""

    def __init__(self, config: VadConfig | None = None):
        self.config = config or VadConfig()
        self.reset()

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def utterance_seconds(self) -> float:
        return self._speech_frames * self.config.frame_ms / 1000.0

    def reset(self) -> None:
        self._speaking = False
        self._active_frames = 0
        self._silent_frames = 0
        self._speech_frames = 0

    def energy(self, pcm16: bytes) -> float:
        if not pcm16:
            return 0.0
        usable = len(pcm16) - (len(pcm16) % 2)
        if usable <= 0:
            return 0.0
        samples = struct.unpack(f"<{usable // 2}h", pcm16[:usable])
        return min(1.0, math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768.0)

    def feed(self, pcm16: bytes) -> VadEvent:
        loud = self.energy(pcm16) >= self.config.threshold
        if loud:
            self._active_frames += 1
            self._silent_frames = 0
            if not self._speaking and self._active_frames >= self.config.start_frames:
                self._speaking = True
                self._speech_frames = 0
                return VadEvent.SPEECH_START
            if self._speaking:
                self._speech_frames += 1
                if self.utterance_seconds >= self.config.max_utterance_seconds:
                    self._speaking = False
                    self._active_frames = 0
                    return VadEvent.SPEECH_END
                return VadEvent.SPEECH
            return VadEvent.SILENCE

        self._active_frames = 0
        if self._speaking:
            self._silent_frames += 1
            if self._silent_frames >= self.config.end_frames:
                self._speaking = False
                self._silent_frames = 0
                return VadEvent.SPEECH_END
            return VadEvent.SPEECH
        return VadEvent.SILENCE
