"""Portable model profile with capability overrides."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model_id: str
    context_window: int | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_structured_output: bool | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.provider).strip() or not str(self.model_id).strip():
            raise ValueError("model profile provider and model_id are required")
        if self.context_window is not None and self.context_window < 256:
            raise ValueError("context_window must be at least 256")

    @classmethod
    def from_config(cls, value: Mapping[str, Any]) -> "ModelProfile":
        if not isinstance(value, Mapping):
            raise ValueError("model profile must be an object")
        profile = value.get("profile") or {}
        if not isinstance(profile, Mapping):
            raise ValueError("model profile.profile must be an object")

        def optional_bool(key: str) -> bool | None:
            raw = profile.get(key)
            return raw if isinstance(raw, bool) else None

        raw_window = profile.get("context_window")
        window = int(raw_window) if isinstance(raw_window, int) else None
        return cls(
            provider=str(value.get("provider") or "").strip(),
            model_id=str(value.get("model_id") or "").strip(),
            context_window=window,
            supports_tools=optional_bool("supports_tools"),
            supports_vision=optional_bool("supports_vision"),
            supports_structured_output=optional_bool("supports_structured_output"),
            overrides=dict(profile),
        )

    def to_config(self) -> dict[str, Any]:
        profile = dict(self.overrides)
        if self.context_window is not None:
            profile["context_window"] = self.context_window
        for key, value in (
            ("supports_tools", self.supports_tools),
            ("supports_vision", self.supports_vision),
            ("supports_structured_output", self.supports_structured_output),
        ):
            if value is not None:
                profile[key] = value
        return {"provider": self.provider, "model_id": self.model_id, "profile": profile}


__all__ = ["ModelProfile"]
