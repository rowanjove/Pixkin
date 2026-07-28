import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.pet_generation_diagnostics import PetGenerationDiagnostics
from core.pet_generation_run import PetGenerationRunStore
from core.services.pet_lab_service import PetLabCandidate
from ui.pet_lab_components import (
    AnimationPreviewDialog,
    CandidateSelectionDialog,
    GenerationDiagnosticsDialog,
    PackageInstallDialog,
    PetLabHatchForm,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class PetLabComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_package_confirmation_defaults_to_no_and_accepts_yes(self):
        preview = QPixmap(32, 32)
        dialog = PackageInstallDialog(
            title="最终预览",
            icon=QMessageBox.Icon.Question,
            text="安装角色？",
            informative_text="最终确认",
            preview=preview,
        )

        self.assertEqual(
            dialog.defaultButton(),
            dialog.button(QMessageBox.StandardButton.No),
        )
        yes = dialog.button(QMessageBox.StandardButton.Yes)
        with (
            patch.object(dialog, "exec", return_value=0),
            patch.object(dialog, "clickedButton", return_value=yes),
        ):
            self.assertTrue(dialog.exec_confirmation())
        dialog.deleteLater()

    def test_candidate_selection_component_returns_chosen_version(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "first.png"
            second = base / "second.png"
            Image.new("RGBA", (32, 32), (200, 80, 90, 255)).save(first)
            Image.new("RGBA", (32, 32), (80, 90, 200, 255)).save(second)
            dialog = CandidateSelectionDialog(
                "idle",
                [
                    PetLabCandidate(
                        "attempt-001",
                        first,
                        True,
                        "版本 1 · 当前",
                    ),
                    PetLabCandidate(
                        "attempt-002",
                        second,
                        False,
                        "版本 2",
                    ),
                ],
            )
            dialog.candidate_list.setCurrentRow(1)
            with patch.object(
                dialog,
                "exec",
                return_value=dialog.DialogCode.Accepted,
            ):
                selected = dialog.exec_selection()

            self.assertEqual(selected, "attempt-002")
            dialog.deleteLater()

    def test_hatch_form_normalizes_values_and_owns_mode_budget(self):
        form = PetLabHatchForm(
            {
                "base_url": " https://images.example/v1 ",
                "model": "",
                "quality": "high",
            },
            " secret ",
        )
        form.name_input.setText("  Nova  ")
        form.personality_input.setText(" 温暖 ")
        form.style_input.setPlainText(" 蓝色围巾 ")
        form.mode_input.setCurrentIndex(
            form.mode_input.findData("full")
        )
        form.symmetry_input.setChecked(True)

        values = form.values()

        self.assertEqual(values.pet_name, "Nova")
        self.assertEqual(values.model, "gpt-image-2")
        self.assertEqual(values.api_key, "secret")
        self.assertEqual(values.generation_mode, "full")
        self.assertTrue(values.allow_horizontal_mirror)
        self.assertEqual(values.planned_api_calls, 39)
        self.assertEqual(values.max_api_calls, 42)
        self.assertIn("39 次图像生成", form.mode_hint)
        form.deleteLater()

    def test_hatch_form_validation_reports_first_missing_requirement(self):
        form = PetLabHatchForm({}, "")

        missing_reference = form.values().validation_issue(0)
        self.assertEqual(missing_reference.title, "还差一张图")

        form.name_input.setText("Nova")
        missing_key = form.values().validation_issue(1)
        self.assertEqual(missing_key.title, "需要图像 API Key")
        form.deleteLater()

    def test_animation_preview_releases_movie_file_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            preview = Path(directory) / "idle.gif"
            Image.new(
                "RGBA",
                (32, 32),
                (120, 80, 180, 255),
            ).save(preview, "GIF")
            dialog = AnimationPreviewDialog({"idle": preview})

            self.assertIsNotNone(dialog._movie)
            dialog.release_movie()
            self.assertIsNone(dialog._movie)
            self.assertEqual(dialog.canvas.text(), "")
            dialog.deleteLater()

    def test_generation_diagnostic_message_contains_actionable_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            run = store.create(
                run_id="diagnostic-component",
                request={"max_api_calls": 5},
                task_ids=["canonical"],
            )
            store.update_task(
                run["id"],
                "canonical",
                "failed",
                error="timed out",
            )
            store.record_api_call(
                run["id"],
                "canonical",
                duration_ms=300,
                outcome="failed",
                error_category="timeout",
            )
            record = store.load(run["id"])
            report = PetGenerationDiagnostics.summarize(record)

            message = GenerationDiagnosticsDialog.message(
                report,
                record,
            )

            self.assertIn("剩余真实调用：1", message)
            self.assertIn("成功/失败调用：0/1", message)
            self.assertIn("待处理任务：canonical", message)
