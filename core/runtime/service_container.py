"""Explicit dependency container used only by the application composition root."""

from __future__ import annotations

from typing import Any, TypeVar


T = TypeVar("T")


class ServiceContainer:
    """Small typed-by-convention registry; UI code receives dependencies directly."""

    def __init__(self):
        self._services: dict[str, Any] = {}
        self._started: list[str] = []

    def register(self, name: str, service: Any, *, replace: bool = False) -> Any:
        key = str(name or "").strip()
        if not key:
            raise ValueError("service name cannot be empty")
        if key in self._services and not replace:
            raise KeyError(f"service already registered: {key}")
        self._services[key] = service
        return service

    def get(self, name: str, expected_type: type[T] | None = None) -> T:
        key = str(name or "").strip()
        if key not in self._services:
            raise KeyError(f"service not registered: {key}")
        service = self._services[key]
        if expected_type is not None and not isinstance(service, expected_type):
            raise TypeError(f"service {key} is not {expected_type.__name__}")
        return service

    def try_get(self, name: str, expected_type: type[T] | None = None) -> T | None:
        try:
            return self.get(name, expected_type)
        except KeyError:
            return None

    def start(self) -> None:
        started: list[str] = []
        try:
            for name, service in self._services.items():
                start = getattr(service, "start", None)
                if callable(start):
                    start()
                started.append(name)
        except Exception:
            self._stop_names(reversed(started))
            raise
        self._started = started

    def stop(self) -> None:
        self._stop_names(reversed(self._started))
        self._started.clear()

    def _stop_names(self, names) -> None:
        for name in names:
            service = self._services.get(name)
            stop = getattr(service, "stop", None)
            lifecycle = stop if callable(stop) else getattr(service, "close", None)
            if not callable(lifecycle):
                continue
            try:
                lifecycle()
            except Exception:
                # Shutdown is best effort; callers still get a clean loop.
                continue

    def names(self) -> tuple[str, ...]:
        return tuple(self._services)
