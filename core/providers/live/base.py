"""Provider-neutral contracts for livestream status checks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping


LiveRoom = Mapping[str, object]


@dataclass(frozen=True)
class LiveStatus:
    is_live: bool
    title: str = ""
    anchor_name: str = ""


@dataclass(frozen=True)
class LiveProviderCapabilities:
    status_check: bool = True
    safe_url_validation: bool = True


@dataclass(frozen=True)
class LiveProviderHealth:
    healthy: bool
    message: str


class LiveProviderError(RuntimeError):
    pass


class LiveProvider(ABC):
    """External platform boundary consumed by the live application service."""

    platform: str

    @property
    @abstractmethod
    def capabilities(self) -> LiveProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    def check(self, room: LiveRoom) -> LiveStatus:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> LiveProviderHealth:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
