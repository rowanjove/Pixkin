import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock
from unittest import mock

from core.providers.tts.base import (
    TtsError,
    TtsProviderCapabilities,
    TtsProviderHealth,
    TtsVoice,
)
from core.providers.tts.openai_tts import OpenAiCompatibleTtsProvider
from core.providers.tts.windows_sapi import WindowsSapiProvider


class TtsProviderTests(unittest.TestCase):
    def test_tts_voice_and_capabilities_dataclass(self):
        voice = TtsVoice(id="v1", name="Voice 1", language="zh-CN", gender="female")
        self.assertEqual(voice.id, "v1")
        self.assertEqual(voice.name, "Voice 1")

        caps = TtsProviderCapabilities(streaming=True, offline=True)
        self.assertTrue(caps.streaming)
        self.assertTrue(caps.offline)

    def test_windows_sapi_provider_list_voices_and_health(self):
        provider = WindowsSapiProvider()
        token = MagicMock()
        token.GetDescription.return_value = "Microsoft Huihui Desktop - Chinese"
        token.Id = "ZH-CN-FAKE"
        fake_voice = MagicMock()
        fake_voice.GetVoices.return_value = [token]
        with mock.patch.object(
            provider,
            "_voice_com",
            return_value=nullcontext(fake_voice),
        ):
            health = provider.health_check()
            voices = provider.list_voices()
        self.assertIsInstance(health, TtsProviderHealth)
        self.assertTrue(health.healthy)
        self.assertGreater(health.voices_count, 0)

        self.assertIsInstance(voices, list)
        self.assertGreater(len(voices), 0)
        first_voice = voices[0]
        self.assertTrue(first_voice.id)

    def test_windows_sapi_synthesize_empty_or_whitespace(self):
        provider = WindowsSapiProvider()
        data = provider.synthesize("")
        self.assertEqual(data, b"")
        data = provider.synthesize("   ")
        self.assertEqual(data, b"")

    def test_openai_tts_url_and_capabilities(self):
        provider = OpenAiCompatibleTtsProvider(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="custom-tts",
        )
        self.assertEqual(provider._speech_url(), "https://api.example.com/v1/audio/speech")
        self.assertTrue(provider.capabilities.streaming)
        self.assertFalse(provider.capabilities.offline)

        health = provider.health_check()
        self.assertTrue(health.healthy)

    def test_openai_tts_rejects_insecure_or_credentialed_endpoints(self):
        insecure = OpenAiCompatibleTtsProvider(
            base_url="http://api.example.com/v1"
        )
        with self.assertRaises(TtsError) as insecure_error:
            insecure._speech_url()
        self.assertEqual(insecure_error.exception.category, "invalid_url")

        credentialed = OpenAiCompatibleTtsProvider(
            base_url="https://user:secret@api.example.com/v1?token=leak"
        )
        with self.assertRaises(TtsError):
            credentialed._speech_url()

        local = OpenAiCompatibleTtsProvider(
            base_url="http://localhost:8080/v1"
        )
        self.assertEqual(
            local._speech_url(),
            "http://localhost:8080/v1/audio/speech",
        )

    def test_openai_tts_synthesize_mock_success(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = b"fake_mp3_data"
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"fake_mp3_data"]
        mock_session.post.return_value = mock_resp

        provider = OpenAiCompatibleTtsProvider(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            session=mock_session,
        )
        audio = provider.synthesize("你好世界", voice="nova", speed=1.2)
        self.assertEqual(audio, b"fake_mp3_data")
        mock_session.post.assert_called_once()
        args, kwargs = mock_session.post.call_args
        self.assertEqual(kwargs["json"]["input"], "你好世界")
        self.assertEqual(kwargs["json"]["voice"], "nova")
        self.assertEqual(kwargs["json"]["speed"], 1.2)

    def test_openai_tts_synthesize_network_error(self):
        import requests
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.ConnectionError("Connection refused")

        provider = OpenAiCompatibleTtsProvider(
            base_url="https://api.example.com/v1",
            session=mock_session,
        )
        with self.assertRaises(TtsError) as ctx:
            provider.synthesize("测试错误")
        self.assertIn("TTS 请求失败", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
