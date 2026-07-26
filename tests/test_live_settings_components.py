import os
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit

from ui.live_settings_components import LiveRoomEditor


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class LiveRoomEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_platform_choices_come_from_capable_live_providers(self):
        editor = LiveRoomEditor()
        editor.add_room({"platform": "bilibili", "room_id": "123"})
        platform = editor.table.cellWidget(0, 1)

        self.assertIsInstance(platform, QComboBox)
        self.assertEqual(
            {
                platform.itemData(index)
                for index in range(platform.count())
            },
            {"bilibili", "douyin"},
        )
        self.assertIn(
            "安全链接校验",
            platform.itemData(
                platform.currentIndex(),
                role=Qt.ItemDataRole.ToolTipRole,
            ),
        )
        editor.deleteLater()

    def test_values_trim_fields_default_anchor_and_ignore_blank_rooms(self):
        editor = LiveRoomEditor(
            [
                {
                    "enabled": False,
                    "platform": "douyin",
                    "room_id": "  https://live.douyin.com/42  ",
                    "anchor_name": " ",
                },
                {"platform": "bilibili", "room_id": ""},
            ]
        )

        self.assertEqual(
            editor.values(),
            [
                {
                    "enabled": False,
                    "platform": "douyin",
                    "room_id": "https://live.douyin.com/42",
                    "anchor_name": "关注的主播",
                }
            ],
        )
        editor.deleteLater()

    def test_remove_selected_deletes_only_selected_room(self):
        editor = LiveRoomEditor(
            [
                {"platform": "bilibili", "room_id": "1"},
                {"platform": "douyin", "room_id": "2"},
            ]
        )
        editor.table.selectRow(0)

        editor.remove_selected()

        self.assertEqual(editor.table.rowCount(), 1)
        remaining = editor.table.cellWidget(0, 2)
        self.assertIsInstance(remaining, QLineEdit)
        self.assertEqual(remaining.text(), "2")
        editor.deleteLater()


if __name__ == "__main__":
    unittest.main()
