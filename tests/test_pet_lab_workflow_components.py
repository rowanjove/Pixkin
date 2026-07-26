import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PyQt6.QtWidgets import QApplication

from core.services.pet_lab_service import PetLabTaskTool
from ui.pet_lab_workflow_components import (
    PetLabRunOption,
    PetLabTaskPanel,
    ReferenceImagePicker,
)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class PetLabWorkflowComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_reference_picker_deduplicates_caps_and_removes_images(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            paths = []
            for index in range(5):
                path = base / f"reference-{index}.png"
                Image.new(
                    "RGBA",
                    (16, 16),
                    (index * 30, 80, 120, 255),
                ).save(path)
                paths.append(path)
            picker = ReferenceImagePicker()

            picker.add_paths([paths[0], paths[0], *paths[1:]])

            self.assertEqual(picker.paths, paths[:4])
            self.assertEqual(picker.list_widget.count(), 4)
            picker.list_widget.setCurrentRow(1)
            picker.remove_selected()
            self.assertEqual(
                picker.paths,
                [paths[0], paths[2], paths[3]],
            )
            picker.deleteLater()

    def test_task_panel_owns_run_and_task_action_states(self):
        panel = PetLabTaskPanel()
        panel.set_runs(
            [
                PetLabRunOption("run-1", "Nova · 自动 QA"),
                PetLabRunOption("run-2", "Mochi · 生成失败"),
            ],
            selected_id="run-2",
        )
        panel.show_record(
            "健康状态：degraded",
            [
                PetLabTaskTool("idle", "failed", 2),
                PetLabTaskTool("talking", "complete", 1),
            ],
        )

        self.assertEqual(panel.selected_run_id, "run-2")
        self.assertEqual(panel.selected_task_id, "idle")
        self.assertTrue(panel.retry_btn.isEnabled())
        self.assertTrue(panel.candidate_btn.isEnabled())
        panel.set_busy(True)
        self.assertFalse(panel.continue_btn.isEnabled())
        self.assertFalse(panel.retry_btn.isEnabled())
        self.assertFalse(panel.candidate_btn.isEnabled())
        panel.deleteLater()

    def test_task_panel_empty_state_disables_record_actions(self):
        panel = PetLabTaskPanel()
        panel.set_runs([])
        panel.clear_run("任务记录无法读取。")

        self.assertIsNone(panel.selected_run_id)
        self.assertEqual(panel.run_health.text(), "任务记录无法读取。")
        self.assertFalse(panel.diagnostic_btn.isEnabled())
        self.assertFalse(panel.copy_issue_btn.isEnabled())
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
