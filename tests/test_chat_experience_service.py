import unittest

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from core.services.chat_experience_service import (
    estimate_chat,
    preset_prompt,
)
from ui.chat_message_components import MessageRow


class ChatExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_presets_and_estimates_are_deterministic_and_transparent(self):
        estimate = estimate_chat(
            latency_seconds=1.234,
            input_text="a" * 400,
            output_text="b" * 200,
            input_cost_per_million=2.0,
            output_cost_per_million=4.0,
        )

        self.assertEqual(estimate.latency_ms, 1234)
        self.assertEqual(estimate.input_tokens, 100)
        self.assertEqual(estimate.output_tokens, 50)
        self.assertAlmostEqual(estimate.estimated_cost, 0.0004)
        self.assertIn("优先快速", preset_prompt("fast"))
        self.assertEqual(preset_prompt("unknown"), "")

    def test_message_exposes_metrics_and_edit_resend(self):
        edited = []
        assistant = MessageRow(
            "assistant",
            "answer",
            metadata={
                "chat_metrics": {
                    "latency_ms": 1500,
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "estimated_cost": 0.001,
                }
            },
        )
        user = MessageRow(
            "user",
            "editable",
            edit_resend=edited.append,
        )

        metrics = assistant.findChild(QLabel, "chatMetrics")
        edit = user.findChild(QPushButton, "editResendButton")
        self.assertIn("1.5s", metrics.text())
        self.assertIn("20+10", metrics.text())
        edit.click()
        self.assertEqual(edited, ["editable"])
        assistant.deleteLater()
        user.deleteLater()


if __name__ == "__main__":
    unittest.main()
