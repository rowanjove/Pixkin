"""Bounded, atomic persistence for privacy-preserving tool audit events."""

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.services.tool_permission_service import (
    ToolAuditEvent,
    ToolPermissionLevel,
)


class ToolAuditStore:
    """Persist the newest audit events without unbounded disk growth."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: str | Path,
        *,
        max_events: int = 1000,
        max_bytes: int = 1024 * 1024,
    ):
        self.path = Path(path)
        self.max_events = max(1, int(max_events))
        self.max_bytes = max(256, int(max_bytes))
        self._lock = threading.Lock()
        self._events = self._load()

    def append(self, event: ToolAuditEvent) -> None:
        with self._lock:
            candidates = [*self._events, event][-self.max_events :]
            candidates = self._trim_to_size(candidates)
            self._write(candidates)
            self._events = candidates

    def events(self) -> tuple[ToolAuditEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def clear(self) -> None:
        with self._lock:
            self._write([])
            self._events = []

    def export(self, destination: str | Path) -> Path:
        target = Path(destination)
        with self._lock:
            self._write_to(target, self._events)
        return target

    def _load(self) -> list[ToolAuditEvent]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != self.SCHEMA_VERSION:
                return []
            raw_events = payload.get("events")
            if not isinstance(raw_events, list):
                return []
            events = [
                self._event_from_dict(item)
                for item in raw_events[-self.max_events :]
                if isinstance(item, Mapping)
            ]
            return self._trim_to_size(events)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return []

    def _trim_to_size(
        self,
        events: list[ToolAuditEvent],
    ) -> list[ToolAuditEvent]:
        trimmed = list(events)
        while trimmed and len(self._encode(trimmed)) > self.max_bytes:
            trimmed.pop(0)
        return trimmed

    def _write(self, events: list[ToolAuditEvent]) -> None:
        self._write_to(self.path, events)

    def _write_to(
        self,
        destination: Path,
        events: list[ToolAuditEvent],
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(self._encode(events))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @classmethod
    def _encode(cls, events: list[ToolAuditEvent]) -> bytes:
        payload = {
            "schema_version": cls.SCHEMA_VERSION,
            "events": [cls._event_to_dict(event) for event in events],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _event_to_dict(event: ToolAuditEvent) -> dict[str, Any]:
        return {
            "timestamp": event.timestamp,
            "tool_name": event.tool_name,
            "level": int(event.level),
            "arguments": dict(event.arguments),
            "authorization": event.authorization,
            "result_status": event.result_status,
            "duration_ms": event.duration_ms,
            "session_id": event.session_id,
        }

    @staticmethod
    def _event_from_dict(value: Mapping[str, Any]) -> ToolAuditEvent:
        arguments = value.get("arguments", {})
        if not isinstance(arguments, Mapping):
            arguments = {}
        return ToolAuditEvent(
            timestamp=float(value.get("timestamp", 0.0)),
            tool_name=str(value.get("tool_name", "")),
            level=ToolPermissionLevel(int(value.get("level", 0))),
            arguments=dict(arguments),
            authorization=str(value.get("authorization", "")),
            result_status=str(value.get("result_status", "")),
            duration_ms=max(0, int(value.get("duration_ms", 0))),
            session_id=str(value.get("session_id", "")),
        )
