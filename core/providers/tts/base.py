"""Contracts shared by Text-to-Speech (TTS) providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


class TtsError(Exception):
    """Base exception for TTS operations."""

    def __init__(self, message: str, *, category: str = "general", retriable: bool = False):
        super().__init__(message)
        self.message = message
        self.category = category
        self.retriable = retriable


@dataclass(frozen=True)
class TtsVoice:
    """Metadata describing an available voice."""

    id: str
    name: str
    language: str = "zh-CN"
    gender: str = "neutral"
    description: str = ""


@dataclass(frozen=True)
class TtsProviderCapabilities:
    """Declared capabilities of a TTS provider."""

    streaming: bool = True
    offline: bool = False
    voice_list: bool = True
    pitch_control: bool = False
    speed_control: bool = True


@dataclass(frozen=True)
class TtsProviderHealth:
    """Health check outcome for a TTS provider."""

    healthy: bool
    message: str
    category: str = "ok"
    voices_count: int = 0


class TtsProvider(ABC):
    """Provider-neutral interface for speech synthesis."""

    @property
    @abstractmethod
    def capabilities(self) -> TtsProviderCapabilities:
        """Return provider capabilities."""
        raise NotImplementedError

    @abstractmethod
    def list_voices(self) -> List[TtsVoice]:
        """Return available voices for this provider."""
        raise NotImplementedError

    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        speed: float = 1.0,
    ) -> bytes:
        """Synthesize plain text into audio bytes (e.g. WAV or MP3)."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> TtsProviderHealth:
        """Verify connectivity or readiness."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release underlying resources."""
        raise NotImplementedError
