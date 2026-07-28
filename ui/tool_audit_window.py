"""Local viewer for privacy-preserving tool audit records."""

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.services.tool_audit_store import ToolAuditStore


class ToolAuditWindow(QDialog):
    """View, export and clear the bounded local tool audit."""

    def __init__(self, store: ToolAuditStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Pixkin · 工具审计")
        self.setMinimumSize(760, 480)
        self.resize(860, 560)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("工具调用审计")
        title.setStyleSheet("font-size: 21px; font-weight: 800;")
        hint = QLabel(
            "仅记录工具、脱敏参数、授权结果和耗时；不记录 API Key 或完整聊天内容。"
        )
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["时间", "会话", "工具", "等级", "授权", "结果", "耗时"]
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        self.count_label = QLabel()
        buttons = QHBoxLayout()
        export_button = QPushButton("导出 JSON")
        export_button.clicked.connect(self._export)
        clear_button = QPushButton("清除审计")
        clear_button.clicked.connect(self._clear)
        close_button = QPushButton("完成")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(self.count_label)
        buttons.addStretch()
        buttons.addWidget(export_button)
        buttons.addWidget(clear_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        events = self.store.events()
        self.table.setRowCount(len(events))
        for row, event in enumerate(reversed(events)):
            try:
                timestamp = datetime.fromtimestamp(
                    event.timestamp
                ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except (OSError, OverflowError, ValueError):
                timestamp = "-"
            values = (
                timestamp,
                event.session_id[:12] or "-",
                event.tool_name,
                f"L{int(event.level)}",
                event.authorization,
                event.result_status,
                f"{event.duration_ms} ms",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(
                    Qt.ItemDataRole.ToolTipRole,
                    str(dict(event.arguments))
                    if column == 2
                    else str(value),
                )
                self.table.setItem(row, column, item)
        self.count_label.setText(f"本机保留 {len(events)} 条")
        self.table.resizeColumnsToContents()

    def _export(self) -> None:
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "导出工具审计",
            "Pixkin-工具审计.json",
            "JSON (*.json)",
        )
        if not destination:
            return
        try:
            target = self.store.export(destination)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(
            self,
            "导出完成",
            f"脱敏审计已保存到：\n{target}",
        )

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "清除工具审计",
            "确定永久清除本机全部工具审计记录吗？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.clear()
        except OSError as exc:
            QMessageBox.warning(self, "清除失败", str(exc))
            return
        self.refresh()
