"""UI controller for Pet Lab diagnostic and anonymous issue-bundle actions."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Type

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from core.pet_generation_diagnostics import (
    IssueBundleError,
    PetGenerationDiagnostics,
)
from core.pet_generation_run import (
    PetGenerationRunError,
    PetGenerationRunStore,
)
from ui.pet_lab_components import GenerationDiagnosticsDialog


class PetLabDiagnosticsController:
    def __init__(
        self,
        *,
        parent: QWidget,
        run_store: PetGenerationRunStore,
        selected_run_id: Callable[[], str | None],
        set_status: Callable[[str], None],
        show_error: Callable[[str], None],
        file_dialog: Type[QFileDialog] = QFileDialog,
        message_box: Type[QMessageBox] = QMessageBox,
    ):
        self.parent = parent
        self.run_store = run_store
        self.selected_run_id = selected_run_id
        self.set_status = set_status
        self.show_error = show_error
        self.file_dialog = file_dialog
        self.message_box = message_box

    def show(self):
        loaded = self._load_report()
        if loaded is None:
            return
        report, record = loaded
        GenerationDiagnosticsDialog.show(
            self.parent,
            report,
            record,
        )

    def copy_summary(self):
        loaded = self._load_report()
        if loaded is None:
            return
        report, _record = loaded
        QApplication.clipboard().setText(
            PetGenerationDiagnostics.technical_summary(report)
        )
        self.set_status(
            "脱敏技术摘要已复制；其中不含 API Key、角色描述或本地路径。"
        )

    def copy_issue_markdown(self):
        loaded = self._load_report()
        if loaded is None:
            return
        report, _record = loaded
        QApplication.clipboard().setText(
            PetGenerationDiagnostics.github_issue_markdown(
                report,
                bundle_filename="Pixkin-Issue.zip",
            )
        )
        self.set_status(
            "GitHub Issue 模板已复制；请补充复现步骤后附上匿名问题包。"
        )

    def export_issue_bundle(self):
        run_id = self.selected_run_id()
        if not run_id:
            return
        destination, _ = self.file_dialog.getSaveFileName(
            self.parent,
            "导出匿名问题包",
            "Pixkin-Issue.zip",
            "ZIP 压缩包 (*.zip)",
        )
        if not destination:
            return
        path = Path(destination)
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(".zip")
        try:
            record = self.run_store.load(run_id)
            result = PetGenerationDiagnostics.build_issue_bundle(
                record,
                workspace=self.run_store.workspace(run_id),
                destination=path,
            )
        except (OSError, PetGenerationRunError, ValueError) as exc:
            self.show_error(f"匿名问题包导出失败：{exc}")
            return
        self.set_status(f"匿名问题包已导出：{result['path']}")
        self.message_box.information(
            self.parent,
            "匿名问题包已导出",
            "问题包只包含脱敏诊断与 QA JSON；"
            "不包含参考图、生成图片、API Key、角色名字或自由文本设定；"
            "清单已记录每个文件的 SHA-256 哈希。",
        )

    def inspect_issue_bundle(self):
        source, _ = self.file_dialog.getOpenFileName(
            self.parent,
            "检查 Pixkin 问题包",
            "",
            "ZIP 压缩包 (*.zip)",
        )
        if not source:
            return
        try:
            result = PetGenerationDiagnostics.inspect_issue_bundle(
                Path(source)
            )
        except (OSError, IssueBundleError) as exc:
            self.show_error(f"问题包检查失败：{exc}")
            return
        diagnostic = result["diagnostic"]
        self.message_box.information(
            self.parent,
            "问题包检查通过",
            (
                f"匿名编号：{result['anonymous_run_id']}\n"
                f"健康状态：{diagnostic.get('health', 'unknown')}\n"
                f"流程阶段：{diagnostic.get('stage', 'unknown')}\n"
                f"文件数量：{len(result['contents'])}\n\n"
                "检查过程只读取 ZIP 内的白名单 JSON，未解压文件。"
            ),
        )
        self.set_status("问题包完整性与隐私结构检查通过。")

    def _load_report(self):
        run_id = self.selected_run_id()
        if not run_id:
            return None
        try:
            record = self.run_store.load(run_id)
        except PetGenerationRunError as exc:
            self.show_error(str(exc))
            return None
        return PetGenerationDiagnostics.summarize(record), record
