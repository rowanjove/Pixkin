"""Built-in provider catalog used by the application composition root."""

from __future__ import annotations

from typing import Any

from core.providers.chat.openai_compatible import OpenAICompatibleChatProvider
from core.providers.image.openai_compatible import OpenAICompatibleImageProvider
from core.providers.registry import ProviderDescriptor, ProviderRegistry
from core.providers.tts.openai_tts import OpenAiCompatibleTtsProvider
from core.providers.tts.windows_sapi import WindowsSapiProvider


def build_builtin_provider_catalog() -> dict[str, ProviderRegistry[Any]]:
    """Return independent registries; callers own provider instances."""
    chat: ProviderRegistry[Any] = ProviderRegistry("chat")
    chat.register(
        ProviderDescriptor(
            "openai_compatible",
            "chat",
            capabilities={
                "streaming": True,
                "tool_calls": True,
                "cancellation": True,
                "health_check": True,
            },
        ),
        OpenAICompatibleChatProvider,
    )

    image: ProviderRegistry[Any] = ProviderRegistry("image")
    image.register(
        ProviderDescriptor(
            "openai_compatible",
            "image",
            capabilities={"editing": True, "health_check": True},
        ),
        OpenAICompatibleImageProvider,
    )

    tts: ProviderRegistry[Any] = ProviderRegistry("tts")
    tts.register(
        ProviderDescriptor(
            "windows_sapi",
            "tts",
            capabilities={"offline": True, "streaming": False},
        ),
        WindowsSapiProvider,
    )
    tts.register(
        ProviderDescriptor(
            "openai_compatible",
            "tts",
            capabilities={"offline": False, "streaming": True},
        ),
        OpenAiCompatibleTtsProvider,
    )
    return {
        "chat": chat,
        "image": image,
        "tts": tts,
        # Extension points intentionally start empty; third-party providers
        # register through the same contract without changing Core.
        "vision": ProviderRegistry("vision"),
        "embedding": ProviderRegistry("embedding"),
        "asr": ProviderRegistry("asr"),
        "realtime": ProviderRegistry("realtime"),
        "trigger": ProviderRegistry("trigger"),
    }
