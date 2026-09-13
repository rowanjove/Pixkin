"""Text-to-Speech (TTS) providers for Pixkin."""

from core.providers.tts.base import (
    TtsError,
    TtsProvider,
    TtsProviderCapabilities,
    TtsProviderHealth,
    TtsVoice,
)
from core.providers.tts.openai_tts import OpenAiCompatibleTtsProvider
from core.providers.tts.windows_sapi import WindowsSapiProvider

__all__ = [
    "TtsError",
    "TtsProvider",
    "TtsProviderCapabilities",
    "TtsProviderHealth",
    "TtsVoice",
    "WindowsSapiProvider",
    "OpenAiCompatibleTtsProvider",
]
