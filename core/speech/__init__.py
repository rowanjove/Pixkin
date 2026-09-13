"""Provider-neutral speech pipeline primitives.

The desktop UI may still use push-to-talk, while these primitives make VAD,
interruption and sentence streaming testable without audio hardware.
"""

from core.speech.interruption import SpeechInterruptionController, SpeechState
from core.speech.pipeline import SpeechPipeline, SpeechPipelineError
from core.speech.vad import VoiceActivityDetector, VadConfig, VadEvent

__all__ = [
    "SpeechInterruptionController",
    "SpeechState",
    "SpeechPipeline",
    "SpeechPipelineError",
    "VoiceActivityDetector",
    "VadConfig",
    "VadEvent",
]
