"""Provider-neutral livestream monitoring policy and trusted room state."""

from __future__ import annotations

import random
from datetime import datetime
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping

from core.providers.live import LiveRoom, LiveStatus, check_live_room


LiveChecker = Callable[[LiveRoom], LiveStatus]
JitterSource = Callable[[], float]


@dataclass(frozen=True)
class LiveRoomState:
    room_key: str
    platform: str
    is_live: bool | None = None
    title: str = ""
    anchor_name: str = ""
    error: str = ""
    consecutive_failures: int = 0
    last_success_at: float | None = None
    last_success_wall_time: float | None = None
    next_check_at: float = 0.0
    retry_after_seconds: float = 0.0
    last_notified_at: float | None = None
    notification_pending: bool = False
    group: str = ""

    @property
    def has_trusted_status(self) -> bool:
        return self.last_success_at is not None


@dataclass(frozen=True)
class LiveCheckOutcome:
    state: LiveRoomState
    should_notify: bool


@dataclass(frozen=True)
class LiveCycleSummary:
    live_count: int
    total_count: int
    error_count: int


class LiveService:
    """Own status transitions, notification policy, and per-room backoff."""

    def __init__(
        self,
        rooms: Iterable[Mapping[str, object]],
        *,
        interval_seconds: int = 60,
        notify_if_live_on_start: bool = False,
        checker: LiveChecker = check_live_room,
        max_backoff_seconds: int = 900,
        jitter_source: JitterSource = random.random,
        quiet_start: str = "",
        quiet_end: str = "",
        repeat_reminder_minutes: int = 0,
    ):
        self.rooms = [
            dict(room) for room in rooms if room.get("enabled", True)
        ]
        self.interval_seconds = max(15, int(interval_seconds))
        self.notify_if_live_on_start = bool(notify_if_live_on_start)
        self.max_backoff_seconds = max(
            self.interval_seconds, int(max_backoff_seconds)
        )
        self._checker = checker
        self._jitter_source = jitter_source
        self.quiet_start = self._parse_clock(quiet_start)
        self.quiet_end = self._parse_clock(quiet_end)
        self.repeat_reminder_seconds = max(
            0, int(repeat_reminder_minutes)
        ) * 60
        self._states: Dict[str, LiveRoomState] = {}

    @staticmethod
    def room_key(room: Mapping[str, object]) -> str:
        return (
            f"{room.get('platform', '')}:"
            f"{room.get('room_id') or room.get('room_url') or ''}"
        )

    def check(self, room: Mapping[str, object]) -> LiveStatus:
        return self._checker(room)

    def due_rooms(self, now: float) -> list[Dict[str, object]]:
        return [
            room
            for room in self.rooms
            if self._states.get(
                self.room_key(room),
                LiveRoomState(
                    room_key=self.room_key(room),
                    platform=str(room.get("platform") or ""),
                ),
            ).next_check_at
            <= now
        ]

    def record_success(
        self,
        room: Mapping[str, object],
        status: LiveStatus,
        *,
        now: float,
        wall_time: float | None = None,
    ) -> LiveCheckOutcome:
        key = self.room_key(room)
        previous = self._states.get(key)
        previous_live = previous.is_live if previous else None
        should_notify = status.is_live and (
            previous_live is False
            or (
                previous_live is None
                and self.notify_if_live_on_start
            )
        )
        wall_time = now if wall_time is None else wall_time
        pending = bool(
            previous
            and previous.notification_pending
            and status.is_live
        )
        repeat_due = bool(
            status.is_live
            and previous
            and previous.last_notified_at is not None
            and self.repeat_reminder_seconds
            and wall_time - previous.last_notified_at
            >= self.repeat_reminder_seconds
        )
        should_notify = bool(should_notify or pending or repeat_due)
        quiet = self._is_quiet(wall_time)
        notification_pending = bool(
            status.is_live and should_notify and quiet
        )
        should_notify = bool(should_notify and not quiet)
        last_notified_at = (
            wall_time
            if should_notify
            else (
                previous.last_notified_at
                if previous
                else None
            )
        )
        state = LiveRoomState(
            room_key=key,
            platform=str(room.get("platform") or ""),
            is_live=status.is_live,
            title=status.title,
            anchor_name=(
                status.anchor_name
                or str(room.get("anchor_name") or "")
            ),
            last_success_at=now,
            last_success_wall_time=wall_time,
            next_check_at=now + self.interval_seconds,
            last_notified_at=last_notified_at,
            notification_pending=notification_pending,
            group=str(room.get("group") or ""),
        )
        self._states[key] = state
        return LiveCheckOutcome(state=state, should_notify=should_notify)

    def record_failure(
        self,
        room: Mapping[str, object],
        error: Exception,
        *,
        now: float,
    ) -> LiveCheckOutcome:
        key = self.room_key(room)
        previous = self._states.get(key)
        failures = (previous.consecutive_failures if previous else 0) + 1
        exponent = min(failures - 1, 10)
        nominal_delay = min(
            self.max_backoff_seconds,
            self.interval_seconds * (2**exponent),
        )
        jitter = 0.9 + (0.2 * float(self._jitter_source()))
        retry_after = max(1.0, nominal_delay * jitter)
        state = LiveRoomState(
            room_key=key,
            platform=str(room.get("platform") or ""),
            is_live=previous.is_live if previous else None,
            title=previous.title if previous else "",
            anchor_name=(
                previous.anchor_name
                if previous
                else str(room.get("anchor_name") or "")
            ),
            error=str(error),
            consecutive_failures=failures,
            last_success_at=previous.last_success_at if previous else None,
            last_success_wall_time=(
                previous.last_success_wall_time
                if previous
                else None
            ),
            next_check_at=now + retry_after,
            retry_after_seconds=retry_after,
            last_notified_at=(
                previous.last_notified_at if previous else None
            ),
            notification_pending=(
                previous.notification_pending if previous else False
            ),
            group=str(room.get("group") or ""),
        )
        self._states[key] = state
        return LiveCheckOutcome(state=state, should_notify=False)

    def state_for(
        self, room_or_key: Mapping[str, object] | str
    ) -> LiveRoomState | None:
        key = (
            room_or_key
            if isinstance(room_or_key, str)
            else self.room_key(room_or_key)
        )
        return self._states.get(key)

    def summary(self) -> LiveCycleSummary:
        return LiveCycleSummary(
            live_count=sum(
                state.is_live is True for state in self._states.values()
            ),
            total_count=len(self.rooms),
            error_count=sum(
                bool(state.error) for state in self._states.values()
            ),
        )

    def seconds_until_next_check(self, now: float) -> float:
        if not self.rooms:
            return float(self.interval_seconds)
        due_at = [
            self._states.get(
                self.room_key(room),
                LiveRoomState(
                    room_key=self.room_key(room),
                    platform=str(room.get("platform") or ""),
                ),
            ).next_check_at
            for room in self.rooms
        ]
        return max(0.05, min(due_at) - now)

    @staticmethod
    def _parse_clock(value: str) -> int | None:
        clean = str(value or "").strip()
        if not clean:
            return None
        try:
            hour, minute = map(int, clean.split(":"))
        except (TypeError, ValueError):
            raise ValueError("免打扰时间必须使用 HH:MM")
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("免打扰时间必须使用 HH:MM")
        return hour * 60 + minute

    def _is_quiet(self, wall_time: float) -> bool:
        if self.quiet_start is None or self.quiet_end is None:
            return False
        try:
            current = datetime.fromtimestamp(wall_time).astimezone()
        except (OSError, OverflowError, ValueError):
            return False
        minute = current.hour * 60 + current.minute
        if self.quiet_start == self.quiet_end:
            return True
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= minute < self.quiet_end
        return minute >= self.quiet_start or minute < self.quiet_end
