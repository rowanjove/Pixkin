"""Small, Qt-independent event bus for runtime coordination.

The bus deliberately does not persist payloads or execute handlers on a
background thread. Publishers already run in either the Qt thread or a
worker controlled by the caller; keeping dispatch synchronous makes ordering,
cancellation and tests deterministic. A faulty subscriber is isolated and
reported rather than taking down the publisher.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping


LOGGER = logging.getLogger("desktop_pet.runtime.events")
EventHandler = Callable[["EventEnvelope"], None]


@dataclass(frozen=True)
class EventEnvelope:
    """Versioned event metadata shared by sensors, triggers and services."""

    type: str
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 0
    sensitivity: str = "normal"
    schema_version: int = 1
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = ""
    occurred_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not str(self.type).strip():
            raise ValueError("event type cannot be empty")
        if not str(self.source).strip():
            raise ValueError("event source cannot be empty")
        if self.schema_version < 1:
            raise ValueError("event schema_version must be positive")
        if self.sensitivity not in {"normal", "private", "secret"}:
            raise ValueError("unsupported event sensitivity")
        if not isinstance(self.payload, Mapping):
            raise TypeError("event payload must be a mapping")
        # Keep the envelope's top-level payload immutable even when callers
        # pass a mutable dict into the frozen dataclass.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class EventDeliveryReport:
    event_id: str
    delivered: int
    failed: int
    failures: tuple[str, ...] = ()


class EventBus:
    """Thread-safe in-memory publish/subscribe bus with isolated handlers."""

    def __init__(self, *, max_subscribers_per_type: int = 100):
        self.max_subscribers_per_type = max(1, int(max_subscribers_per_type))
        self._handlers: dict[str, dict[str, EventHandler]] = defaultdict(dict)
        self._lock = threading.RLock()

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        token: str | None = None,
    ) -> str:
        event_key = str(event_type or "").strip()
        if not event_key or not callable(handler):
            raise ValueError("event_type and callable handler are required")
        subscription = str(token or uuid.uuid4().hex)
        with self._lock:
            handlers = self._handlers[event_key]
            if subscription not in handlers and len(handlers) >= self.max_subscribers_per_type:
                raise ValueError(f"too many subscribers for {event_key}")
            handlers[subscription] = handler
        return subscription

    def unsubscribe(self, event_type: str, token: str) -> bool:
        with self._lock:
            handlers = self._handlers.get(str(event_type or "").strip())
            if not handlers:
                return False
            removed = handlers.pop(str(token), None) is not None
            if not handlers:
                self._handlers.pop(str(event_type or "").strip(), None)
            return removed

    def publish(self, event: EventEnvelope) -> EventDeliveryReport:
        if not isinstance(event, EventEnvelope):
            raise TypeError("publish expects EventEnvelope")
        with self._lock:
            handlers = [
                *self._handlers.get(event.type, {}).values(),
                *self._handlers.get("*", {}).values(),
            ]
        failures: list[str] = []
        delivered = 0
        for handler in handlers:
            try:
                handler(event)
                delivered += 1
            except Exception as exc:  # subscribers are extension boundaries
                failures.append(type(exc).__name__)
                LOGGER.exception(
                    "event subscriber failed type=%s source=%s",
                    event.type,
                    event.source,
                )
        return EventDeliveryReport(
            event_id=event.event_id,
            delivered=delivered,
            failed=len(failures),
            failures=tuple(failures),
        )

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
