"""Review dialogs for canonical identity, action consistency, and QA."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QWidget

from core.services.pet_lab_service import PetLabService


class PetLabReviewDialogController:
    def __init__(
        self,
        *,
        parent: QWidget,
        candidate_count: Callable[[str, str], int],
        tasks_with_candidates: Callable[
            [str, Iterable[str]], list[str]
        ],
        choose_candidate: Callable[..., bool],
        show_error: Callable[[str], None],
    ):
        self.parent = parent
        self.candidate_count = candidate_count
        self.tasks_with_candidates = tasks_with_candidates
        self.choose_candidate = choose_candidate
        self.show_error = show_error

    def confirm_canonical(
        self,
        run_id: str,
        image_path: str,
    ) -> str:
        dialog = QMessageBox(self.parent)
        dialog.setWindowTitle("确认伙伴身份稿")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText("先确认角色身份，再生成全部动作。")
        dialog.setInformativeText(
            "请检查轮廓、配色、脸部特征和配饰是否正确。"
            "确认后，后续姿态会以这张身份稿为唯一基准。"
        )
        accept = dialog.addButton(
            "身份正确，继续", QMessageBox.ButtonRole.AcceptRole
        )
        regenerate = dialog.addButton(
            "重新生成", QMessageBox.ButtonRole.DestructiveRole
        )
        later = dialog.addButton(
            "稍后处理", QMessageBox.ButtonRole.RejectRole
        )
        history = None
        if self.candidate_count(run_id, "canonical") > 1:
            history = dialog.addButton(
                "查看历史版本", QMessageBox.ButtonRole.ActionRole
            )
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            dialog.setIconPixmap(
                pixmap.scaled(
                    220,
                    220,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is accept:
            return "accepted"
        if clicked is regenerate:
            return "rejected"
        if clicked is later:
            return "deferred"
        if history is not None and clicked is history:
            if self.choose_candidate(
                run_id,
                "canonical",
                continue_flow=False,
            ):
                return "switched"
        return "deferred"

    def confirm_action_review(
        self,
        *,
        run_id: str,
        title: str,
        sheet_path: str,
        report_path: str,
        allow_accept: bool,
    ) -> tuple[str, str | None]:
        try:
            report = json.loads(
                Path(report_path).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            self.show_error(f"QA 报告无法读取：{exc}")
            return "deferred", None

        summary = report.get("summary", {})
        error_count = int(summary.get("errors", 0))
        warning_count = int(summary.get("warnings", 0))
        dialog = QMessageBox(self.parent)
        dialog.setWindowTitle(title)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(
            f"自动检查：{error_count} 个错误，{warning_count} 个提醒。"
        )
        messages = [
            str(issue.get("message", ""))
            for issue in [
                *report.get("errors", []),
                *report.get("warnings", []),
            ][:5]
        ]
        dialog.setInformativeText(
            "\n".join(messages)
            or "请对照身份稿检查轮廓、配色、配饰和动作语义。"
        )
        accept = None
        if allow_accept and not error_count:
            accept = dialog.addButton(
                "一致，继续", QMessageBox.ButtonRole.AcceptRole
            )
        retry = dialog.addButton(
            "选择动作返工", QMessageBox.ButtonRole.DestructiveRole
        )
        versioned_tasks = self.tasks_with_candidates(
            run_id,
            report.get("expected_states", []),
        )
        history = None
        if versioned_tasks:
            history = dialog.addButton(
                "查看候选版本", QMessageBox.ButtonRole.ActionRole
            )
        dialog.addButton(
            "稍后处理", QMessageBox.ButtonRole.RejectRole
        )
        pixmap = QPixmap(sheet_path)
        if not pixmap.isNull():
            dialog.setIconPixmap(
                pixmap.scaled(
                    420,
                    300,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        dialog.exec()
        clicked = dialog.clickedButton()
        if accept is not None and clicked is accept:
            return "accepted", None
        if history is not None and clicked is history:
            task_id = self._choose_versioned_task(versioned_tasks)
            if task_id and self.choose_candidate(
                run_id,
                task_id,
                continue_flow=False,
            ):
                return "switched", task_id
            return "deferred", None
        if clicked is not retry:
            return "deferred", None

        candidates = PetLabService.qa_retry_candidates(report)
        if not candidates:
            return "deferred", None
        selected, confirmed = QInputDialog.getItem(
            self.parent,
            "选择返工动作",
            "只重新生成这个动作：",
            candidates,
            0,
            False,
        )
        if not confirmed:
            return "deferred", None
        return "retry", str(selected)

    def _choose_versioned_task(
        self,
        versioned_tasks: list[str],
    ) -> str | None:
        if len(versioned_tasks) == 1:
            return versioned_tasks[0]
        task_id, confirmed = QInputDialog.getItem(
            self.parent,
            "选择动作",
            "查看哪个动作的候选版本：",
            versioned_tasks,
            0,
            False,
        )
        return str(task_id) if confirmed else None
