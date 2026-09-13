"""Typed-by-kind provider registry shared by chat, image, speech and triggers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, TypeVar


LOGGER = logging.getLogger("desktop_pet.providers.registry")
ProviderT = TypeVar("ProviderT")


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    kind: str
    capabilities: Any = field(default_factory=dict)
    config_schema: dict[str, Any] = field(default_factory=dict)
    trust_level: str = "builtin"
    api_version: int = 1

    def __post_init__(self) -> None:
        if not str(self.id).strip() or not str(self.kind).strip():
            raise ValueError("provider id and kind are required")
        if self.trust_level not in {"builtin", "trusted", "isolated"}:
            raise ValueError("unsupported provider trust level")
        if self.api_version < 1:
            raise ValueError("provider api_version must be positive")


@dataclass(frozen=True)
class RegisteredProvider(Generic[ProviderT]):
    descriptor: ProviderDescriptor
    factory: Callable[..., ProviderT]


class ProviderRegistry(Generic[ProviderT]):
    """Registry with duplicate checks and provider lifecycle helpers."""

    def __init__(self, kind: str):
        self.kind = str(kind or "").strip()
        if not self.kind:
            raise ValueError("provider kind cannot be empty")
        self._providers: dict[str, RegisteredProvider[ProviderT]] = {}
        self._default_id: str | None = None

    def register(
        self,
        descriptor: ProviderDescriptor,
        factory: Callable[..., ProviderT],
        *,
        replace: bool = False,
    ) -> None:
        if descriptor.kind != self.kind:
            raise ValueError(
                f"provider kind mismatch: {descriptor.kind} != {self.kind}"
            )
        if not callable(factory):
            raise ValueError("provider factory must be callable")
        if descriptor.id in self._providers and not replace:
            raise KeyError(f"provider already registered: {descriptor.id}")
        self._providers[descriptor.id] = RegisteredProvider(descriptor, factory)
        if self._default_id is None:
            self._default_id = descriptor.id

    def unregister(self, provider_id: str) -> bool:
        removed = self._providers.pop(str(provider_id), None) is not None
        if removed and self._default_id == str(provider_id):
            self._default_id = next(iter(self._providers), None)
        return removed

    @property
    def default_id(self) -> str | None:
        return self._default_id

    def set_default(self, provider_id: str) -> None:
        if str(provider_id) not in self._providers:
            raise KeyError(f"provider not registered: {provider_id}")
        self._default_id = str(provider_id)

    def descriptor(self, provider_id: str) -> ProviderDescriptor:
        return self._providers[str(provider_id)].descriptor

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(item.descriptor for item in self._providers.values())

    def create(self, provider_id: str, **kwargs: Any) -> ProviderT:
        registered = self._providers.get(str(provider_id))
        if registered is None:
            raise KeyError(f"provider not registered: {provider_id}")
        return registered.factory(**kwargs)

    def create_default(self, **kwargs: Any) -> ProviderT:
        if self._default_id is None:
            raise KeyError(f"no {self.kind} provider registered")
        return self.create(self._default_id, **kwargs)

    def capability(self, provider_id: str, name: str, default: Any = False) -> Any:
        value = self.descriptor(provider_id).capabilities
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def health_check(self, provider_id: str, *, check_kwargs: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        provider = self.create(provider_id, **kwargs)
        try:
            check = getattr(provider, "health_check", None)
            if not callable(check):
                raise TypeError(f"provider {provider_id} has no health_check")
            return check(**(check_kwargs or {}))
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    LOGGER.warning("provider close failed id=%s", provider_id)

    def load_descriptors(
        self,
        values: Iterable[tuple[ProviderDescriptor, Callable[..., ProviderT]]],
    ) -> None:
        for descriptor, factory in values:
            self.register(descriptor, factory)
