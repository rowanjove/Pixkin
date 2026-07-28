"""Pet Lab controls for reference images and resumable run tooling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.services.pet_lab_service import PetLabTaskTool


@dataclass(frozen=True)
class PetLabRunOption:
    run_id: str
    label: str


class ReferenceImagePicker(QWidget):
    """Own a deduplicated, ordered set of up to four reference images."""

    paths_changed = pyqtSignal(object)

    def __init__(
        self,
        paths: Iterable[Path] = (),
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._paths: list[Path] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(QSize(92, 92))
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setFixedHeight(138)
        layout.addWidget(self.list_widget)

        actions = QHBoxLayout()
        add = QPushButton("＋ 添加图片")
        add.setObjectName("secondary")
        add.clicked.connect(self.choose_files)
        remove = QPushButton("移除选中")
        remove.setObjectName("danger")
        remove.clicked.connect(self.remove_selected)
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        layout.addLayout(actions)
        self.set_paths(paths)

    @property
    def paths(self) -> list[Path]:
        return list(self._paths)

    def set_paths(self, paths: Iterable[Path]):
        self._paths = []
        self.list_widget.clear()
        self.add_paths(paths, emit=False)

    def add_paths(
        self,
        paths: Iterable[Path],
        *,
        emit: bool = True,
    ):
        changed = False
        for raw in paths:
            path = Path(raw)
            if path in self._paths or len(self._paths) >= 4:
                continue
            self._paths.append(path)
            pixmap = QPixmap(str(path))
            item = QListWidgetItem(QIcon(pixmap), path.stem[:16])
            item.setToolTip(str(path))
            self.list_widget.addItem(item)
            changed = True
        if changed and emit:
            self.paths_changed.emit(self.paths)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择风格示意图",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
        self.add_paths(Path(raw) for raw in paths)

    def remove_selected(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self.list_widget.takeItem(row)
        self._paths.pop(row)
        self.paths_changed.emit(self.paths)


class PetLabTaskPanel(QWidget):
    """Render resumable runs, diagnostics actions, and per-task tools."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._busy = False
        self._has_runs = False
        self._tools: dict[str, PetLabTaskTool] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        resume_row = QHBoxLayout()
        self.run_input = QComboBox()
        self.run_input.setToolTip(
            "身份待确认、失败或尚未安装的孵化任务"
        )
        self.continue_btn = QPushButton("继续未完成任务")
        self.continue_btn.setObjectName("secondary")
        resume_row.addWidget(self.run_input, 1)
        resume_row.addWidget(self.continue_btn)
        layout.addLayout(resume_row)

        health_row = QHBoxLayout()
        self.run_health = QLabel("选择未完成任务后显示批次健康状态。")
        self.run_health.setObjectName("muted")
        self.run_health.setWordWrap(True)
        self.diagnostic_btn = QPushButton("查看诊断")
        self.diagnostic_btn.setObjectName("secondary")
        self.diagnostic_btn.setEnabled(False)
        health_row.addWidget(self.run_health, 1)
        health_row.addWidget(self.diagnostic_btn)
        layout.addLayout(health_row)

        support_row = QHBoxLayout()
        self.copy_diagnostic_btn = QPushButton("复制摘要")
        self.copy_diagnostic_btn.setObjectName("secondary")
        self.copy_issue_btn = QPushButton("复制 Issue")
        self.copy_issue_btn.setObjectName("secondary")
        self.export_diagnostic_btn = QPushButton("导出问题包")
        self.export_diagnostic_btn.setObjectName("secondary")
        self.inspect_issue_btn = QPushButton("检查问题包")
        self.inspect_issue_btn.setObjectName("secondary")
        support_row.addStretch()
        support_row.addWidget(self.copy_diagnostic_btn)
        support_row.addWidget(self.copy_issue_btn)
        support_row.addWidget(self.export_diagnostic_btn)
        support_row.addWidget(self.inspect_issue_btn)
        layout.addLayout(support_row)

        retry_row = QHBoxLayout()
        self.retry_task_input = QComboBox()
        self.retry_task_input.setToolTip(
            "选择要重试或切换历史版本的任务"
        )
        self.retry_btn = QPushButton("重试选中动作")
        self.retry_btn.setObjectName("secondary")
        self.candidate_btn = QPushButton("查看候选版本")
        self.candidate_btn.setObjectName("secondary")
        retry_row.addWidget(self.retry_task_input, 1)
        retry_row.addWidget(self.retry_btn)
        retry_row.addWidget(self.candidate_btn)
        layout.addLayout(retry_row)

        self.retry_task_input.currentIndexChanged.connect(
            self.refresh_task_actions
        )
        self.clear_run()

    @property
    def selected_run_id(self) -> str | None:
        value = self.run_input.currentData()
        return str(value) if value else None

    @property
    def selected_task_id(self) -> str | None:
        value = self.retry_task_input.currentData()
        return str(value) if value else None

    def set_runs(
        self,
        options: Iterable[PetLabRunOption],
        *,
        selected_id: str | None = None,
        busy: bool = False,
    ):
        options = list(options)
        self._has_runs = bool(options)
        self._busy = busy
        self.run_input.blockSignals(True)
        self.run_input.clear()
        for option in options:
            self.run_input.addItem(option.label, option.run_id)
        if not options:
            self.run_input.addItem("没有未完成任务", None)
        if selected_id:
            index = self.run_input.findData(selected_id)
            if index >= 0:
                self.run_input.setCurrentIndex(index)
        self.run_input.blockSignals(False)
        self.continue_btn.setEnabled(self._has_runs and not busy)

    def clear_run(self, message: str | None = None):
        self._tools = {}
        self.retry_task_input.blockSignals(True)
        self.retry_task_input.clear()
        self.retry_task_input.addItem("没有可用的任务工具", None)
        self.retry_task_input.blockSignals(False)
        self.run_health.setText(
            message or "选择未完成任务后显示批次健康状态。"
        )
        for button in (
            self.diagnostic_btn,
            self.copy_diagnostic_btn,
            self.copy_issue_btn,
            self.export_diagnostic_btn,
            self.retry_btn,
            self.candidate_btn,
        ):
            button.setEnabled(False)

    def show_record(
        self,
        health_text: str,
        tools: Iterable[PetLabTaskTool],
        *,
        busy: bool = False,
    ):
        self._busy = busy
        self.run_health.setText(health_text)
        for button in (
            self.diagnostic_btn,
            self.copy_diagnostic_btn,
            self.copy_issue_btn,
            self.export_diagnostic_btn,
        ):
            button.setEnabled(True)
        self._tools = {tool.task_id: tool for tool in tools}
        self.retry_task_input.blockSignals(True)
        self.retry_task_input.clear()
        for tool in self._tools.values():
            self.retry_task_input.addItem(tool.label, tool.task_id)
        if not self._tools:
            self.retry_task_input.addItem(
                "没有可用的任务工具", None
            )
        self.retry_task_input.blockSignals(False)
        self.refresh_task_actions()

    def set_busy(self, busy: bool):
        self._busy = busy
        self.continue_btn.setEnabled(self._has_runs and not busy)
        self.refresh_task_actions()

    def refresh_task_actions(self, _index: int | None = None):
        tool = self._tools.get(self.selected_task_id or "")
        self.retry_btn.setEnabled(
            bool(tool and tool.retryable and not self._busy)
        )
        self.candidate_btn.setEnabled(
            bool(
                tool
                and tool.can_choose_candidate
                and not self._busy
            )
        )
