"""Compatibility imports for the livestream provider package."""

from core.providers.live import (
    ADAPTERS,
    BilibiliLiveAdapter,
    DouyinLiveAdapter,
    LiveProviderError,
    LiveStatus,
    check_live_room,
)


LivePlatformError = LiveProviderError

__all__ = [
    "ADAPTERS",
    "BilibiliLiveAdapter",
    "DouyinLiveAdapter",
    "LivePlatformError",
    "LiveStatus",
    "check_live_room",
]
