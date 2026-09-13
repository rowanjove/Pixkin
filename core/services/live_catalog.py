"""Composition helper exposing live provider descriptors to UI/controllers."""

from core.providers.live import ADAPTERS


def builtin_live_providers():
    return dict(ADAPTERS)


__all__ = ["builtin_live_providers"]
