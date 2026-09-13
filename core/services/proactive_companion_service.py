"""Rule-based proactive companion decision engine."""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from core.pet_animator import PetState
from core.services.context_sensor_service import DesktopContextSnapshot


@dataclass(frozen=True)
class ProactivePolicyConfig:
    """Configuration governing proactive companion nudges."""

    enabled: bool = False
    quiet_fullscreen: bool = True
    work_stretch_reminder: bool = True
    work_stretch_interval_minutes: int = 90
    sleep_guard: bool = False
    sleep_guard_hour: int = 23
    sleep_guard_minute: int = 30
    min_prompt_interval_seconds: int = 3600


@dataclass(frozen=True)
class ProactiveEvent:
    """A proactive nudge event emitted by the companion."""

    kind: str
    target_state: PetState
    message: str


class ProactiveCompanionService:
    """Evaluate context snapshots and decide when to proactively nudge the user."""

    def __init__(
        self,
        config: ProactivePolicyConfig,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self.config = config
        self._now_fn = now_fn or datetime.now
        self._last_active_timestamp: Optional[float] = None
        self._continuous_active_seconds: float = 0.0
        self._last_nudge_timestamp: float = 0.0
        self._sleep_guard_triggered_today: Optional[str] = None

    def evaluate(self, snapshot: DesktopContextSnapshot) -> Optional[ProactiveEvent]:
        """Evaluate a desktop context snapshot against proactive rules."""
        if not self.config.enabled:
            return None

        # 1. Respect fullscreen apps/games
        if self.config.quiet_fullscreen and snapshot.is_fullscreen:
            return None

        current_time = snapshot.timestamp
        now_dt = self._now_fn()

        # Update continuous active tracking
        if self._last_active_timestamp is not None:
            elapsed = max(0.0, current_time - self._last_active_timestamp)
            if snapshot.idle_seconds < 120.0:
                self._continuous_active_seconds += elapsed
            elif snapshot.idle_seconds >= 300.0:
                # User left the desk for 5+ minutes, reset fatigue counter
                self._continuous_active_seconds = 0.0
        self._last_active_timestamp = current_time

        # Throttle checks: do not spam the user
        if (
            self._last_nudge_timestamp > 0
            and current_time - self._last_nudge_timestamp < self.config.min_prompt_interval_seconds
        ):
            return None

        # 2. Check Sleep Guard (late night health nudge)
        if self.config.sleep_guard:
            is_late_night = (
                (now_dt.hour == self.config.sleep_guard_hour and now_dt.minute >= self.config.sleep_guard_minute)
                or (now_dt.hour > self.config.sleep_guard_hour)
                or (now_dt.hour < 5)
            )
            today_str = now_dt.strftime("%Y-%m-%d")
            if is_late_night and self._sleep_guard_triggered_today != today_str:
                self._sleep_guard_triggered_today = today_str
                self._last_nudge_timestamp = current_time
                return ProactiveEvent(
                    kind="sleep_guard",
                    target_state=PetState.SLEEP,
                    message="夜深啦，小家伙有点困了呢，主人也早点休息不要熬夜哦~",
                )

        # 3. Check Work Stretch Reminder (fatigue/continuous work)
        if self.config.work_stretch_reminder:
            threshold_seconds = self.config.work_stretch_interval_minutes * 60.0
            if self._continuous_active_seconds >= threshold_seconds:
                # Reset counter and record nudge
                self._continuous_active_seconds = 0.0
                self._last_nudge_timestamp = current_time
                return ProactiveEvent(
                    kind="stretch",
                    target_state=PetState.STRETCH,
                    message="主人已经专注工作好久啦，伸个懒腰、站起来喝杯水吧！",
                )

        return None
