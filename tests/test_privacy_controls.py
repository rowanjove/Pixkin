import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from core.chat_history_store import ChatHistoryStore
from core.config import ConfigManager
from core.privacy import model_data_scope, privacy_scope_fingerprint
from core.services.chat_service import ChatSessionService
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionResource,
    PermissionState,
)
from main import DesktopPetApp
from ui.privacy_settings_components import PrivacySettingsPanel
from ui.history_window import HistoryWindow


class PrivacyStoreTests(unittest.TestCase):
    def test_network_denial_blocks_chat_before_persisting_or_starting_worker(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.ai_worker = None
        controller.context_permission_service = ContextPermissionService()
        controller.pet_window = SimpleNamespace(
            chat_window=MagicMock(),
        )
        controller._store_message = MagicMock()
        controller._start_ai_response = MagicMock()

        controller._on_user_send_message("不会发送")

        controller._store_message.assert_not_called()
        controller._start_ai_response.assert_not_called()
        controller.pet_window.chat_window.append_message.assert_called_once()

    def test_retention_prunes_only_expired_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            session = store.create_session("role", "角色")
            now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
            store.add_message(
                session,
                "user",
                "expired",
                created_at=(now - timedelta(days=8)).isoformat(),
            )
            store.add_message(
                session,
                "assistant",
                "kept",
                created_at=(now - timedelta(days=2)).isoformat(),
            )

            deleted = store.prune_older_than(7, now=now)

            self.assertEqual(deleted, 1)
            self.assertEqual(
                [item["content"] for item in store.list_messages()],
                ["kept"],
            )

    def test_no_save_keeps_runtime_context_out_of_database(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            service = ChatSessionService(store)
            service.activate_character("role", "角色")
            service.add_message("user", "old")

            self.assertEqual(service.set_retention(0), 1)
            service.add_message("user", "runtime only")

            self.assertEqual(store.list_messages(), [])
            self.assertEqual(
                service.context(),
                [{"role": "user", "content": "runtime only"}],
            )

            service.set_retention(30)
            service.add_message("assistant", "durable again")
            self.assertEqual(
                [item["content"] for item in store.list_messages()],
                ["durable again"],
            )

    def test_delete_character_does_not_touch_other_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            first = store.create_session("first", "甲")
            store.add_message(first, "user", "remove")
            second = store.create_session("second", "乙")
            store.add_message(second, "user", "keep")

            self.assertEqual(store.delete_character("first"), 1)
            self.assertEqual(
                [item["content"] for item in store.list_messages()],
                ["keep"],
            )


class PrivacyUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_exposes_all_retention_choices(self):
        panel = PrivacySettingsPanel(
            retention_days=30,
            chat_store=None,
            audit_store=None,
        )

        self.assertEqual(panel.retention_days(), 30)
        self.assertEqual(
            [
                panel.retention_combo.itemData(index)
                for index in range(panel.retention_combo.count())
            ],
            [0, 7, 30, -1],
        )
        panel.close()

    def test_first_model_use_records_explicit_notice_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            controller = DesktopPetApp.__new__(DesktopPetApp)
            controller.ready = True
            controller.config_mgr = config
            controller.pet_window = SimpleNamespace(
                chat_window=MagicMock()
            )

            with patch(
                "main.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ) as question:
                self.assertTrue(
                    controller._ensure_model_privacy_notice()
                )

            self.assertTrue(
                config.get(
                    "privacy",
                    "model_notice_acknowledged",
                    False,
                )
            )
            self.assertEqual(
                config.get(
                    "privacy",
                    "model_notice_fingerprint",
                    "",
                ),
                privacy_scope_fingerprint(
                    endpoint=config.get("api", "base_url", ""),
                    model=config.get("api", "model", ""),
                    data_scope=model_data_scope({}),
                ),
            )
            question.assert_called_once()

            config.update_section(
                "api",
                {"base_url": "https://other.example/v1"},
            )
            with patch(
                "main.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as changed_endpoint_question:
                self.assertFalse(
                    controller._ensure_model_privacy_notice()
                )
            changed_endpoint_question.assert_called_once()

    def test_model_notice_scope_changes_when_desktop_context_is_granted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            controller = DesktopPetApp.__new__(DesktopPetApp)
            controller.ready = True
            controller.config_mgr = config
            controller.context_permission_service = ContextPermissionService()
            controller.pet_window = SimpleNamespace(
                chat_window=MagicMock()
            )

            with patch(
                "main.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ) as first_question:
                self.assertTrue(controller._ensure_model_privacy_notice())
            first_question.assert_called_once()

            controller.context_permission_service.set_state(
                PermissionResource.CLIPBOARD,
                PermissionState.ALLOW_SESSION,
                session_only=True,
            )
            with patch(
                "main.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as second_question:
                self.assertFalse(controller._ensure_model_privacy_notice())
            second_question.assert_called_once()

    def test_privacy_panel_preserves_effective_session_state(self):
        permissions = ContextPermissionService()
        permissions.set_state(
            PermissionResource.CLIPBOARD,
            PermissionState.ALLOW_SESSION,
            session_only=True,
        )
        panel = PrivacySettingsPanel(
            retention_days=30,
            chat_store=None,
            audit_store=None,
            context_permission_service=permissions,
        )
        clipboard_combo = panel.context_permission_combos[PermissionResource.CLIPBOARD]
        self.assertEqual(
            clipboard_combo.currentData(),
            PermissionState.ALLOW_SESSION.value,
        )
        self.assertEqual(
            panel.context_permissions()[PermissionResource.CLIPBOARD.value],
            PermissionState.DENY.value,
        )
        panel.close()

    def test_declined_model_notice_blocks_sending(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.ready = True
        controller.config_mgr = MagicMock()
        controller.config_mgr.get.side_effect = (
            lambda section, key=None, default=None: (
                False
                if (section, key)
                == ("privacy", "model_notice_acknowledged")
                else "https://example.test/v1"
            )
        )
        controller.pet_window = SimpleNamespace(
            chat_window=MagicMock()
        )

        with patch(
            "main.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.assertFalse(
                controller._ensure_model_privacy_notice()
            )
        controller.config_mgr.update_section.assert_not_called()

    def test_export_then_delete_removes_only_visible_character(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store = ChatHistoryStore(base / "history.sqlite3")
            first = store.create_session("first", "甲")
            store.add_message(first, "user", "exported")
            second = store.create_session("second", "乙")
            store.add_message(second, "user", "kept")
            window = HistoryWindow(
                store,
                "first",
                "甲",
                first,
            )

            with (
                patch(
                    "ui.history_window.QFileDialog.getSaveFileName",
                    return_value=(str(base / "history.md"), "Markdown"),
                ),
                patch.object(QMessageBox, "information"),
                patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
            ):
                window._export_visible(delete_after=True)

            self.assertEqual(
                [item["content"] for item in store.list_messages()],
                ["kept"],
            )
            window.close()
