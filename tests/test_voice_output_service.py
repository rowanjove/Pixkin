import io
import struct
import unittest
import wave

from core.providers.tts.base import TtsProvider, TtsProviderCapabilities, TtsProviderHealth
from core.services.voice_output_service import (
    SentenceSplitter,
    VoiceOutputService,
    calculate_wav_rms,
)
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionResource,
    PermissionState,
)


class FakeTtsProvider(TtsProvider):
    def __init__(self):
        self.synthesized = []
        self.closed = False

    @property
    def capabilities(self):
        return TtsProviderCapabilities()

    def list_voices(self):
        return []

    def synthesize(self, text, *, voice=None, speed=1.0):
        self.synthesized.append((text, voice, speed))
        return b"RIFF" + text.encode("utf-8")

    def health_check(self):
        return TtsProviderHealth(True, "ok")

    def close(self):
        self.closed = True


class VoiceOutputServiceTests(unittest.TestCase):
    def test_sentence_splitter_primary_punctuation(self):
        splitter = SentenceSplitter(min_length=3, max_length=20)
        s1 = splitter.feed("你好啊！")
        self.assertEqual(s1, ["你好啊！"])

        s2 = splitter.feed("这是第一句。这是第二句？是的！")
        self.assertEqual(s2, ["这是第一句。", "这是第二句？", "是的！"])

    def test_sentence_splitter_incremental_feed(self):
        splitter = SentenceSplitter(min_length=2, max_length=20)
        self.assertEqual(splitter.feed("你好"), [])
        self.assertEqual(splitter.feed("朋友"), [])
        self.assertEqual(splitter.feed("！今天天气"), ["你好朋友！"])
        self.assertEqual(splitter.flush(), ["今天天气"])

    def test_sentence_splitter_long_sentence_fallback(self):
        splitter = SentenceSplitter(min_length=3, max_length=10)
        # Without punctuation, should split when buffer exceeds max_length * 2
        res = splitter.feed("这是一段非常非常非常非常非常非常长的句子没有任何标点")
        self.assertTrue(len(res) > 0)
        flushed = splitter.flush()
        self.assertTrue(len(flushed) > 0)

    def test_calculate_wav_rms(self):
        # Empty or invalid header
        self.assertEqual(calculate_wav_rms(b""), 0.0)
        self.assertEqual(calculate_wav_rms(b"INVALID"), 0.0)

        # Generate standard mono 16-bit WAV with known amplitude
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            # 100 samples with constant amplitude of 16384 (approx 0.5)
            samples = struct.pack("<100h", *([16384] * 100))
            wf.writeframes(samples)

        rms = calculate_wav_rms(buf.getvalue())
        self.assertAlmostEqual(rms, 0.5, places=1)

    def test_voice_output_service_orchestration(self):
        fake_provider = FakeTtsProvider()
        permissions = ContextPermissionService()
        permissions.set_state(
            PermissionResource.NETWORK,
            PermissionState.ALLOW_SESSION,
        )
        service = VoiceOutputService(
            fake_provider,
            voice="test-voice",
            speed=1.2,
            enabled=True,
            permission_service=permissions,
        )

        data = service.synthesize_sentence("你好小助手")
        self.assertTrue(data.startswith(b"RIFF"))
        self.assertEqual(len(fake_provider.synthesized), 1)
        self.assertEqual(fake_provider.synthesized[0][0], "你好小助手")
        self.assertEqual(fake_provider.synthesized[0][1], "test-voice")
        self.assertEqual(fake_provider.synthesized[0][2], 1.2)

        # When disabled
        service.enabled = False
        data_disabled = service.synthesize_sentence("不再发声")
        self.assertEqual(data_disabled, b"")
        self.assertEqual(len(fake_provider.synthesized), 1)

        service.close()
        self.assertTrue(fake_provider.closed)


if __name__ == "__main__":
    unittest.main()
