import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from core.providers.chat.base import (
    ChatProviderHealth,
    classify_chat_error,
)
from core.providers.chat.openai_compatible import (
    OpenAICompatibleChatProvider,
)
from core.services.session_secret_store import SessionSecretStore
from ui.model_settings_components import ModelSettingsPanel


class FakeError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ChatProviderHealthTests(unittest.TestCase):
    def test_health_lists_models_and_accepts_configured_model(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                list=lambda: SimpleNamespace(
                    data=[
                        SimpleNamespace(id="model-b"),
                        SimpleNamespace(id="model-a"),
                    ]
                )
            ),
            close=lambda: None,
        )
        provider = OpenAICompatibleChatProvider(
            api_key="key",
            base_url="https://example.test/v1",
            client=client,
        )

        health = provider.health_check("model-a")

        self.assertTrue(health.healthy)
        self.assertEqual(health.models, ("model-a", "model-b"))

    def test_health_reports_model_not_found_with_choices(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                list=lambda: SimpleNamespace(
                    data=[SimpleNamespace(id="available")]
                )
            ),
            close=lambda: None,
        )
        provider = OpenAICompatibleChatProvider(
            api_key="key",
            base_url="https://example.test/v1",
            client=client,
        )

        health = provider.health_check("missing")

        self.assertFalse(health.healthy)
        self.assertEqual(health.category, "model_not_found")
        self.assertEqual(health.models, ("available",))

    def test_health_classifies_authentication_failure(self):
        def fail():
            raise FakeError("invalid key", 401)

        client = SimpleNamespace(
            models=SimpleNamespace(list=fail),
            close=lambda: None,
        )
        provider = OpenAICompatibleChatProvider(
            api_key="key",
            base_url="https://example.test/v1",
            client=client,
        )

        health = provider.health_check("model")

        self.assertFalse(health.healthy)
        self.assertEqual(health.category, "authentication")
        self.assertIn("API Key", health.recovery)

    def test_error_classifier_distinguishes_recovery_categories(self):
        cases = (
            (FakeError("", 401), "authentication", False),
            (FakeError("", 403), "permission", False),
            (FakeError("", 404), "model_not_found", False),
            (FakeError("", 429), "rate_limit", True),
            (FakeError("", 504), "timeout", True),
            (FakeError("", 500), "server", True),
            (ConnectionError("offline"), "connection", True),
            (FakeError("", 400), "protocol", False),
        )
        for error, category, retriable in cases:
            with self.subTest(category=category):
                details = classify_chat_error(error)
                self.assertEqual(details.category, category)
                self.assertEqual(details.retriable, retriable)


class ModelSettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_health_result_enables_model_selection(self):
        config = SimpleNamespace(
            get=lambda section, key=None, default=None: {
                ("api", "base_url"): "https://example.test/v1",
                ("api", "api_key"): "",
                ("api", "model"): "old-model",
            }.get((section, key), default)
        )
        panel = ModelSettingsPanel(config, SessionSecretStore())

        panel._on_health_checked(
            ChatProviderHealth(
                True,
                "连接正常",
                models=("model-a", "model-b"),
            )
        )

        self.assertTrue(panel.choose_model_btn.isEnabled())
        self.assertIn("2 个模型", panel.connection_status.text())
        panel.close()
