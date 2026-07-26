import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication

from core.config import ConfigManager
from core.services.global_hotkey_service import (
    GlobalHotkeyManager,
    HotkeyError,
    parse_hotkey,
)
from core.services.session_secret_store import SessionSecretStore
from core.services.voice_input_service import (
    VoiceEndpoint,
    VoiceInputError,
    VoiceTranscriptionService,
)
from ui.voice_input_components import VoiceSettingsPanel


class Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"text": "转写结果"}


class VoiceAndHotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_voice_endpoint_requires_https_and_disables_redirects(self):
        calls = []
        service = VoiceTranscriptionService(
            VoiceEndpoint("https://voice.example/v1", "whisper-1"),
            post=lambda *args, **kwargs: (
                calls.append((args, kwargs)) or Response()
            ),
        )

        result = service.transcribe(b"RIFF-audio", api_key="secret")

        self.assertEqual(result, "转写结果")
        self.assertEqual(
            calls[0][0][0],
            "https://voice.example/v1/audio/transcriptions",
        )
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertEqual(
            calls[0][1]["headers"]["Authorization"],
            "Bearer secret",
        )
        with self.assertRaises(VoiceInputError):
            VoiceEndpoint("http://voice.example/v1", "model").transcription_url()

    def test_voice_service_requires_independent_key_and_bounded_audio(self):
        service = VoiceTranscriptionService(
            VoiceEndpoint("https://voice.example/v1", "model"),
            post=MagicMock(),
        )

        with self.assertRaises(VoiceInputError):
            service.transcribe(b"audio", api_key="")
        with self.assertRaises(VoiceInputError):
            service.transcribe(b"", api_key="secret")

    def test_hotkeys_parse_register_conflict_and_cleanup(self):
        registrations = []
        unregistrations = []
        app = MagicMock()

        def register(_window, identifier, modifiers, key):
            registrations.append((identifier, modifiers, key))
            return len(registrations) == 1

        manager = GlobalHotkeyManager(
            app,
            register=register,
            unregister=lambda _window, identifier: (
                unregistrations.append(identifier) or True
            ),
        )
        conflicts = manager.register(
            {
                "toggle_pet": ("Ctrl+Alt+P", MagicMock()),
                "open_chat": ("Ctrl+Alt+C", MagicMock()),
            }
        )

        self.assertEqual(conflicts, ["open_chat"])
        self.assertEqual(parse_hotkey("Ctrl+Alt+P").virtual_key, ord("P"))
        app.installNativeEventFilter.assert_called_once_with(manager)
        manager.close()
        self.assertEqual(unregistrations, [registrations[0][0]])
        with self.assertRaises(HotkeyError):
            parse_hotkey("P")

    def test_settings_exposes_persistent_mic_state_and_separate_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            panel = VoiceSettingsPanel(config, SessionSecretStore())

            values = panel.values()

            self.assertFalse(values.enabled)
            self.assertEqual(
                values.base_url,
                "https://api.openai.com/v1",
            )
            self.assertEqual(
                set(values.hotkeys),
                {
                    "toggle_pet",
                    "open_chat",
                    "stop_generation",
                    "mute_voice",
                },
            )
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
