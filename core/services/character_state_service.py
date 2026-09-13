"""Deterministic character-life state and behavior scoring."""

from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class CharacterState:
    character_id: str
    mood: float = 0.6
    energy: float = 0.8
    attention: float = 0.5
    affinity: float = 0.5
    boredom: float = 0.1
    focus: float = 0.0
    activity: str = "idle"
    last_interaction: str = ""
    last_sleep: str = ""
    last_speech: str = ""
    environment: str = "desktop"

    def __post_init__(self) -> None:
        if not str(self.character_id).strip():
            raise ValueError("character_id cannot be empty")
        for key in ("mood", "energy", "attention", "affinity", "boredom", "focus"):
            value = float(getattr(self, key))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{key} must be between 0 and 1")


class CharacterStateService:
    """Apply explicit domain events and persist state atomically per character."""

    SCHEMA_VERSION = 1
    MAX_FILE_BYTES = 2 * 1024 * 1024
    MAX_CHARACTERS = 100
    MAX_CHARACTER_ID_CHARS = 128

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, CharacterState] = {}
        self._load()

    def get(self, character_id: str) -> CharacterState:
        key = str(character_id or "").strip()
        if not key:
            raise ValueError("character_id cannot be empty")
        return self._states.get(key, CharacterState(character_id=key))

    def transition(
        self,
        character_id: str,
        event_type: str,
        *,
        occurred_at: str | None = None,
        environment: str | None = None,
    ) -> CharacterState:
        current = self.get(character_id)
        now = occurred_at or self._now()
        event = str(event_type or "").strip().lower()
        next_state = current
        if event in {"user_interaction", "user_returned"}:
            next_state = replace(
                current,
                mood=self._adjust(current.mood, 0.04),
                energy=self._adjust(current.energy, 0.01),
                attention=0.9,
                affinity=self._adjust(current.affinity, 0.02),
                boredom=self._adjust(current.boredom, -0.18),
                focus=self._adjust(current.focus, 0.1),
                activity="engaged",
                last_interaction=now,
            )
        elif event in {"assistant_spoke", "speech_finished"}:
            next_state = replace(
                current,
                attention=0.7,
                energy=self._adjust(current.energy, -0.005),
                activity="talking",
                last_speech=now,
            )
        elif event in {"idle_tick", "system_idle"}:
            next_state = replace(
                current,
                attention=self._adjust(current.attention, -0.04),
                energy=self._adjust(current.energy, -0.01),
                boredom=self._adjust(current.boredom, 0.03),
                focus=self._adjust(current.focus, -0.02),
                activity="idle",
            )
        elif event in {"sleep", "sleep_guard"}:
            next_state = replace(
                current,
                energy=self._adjust(current.energy, 0.2),
                boredom=self._adjust(current.boredom, -0.1),
                activity="sleeping",
                last_sleep=now,
            )
        elif event in {"stretch", "proactive_stretch"}:
            next_state = replace(
                current,
                energy=self._adjust(current.energy, 0.03),
                attention=self._adjust(current.attention, 0.05),
                boredom=self._adjust(current.boredom, -0.08),
                activity="stretching",
            )
        elif event in {"wake", "startup"}:
            next_state = replace(
                current,
                energy=self._adjust(current.energy, -0.03),
                attention=0.5,
                activity="idle",
            )
        elif event:
            # Unknown domain events do not mutate state; callers can still
            # record them in EventBus without allowing arbitrary state writes.
            next_state = current
        if environment:
            next_state = replace(next_state, environment=str(environment)[:80])
        self._states[next_state.character_id] = next_state
        self._save()
        return next_state

    def behavior_score(
        self,
        *,
        base_weight: float,
        state: CharacterState,
        context_modifier: float = 1.0,
        cooldown_modifier: float = 1.0,
        personality_modifier: float = 1.0,
    ) -> float:
        """Calculate a non-negative score from state and bounded modifiers."""
        score = float(base_weight)
        score *= max(0.0, min(4.0, float(context_modifier)))
        score *= max(0.0, min(4.0, float(cooldown_modifier)))
        score *= max(0.0, min(4.0, float(personality_modifier)))
        score *= 0.7 + state.energy * 0.3
        score *= 0.8 + state.boredom * 0.4
        return max(0.0, score)

    def choose_behavior(
        self,
        candidates: Iterable[tuple[str, float]],
        *,
        seed: int | None = None,
    ) -> str | None:
        values = [(str(name), max(0.0, float(weight))) for name, weight in candidates]
        values = [(name, weight) for name, weight in values if name and weight > 0]
        if not values:
            return None
        generator = random.Random(seed)
        total = sum(weight for _, weight in values)
        pick = generator.random() * total
        for name, weight in values:
            pick -= weight
            if pick <= 0:
                return name
        return values[-1][0]

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            if self.path.stat().st_size > self.MAX_FILE_BYTES:
                raise ValueError("character state file is too large")
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, Mapping):
                raise ValueError("character state payload is invalid")
            if document.get("schema_version") != self.SCHEMA_VERSION:
                raise ValueError("character state schema is unsupported")
            values = document.get("states", {})
            if not isinstance(values, Mapping):
                raise ValueError("character state payload is invalid")
            if len(values) > self.MAX_CHARACTERS:
                raise ValueError("too many character states")
            for character_id, raw in values.items():
                if not isinstance(raw, Mapping):
                    continue
                normalized_id = str(character_id)
                if not normalized_id.strip() or len(normalized_id) > self.MAX_CHARACTER_ID_CHARS:
                    raise ValueError("character id is invalid")
                self._states[str(character_id)] = CharacterState(
                    character_id=normalized_id,
                    **{key: raw[key] for key in (
                        "mood", "energy", "attention", "affinity", "boredom",
                        "focus", "activity", "last_interaction", "last_sleep",
                        "last_speech", "environment",
                    ) if key in raw},
                )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("character state file cannot be read safely") from exc

    def _save(self) -> None:
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "states": {key: asdict(value) for key, value in self._states.items()},
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary)
        try:
            with open(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _adjust(value: float, delta: float) -> float:
        return max(0.0, min(1.0, float(value) + float(delta)))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
