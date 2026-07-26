"""Explicit entry-point protocol for optional livestream adapters."""

from __future__ import annotations

from importlib import metadata

from core.providers.live.base import LiveProvider


class LiveProviderPluginError(ValueError):
    pass


class LiveProviderRegistry:
    """Load only user-enabled adapters implementing protocol version 1."""

    ENTRY_POINT_GROUP = "pixkin.live_providers"
    API_VERSION = 1

    def __init__(self, builtins=()):
        self.providers = {
            provider.platform: provider for provider in builtins
        }

    def load_enabled(
        self,
        enabled_ids,
        *,
        entry_points=None,
    ) -> dict[str, LiveProvider]:
        enabled = {str(value) for value in enabled_ids}
        candidates = (
            entry_points
            if entry_points is not None
            else metadata.entry_points().select(
                group=self.ENTRY_POINT_GROUP
            )
        )
        loaded = dict(self.providers)
        for point in candidates:
            if point.name not in enabled:
                continue
            provider = point.load()()
            self._validate(provider, expected_id=point.name)
            if provider.platform in loaded:
                raise LiveProviderPluginError(
                    f"直播适配器 ID 冲突：{provider.platform}"
                )
            loaded[provider.platform] = provider
        return loaded

    @classmethod
    def _validate(cls, provider, *, expected_id: str):
        if (
            not isinstance(provider, LiveProvider)
            or provider.platform != expected_id
            or getattr(provider, "api_version", None)
            != cls.API_VERSION
            or getattr(provider, "credential_scope", None)
            != "per_room"
            or not provider.capabilities.status_check
            or not provider.capabilities.safe_url_validation
        ):
            raise LiveProviderPluginError(
                f"直播适配器不符合安全协议：{expected_id}"
            )
