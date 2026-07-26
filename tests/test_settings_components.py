import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.settings_character_components import CharacterManagerPanel
from ui.settings_profile_components import UserProfileEditor
from core.services.memory_service import MemoryService
from ui.extension_settings_components import ExtensionSettingsPanel
from ui.memory_settings_components import MemorySettingsPanel


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def package(package_id, name):
    return SimpleNamespace(
        package_id=package_id,
        name=name,
        version="2.0",
        author="Pixkin",
        description=f"{name} description",
        preview=None,
    )


class SettingsComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_profile_editor_normalizes_name_and_updates_text_avatar(self):
        editor = UserProfileEditor(display_name=" 我 ")
        editor.name_input.setText("  林墨  ")

        values = editor.values()

        self.assertEqual(values.display_name, "林墨")
        self.assertEqual(editor.avatar_preview.text(), "林墨")
        self.assertEqual(values.avatar_source, "")
        editor.deleteLater()

    def test_profile_editor_keeps_valid_avatar_source(self):
        with tempfile.TemporaryDirectory() as directory:
            avatar = Path(directory) / "avatar.png"
            Image.new("RGB", (64, 48), (180, 70, 120)).save(avatar)
            editor = UserProfileEditor(
                display_name="Nova",
                avatar_source=str(avatar),
            )

            self.assertEqual(
                editor.values().avatar_source,
                str(avatar),
            )
            self.assertIsNotNone(editor.avatar_preview.pixmap())
            editor.clear_avatar()
            self.assertEqual(editor.avatar_preview.text(), "No")
            editor.deleteLater()

    def test_character_panel_orders_builtins_and_hides_legacy_alias(self):
        panel = CharacterManagerPanel()
        panel.refresh(
            [
                package("custom", "自定义"),
                package("pip", "Pip"),
                package("default-assistant", "旧山山"),
                package("shanshan", "山山"),
            ],
            active_id="custom",
        )

        ids = [
            panel.character_list.item(row).data(
                Qt.ItemDataRole.UserRole
            )
            for row in range(panel.character_list.count())
        ]
        self.assertEqual(ids, ["shanshan", "pip", "custom"])
        self.assertNotIn("default-assistant", ids)
        self.assertEqual(
            panel.selected_package().package_id,
            "custom",
        )
        self.assertFalse(panel.activate_btn.isEnabled())
        panel.deleteLater()

    def test_character_panel_protects_builtin_actions(self):
        panel = CharacterManagerPanel()
        panel.refresh(
            [
                package("shanshan", "山山"),
                package("custom", "自定义"),
            ],
            active_id="custom",
            selected_id="shanshan",
        )

        self.assertFalse(panel.rename_btn.isEnabled())
        self.assertFalse(panel.delete_btn.isEnabled())
        self.assertTrue(panel.activate_btn.isEnabled())
        panel.character_list.setCurrentRow(1)
        self.assertTrue(panel.rename_btn.isEnabled())
        self.assertTrue(panel.delete_btn.isEnabled())
        panel.deleteLater()

    def test_memory_panel_requires_explicit_candidate_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MemoryService(Path(directory) / "memories.json")
            panel = MemorySettingsPanel(
                service,
                user_id="local-profile",
                character_id="shanshan",
                recent_messages=[
                    {"role": "user", "content": "请记住我喜欢茉莉花茶"}
                ],
            )

            panel.extract_candidates()

            self.assertEqual(panel.candidate_list.count(), 1)
            self.assertEqual(service.list(), [])
            panel.confirm_candidate()
            self.assertEqual(service.list()[0].content, "我喜欢茉莉花茶")
            panel.enable_input.setChecked(False)
            panel.apply_settings()
            self.assertFalse(service.enabled)
            panel.deleteLater()

    def test_extension_panel_preserves_checkbox_state_when_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = ExtensionSettingsPanel(
                {
                    "gameplay": [
                        {
                            "id": "quiet-time",
                            "name": "安静时刻",
                            "icon": "🌙",
                            "prompt": "陪我安静一分钟。",
                            "enabled": True,
                        }
                    ],
                    "enabled_plugins": [],
                },
                Path(directory) / "plugins",
            )
            enabled_item = panel.gameplay_table.item(0, 0)
            enabled_item.setCheckState(Qt.CheckState.Unchecked)

            panel._configured = panel._gameplay_values()
            panel._refresh_gameplay()

            self.assertFalse(panel.values()["gameplay"][0]["enabled"])
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
