import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QWidget

from core.pet_generation_run import PetGenerationRunStore
from ui.pet_lab_install_controller import PetLabInstallDialogController
from ui.pet_lab_review_controller import PetLabReviewDialogController


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeMessageBox:
    class Icon:
        Warning = 1

    class ButtonRole:
        AcceptRole = 1
        DestructiveRole = 2
        ActionRole = 3
        RejectRole = 4

    clicked_text = "选择动作返工"

    def __init__(self, _parent):
        self.buttons = {}

    def setWindowTitle(self, _value):
        pass

    def setIcon(self, _value):
        pass

    def setText(self, _value):
        pass

    def setInformativeText(self, _value):
        pass

    def setIconPixmap(self, _value):
        pass

    def addButton(self, text, _role):
        button = object()
        self.buttons[text] = button
        return button

    def exec(self):
        return 0

    def clickedButton(self):
        return self.buttons[self.clicked_text]


class PetLabDialogControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_review_controller_reports_invalid_qa_json(self):
        parent = QWidget()
        errors = []
        controller = PetLabReviewDialogController(
            parent=parent,
            candidate_count=lambda _run, _task: 0,
            tasks_with_candidates=lambda _run, _tasks: [],
            choose_candidate=lambda *_args, **_kwargs: False,
            show_error=errors.append,
        )
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "qa.json"
            report.write_text("{broken", encoding="utf-8")

            result = controller.confirm_action_review(
                run_id="run-1",
                title="QA",
                sheet_path="",
                report_path=str(report),
                allow_accept=False,
            )

        self.assertEqual(result, ("deferred", None))
        self.assertIn("QA 报告无法读取", errors[0])
        parent.deleteLater()

    def test_review_controller_returns_selected_retry_action(self):
        parent = QWidget()
        controller = PetLabReviewDialogController(
            parent=parent,
            candidate_count=lambda _run, _task: 0,
            tasks_with_candidates=lambda _run, _tasks: [],
            choose_candidate=lambda *_args, **_kwargs: False,
            show_error=lambda _message: None,
        )
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "qa.json"
            report.write_text(
                json.dumps(
                    {
                        "summary": {"errors": 1, "warnings": 0},
                        "errors": [
                            {
                                "message": "动作重复",
                                "states": ["talking"],
                            }
                        ],
                        "warnings": [],
                        "expected_states": ["idle", "talking"],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch(
                    "ui.pet_lab_review_controller.QMessageBox",
                    FakeMessageBox,
                ),
                patch(
                    "ui.pet_lab_review_controller.QInputDialog.getItem",
                    return_value=("talking", True),
                ),
            ):
                result = controller.confirm_action_review(
                    run_id="run-1",
                    title="QA",
                    sheet_path="",
                    report_path=str(report),
                    allow_accept=False,
                )

        self.assertEqual(result, ("retry", "talking"))
        parent.deleteLater()

    def test_install_controller_delegates_final_confirmation(self):
        parent = QWidget()
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            service = MagicMock()
            service.read_archive_preview.return_value = None
            controller = PetLabInstallDialogController(
                parent=parent,
                run_store=store,
                character_service=service,
                active_run_id=lambda: None,
                animation_previews=lambda _run_id: {},
            )
            dialog = MagicMock()
            dialog.exec_confirmation.return_value = True
            with patch(
                "ui.pet_lab_install_controller.PackageInstallDialog",
                return_value=dialog,
            ) as dialog_type:
                confirmed = controller.confirm_install(
                    "generated.zip",
                    SimpleNamespace(name="Nova"),
                )

        self.assertTrue(confirmed)
        dialog_type.assert_called_once()
        dialog.exec_confirmation.assert_called_once_with()
        parent.deleteLater()


if __name__ == "__main__":
    unittest.main()
