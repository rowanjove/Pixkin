"""Privacy and local-data controls for the settings center."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.chat_history_store import ChatHistoryStore
from core.services.tool_audit_store import ToolAuditStore
from core.services.diagnostic_bundle_service import (
    DiagnosticBundleService,
)
from core.services.local_data_backup_service import (
    LocalDataBackupError,
    LocalDataBackupService,
)
from ui.tool_audit_window import ToolAuditWindow


class PrivacySettingsPanel(QWidget):
    """Present retention and destructive data controls in one component."""

    history_changed = pyqtSignal()

    def __init__(
        self,
        *,
        retention_days: int,
        chat_store: ChatHistoryStore | None,
        audit_store: ToolAuditStore | None,
        diagnostic_service: DiagnosticBundleService | None = None,
        local_backup_service: LocalDataBackupService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.chat_store = chat_store
        self.audit_store = audit_store
        self.diagnostic_service = diagnostic_service
        self.local_backup_service = local_backup_service
        self._audit_window = None
        self._build_ui(retention_days)

    def _build_ui(self, retention_days: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        retention_row = QHBoxLayout()
        retention_row.addWidget(QLabel("聊天历史保留"))
        self.retention_combo = QComboBox()
        for label, value in (
            ("不保存", 0),
            ("7 天", 7),
            ("30 天", 30),
            ("永久", -1),
        ):
            self.retention_combo.addItem(label, value)
        index = self.retention_combo.findData(int(retention_days))
        self.retention_combo.setCurrentIndex(max(0, index))
        retention_row.addWidget(self.retention_combo)
        retention_row.addStretch()
        layout.addLayout(retention_row)

        notice = QLabel(
            "聊天内容仅保存在本机 SQLite；发送消息时，当前系统提示词和"
            "受上下文上限约束的对话会发送到你配置的模型接口。"
        )
        notice.setWordWrap(True)
        notice.setObjectName("muted")
        layout.addWidget(notice)

        location = QLabel(
            "本地聊天库："
            + (
                str(self.chat_store.database_path)
                if self.chat_store is not None
                else "本次设置窗口未连接聊天库"
            )
        )
        location.setWordWrap(True)
        location.setObjectName("muted")
        layout.addWidget(location)

        actions = QHBoxLayout()
        audit_button = QPushButton("查看工具审计")
        audit_button.setObjectName("secondary")
        audit_button.setEnabled(self.audit_store is not None)
        audit_button.clicked.connect(self._open_audit)
        diagnostics_button = QPushButton("导出匿名诊断包")
        diagnostics_button.setObjectName("secondary")
        diagnostics_button.setEnabled(
            self.diagnostic_service is not None
        )
        diagnostics_button.clicked.connect(
            self._export_diagnostics
        )
        clear_history = QPushButton("清空全部聊天历史")
        clear_history.setObjectName("danger")
        clear_history.setEnabled(self.chat_store is not None)
        clear_history.clicked.connect(self._clear_history)
        backup_button = QPushButton("备份全部本地数据")
        backup_button.setObjectName("secondary")
        backup_button.setEnabled(self.local_backup_service is not None)
        backup_button.clicked.connect(self._backup_local_data)
        restore_button = QPushButton("从备份恢复")
        restore_button.setObjectName("danger")
        restore_button.setEnabled(self.local_backup_service is not None)
        restore_button.clicked.connect(self._stage_local_restore)
        actions.addWidget(audit_button)
        actions.addWidget(diagnostics_button)
        actions.addWidget(clear_history)
        actions.addStretch()
        layout.addLayout(actions)
        backup_actions = QHBoxLayout()
        backup_actions.addWidget(backup_button)
        backup_actions.addWidget(restore_button)
        backup_actions.addStretch()
        layout.addLayout(backup_actions)

    def retention_days(self) -> int:
        return int(self.retention_combo.currentData())

    def _open_audit(self) -> None:
        if self.audit_store is None:
            return
        window = ToolAuditWindow(self.audit_store, self)
        window.setModal(True)
        self._audit_window = window
        window.exec()
        self._audit_window = None

    def _clear_history(self) -> None:
        if self.chat_store is None:
            return
        answer = QMessageBox.question(
            self,
            "清空全部聊天历史",
            "确定永久删除所有角色的全部聊天内容吗？此操作无法撤销。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = self.chat_store.clear_all()
        self.history_changed.emit()
        QMessageBox.information(
            self,
            "聊天历史已清空",
            f"已删除 {count} 条本地消息。",
        )

    def _export_diagnostics(self) -> None:
        if self.diagnostic_service is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "导出匿名诊断包",
            "Pixkin-匿名诊断.zip",
            "ZIP (*.zip)",
        )
        if not destination:
            return
        try:
            target = self.diagnostic_service.export(destination)
            self.diagnostic_service.inspect(target)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(
            self,
            "诊断包已导出",
            "诊断包仅包含版本、系统摘要和脱敏崩溃摘要：\n"
            f"{target}",
        )

    def _backup_local_data(self) -> None:
        if self.local_backup_service is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "备份全部本地数据",
            "Pixkin-本地数据备份.zip",
            "ZIP (*.zip)",
        )
        if not destination:
            return
        try:
            target = self.local_backup_service.export(destination)
            self.local_backup_service.inspect(target)
        except (OSError, LocalDataBackupError) as exc:
            QMessageBox.warning(self, "备份失败", str(exc))
            return
        QMessageBox.information(
            self,
            "备份完成",
            "备份包含配置、聊天、角色、头像和孵化任务，"
            "可能含私人内容，请妥善保存：\n"
            f"{target}",
        )

    def _stage_local_restore(self) -> None:
        if self.local_backup_service is None:
            return
        source, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Pixkin 本地数据备份",
            "",
            "ZIP (*.zip)",
        )
        if not source:
            return
        answer = QMessageBox.question(
            self,
            "确认安排恢复",
            "备份会先完整校验并暂存，下次启动 Pixkin 时才替换数据；"
            "替换前仍会保留当前数据备份。是否继续？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.local_backup_service.stage_restore(source)
        except (OSError, LocalDataBackupError) as exc:
            QMessageBox.warning(self, "恢复暂存失败", str(exc))
            return
        QMessageBox.information(
            self,
            "恢复已安排",
            "请正常退出并重新启动 Pixkin。下次启动会校验暂存文件，"
            "恢复前的当前数据仍会保留在 backups 目录。",
        )
