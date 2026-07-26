"""Reusable visual components for Pet Lab review and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QMovie, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.pet_generation_diagnostics import PetGenerationDiagnostics
from core.pet_generator import PetGenerationWorker
from core.services.pet_lab_service import PetLabCandidate


@dataclass(frozen=True)
class PetLabValidationIssue:
    title: str
    message: str


@dataclass(frozen=True)
class PetLabHatchInput:
    pet_name: str
    personality: str
    style_notes: str
    generation_mode: str
    allow_horizontal_mirror: bool
    max_api_calls: int
    base_url: str
    model: str
    quality: str
    session_only_key: bool
    api_key: str = field(repr=False)

    @property
    def full_hatch(self) -> bool:
        return self.generation_mode != "basic"

    @property
    def planned_api_calls(self) -> int:
        return PetGenerationWorker.planned_api_calls(
            self.generation_mode,
            self.allow_horizontal_mirror,
        )

    def validation_issue(
        self, reference_count: int
    ) -> PetLabValidationIssue | None:
        if reference_count < 1:
            return PetLabValidationIssue(
                "还差一张图",
                "请至少添加一张风格示意图。",
            )
        if not self.pet_name:
            return PetLabValidationIssue(
                "给它一个名字",
                "请输入新伙伴的名字。",
            )
        if not self.api_key:
            return PetLabValidationIssue(
                "需要图像 API Key",
                "伙伴工坊需要支持图像编辑接口的 API Key。",
            )
        return None


class PetLabHatchForm(QWidget):
    """Collect and normalize identity plus image-generation settings."""

    mode_hint_changed = pyqtSignal(str)

    def __init__(
        self,
        image_settings: Mapping[str, Any],
        api_key: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        identity_form = QFormLayout()
        identity_form.setHorizontalSpacing(14)
        identity_form.setVerticalSpacing(10)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：Mochi、Nova、团子")
        self.personality_input = QLineEdit()
        self.personality_input.setPlaceholderText("聪明、温暖、毒舌但可靠…")
        self.style_input = QTextEdit()
        self.style_input.setPlaceholderText(
            "可补充配色、服饰或主播标志性元素。这里无法关闭卡通限制。"
        )
        self.style_input.setFixedHeight(66)
        self.mode_input = QComboBox()
        self.mode_input.addItem(
            "基础孵化 · 4 状态 / 5 次生成", "basic"
        )
        self.mode_input.addItem(
            "标准孵化 · 19 状态 / 20 次生成", "standard"
        )
        self.mode_input.addItem(
            "完整孵化 · 44 状态 / 45 次生成", "full"
        )
        self.symmetry_input = QCheckBox(
            "角色左右完全对称，允许安全镜像左右动作"
        )
        self.symmetry_input.setToolTip(
            "仅在角色没有单侧配饰、文字、徽标、道具或不对称光照时启用；"
            "完整模式可少调用 6 次图像 API。"
        )
        self.call_budget_input = QSpinBox()
        self.call_budget_input.setRange(5, 100)
        self.call_budget_input.setSuffix(" 次")
        self.call_budget_input.setToolTip(
            "达到上限后任务会安全停止；初始计划之外的额度用于返工。"
        )
        identity_form.addRow("角色名字", self.name_input)
        identity_form.addRow("性格设定", self.personality_input)
        identity_form.addRow("风格补充", self.style_input)
        identity_form.addRow("孵化模式", self.mode_input)
        identity_form.addRow("镜像优化", self.symmetry_input)
        identity_form.addRow("最大 API 调用", self.call_budget_input)
        layout.addLayout(identity_form)

        self.api_url = QLineEdit(
            str(
                image_settings.get(
                    "base_url", "https://api.openai.com/v1"
                )
            )
        )
        self.api_model = QLineEdit(
            str(image_settings.get("model", "gpt-image-2"))
        )
        self.api_key = QLineEdit(
            api_key or str(image_settings.get("api_key", ""))
        )
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.session_key = QCheckBox(
            "仅本次运行使用 API Key（不写入凭据管理器）"
        )
        self.quality = QComboBox()
        for label, value in (
            ("低", "low"),
            ("标准", "medium"),
            ("高", "high"),
        ):
            self.quality.addItem(label, value)
        quality_index = self.quality.findData(
            image_settings.get("quality", "medium")
        )
        self.quality.setCurrentIndex(max(0, quality_index))
        api_form = QFormLayout()
        api_form.setHorizontalSpacing(14)
        api_form.addRow("图像接口", self.api_url)
        api_form.addRow("图像模型", self.api_model)
        api_form.addRow("API Key", self.api_key)
        api_form.addRow("", self.session_key)
        api_form.addRow("生成质量", self.quality)
        layout.addLayout(api_form)

        self._mode_hint = ""
        self.mode_input.currentIndexChanged.connect(
            self._update_mode_state
        )
        self.symmetry_input.stateChanged.connect(
            self._update_mode_state
        )
        self._update_mode_state()

    @property
    def mode_hint(self) -> str:
        return self._mode_hint

    def values(self) -> PetLabHatchInput:
        mode = str(self.mode_input.currentData() or "basic")
        return PetLabHatchInput(
            pet_name=self.name_input.text().strip(),
            personality=self.personality_input.text().strip(),
            style_notes=self.style_input.toPlainText().strip(),
            generation_mode=mode,
            allow_horizontal_mirror=bool(
                mode == "full" and self.symmetry_input.isChecked()
            ),
            max_api_calls=self.call_budget_input.value(),
            base_url=self.api_url.text().strip(),
            model=self.api_model.text().strip() or "gpt-image-2",
            quality=str(self.quality.currentData() or "medium"),
            session_only_key=self.session_key.isChecked(),
            api_key=self.api_key.text().strip(),
        )

    def _update_mode_state(self, _value: int | None = None):
        mode = str(self.mode_input.currentData() or "basic")
        is_full = mode == "full"
        self.symmetry_input.setEnabled(is_full)
        if not is_full and self.symmetry_input.isChecked():
            self.symmetry_input.blockSignals(True)
            self.symmetry_input.setChecked(False)
            self.symmetry_input.blockSignals(False)
        allow_mirror = bool(
            is_full and self.symmetry_input.isChecked()
        )
        planned_calls = PetGenerationWorker.planned_api_calls(
            mode,
            allow_mirror,
        )
        self.call_budget_input.blockSignals(True)
        self.call_budget_input.setMinimum(planned_calls)
        self.call_budget_input.setValue(min(100, planned_calls + 3))
        self.call_budget_input.blockSignals(False)
        if mode == "full":
            mirror_copy = (
                "已启用安全镜像，计划 39 次图像生成"
                if allow_mirror
                else "默认不镜像，计划 45 次图像生成"
            )
            hint = (
                "完整孵化包含 1 张身份稿和 44 个状态；"
                f"{mirror_copy}，"
                "含移动、跳跃、高级反馈与四向贴边；成本和耗时显著高于标准模式，"
                "实际数值由所选接口、模型与质量决定。"
            )
        elif mode == "standard":
            hint = (
                "标准孵化包含 1 张身份稿和 19 个状态，共 20 次图像生成；"
                "实际费用和耗时由所选接口、模型与质量决定。"
            )
        else:
            hint = (
                "基础孵化包含 1 张身份稿和 4 个核心状态，共 5 次图像生成；"
                "适合先验证角色身份与整体风格。"
            )
        self._mode_hint = hint
        self.mode_hint_changed.emit(hint)


class CandidateSelectionDialog(QDialog):
    """Render archived candidates and return the selected candidate ID."""

    def __init__(
        self,
        task_id: str,
        candidates: list[PetLabCandidate],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"选择 {task_id} 候选版本")
        self.setMinimumSize(620, 500)
        self.resize(620, 500)
        layout = QVBoxLayout(self)
        copy = QLabel(
            "所有原始结果都会保留。选择一个版本作为当前精灵，"
            "随后重新执行受影响的审核与 QA。"
        )
        copy.setWordWrap(True)
        copy.setObjectName("muted")
        layout.addWidget(copy)
        self.candidate_list = QListWidget()
        self.candidate_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.candidate_list.setIconSize(QSize(150, 162))
        self.candidate_list.setGridSize(QSize(180, 205))
        self.candidate_list.setSpacing(6)
        self.candidate_list.setResizeMode(
            QListWidget.ResizeMode.Adjust
        )
        self.candidate_list.setMovement(QListWidget.Movement.Static)
        layout.addWidget(self.candidate_list, 1)
        selected_item = None
        for candidate in candidates:
            item = QListWidgetItem(
                QIcon(QPixmap(str(candidate.sprite_path))),
                candidate.label,
            )
            item.setData(
                Qt.ItemDataRole.UserRole,
                candidate.candidate_id,
            )
            item.setToolTip(str(candidate.sprite_path))
            self.candidate_list.addItem(item)
            if candidate.selected:
                selected_item = item
        if selected_item is not None:
            self.candidate_list.setCurrentItem(selected_item)
        elif self.candidate_list.count():
            self.candidate_list.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setText("使用此版本")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.candidate_list.itemDoubleClicked.connect(
            lambda _item: self.accept()
        )
        layout.addWidget(buttons)

    def exec_selection(self) -> str | None:
        if self.exec() != QDialog.DialogCode.Accepted:
            return None
        item = self.candidate_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))


class AnimationPreviewDialog(QDialog):
    """Play one generated action preview at a time and release file handles."""

    def __init__(
        self,
        previews: Mapping[str, Path],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.previews = {
            str(state): Path(path)
            for state, path in previews.items()
        }
        self._movie: QMovie | None = None
        self.setWindowTitle("动作循环预览")
        self.setMinimumSize(400, 430)
        layout = QVBoxLayout(self)
        copy = QLabel(
            "逐动作检查循环是否完整、角色是否抖动，以及脚底和尺寸是否漂移。"
        )
        copy.setObjectName("muted")
        copy.setWordWrap(True)
        layout.addWidget(copy)
        self.state_input = QComboBox()
        for state in self.previews:
            self.state_input.addItem(state, state)
        layout.addWidget(self.state_input)
        self.canvas = QLabel()
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setMinimumSize(320, 330)
        layout.addWidget(self.canvas, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.state_input.currentIndexChanged.connect(self.play_selected)
        self.play_selected()

    def play_selected(self):
        self.release_movie()
        state = self.state_input.currentData()
        path = self.previews.get(str(state))
        if path is None:
            return
        movie = QMovie(str(path))
        movie.setScaledSize(QSize(288, 312))
        self.canvas.setMovie(movie)
        self._movie = movie
        movie.start()

    def release_movie(self):
        if self._movie is None:
            return
        self._movie.stop()
        self.canvas.clear()
        self._movie.setFileName("")
        self._movie.deleteLater()
        self._movie = None

    def exec_preview(self) -> int:
        try:
            return int(self.exec())
        finally:
            self.release_movie()
            self.deleteLater()


class PackageInstallDialog(QMessageBox):
    """Final install/replace confirmation with optional animation review."""

    def __init__(
        self,
        *,
        title: str,
        text: str,
        informative_text: str,
        icon: QMessageBox.Icon,
        preview: QPixmap | None = None,
        animation_previews: Mapping[str, Path] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.animation_previews = dict(animation_previews or {})
        self.setWindowTitle(title)
        self.setIcon(icon)
        self.setText(text)
        self.setInformativeText(informative_text)
        self.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        self.setDefaultButton(QMessageBox.StandardButton.No)
        if preview is not None and not preview.isNull():
            self.setIconPixmap(preview)
        self._preview_button = None
        if self.animation_previews:
            self._preview_button = self.addButton(
                "查看动画预览",
                QMessageBox.ButtonRole.ActionRole,
            )

    def exec_confirmation(self) -> bool:
        while True:
            self.exec()
            clicked = self.clickedButton()
            if (
                self._preview_button is not None
                and clicked is self._preview_button
            ):
                AnimationPreviewDialog(
                    self.animation_previews,
                    self,
                ).exec_preview()
                continue
            return clicked is self.button(
                QMessageBox.StandardButton.Yes
            )


class GenerationDiagnosticsDialog:
    """Format and show a generation health report."""

    @staticmethod
    def message(
        report: Mapping[str, Any],
        record: Mapping[str, Any],
    ) -> str:
        api = report["api"]
        tasks = report["tasks"]
        preflight = report.get("preflight") or {}
        categories = report["errors"]["categories"]
        messages = [
            PetGenerationDiagnostics.compact_text(report),
            (
                f"剩余真实调用：{api['remaining_calls']}；"
                f"成功/失败调用："
                f"{api['successful_calls']}/{api['failed_calls']}"
            ),
            (
                f"待处理任务：{', '.join(tasks['remaining_ids'][:8])}"
                + ("…" if len(tasks["remaining_ids"]) > 8 else "")
            ),
            (
                "能力预检："
                + str(preflight.get("status", "尚未执行"))
                + (
                    f"（{preflight.get('note')}）"
                    if preflight.get("note")
                    else ""
                )
            ),
        ]
        if categories:
            messages.append(
                "错误分类："
                + "、".join(
                    f"{name} × {count}"
                    for name, count in categories.items()
                )
            )
        if report["errors"].get("latest"):
            messages.append(
                "最近错误：" + str(report["errors"]["latest"])
            )
        artifact = record.get("artifacts", {}).get(
            "generation_diagnostic_report"
        )
        if artifact:
            messages.append("JSON 报告：" + str(artifact))
        return "\n".join(messages)

    @classmethod
    def show(
        cls,
        parent: QWidget,
        report: Mapping[str, Any],
        record: Mapping[str, Any],
    ):
        QMessageBox.information(
            parent,
            "生成批次诊断",
            cls.message(report, record),
        )
