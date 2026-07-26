"""Startup recovery entry for a damaged local chat database."""

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.services.chat_database_recovery_service import (
    ChatDatabaseRecoveryError,
    ChatDatabaseRecoveryService,
)


class ChatDatabaseRecoveryDialog(QDialog):
    def __init__(
        self,
        service: ChatDatabaseRecoveryService,
        backup_root: str | Path,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.backup_root = Path(backup_root)
        self.backup_path = None
        self.setWindowTitle("Pixkin · 聊天数据库恢复")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        title = QLabel("聊天数据库损坏，Pixkin 已停止写入")
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        detail = QLabel(
            "你可以先尝试只读导出仍可读取的消息，再备份原文件并"
            "重建空数据库。重建前原文件会完整复制并校验 SHA-256。"
        )
        detail.setWordWrap(True)
        export_button = QPushButton("只读导出可恢复消息")
        export_button.clicked.connect(self._export)
        rebuild_button = QPushButton("备份原文件并重建")
        rebuild_button.clicked.connect(self._rebuild)
        exit_button = QPushButton("暂不处理并退出")
        exit_button.clicked.connect(self.reject)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(export_button)
        layout.addWidget(rebuild_button)
        layout.addWidget(exit_button)

    def _export(self) -> None:
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "导出可恢复聊天消息",
            "Pixkin-可恢复聊天消息.json",
            "JSON (*.json)",
        )
        if not destination:
            return
        try:
            target = self.service.export_readonly(destination)
        except (OSError, ChatDatabaseRecoveryError) as exc:
            QMessageBox.warning(self, "只读导出失败", str(exc))
            return
        QMessageBox.information(
            self,
            "只读导出完成",
            f"可读取的消息已保存到：\n{target}",
        )

    def _rebuild(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认重建聊天数据库",
            "确定备份损坏文件并创建空聊天数据库吗？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.backup_path = self.service.backup_and_remove(
                self.backup_root
            )
        except (OSError, ChatDatabaseRecoveryError) as exc:
            QMessageBox.critical(self, "重建准备失败", str(exc))
            return
        self.accept()
