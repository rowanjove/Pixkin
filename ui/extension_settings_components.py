"""Settings controls for prompt gameplay and declarative plugins."""

from __future__ import annotations

import uuid
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.services.extension_service import (
    DEFAULT_GAMEPLAY_EXTENSIONS,
    ExtensionManifestError,
    ExtensionService,
    GameplayExtension,
)


class GameplayEditDialog(QDialog):
    def __init__(self, value: GameplayExtension | None = None, parent=None):
        super().__init__(parent)
        self.extension_id = (
            value.id if value is not None else f"local-{uuid.uuid4().hex[:12]}"
        )
        self.setWindowTitle("编辑加号菜单玩法")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        self.name_input = QLineEdit(value.name if value else "")
        self.name_input.setPlaceholderText("例如：三分钟灵感挑战")
        self.icon_input = QLineEdit(value.icon if value else "✨")
        self.icon_input.setMaxLength(4)
        self.icon_input.setMaximumWidth(90)
        self.prompt_input = QTextEdit(value.prompt if value else "")
        self.prompt_input.setPlaceholderText(
            "点击玩法后直接发送给伙伴的提示词"
        )
        self.prompt_input.setMinimumHeight(130)
        self.enabled_input = QCheckBox("显示在聊天窗口的加号菜单中")
        self.enabled_input.setChecked(value.enabled if value else True)
        form.addRow("玩法名称", self.name_input)
        form.addRow("图标", self.icon_input)
        form.addRow("提示词", self.prompt_input)
        form.addRow("", self.enabled_input)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_valid(self):
        try:
            self.value()
        except ExtensionManifestError as exc:
            QMessageBox.warning(self, "玩法设置无效", str(exc))
            return
        self.accept()

    def value(self) -> GameplayExtension:
        return ExtensionService.normalize_gameplay([
            {
                "id": self.extension_id,
                "name": self.name_input.text(),
                "icon": self.icon_input.text(),
                "prompt": self.prompt_input.toPlainText(),
                "enabled": self.enabled_input.isChecked(),
            }
        ])[0]


class ExtensionSettingsPanel(QWidget):
    """Edit the chat plus menu and install data-only plugin manifests."""

    def __init__(self, config: dict, plugin_root: str | Path, parent=None):
        super().__init__(parent)
        self.service = ExtensionService(plugin_root)
        self._configured = self._safe_gameplay(config.get("gameplay"))
        enabled = config.get("enabled_plugins")
        self._enabled_plugins = {
            str(item)
            for item in (enabled if isinstance(enabled, list) else [])
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)

        gameplay_title = QLabel("加号菜单玩法")
        gameplay_title.setObjectName("cardTitle")
        gameplay_hint = QLabel(
            "这些玩法会出现在聊天输入框左侧的“＋”菜单中；"
            "双击即可编辑名称、图标和提示词。"
        )
        gameplay_hint.setObjectName("muted")
        gameplay_hint.setWordWrap(True)
        layout.addWidget(gameplay_title)
        layout.addWidget(gameplay_hint)
        self.gameplay_table = QTableWidget(0, 4)
        self.gameplay_table.setHorizontalHeaderLabels(
            ["启用", "图标", "玩法名称", "发送提示词"]
        )
        self.gameplay_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.gameplay_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.gameplay_table.setAlternatingRowColors(True)
        self.gameplay_table.verticalHeader().hide()
        header = self.gameplay_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.resizeSection(0, 56)
        header.resizeSection(1, 56)
        header.resizeSection(2, 150)
        self.gameplay_table.setFixedHeight(150)
        self.gameplay_table.cellDoubleClicked.connect(
            lambda row, _column: self._edit_gameplay(row)
        )
        layout.addWidget(self.gameplay_table)
        gameplay_actions = QHBoxLayout()
        add = QPushButton("新增玩法")
        add.setObjectName("primary")
        edit = QPushButton("编辑")
        edit.setObjectName("secondary")
        remove = QPushButton("移除")
        remove.setObjectName("danger")
        reset = QPushButton("恢复默认")
        reset.setObjectName("secondary")
        add.clicked.connect(self._add_gameplay)
        edit.clicked.connect(self._edit_selected_gameplay)
        remove.clicked.connect(self._remove_gameplay)
        reset.clicked.connect(self._reset_gameplay)
        gameplay_actions.addWidget(add)
        gameplay_actions.addWidget(edit)
        gameplay_actions.addWidget(remove)
        gameplay_actions.addStretch()
        gameplay_actions.addWidget(reset)
        layout.addLayout(gameplay_actions)

        plugin_title = QLabel("插件扩展")
        plugin_title.setObjectName("cardTitle")
        plugin_hint = QLabel(
            "导入 JSON 插件清单即可增加玩法。插件只包含名称和提示词，"
            "不会加载或执行第三方代码。"
        )
        plugin_hint.setObjectName("muted")
        plugin_hint.setWordWrap(True)
        layout.addWidget(plugin_title)
        layout.addWidget(plugin_hint)
        self.plugin_table = QTableWidget(0, 3)
        self.plugin_table.setHorizontalHeaderLabels(
            ["启用", "插件", "版本与能力"]
        )
        self.plugin_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.plugin_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.plugin_table.verticalHeader().hide()
        plugin_header = self.plugin_table.horizontalHeader()
        plugin_header.setStretchLastSection(True)
        plugin_header.resizeSection(0, 56)
        plugin_header.resizeSection(1, 210)
        self.plugin_table.setFixedHeight(75)
        layout.addWidget(self.plugin_table)
        plugin_actions = QHBoxLayout()
        install = QPushButton("导入插件清单")
        install.setObjectName("secondary")
        remove_plugin = QPushButton("移除插件")
        remove_plugin.setObjectName("danger")
        install.clicked.connect(self._install_plugin)
        remove_plugin.clicked.connect(self._remove_plugin)
        plugin_actions.addWidget(install)
        plugin_actions.addWidget(remove_plugin)
        plugin_actions.addStretch()
        layout.addLayout(plugin_actions)

        self._refresh_gameplay()
        self._refresh_plugins()

    @staticmethod
    def _safe_gameplay(value) -> list[GameplayExtension]:
        try:
            return ExtensionService.normalize_gameplay(value)
        except ExtensionManifestError:
            return ExtensionService.normalize_gameplay(
                DEFAULT_GAMEPLAY_EXTENSIONS
            )

    def _refresh_gameplay(self):
        self.gameplay_table.setRowCount(0)
        for extension in self._configured:
            row = self.gameplay_table.rowCount()
            self.gameplay_table.insertRow(row)
            enabled = QTableWidgetItem("")
            enabled.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            enabled.setCheckState(
                Qt.CheckState.Checked
                if extension.enabled
                else Qt.CheckState.Unchecked
            )
            enabled.setData(Qt.ItemDataRole.UserRole, extension.id)
            icon = QTableWidgetItem(extension.icon)
            name = QTableWidgetItem(extension.name)
            prompt = QTableWidgetItem(extension.prompt)
            for item in (icon, name, prompt):
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
            self.gameplay_table.setItem(row, 0, enabled)
            self.gameplay_table.setItem(row, 1, icon)
            self.gameplay_table.setItem(row, 2, name)
            self.gameplay_table.setItem(row, 3, prompt)

    def _refresh_plugins(self):
        self.plugin_table.setRowCount(0)
        for plugin in self.service.list_plugins():
            row = self.plugin_table.rowCount()
            self.plugin_table.insertRow(row)
            enabled = QTableWidgetItem("")
            enabled.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            enabled.setCheckState(
                Qt.CheckState.Checked
                if plugin.id in self._enabled_plugins
                else Qt.CheckState.Unchecked
            )
            enabled.setData(Qt.ItemDataRole.UserRole, plugin.id)
            name = QTableWidgetItem(plugin.name)
            name.setToolTip(plugin.description)
            detail = QTableWidgetItem(
                f"v{plugin.version} · {len(plugin.gameplay)} 项玩法"
            )
            self.plugin_table.setItem(row, 0, enabled)
            self.plugin_table.setItem(row, 1, name)
            self.plugin_table.setItem(row, 2, detail)

    def _selected_gameplay_row(self) -> int:
        return self.gameplay_table.currentRow()

    def _add_gameplay(self):
        self._configured = self._gameplay_values()
        dialog = GameplayEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._configured.append(dialog.value())
        self._refresh_gameplay()
        self.gameplay_table.setCurrentCell(
            self.gameplay_table.rowCount() - 1, 2
        )

    def _edit_selected_gameplay(self):
        self._edit_gameplay(self._selected_gameplay_row())

    def _edit_gameplay(self, row: int):
        self._configured = self._gameplay_values()
        if not 0 <= row < len(self._configured):
            return
        dialog = GameplayEditDialog(self._configured[row], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._configured[row] = dialog.value()
        self._refresh_gameplay()
        self.gameplay_table.setCurrentCell(row, 2)

    def _remove_gameplay(self):
        row = self._selected_gameplay_row()
        self._configured = self._gameplay_values()
        if not 0 <= row < len(self._configured):
            return
        self._configured.pop(row)
        self._refresh_gameplay()

    def _reset_gameplay(self):
        self._configured = ExtensionService.normalize_gameplay(
            DEFAULT_GAMEPLAY_EXTENSIONS
        )
        self._refresh_gameplay()

    def _install_plugin(self):
        self._enabled_plugins = set(self._enabled_plugin_values())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Pixkin 插件清单",
            "",
            "Pixkin 插件清单 (*.json)",
        )
        if not path:
            return
        try:
            plugin = self.service.install_plugin(path)
        except ExtensionManifestError as exc:
            QMessageBox.warning(self, "插件无法导入", str(exc))
            return
        self._enabled_plugins.add(plugin.id)
        self._refresh_plugins()

    def _remove_plugin(self):
        self._enabled_plugins = set(self._enabled_plugin_values())
        row = self.plugin_table.currentRow()
        item = self.plugin_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        plugin_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        answer = QMessageBox.question(
            self,
            "移除插件",
            "确定移除该插件清单吗？插件提供的玩法也会从加号菜单消失。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.remove_plugin(plugin_id)
        except ExtensionManifestError as exc:
            QMessageBox.warning(self, "插件无法移除", str(exc))
            return
        self._enabled_plugins.discard(plugin_id)
        self._refresh_plugins()

    def _gameplay_values(self) -> list[GameplayExtension]:
        gameplay = []
        for row in range(self.gameplay_table.rowCount()):
            enabled = self.gameplay_table.item(row, 0)
            gameplay.append({
                "id": str(enabled.data(Qt.ItemDataRole.UserRole)),
                "name": self.gameplay_table.item(row, 2).text(),
                "icon": self.gameplay_table.item(row, 1).text(),
                "prompt": self.gameplay_table.item(row, 3).text(),
                "enabled": enabled.checkState() == Qt.CheckState.Checked,
            })
        return ExtensionService.normalize_gameplay(gameplay)

    def _enabled_plugin_values(self) -> list[str]:
        enabled_plugins = []
        for row in range(self.plugin_table.rowCount()):
            item = self.plugin_table.item(row, 0)
            if item.checkState() == Qt.CheckState.Checked:
                enabled_plugins.append(
                    str(item.data(Qt.ItemDataRole.UserRole))
                )
        return enabled_plugins

    def values(self) -> dict:
        normalized = self._gameplay_values()
        return {
            "gameplay": [item.to_config() for item in normalized],
            "enabled_plugins": self._enabled_plugin_values(),
        }
