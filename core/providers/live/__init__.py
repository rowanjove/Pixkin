"""Livestream provider contracts and built-in platform implementations."""

from core.providers.live.base import (
    LiveProvider,
    LiveProviderCapabilities,
    LiveProviderError,
    LiveProviderHealth,
    LiveRoom,
    LiveStatus,
)
from core.providers.live.platforms import (
    ADAPTERS,
    BilibiliLiveAdapter,
    DouyinLiveAdapter,
    LiveProviderRouter,
    check_live_room,
)

__all__ = [
    "ADAPTERS",
    "BilibiliLiveAdapter",
    "DouyinLiveAdapter",
    "LiveProvider",
    "LiveProviderCapabilities",
    "LiveProviderError",
    "LiveProviderHealth",
    "LiveProviderRouter",
    "LiveRoom",
    "LiveStatus",
    "check_live_room",
]
