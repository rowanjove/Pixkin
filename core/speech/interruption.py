"""Speech state and safe user interruption semantics."""

from __future__ import annotations

from enum import Enum
from threading import RLock
from typing import Callable


class SpeechState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    TALKING = "talking"
    INTERRUPTED = "interrupted"


class SpeechInterruptionController:
    """Small state machine shared by capture, ASR, LLM and TTS layers."""

    def __init__(self, *, on_cancel_tts: Callable[[], None] | None = None):
        self._state = SpeechState.IDLE
        self._on_cancel_tts = on_cancel_tts
        self._lock = RLock()
        self._listeners: list[Callable[[SpeechState], None]] = []

    @property
    def state(self) -> SpeechState:
        with self._lock:
            return self._state

    def subscribe(self, listener: Callable[[SpeechState], None]) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("speech state listener must be callable")
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _set(self, state: SpeechState) -> SpeechState:
        with self._lock:
            self._state = state
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(state)
            except Exception:
                # UI observers must never break audio control.
                continue
        return state

    def begin_listening(self) -> SpeechState:
        if self.state == SpeechState.TALKING and self._on_cancel_tts:
            self._on_cancel_tts()
        return self._set(SpeechState.LISTENING)

    def begin_thinking(self) -> SpeechState:
        return self._set(SpeechState.THINKING)

    def begin_talking(self) -> SpeechState:
        return self._set(SpeechState.TALKING)

    def interrupt(self) -> SpeechState:
        if self.state == SpeechState.TALKING and self._on_cancel_tts:
            self._on_cancel_tts()
        return self._set(SpeechState.INTERRUPTED)

    def finish(self) -> SpeechState:
        return self._set(SpeechState.IDLE)
