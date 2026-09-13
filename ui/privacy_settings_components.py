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
from core.runtime.permissions import PermissionResource, PermissionState
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
        config_manager=None,
        context_permission_service=None,
        parent=None,
    ):
        super().__init__(parent)
        self.chat_store = chat_store
        self.audit_store = audit_store
        self.diagnostic_service = diagnostic_service
        self.local_backup_service = local_backup_service
        self.config_manager = config_manager
        self.context_permission_service = context_permission_service
        self.context_permission_combos = {}
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

        context_title = QLabel("桌面感知权限")
        context_title.setObjectName("sectionTitle")
        layout.addWidget(context_title)
        context_hint = QLabel(
            "默认拒绝。只有允许后，Pixkin 才会读取对应桌面信息；"
            "原始内容不写入日志。"
        )
        context_hint.setWordWrap(True)
        context_hint.setObjectName("muted")
        layout.addWidget(context_hint)
        saved_permissions = {}
        if self.config_manager is not None:
            raw = self.config_manager.get(
                "privacy", "context_permissions", {}
            )
            if isinstance(raw, dict):
                saved_permissions = raw
        for resource, label in (
            (PermissionResource.WINDOW_METADATA, "前台窗口与进程名"),
            (PermissionResource.SYSTEM_STATE, "系统空闲时间"),
            (PermissionResource.CLIPBOARD, "剪贴板文本"),
            (PermissionResource.SCREEN, "屏幕截图（仅按需）"),
            (PermissionResource.PLUGIN, "第三方插件进程"),
            (PermissionResource.MCP, "MCP 外部工具"),
            (PermissionResource.MICROPHONE, "麦克风录音"),
            (PermissionResource.CAMERA, "摄像头（当前未持续采集）"),
            (PermissionResource.FILESYSTEM, "文件访问（按工具请求）"),
            (PermissionResource.NETWORK, "网络访问（按 Provider/工具请求）"),
            (PermissionResource.EXTERNAL_ACTION, "外部动作（按次确认）"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            combo = QComboBox()
            for state, state_label in (
                (PermissionState.DENY, "拒绝"),
                (PermissionState.ASK, "每次询问"),
                (PermissionState.ALLOW_SESSION, "本次运行"),
                (PermissionState.ALLOW_ALWAYS, "始终允许"),
            ):
                combo.addItem(state_label, state.value)
            try:
                if self.context_permission_service is not None:
                    saved = self.context_permission_service.state(resource).value
                else:
                    saved = PermissionState(
                        saved_permissions.get(resource.value, PermissionState.DENY.value)
                    ).value
                index = combo.findData(saved)
                combo.setCurrentIndex(max(0, index))
            except ValueError:
                combo.setCurrentIndex(0)
            row.addWidget(combo)
            row.addStretch()
            layout.addLayout(row)
            self.context_permission_combos[resource] = combo

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

    def context_permissions(self) -> dict[str, str]:
        # Persisting a session choice as DENY clears an older persistent grant;
        # the owning application reapplies the in-memory session grant after
        # saving this settings page. This prevents an ``ALLOW_ALWAYS`` value
        # from surviving when the user downgrades it to this-run access.
        return {
            resource.value: (
                PermissionState.DENY.value
                if combo.currentData() == PermissionState.ALLOW_SESSION.value
                else str(combo.currentData())
            )
            for resource, combo in self.context_permission_combos.items()
        }

    def effective_context_permissions(self) -> dict[str, str]:
        """Return the runtime state shown in the controls, including session grants."""
        return {
            resource.value: str(combo.currentData())
            for resource, combo in self.context_permission_combos.items()
        }

    def context_session_permissions(self) -> tuple[PermissionResource, ...]:
        """Return grants that should live only until the current process exits."""
        return tuple(
            resource
            for resource, combo in self.context_permission_combos.items()
            if combo.currentData() == PermissionState.ALLOW_SESSION.value
        )

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
            "备份包含配置、聊天、角色、头像、孵化任务、插件、审计和崩溃记录，"
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
