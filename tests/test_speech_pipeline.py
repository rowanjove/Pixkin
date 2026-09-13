import struct
import unittest

from core.speech import (
    SpeechInterruptionController,
    SpeechPipeline,
    SpeechState,
    VadConfig,
    VadEvent,
    VoiceActivityDetector,
)


def pcm(value: int, samples: int = 320) -> bytes:
    return struct.pack(f"<{samples}h", *([value] * samples))


class SpeechPipelineTests(unittest.TestCase):
    def test_vad_detects_start_and_end_with_hysteresis(self):
        vad = VoiceActivityDetector(
            VadConfig(threshold=0.01, start_frames=2, end_frames=2)
        )
        self.assertEqual(vad.feed(pcm(0)), VadEvent.SILENCE)
        self.assertEqual(vad.feed(pcm(2000)), VadEvent.SILENCE)
        self.assertEqual(vad.feed(pcm(2000)), VadEvent.SPEECH_START)
        self.assertEqual(vad.feed(pcm(2000)), VadEvent.SPEECH)
        self.assertEqual(vad.feed(pcm(0)), VadEvent.SPEECH)
        self.assertEqual(vad.feed(pcm(0)), VadEvent.SPEECH_END)

    def test_pipeline_injects_asr_and_can_be_interrupted(self):
        states = []
        controller = SpeechInterruptionController()
        controller.subscribe(states.append)
        pipeline = SpeechPipeline(
            vad=VoiceActivityDetector(
                VadConfig(threshold=0.01, start_frames=1, end_frames=1)
            ),
            asr=lambda audio: f"{len(audio)} bytes",
            controller=controller,
        )
        pipeline.start()
        pipeline.feed(pcm(2000))
        pipeline.feed(pcm(0))
        result = pipeline.finish()
        self.assertEqual(result.text, f"{len(pcm(2000))} bytes")
        self.assertEqual(states[0], SpeechState.LISTENING)
        self.assertEqual(states[-1], SpeechState.IDLE)

        pipeline.start()
        pipeline.interrupt()
        interrupted = pipeline.finish()
        self.assertTrue(interrupted.interrupted)
        self.assertEqual(interrupted.text, "")


if __name__ == "__main__":
    unittest.main()
