import enum
import random
import time
from collections import deque

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class PetState(enum.Enum):
    IDLE = "idle"
    BLINK = "blink"
    LOOK_AROUND = "look_around"
    STRETCH = "stretch"
    WAVE = "wave"
    NOD = "nod"
    SLEEP = "sleep"
    WAKE = "wake"
    LISTENING = "listening"
    THINKING = "thinking"
    TALKING = "talking"
    WORKING = "working"
    WAITING = "waiting"
    SUCCESS = "success"
    FAILED = "failed"
    TOUCH = "touch"
    HAPPY = "happy"
    ANNOYED = "annoyed"
    DRAGGING = "dragging"
    WALK_LEFT = "walk_left"
    WALK_RIGHT = "walk_right"
    RUN_LEFT = "run_left"
    RUN_RIGHT = "run_right"
    JUMP = "jump"
    LAND = "land"
    ALERTING = "alerting"
    ALERTING_IMPORTANT = "alerting_important"
    CELEBRATE_LIVE = "celebrate_live"

    # v1 compatibility state. New packages use directional edge states.
    EDGE_DOCKED = "edge_docked"

    EDGE_ENTER_LEFT = "edge_enter_left"
    EDGE_IDLE_LEFT = "edge_idle_left"
    EDGE_HOVER_LEFT = "edge_hover_left"
    EDGE_EXIT_LEFT = "edge_exit_left"
    EDGE_ENTER_RIGHT = "edge_enter_right"
    EDGE_IDLE_RIGHT = "edge_idle_right"
    EDGE_HOVER_RIGHT = "edge_hover_right"
    EDGE_EXIT_RIGHT = "edge_exit_right"
    EDGE_ENTER_TOP = "edge_enter_top"
    EDGE_IDLE_TOP = "edge_idle_top"
    EDGE_HOVER_TOP = "edge_hover_top"
    EDGE_EXIT_TOP = "edge_exit_top"
    EDGE_ENTER_BOTTOM = "edge_enter_bottom"
    EDGE_IDLE_BOTTOM = "edge_idle_bottom"
    EDGE_HOVER_BOTTOM = "edge_hover_bottom"
    EDGE_EXIT_BOTTOM = "edge_exit_bottom"


STATE_PRIORITIES = {
    PetState.DRAGGING: 100,
    PetState.ALERTING_IMPORTANT: 90,
    PetState.CELEBRATE_LIVE: 90,
    PetState.TOUCH: 80,
    PetState.ANNOYED: 80,
    PetState.LISTENING: 70,
    PetState.THINKING: 70,
    PetState.TALKING: 70,
    PetState.WORKING: 70,
    PetState.SUCCESS: 70,
    PetState.FAILED: 70,
    PetState.WALK_LEFT: 60,
    PetState.WALK_RIGHT: 60,
    PetState.RUN_LEFT: 60,
    PetState.RUN_RIGHT: 60,
    PetState.JUMP: 60,
    PetState.LAND: 60,
    PetState.EDGE_DOCKED: 60,
    PetState.EDGE_ENTER_LEFT: 60,
    PetState.EDGE_IDLE_LEFT: 60,
    PetState.EDGE_HOVER_LEFT: 60,
    PetState.EDGE_EXIT_LEFT: 60,
    PetState.EDGE_ENTER_RIGHT: 60,
    PetState.EDGE_IDLE_RIGHT: 60,
    PetState.EDGE_HOVER_RIGHT: 60,
    PetState.EDGE_EXIT_RIGHT: 60,
    PetState.EDGE_ENTER_TOP: 60,
    PetState.EDGE_IDLE_TOP: 60,
    PetState.EDGE_HOVER_TOP: 60,
    PetState.EDGE_EXIT_TOP: 60,
    PetState.EDGE_ENTER_BOTTOM: 60,
    PetState.EDGE_IDLE_BOTTOM: 60,
    PetState.EDGE_HOVER_BOTTOM: 60,
    PetState.EDGE_EXIT_BOTTOM: 60,
    PetState.WAVE: 40,
    PetState.STRETCH: 40,
    PetState.NOD: 40,
    PetState.HAPPY: 40,
    PetState.ALERTING: 40,
}


class PetAnimator(QObject):
    """Priority-aware pet animation state scheduler."""

    state_changed = pyqtSignal(PetState)
    frame_updated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.current_state = PetState.IDLE
        self._priority_overrides = {}
        self._interruptible_overrides = {}
        self._active_priority = self.priority_for(PetState.IDLE)
        self._resume_stack = []
        self._sequence = []
        self._timer_mode = None
        self._recent_ambient = deque(maxlen=3)
        self._last_ambient_at = {}
        self.idle_interval_seconds = (7.0, 14.0)
        self.ambient_weights = {
            PetState.BLINK: 5.0,
            PetState.LOOK_AROUND: 2.0,
            PetState.NOD: 1.0,
            PetState.STRETCH: 0.8,
            PetState.WAVE: 0.6,
            PetState.SLEEP: 0.35,
        }
        self.cooldown_seconds = {
            PetState.WAVE: 45.0,
            PetState.STRETCH: 60.0,
            PetState.SLEEP: 90.0,
        }
        self.speed_multiplier = 1.0

        self._durations = {
            PetState.BLINK: 420,
            PetState.LOOK_AROUND: 1000,
            PetState.STRETCH: 1500,
            PetState.WAVE: 1800,
            PetState.NOD: 1100,
            PetState.SLEEP: 2600,
            PetState.WAKE: 900,
            PetState.SUCCESS: 1400,
            PetState.FAILED: 1900,
            PetState.TOUCH: 1100,
            PetState.HAPPY: 1400,
            PetState.ANNOYED: 1400,
            PetState.ALERTING: 2400,
            PetState.ALERTING_IMPORTANT: 3200,
            PetState.CELEBRATE_LIVE: 3200,
            PetState.WALK_LEFT: 1800,
            PetState.WALK_RIGHT: 1800,
            PetState.RUN_LEFT: 1050,
            PetState.RUN_RIGHT: 1050,
            PetState.JUMP: 900,
        }

        self._random_timer = QTimer(self)
        self._random_timer.setSingleShot(True)
        self._random_timer.timeout.connect(self._on_random_trigger)
        self._schedule_next_ambient()

        self._action_timer = QTimer(self)
        self._action_timer.setSingleShot(True)
        self._action_timer.timeout.connect(self._on_action_timeout)

    def priority_for(self, state: PetState) -> int:
        return self._priority_overrides.get(
            state, STATE_PRIORITIES.get(state, 10)
        )

    def configure_animation_policies(self, animations):
        """应用当前角色包声明的优先级和可打断规则。"""
        priorities = {}
        interruptible = {}
        for state_name, animation in (animations or {}).items():
            try:
                state = PetState(str(state_name))
                declared_priority = max(
                    0, min(100, int(animation.priority))
                )
                priorities[state] = (
                    100 if state == PetState.DRAGGING
                    else declared_priority
                )
                interruptible[state] = bool(animation.interruptible)
            except (TypeError, ValueError, AttributeError):
                continue
        self._priority_overrides = priorities
        self._interruptible_overrides = interruptible
        self._active_priority = self.priority_for(self.current_state)

    def is_interruptible(self, state: PetState) -> bool:
        return self._interruptible_overrides.get(state, True)

    def configure_behavior(self, behavior):
        behavior = behavior if isinstance(behavior, dict) else {}
        self.idle_interval_seconds = (7.0, 14.0)
        self.ambient_weights = {
            PetState.BLINK: 5.0,
            PetState.LOOK_AROUND: 2.0,
            PetState.NOD: 1.0,
            PetState.STRETCH: 0.8,
            PetState.WAVE: 0.6,
            PetState.SLEEP: 0.35,
        }
        self.cooldown_seconds = {
            PetState.WAVE: 45.0,
            PetState.STRETCH: 60.0,
            PetState.SLEEP: 90.0,
        }
        self.speed_multiplier = 1.0
        interval = behavior.get("idle_interval_seconds", self.idle_interval_seconds)
        if isinstance(interval, (list, tuple)) and len(interval) == 2:
            try:
                lower, upper = float(interval[0]), float(interval[1])
                lower = max(2.0, lower)
                upper = max(lower, upper)
                self.idle_interval_seconds = (lower, upper)
            except (TypeError, ValueError):
                pass

        weights = behavior.get("ambient_weights")
        provided_weight_states = set()
        if isinstance(weights, dict):
            parsed = {}
            for state_name, weight in weights.items():
                try:
                    state = PetState(str(state_name))
                    value = max(0.0, float(weight))
                except (ValueError, TypeError):
                    continue
                provided_weight_states.add(state)
                if value:
                    parsed[state] = value
            self.ambient_weights = parsed

        temperament = str(
            behavior.get("motion_temperament", "")
        ).lower()
        movement_factor = {
            "calm": 0.65,
            "precise": 0.8,
            "gentle": 0.55,
            "lively": 1.35,
        }.get(temperament, 1.0)
        movement_weights = {
            PetState.WALK_LEFT: 0.7,
            PetState.WALK_RIGHT: 0.7,
            PetState.RUN_LEFT: 0.18,
            PetState.RUN_RIGHT: 0.18,
            PetState.JUMP: 0.4,
            PetState.HAPPY: 0.35,
        }
        for state, weight in movement_weights.items():
            if (
                state in self._priority_overrides
                and state not in provided_weight_states
            ):
                self.ambient_weights[state] = weight * movement_factor
        for state, seconds in {
            PetState.WALK_LEFT: 24.0,
            PetState.WALK_RIGHT: 24.0,
            PetState.RUN_LEFT: 55.0,
            PetState.RUN_RIGHT: 55.0,
            PetState.JUMP: 35.0,
            PetState.HAPPY: 30.0,
        }.items():
            self.cooldown_seconds.setdefault(state, seconds)

        cooldowns = behavior.get("cooldown_seconds")
        if isinstance(cooldowns, dict):
            parsed = {}
            for state_name, seconds in cooldowns.items():
                try:
                    state = PetState(str(state_name))
                    parsed[state] = max(0.0, float(seconds))
                except (ValueError, TypeError):
                    continue
            self.cooldown_seconds.update(parsed)

        try:
            self.speed_multiplier = max(
                0.5, min(2.0, float(behavior.get("speed_multiplier", 1.0)))
            )
        except (TypeError, ValueError):
            self.speed_multiplier = 1.0
        self._schedule_next_ambient()

    def set_state(self, new_state: PetState):
        """Force a state and discard transient restore context."""
        self._sequence.clear()
        self._resume_stack.clear()
        self._timer_mode = None
        self._action_timer.stop()
        self._set_state(new_state, self.priority_for(new_state))

    def request_state(
        self,
        new_state: PetState,
        *,
        duration_ms=None,
        restore=False,
        priority=None,
        complete_current=False,
    ) -> bool:
        """Request a state without allowing lower priority interruptions."""
        requested_priority = (
            self.priority_for(new_state) if priority is None else int(priority)
        )
        if (
            new_state != self.current_state
            and new_state != PetState.DRAGGING
            and not complete_current
            and not self.is_interruptible(self.current_state)
        ):
            return False
        if requested_priority < self._active_priority:
            return False
        if new_state == self.current_state and duration_ms is None:
            return True

        previous = (self.current_state, self._active_priority)
        self._sequence.clear()
        self._action_timer.stop()
        if restore and new_state != self.current_state:
            self._resume_stack.append(previous)
        elif not restore:
            self._resume_stack.clear()
        self._set_state(new_state, requested_priority)
        if duration_ms is not None:
            self._timer_mode = "transient"
            self._action_timer.start(max(1, int(duration_ms)))
        else:
            self._timer_mode = None
        return True

    def finish_transient(self):
        if self._timer_mode != "transient":
            return
        self._action_timer.stop()
        self._timer_mode = None
        if self._resume_stack:
            state, priority = self._resume_stack.pop()
            self._set_state(state, priority)
        else:
            self._set_state(PetState.IDLE, self.priority_for(PetState.IDLE))

    def _set_state(self, new_state: PetState, priority=None):
        self._active_priority = (
            self.priority_for(new_state) if priority is None else int(priority)
        )
        if self.current_state != new_state:
            self.current_state = new_state
            self.state_changed.emit(new_state)

    def play_alert(self):
        """v1-compatible alert that restores the previous context."""
        self.request_state(
            PetState.ALERTING,
            duration_ms=3200,
            restore=True,
            priority=90,
        )

    def play_sequence(self, states):
        if self.current_state != PetState.IDLE:
            return False
        self._sequence = list(states)
        self._resume_stack.clear()
        self._timer_mode = "sequence"
        self._advance_sequence()
        return True

    def _duration_for(self, state: PetState) -> int:
        return max(
            120,
            int(self._durations.get(state, 1200) / self.speed_multiplier),
        )

    def _advance_sequence(self):
        if self._sequence:
            state = self._sequence.pop(0)
            self._set_state(state, self.priority_for(state))
            self._action_timer.start(self._duration_for(state))
        else:
            self._timer_mode = None
            self._set_state(PetState.IDLE, self.priority_for(PetState.IDLE))

    def _on_action_timeout(self):
        if self._timer_mode == "sequence":
            self._advance_sequence()
        elif self._timer_mode == "transient":
            self.finish_transient()

    def _schedule_next_ambient(self):
        if not hasattr(self, "_random_timer"):
            return
        lower, upper = self.idle_interval_seconds
        self._random_timer.start(int(random.uniform(lower, upper) * 1000))

    def _on_random_trigger(self):
        try:
            if self.current_state != PetState.IDLE:
                return
            now = time.monotonic()
            choices = []
            weights = []
            for state, weight in self.ambient_weights.items():
                if state in self._recent_ambient:
                    continue
                cooldown = self.cooldown_seconds.get(state, 0.0)
                if now - self._last_ambient_at.get(state, 0.0) < cooldown:
                    continue
                choices.append(state)
                weights.append(weight)
            if not choices:
                return
            state = random.choices(choices, weights=weights, k=1)[0]
            self._recent_ambient.append(state)
            self._last_ambient_at[state] = now
            self.play_sequence([state])
        finally:
            self._schedule_next_ambient()
