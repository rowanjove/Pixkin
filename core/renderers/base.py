"""Provider-neutral character renderer boundary (v1.5 ships sprites only)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CharacterRenderer(ABC):
    @abstractmethod
    def load(self, character: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def unload(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def play(self, state: str, *, loop: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_state(self, state: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_direction(self, direction: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_expression(self, expression: str) -> None:
        raise NotImplementedError


class SpriteRenderer(CharacterRenderer):
    """Minimal callback adapter for the existing Qt sprite renderer."""

    def __init__(self, *, on_state=None):
        self.character = None
        self.state = "idle"
        self.direction = "right"
        self.expression = "neutral"
        self._on_state = on_state

    def load(self, character: Any) -> None:
        self.character = character

    def unload(self) -> None:
        self.character = None

    def play(self, state: str, *, loop: bool = False) -> None:
        del loop
        self.set_state(state)

    def set_state(self, state: str) -> None:
        self.state = str(state or "idle")
        if self._on_state:
            self._on_state(self.state)

    def set_direction(self, direction: str) -> None:
        self.direction = str(direction or "right")

    def set_expression(self, expression: str) -> None:
        self.expression = str(expression or "neutral")


__all__ = ["CharacterRenderer", "SpriteRenderer"]
