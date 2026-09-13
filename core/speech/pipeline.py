"""Hardware-agnostic speech pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.speech.interruption import SpeechInterruptionController, SpeechState
from core.speech.vad import VadEvent, VoiceActivityDetector


class SpeechPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class SpeechPipelineResult:
    text: str
    interrupted: bool = False


class SpeechPipeline:
    """Connect PCM frames, VAD events, ASR and TTS boundaries.

    ASR is deliberately injected as a callable. A real streaming provider can
    implement it later without changing the state machine or permission edge.
    """

    def __init__(
        self,
        *,
        vad: VoiceActivityDetector | None = None,
        asr: Callable[[bytes], str] | None = None,
        controller: SpeechInterruptionController | None = None,
        on_event: Callable[[VadEvent], None] | None = None,
    ):
        self.vad = vad or VoiceActivityDetector()
        self.asr = asr
        self.controller = controller or SpeechInterruptionController()
        self.on_event = on_event
        self._frames: list[bytes] = []
        self._interrupted = False

    def start(self) -> None:
        self.vad.reset()
        self._frames.clear()
        self._interrupted = False
        self.controller.begin_listening()

    def feed(self, frame: bytes) -> VadEvent:
        if self.controller.state not in {SpeechState.LISTENING, SpeechState.INTERRUPTED}:
            raise SpeechPipelineError("speech pipeline is not listening")
        data = bytes(frame or b"")
        event = self.vad.feed(data)
        if event in {VadEvent.SPEECH_START, VadEvent.SPEECH}:
            self._frames.append(data)
        if self.on_event:
            self.on_event(event)
        if event is VadEvent.SPEECH_END:
            self.controller.begin_thinking()
        return event

    def finish(self) -> SpeechPipelineResult:
        if self._interrupted:
            self.controller.finish()
            return SpeechPipelineResult("", interrupted=True)
        if self.asr is None:
            text = ""
        else:
            text = str(self.asr(b"".join(self._frames)) or "").strip()
        self.controller.finish()
        return SpeechPipelineResult(text)

    def interrupt(self) -> None:
        self._interrupted = True
        self._frames.clear()
        self.controller.interrupt()


__all__ = ["SpeechPipeline", "SpeechPipelineError", "SpeechPipelineResult"]
