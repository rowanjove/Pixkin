import os
import unittest
from datetime import date, timedelta

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from ui.chat_message_components import (
    ChatMessageView,
    MessageRow,
    WelcomeMessageCard,
    friendly_day_label,
    render_transcript_html,
)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class ChatMessageComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_message_view_normalizes_untrusted_mapping(self):
        message = ChatMessageView.from_mapping(
            {
                "role": "",
                "content": None,
                "metadata": ["not", "a", "mapping"],
                "created_at": "2026-07-25T10:00:00+08:00",
            }
        )

        self.assertEqual(message.role, "system")
        self.assertEqual(message.display_content, "•••")
        self.assertEqual(message.metadata, {})
        self.assertEqual(message.day, "2026-07-25")

    def test_transcript_html_escapes_role_and_content(self):
        rendered = render_transcript_html(
            [
                {
                    "role": "<user>",
                    "content": "<script>bad()</script>\n下一行",
                }
            ]
        )

        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;user&gt;", rendered)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;<br>下一行", rendered)

    def test_friendly_day_labels_are_deterministic(self):
        today = date(2026, 7, 26)

        self.assertEqual(friendly_day_label(str(today), today=today), "今天")
        self.assertEqual(
            friendly_day_label(str(today - timedelta(days=1)), today=today),
            "昨天",
        )
        self.assertEqual(
            friendly_day_label("2020-01-02", today=today),
            "2020年01月02日",
        )
        self.assertEqual(friendly_day_label("not-a-day", today=today), "not-a-day")

    def test_rows_and_welcome_card_preserve_plain_text_and_shortcuts(self):
        alert = MessageRow(
            "alert",
            "fallback",
            metadata={
                "platform": "<B站>",
                "headline": "<主播>",
                "body": "<b>纯文本</b>",
            },
        )
        self.assertEqual(
            alert.findChild(QLabel, "alertTitle").text(),
            "<b>纯文本</b>",
        )

        prompts = []
        welcome = WelcomeMessageCard("Pip", None, prompts.append)
        buttons = welcome.findChildren(QPushButton, "suggestion")
        buttons[0].click()
        self.assertEqual(prompts, ["介绍一下你能调用的工具"])

        alert.deleteLater()
        welcome.deleteLater()

    def test_assistant_memory_disclosure_only_shows_used_items(self):
        row = MessageRow(
            "assistant",
            "好的",
            metadata={
                "used_memories": [
                    {"id": "one", "content": "<偏好中文>"},
                    {"id": "two", "content": "喜欢咖啡"},
                ]
            },
        )

        button = row.findChild(QPushButton, "memoryDetailsButton")
        details = row.findChild(QLabel, "memoryDetails")
        self.assertEqual(button.text(), "本次使用 2 条记忆")
        self.assertTrue(details.isHidden())
        button.click()
        self.assertFalse(details.isHidden())
        self.assertIn("<偏好中文>", details.text())
        row.deleteLater()

    def test_legacy_redacted_metrics_do_not_crash_message_rendering(self):
        row = MessageRow(
            "assistant",
            "完成",
            metadata={
                "chat_metrics": {
                    "latency_ms": 1200,
                    "input_tokens": "<redacted-secret>",
                    "output_tokens": "<redacted-secret>",
                    "estimated_cost": "<redacted-secret>",
                }
            },
        )

        metrics = row.findChild(QLabel, "chatMetrics")
        self.assertIsNotNone(metrics)
        self.assertIn("0+0 tokens", metrics.text())
        row.deleteLater()
