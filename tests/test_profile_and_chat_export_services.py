import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.services.chat_export_service import ChatExportService
from core.services.user_profile_service import (
    UserProfileError,
    UserProfileService,
)


class UserProfileServiceTests(unittest.TestCase):
    def test_avatar_is_validated_and_copied_with_stable_digest_name(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source.png"
            Image.new("RGB", (40, 30), (80, 120, 180)).save(source)

            first = Path(
                UserProfileService.persist_avatar(source, base / "profile")
            )
            second = Path(
                UserProfileService.persist_avatar(source, base / "profile")
            )

            self.assertTrue(first.is_file())
            self.assertEqual(first, second)
            self.assertRegex(first.name, r"^user-avatar-[0-9a-f]{12}\.png$")
            self.assertEqual(first.read_bytes(), source.read_bytes())

    def test_missing_or_invalid_avatar_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            invalid = base / "invalid.png"
            invalid.write_text("not an image", encoding="utf-8")

            with self.assertRaises(UserProfileError):
                UserProfileService.persist_avatar(
                    base / "missing.png",
                    base / "profile",
                )
            with self.assertRaises(UserProfileError):
                UserProfileService.persist_avatar(
                    invalid,
                    base / "profile",
                )


class ChatExportServiceTests(unittest.TestCase):
    MESSAGES = [
        {
            "role": "assistant",
            "content": "你好",
            "character_name": "椰子",
            "created_at": "2026-07-25T09:10:11+08:00",
        }
    ]

    def test_exports_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            markdown = ChatExportService.export(
                self.MESSAGES,
                base / "history.md",
                day_label="2026-07-25",
            )
            json_file = ChatExportService.export(
                self.MESSAGES,
                base / "history.json",
                day_label="2026-07-25",
                json_format=True,
            )

            self.assertIn(
                "## 2026-07-25 09:10:11 · 椰子",
                markdown.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                json.loads(json_file.read_text(encoding="utf-8")),
                self.MESSAGES,
            )

    def test_failed_atomic_replace_preserves_existing_export(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "history.md"
            target.write_text("old export", encoding="utf-8")

            with (
                patch(
                    "core.services.chat_export_service.os.replace",
                    side_effect=OSError("replace failed"),
                ),
                self.assertRaises(OSError),
            ):
                ChatExportService.export(
                    self.MESSAGES,
                    target,
                    day_label="全部日期",
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "old export")
            self.assertEqual(
                list(target.parent.glob(f".{target.name}.*.tmp")),
                [],
            )
