"""Read-only archive inspector and visual behavior override editor."""

from __future__ import annotations

import copy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class CharacterPackageInspectorDialog(QDialog):
    """Preview a validated package report without installing it."""

    def __init__(self, inspection, parent=None):
        super().__init__(parent)
        self.setWindowTitle("角色包检查器")
        self.resize(560, 500)
        layout = QVBoxLayout(self)
        preview = QLabel("无预览图")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFixedHeight(170)
        if inspection.preview_bytes:
            pixmap = QPixmap()
            pixmap.loadFromData(inspection.preview_bytes)
            if not pixmap.isNull():
                preview.setPixmap(
                    pixmap.scaled(
                        150,
                        150,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        layout.addWidget(preview)
        form = QFormLayout()
        form.addRow("角色", QLabel(f"{inspection.name} ({inspection.package_id})"))
        form.addRow("版本", QLabel(inspection.version))
        form.addRow("作者声明", QLabel(inspection.author))
        trust = (
            "官方哈希已验证"
            if inspection.official
            else "第三方包；作者声明不受信任"
        )
        form.addRow("来源", QLabel(trust))
        form.addRow(
            "兼容性",
            QLabel(inspection.compatibility_message),
        )
        form.addRow("文件数", QLabel(str(inspection.file_count)))
        fingerprint = QLabel(inspection.fingerprint)
        fingerprint.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        fingerprint.setWordWrap(True)
        form.addRow("SHA-256 指纹", fingerprint)
        description = QLabel(inspection.description or "未提供")
        description.setWordWrap(True)
        form.addRow("说明", description)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CharacterBehaviorEditor(QWidget):
    """Edit per-character weights, cooldowns and peek size visually."""

    preview_requested = pyqtSignal(str)

    def __init__(self, overrides=None, parent=None):
        super().__init__(parent)
        self._overrides = copy.deepcopy(
            overrides if isinstance(overrides, dict) else {}
        )
        self._package_id = ""
        self._states: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(
            "覆盖值只保存在本机配置，不修改或重新签名角色包。"
            "双击动作行可立即预览。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["动作", "随机权重", "冷却（秒）"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.cellDoubleClicked.connect(
            lambda row, _column: self.preview_requested.emit(
                self._states[row]
            )
        )
        layout.addWidget(self.table, 1)
        form = QFormLayout()
        self.peek_input = QSpinBox()
        self.peek_input.setRange(36, 96)
        self.peek_input.setSuffix(" px")
        form.addRow("贴边露出", self.peek_input)
        layout.addLayout(form)

    def set_package(self, package):
        self._commit_current()
        self._package_id = (
            str(package.package_id) if package is not None else ""
        )
        self._states = sorted(
            str(state)
            for state in (
                package.animations.keys() if package is not None else []
            )
            if not str(state).startswith("edge_")
            and str(state) not in {
                "idle",
                "talking",
                "listening",
                "thinking",
                "working",
                "dragging",
            }
        )
        self.table.setRowCount(len(self._states))
        values = self._overrides.get(self._package_id, {})
        weights = values.get("ambient_weights", {})
        cooldowns = values.get("cooldown_seconds", {})
        for row, state in enumerate(self._states):
            name = QTableWidgetItem(state)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name)
            weight = QDoubleSpinBox()
            weight.setRange(0.0, 20.0)
            weight.setDecimals(2)
            weight.setSingleStep(0.1)
            weight.setValue(float(weights.get(state, 0.0)))
            self.table.setCellWidget(row, 1, weight)
            cooldown = QDoubleSpinBox()
            cooldown.setRange(0.0, 3600.0)
            cooldown.setDecimals(1)
            cooldown.setValue(float(cooldowns.get(state, 0.0)))
            self.table.setCellWidget(row, 2, cooldown)
        self.peek_input.setValue(int(values.get("peek_size", 45)))

    def values(self) -> dict:
        self._commit_current()
        return copy.deepcopy(self._overrides)

    def _commit_current(self):
        if not self._package_id:
            return
        weights = {}
        cooldowns = {}
        for row, state in enumerate(self._states):
            weight = self.table.cellWidget(row, 1)
            cooldown = self.table.cellWidget(row, 2)
            if weight is not None:
                weights[state] = weight.value()
            if cooldown is not None:
                cooldowns[state] = cooldown.value()
        self._overrides[self._package_id] = {
            "ambient_weights": weights,
            "cooldown_seconds": cooldowns,
            "peek_size": self.peek_input.value(),
        }
