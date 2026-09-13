import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication, QMessageBox

from core.character_package import CharacterPackageManager
from core.config import ConfigManager
from core.secrets import SecretStore
from core.services.session_secret_store import SessionSecretStore
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionResource,
    PermissionState,
)
from main import DesktopPetApp
from ui.pet_lab_window import PetLabWindow
from ui.settings_window import SettingsWindow


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SecretMigrationTests(unittest.TestCase):
    def test_chat_and_image_plaintext_keys_migrate_then_clear_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            config.update_sections(
                {
                    "api": {"api_key": "legacy-chat"},
                    "image_generation": {"api_key": "legacy-image"},
                }
            )
            controller = DesktopPetApp.__new__(DesktopPetApp)
            controller.config_mgr = config

            with (
                patch.object(SecretStore, "get_api_key", return_value=""),
                patch.object(
                    SecretStore,
                    "set_api_key",
                    return_value=True,
                ) as set_chat,
                patch.object(
                    SecretStore,
                    "get_image_api_key",
                    return_value="",
                ),
                patch.object(
                    SecretStore,
                    "set_image_api_key",
                    return_value=True,
                ) as set_image,
                patch.object(
                    SecretStore,
                    "migrate_legacy_api_key",
                    return_value=True,
                ),
            ):
                controller._migrate_api_key()

            self.assertEqual(config.get("api", "api_key"), "")
            self.assertEqual(config.get("image_generation", "api_key"), "")
            set_chat.assert_called_once_with("legacy-chat")
            set_image.assert_called_once_with("legacy-image")

    def test_failed_config_cleanup_rolls_credential_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            config.update_section("api", {"api_key": "legacy-chat"})
            controller = DesktopPetApp.__new__(DesktopPetApp)
            controller.config_mgr = config

            with (
                patch.object(
                    SecretStore,
                    "get_api_key",
                    return_value="previous-secure",
                ),
                patch.object(
                    SecretStore,
                    "set_api_key",
                    return_value=True,
                ) as set_chat,
                patch.object(config, "update_section", return_value=False),
                patch.object(
                    SecretStore,
                    "migrate_legacy_api_key",
                    return_value=True,
                ),
            ):
                controller._migrate_api_key()

            self.assertEqual(config.get("api", "api_key"), "legacy-chat")
            self.assertEqual(
                [call.args[0] for call in set_chat.call_args_list],
                ["legacy-chat", "previous-secure"],
            )

    def test_chat_worker_never_consumes_plaintext_config_fallback(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.config_mgr = MagicMock()
        controller.config_mgr.get.side_effect = (
            lambda section, key=None, default=None: (
                "legacy-plaintext"
                if (section, key) == ("api", "api_key")
                else {
                    ("api", "base_url"): "https://example.test/v1",
                    ("api", "model"): "test-model",
                    ("pet", "system_prompt"): "system",
                }.get((section, key), default)
            )
        )
        controller.ai_worker = None
        controller.context_permission_service = ContextPermissionService()
        controller.context_permission_service.set_state(
            PermissionResource.NETWORK,
            PermissionState.ALLOW_SESSION,
        )
        controller.tool_registry = MagicMock()
        controller.chat_session = MagicMock()
        controller.chat_session.context.return_value = []
        controller.chat_session.session_id = "session"
        controller.pet_window = SimpleNamespace(
            animator=MagicMock(),
            chat_window=MagicMock(),
        )
        controller._store_message = MagicMock()
        worker = MagicMock()

        with (
            patch.object(SecretStore, "get_api_key", return_value=""),
            patch("main.AiWorkerThread", return_value=worker) as worker_type,
        ):
            controller._on_user_send_message("你好")

        self.assertEqual(worker_type.call_args.kwargs["api_key"], "")
        worker.start.assert_called_once()


class SecretUiPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_rejects_new_key_when_credential_store_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config,
                base / "characters",
            )
            settings = SettingsWindow(config, manager)
            settings.api_url_input.setText("https://changed.example/v1")
            settings.api_key_input.setText("new-secret")

            with (
                patch.object(SecretStore, "get_api_key", return_value=""),
                patch.object(SecretStore, "set_api_key", return_value=False),
                patch.object(QMessageBox, "critical") as critical,
                patch.object(settings, "accept") as accept,
            ):
                settings._save()

            self.assertEqual(config.get("api", "api_key"), "")
            self.assertNotEqual(
                config.get("api", "base_url"),
                "https://changed.example/v1",
            )
            self.assertIn("避免明文密钥", critical.call_args.args[2])
            accept.assert_not_called()
            settings.close()

    def test_validation_failure_happens_before_credential_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config,
                base / "characters",
            )
            settings = SettingsWindow(config, manager)
            settings.api_key_input.setText("new-secret")
            settings.quiet_start_input.setText("invalid")

            with (
                patch.object(
                    SecretStore,
                    "set_api_key",
                ) as save_secret,
                patch.object(QMessageBox, "warning"),
            ):
                settings._save()

            save_secret.assert_not_called()
            settings.close()

    def test_pet_lab_rejects_new_key_without_plaintext_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config,
                base / "characters",
            )
            permissions = ContextPermissionService()
            permissions.set_state(
                PermissionResource.NETWORK,
                PermissionState.ALLOW_SESSION,
                session_only=True,
            )
            lab = PetLabWindow(
                config,
                manager,
                context_permission_service=permissions,
            )
            lab.reference_paths = [base / "reference.png"]
            lab.name_input.setText("安全伙伴")
            lab.api_key.setText("new-image-secret")

            with (
                patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch.object(
                    SecretStore,
                    "get_image_api_key",
                    return_value="",
                ),
                patch.object(
                    SecretStore,
                    "set_image_api_key",
                    return_value=False,
                ),
                patch.object(QMessageBox, "critical") as critical,
                patch.object(lab, "_start_worker") as start_worker,
            ):
                lab._start_hatch()

            self.assertEqual(
                config.get("image_generation", "api_key"),
                "",
            )
            self.assertIn("避免明文密钥", critical.call_args.args[2])
            start_worker.assert_not_called()
            lab.close()

    def test_settings_can_keep_chat_key_in_memory_only(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config,
                base / "characters",
            )
            session_secrets = SessionSecretStore()
            settings = SettingsWindow(
                config,
                manager,
                session_secret_store=session_secrets,
            )
            settings.api_key_input.setText("session-chat-secret")
            settings.session_api_key_cb.setChecked(True)

            with (
                patch.object(
                    SecretStore,
                    "get_api_key",
                    return_value="persistent-old",
                ),
                patch.object(SecretStore, "set_api_key") as save_secret,
            ):
                settings._save()

            save_secret.assert_not_called()
            self.assertEqual(
                session_secrets.get_chat_api_key(),
                "session-chat-secret",
            )
            self.assertEqual(config.get("api", "api_key", ""), "")

    def test_pet_lab_can_keep_image_key_in_memory_only(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config,
                base / "characters",
            )
            session_secrets = SessionSecretStore()
            lab = PetLabWindow(
                config,
                manager,
                session_secret_store=session_secrets,
                context_permission_service=(
                    self._allowed_network_permissions()
                ),
            )
            lab.reference_paths = [base / "reference.png"]
            lab.name_input.setText("会话伙伴")
            lab.api_key.setText("session-image-secret")
            lab.session_key.setChecked(True)

            with (
                patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch.object(
                    SecretStore,
                    "get_image_api_key",
                    return_value="persistent-old",
                ),
                patch.object(
                    SecretStore,
                    "set_image_api_key",
                ) as save_secret,
                patch.object(lab, "_start_worker") as start_worker,
            ):
                lab._start_hatch()

            save_secret.assert_not_called()
            self.assertEqual(
                session_secrets.get_image_api_key(),
                "session-image-secret",
            )
            start_worker.assert_called_once()
            lab.close()

    @staticmethod
    def _allowed_network_permissions():
        permissions = ContextPermissionService()
        permissions.set_state(
            PermissionResource.NETWORK,
            PermissionState.ALLOW_SESSION,
            session_only=True,
        )
        return permissions

    def test_runtime_prefers_session_chat_key(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.session_secrets = SessionSecretStore()
        controller.session_secrets.set_chat_api_key("session-key")

        with patch.object(
            SecretStore,
            "get_api_key",
        ) as persistent:
            self.assertEqual(controller._chat_api_key(), "session-key")

        persistent.assert_not_called()

    def test_write_falls_back_to_session_persistence_on_machine_failure(self):
        fake_win32cred = MagicMock()
        fake_win32cred.CRED_TYPE_GENERIC = 1
        fake_win32cred.CRED_PERSIST_LOCAL_MACHINE = 2
        fake_win32cred.CRED_PERSIST_SESSION = 1
        # 第一次写（LOCAL_MACHINE）抛异常，第二次写（SESSION）成功
        fake_win32cred.CredWrite.side_effect = [OSError("Access denied"), None]

        with patch.dict("sys.modules", {"win32cred": fake_win32cred}):
            success = SecretStore._write("TestTarget", "secret", "comment")
            self.assertTrue(success)
            self.assertEqual(fake_win32cred.CredWrite.call_count, 2)
            first_call_persist = fake_win32cred.CredWrite.call_args_list[0][0][0]["Persist"]
            second_call_persist = fake_win32cred.CredWrite.call_args_list[1][0][0]["Persist"]
            self.assertEqual(first_call_persist, 2)
            self.assertEqual(second_call_persist, 1)
