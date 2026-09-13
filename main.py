import logging
import os
import sys
import traceback
import time
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QLockFile, QThread, QTimer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QPainter, QPixmap
)

from core.ai_engine import AiWorkerThread
from core.app_logging import configure_logging
from core.chat_history_store import (
    ChatDatabaseCorruptError,
    ChatHistoryStore,
)
from core.character_package import (
    DEFAULT_PACKAGE_ID, CharacterPackageManager
)
from core.config import ConfigManager
from core.live_monitor import LiveMonitorThread
from core.providers.live import ADAPTERS, LiveProviderRouter
from core.paths import resource_path, user_data_dir
from core.pet_animator import PetState
from core.privacy import model_data_scope, privacy_scope_fingerprint
from core.secrets import SecretStore
from core.services.chat_service import (
    ChatSessionService,
    ChatSessionSnapshot,
)
from core.services.character_service import CharacterService
from core.services.character_trust_service import (
    OfficialCharacterTrustError,
    OfficialCharacterTrustStore,
)
from core.services.tool_audit_store import ToolAuditStore
from core.services.tool_permission_service import ToolPermissionService
from core.services.session_secret_store import SessionSecretStore
from core.services.crash_report_store import CrashReportStore
from core.services.diagnostic_bundle_service import (
    DiagnosticBundleService,
)
from core.services.chat_database_recovery_service import (
    ChatDatabaseRecoveryService,
)
from core.services.local_data_backup_service import (
    LocalDataBackupError,
    LocalDataBackupService,
)
from core.services.memory_service import MemoryService, MemoryStoreError
from core.services.memory_v2_service import MemoryV2Service
from core.services.character_state_service import CharacterStateService
from core.services.global_hotkey_service import GlobalHotkeyManager
from core.services.voice_input_service import (
    VoiceEndpoint,
    VoiceTranscriptionService,
)
from core.services.chat_experience_service import (
    estimate_chat,
    preset_prompt,
)
from core.services.live_provider_registry import (
    LiveProviderPluginError,
    LiveProviderRegistry,
)
from core.services.update_service import (
    UpdateManifestVerifier,
    UpdateService,
    detect_install_mode,
    installed_manifest_urls,
)
from core.storage.migrations import StorageMigrationError
from core.tool_registry import ToolExecutionContext, ToolRegistry
from core.structured_logging import log_event
from core.version import VERSION
from core.windows_integration import set_start_with_windows
from core.services.context_sensor_service import ContextSensorService
from core.services.perception_context import PerceptionContextProjector
from core.services.proactive_companion_service import (
    ProactiveCompanionService,
    ProactivePolicyConfig,
)
from core.services.voice_output_service import VoiceOutputService
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
    PermissionState,
)
from core.providers.catalog import build_builtin_provider_catalog
from core.speech import SpeechInterruptionController, SpeechState
from core.plugins import PluginHost
from core.runtime import (
    ActionDispatcher,
    EventBus,
    EventEnvelope,
    PixkinKernel,
    ServiceContainer,
    StartupCoordinator,
)
from ui.onboarding_window import FirstRunWindow
from ui.chat_database_recovery_dialog import ChatDatabaseRecoveryDialog
from ui.history_window import HistoryWindow
from ui.pet_lab_window import PetLabWindow
from ui.pet_window import PetWindow
from ui.settings_window import SettingsWindow
from ui.theme import configured_theme, resolved_theme
from ui.tool_permission_bridge import ToolPermissionBridge
from ui.update_settings_components import UpdateCheckWorker
from ui.voice_input_components import (
    PushToTalkRecorder,
    VoiceTranscriptionWorker,
)
from ui.voice_output_components import play_audio_bytes


class TtsPlaybackWorker(QThread):
    """异步执行 TTS 合成并播放音频，不阻塞 Qt 主事件循环。"""

    def __init__(self, service: VoiceOutputService, text: str):
        super().__init__()
        self.service = service
        self.text = text

    def run(self):
        try:
            audio = self.service.synthesize_sentence(self.text)
            if audio and not self.isInterruptionRequested():
                play_audio_bytes(audio)
        except Exception as exc:
            logging.getLogger("desktop_pet.tts").debug(
                "TTS 播放失败: %s", exc
            )

    def stop_playback(self, *, cancel_request: bool = False) -> None:
        self.requestInterruption()
        if cancel_request:
            cancel = getattr(self.service, "cancel", None)
            if callable(cancel):
                cancel()
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass


LOGGER = logging.getLogger("desktop_pet.main")
BUILTIN_CHARACTER_ARCHIVES = ("shanshan.zip", "linlin.zip", "pip.zip")
OPTIONAL_OFFICIAL_CHARACTER_ARCHIVES = ("yeye.zip",)
OFFICIAL_CHARACTER_AUTHORS = {"Pixkin", "Pixkin 伙伴工坊"}
LEGACY_DEFAULT_PACKAGE_IDS = {"", "default-assistant", "pixkin-pip"}


def create_tray_icon() -> QIcon:
    branded = QIcon(str(resource_path("assets/pixkin.ico")))
    if not branded.isNull():
        return branded
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#7467E8")))
    painter.drawEllipse(2, 2, 28, 28)
    painter.setBrush(QBrush(QColor("#FFFFFF")))
    painter.drawEllipse(9, 10, 4, 6)
    painter.drawEllipse(19, 10, 4, 6)
    painter.setBrush(QBrush(QColor("#F4A9C3")))
    painter.drawEllipse(14, 20, 4, 3)
    painter.end()
    return QIcon(pixmap)


class DesktopPetApp:
    """协调首次导入、桌宠、AI、并发监听、托盘与 Windows 集成。"""

    def __init__(self):
        self._startup_started = time.perf_counter()
        self.startup_coordinator = StartupCoordinator(())
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("Pixkin")
        self.app.setOrganizationName("Pixkin")
        self.app.setApplicationVersion(VERSION)
        self.app.setWindowIcon(create_tray_icon())
        self.app.setStyle("Fusion")
        self.app.setFont(QFont("Microsoft YaHei UI", 9))
        self.ready = False
        self._quitting = False
        self._quit_finalized = False
        self._monitor_restart_pending = False
        self._settings_window = None
        self._update_check_worker = None
        self.session_secrets = SessionSecretStore()
        self.crash_report_store = CrashReportStore()
        self.diagnostic_bundle_service = DiagnosticBundleService(
            self.crash_report_store
        )

        self._instance_lock = QLockFile(
            str(user_data_dir() / "pixkin.instance.lock")
        )
        self._instance_lock.setStaleLockTime(5000)
        if not self._instance_lock.tryLock(100):
            QMessageBox.information(
                None, "Pixkin 已经在运行",
                "请在系统托盘中找到正在运行的 Pixkin。"
            )
            return
        self.startup_coordinator.mark("single_instance")
        self.local_data_backup_service = LocalDataBackupService(
            user_data_dir()
        )
        try:
            restore_backup = (
                self.local_data_backup_service.apply_pending_restore()
            )
        except (OSError, LocalDataBackupError) as exc:
            QMessageBox.critical(
                None,
                "本地数据恢复失败",
                f"{exc}\n\n原数据和恢复暂存均已保留，Pixkin 未继续启动。",
            )
            self._instance_lock.unlock()
            return
        if restore_backup is not None:
            QMessageBox.information(
                None,
                "本地数据恢复完成",
                "备份中的配置、聊天、角色和孵化任务已恢复；"
                "恢复前数据保存在：\n"
                f"{restore_backup}",
            )
        self.startup_coordinator.mark("pending_restore")

        try:
            self.config_mgr = ConfigManager()
        except StorageMigrationError as exc:
            QMessageBox.critical(
                None,
                "本地配置版本不兼容",
                f"{exc}\n\nPixkin 未修改原配置文件。"
                "请使用创建该配置的新版 Pixkin，或先备份后迁移。",
            )
            self._instance_lock.unlock()
            return
        self.startup_coordinator.mark("config_migration")
        self.context_permission_service = ContextPermissionService()
        self._load_context_permissions()
        self.event_bus = EventBus()
        self._live_event_subscription = self.event_bus.subscribe(
            "stream.started",
            self._on_stream_started_event,
        )
        self.action_dispatcher = ActionDispatcher(
            self.context_permission_service
        )
        self.service_container = ServiceContainer()
        self.plugin_host = PluginHost(
            user_data_dir() / "plugins",
            permission_service=self.context_permission_service,
        )
        self.discovered_plugins = self.plugin_host.discover()
        self.startup_coordinator.mark("plugin_scan")
        self.provider_catalog = build_builtin_provider_catalog()
        self.startup_coordinator.mark("provider_registration")
        try:
            self.app.styleHints().colorSchemeChanged.connect(
                self._on_system_theme_changed
            )
        except AttributeError:
            pass
        self.update_service = None
        try:
            update_verifier = UpdateManifestVerifier(
                resource_path(
                    "assets/update-public-key.pem"
                ).read_bytes()
            )
            self.update_service = UpdateService(
                update_verifier,
                user_data_dir() / "updates" / "cache",
            )
        except (OSError, ValueError) as exc:
            LOGGER.warning(
                "更新服务初始化失败（%s）",
                type(exc).__name__,
            )
        configured_update_urls = self.config_mgr.get(
            "app", "update_manifest_urls", {}
        )
        self.update_manifest_urls = (
            dict(configured_update_urls)
            if isinstance(configured_update_urls, dict)
            else {}
        )
        self.update_manifest_urls.update(
            installed_manifest_urls()
        )
        self._migrate_api_key()
        self.package_manager = CharacterPackageManager(self.config_mgr)
        self.character_service = CharacterService(self.package_manager)
        self._ensure_builtin_characters()
        self._update_installed_official_characters()
        self._upgrade_legacy_character()
        active_package = self.character_service.get_active()
        if active_package is None:
            onboarding = FirstRunWindow(
                self.package_manager,
                character_service=self.character_service,
            )
            if onboarding.exec() != QDialog.DialogCode.Accepted:
                self._instance_lock.unlock()
                return
            active_package = self.character_service.get_active()
        if active_package is None:
            QMessageBox.critical(
                None, "角色加载失败", "没有可用的角色包，程序无法继续启动。"
            )
            self._instance_lock.unlock()
            return
        self.startup_coordinator.mark("character_load")

        self.tool_permission_bridge = ToolPermissionBridge()
        try:
            self.tool_audit_store = ToolAuditStore(
                user_data_dir() / "audit" / "tool-audit.json"
            )
        except OSError as exc:
            LOGGER.warning(
                "工具审计存储初始化失败（%s）",
                type(exc).__name__,
            )
            self.tool_audit_store = None
        permission_service = ToolPermissionService(
            confirm_external=self.tool_permission_bridge.confirm,
            audit_sink=(
                self.tool_audit_store.append
                if self.tool_audit_store is not None
                else None
            ),
            session_id_provider=lambda: getattr(
                getattr(self, "chat_session", None),
                "session_id",
                "",
            ),
        )
        self.tool_registry = ToolRegistry(permission_service)
        try:
            self.chat_store = ChatHistoryStore()
        except ChatDatabaseCorruptError:
            database_path = user_data_dir() / "chat-history.sqlite3"
            recovery = ChatDatabaseRecoveryService(database_path)
            dialog = ChatDatabaseRecoveryDialog(
                recovery,
                user_data_dir() / "backups",
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._instance_lock.unlock()
                return
            try:
                self.chat_store = ChatHistoryStore(database_path)
            except Exception as exc:
                QMessageBox.critical(
                    None,
                    "聊天数据库重建失败",
                    f"{exc}\n\n原文件备份未被删除。",
                )
                self._instance_lock.unlock()
                return
            QMessageBox.information(
                None,
                "聊天数据库已重建",
                "Pixkin 已创建空聊天数据库；损坏原文件备份在：\n"
                f"{dialog.backup_path}",
            )
        except StorageMigrationError as exc:
            QMessageBox.critical(
                None,
                "聊天历史版本不兼容",
                f"{exc}\n\nPixkin 未修改聊天数据库。"
                "请使用创建该数据库的新版 Pixkin。",
            )
            self._instance_lock.unlock()
            return
        self.local_data_backup_service.chat_store = self.chat_store
        self.chat_session = ChatSessionService(self.chat_store)
        try:
            self.memory_service = MemoryV2Service(
                user_data_dir() / "memory.sqlite3",
                legacy_json_path=user_data_dir() / "memories.json",
            )
        except MemoryStoreError as exc:
            QMessageBox.critical(
                None,
                "长期记忆无法读取",
                f"{exc}\n\n原文件未被修改。请先备份或移走 memories.json 后重试。",
            )
            self._instance_lock.unlock()
            return
        self.startup_coordinator.mark("database_migration")
        chat_session = getattr(self, "chat_session", None)
        if chat_session is not None:
            chat_session.set_retention(
                int(
                    self.config_mgr.get(
                        "privacy",
                        "history_retention_days",
                        -1,
                    )
                )
            )
        self.pet_window = PetWindow(self.config_mgr, active_package)
        self.context_permission_service.set_ask_callback(
            self._ask_context_permission
        )
        self.pet_window.chat_window.set_tool_schemas(
            self.tool_registry.get_tools_schema()
        )
        self.active_character_id = active_package.package_id
        self.active_character_name = active_package.name
        try:
            self.character_state_service = CharacterStateService(
                user_data_dir() / "character-state.json"
            )
            self.character_state_service.transition(
                self.active_character_id,
                "startup",
            )
        except (OSError, ValueError) as exc:
            LOGGER.warning(
                "角色状态初始化失败（%s），本次运行不持久化状态",
                type(exc).__name__,
            )
            self.character_state_service = None
        snapshot = self.chat_session.activate_character(
            active_package.package_id,
            active_package.name,
        )
        self._apply_chat_snapshot(snapshot)
        self.ai_worker = None
        self._ai_session_id = None
        self._ai_permission_token = None
        self._ai_used_memories = []
        self._ai_started_at = 0.0
        self._ai_input_text = ""
        self._voice_worker = None
        self._speech_controller = SpeechInterruptionController(
            on_cancel_tts=self._interrupt_tts
        )
        self._speech_controller.subscribe(self._on_speech_state)
        self._last_user_text = ""
        self._history_window = None
        self.live_monitor = None
        self._room_states = {}
        self.live_providers = dict(ADAPTERS)

        self._setup_tray()
        self.pet_window.chat_window.send_message_signal.connect(
            self._on_user_send_message
        )
        self.pet_window.chat_window.retry_requested.connect(
            self._retry_last_message
        )
        self.pet_window.chat_window.settings_requested.connect(
            self._open_settings
        )
        self.pet_window.chat_window.pet_lab_requested.connect(
            self._open_pet_lab
        )
        self.pet_window.chat_window.history_requested.connect(
            self._open_history
        )
        self.pet_window.chat_window.new_session_requested.connect(
            self._start_new_chat_session
        )
        self.voice_recorder = PushToTalkRecorder(self.app)
        self.voice_recorder.status_changed.connect(
            self._on_voice_status
        )
        self.voice_recorder.recording_finished.connect(
            self._transcribe_voice
        )
        self.voice_recorder.failed.connect(self._on_voice_error)
        self.pet_window.chat_window.push_to_talk_pressed.connect(
            self._start_voice_capture
        )
        self.pet_window.chat_window.push_to_talk_released.connect(
            self._finish_voice_capture
        )
        self.pet_window.chat_window.stop_requested.connect(
            self._stop_ai_generation
        )
        self.hotkey_manager = GlobalHotkeyManager(self.app)
        self._setup_global_hotkeys()
        self._refresh_microphone_status()
        self._setup_live_monitor()
        self._voice_output_service = None
        self._tts_playback_worker = None
        self._tts_playback_workers = []
        self._retired_voice_output_services = []
        self._setup_voice_output()
        self._setup_proactive_companion()
        self._register_runtime_services()
        self.kernel = PixkinKernel(self.service_container)
        self.kernel.start()
        self.startup_coordinator.mark("service_start")
        self.pet_window.show()
        self.startup_coordinator.mark("ui_start")
        self.ready = True
        log_event(
            LOGGER,
            logging.INFO,
            component="application",
            operation="startup",
            message="Pixkin 启动完成",
            character_id=active_package.package_id,
            startup_ms=round(
                (time.perf_counter() - self._startup_started) * 1000,
                2,
            ),
        )
        self._schedule_automatic_update_check()
        self._schedule_smoke_exit()

    def _schedule_smoke_exit(self):
        """让隔离的发行物冒烟在启动成功后走正常退出流程。"""
        performance_seconds = os.environ.get(
            "PIXKIN_PERF_TEST_SECONDS"
        )
        if performance_seconds:
            try:
                delay_ms = round(
                    min(30.0, max(2.0, float(performance_seconds)))
                    * 1000
                )
            except ValueError:
                delay_ms = 3000
            QTimer.singleShot(delay_ms, self._quit_app)
            return
        if os.environ.get("PIXKIN_SMOKE_TEST") == "1":
            QTimer.singleShot(1200, self._quit_app)

    def _schedule_automatic_update_check(self):
        if (
            os.environ.get("PIXKIN_SMOKE_TEST") == "1"
            or os.environ.get("PIXKIN_PERF_TEST_SECONDS")
            or self.update_service is None
            or detect_install_mode() != "installed"
            or not bool(
                self.config_mgr.get(
                    "app", "automatic_updates", True
                )
            )
        ):
            return
        channel = str(
            self.config_mgr.get("app", "update_channel", "stable")
        )
        urls = self.update_manifest_urls
        if (
            channel not in {"stable", "beta"}
            or not isinstance(urls, dict)
            or not str(urls.get(channel, "") or "")
        ):
            return
        if not UpdateService.automatic_check_due(
            str(
                self.config_mgr.get(
                    "app", "last_update_check", ""
                )
                or ""
            )
        ):
            return
        QTimer.singleShot(
            5000,
            lambda: self._start_automatic_update_check(
                str(urls[channel]),
                channel,
            ),
        )

    def _start_automatic_update_check(
        self,
        manifest_url: str,
        channel: str,
    ):
        if self._quitting or self.update_service is None:
            return
        permissions = getattr(self, "context_permission_service", None)
        if permissions is None:
            LOGGER.info("自动更新检查跳过：权限服务不可用")
            return
        permission_token = ContextPermissionService.new_handoff_token()
        if not permissions.decide(
            PermissionResource.NETWORK,
            PermissionOperation.READ,
            handoff_token=permission_token,
        ).allowed:
            permissions.clear_pending(
                PermissionResource.NETWORK,
                PermissionOperation.READ,
                handoff_token=permission_token,
            )
            LOGGER.info("自动更新检查跳过：网络权限未允许")
            return
        worker = UpdateCheckWorker(
            self.update_service,
            manifest_url,
            channel,
            permission_service=permissions,
            permission_token=permission_token,
        )
        worker.completed.connect(self._automatic_update_available)
        worker.completed.connect(self._mark_automatic_update_checked)
        worker.failed.connect(
            lambda message: LOGGER.info(
                "自动更新检查未完成：%s", message
            )
        )
        worker.finished.connect(
            lambda token=permission_token: self._finish_automatic_update_check(
                token
            )
        )
        worker.finished.connect(self._maybe_finish_quit)
        self._update_check_worker = worker
        worker.start()

    def _automatic_update_available(self, release):
        self.tray.showMessage(
            "Pixkin 有新版本",
            f"{release.version} 已通过签名清单验证。"
            "请在设置 → 更新中查看并确认下载。",
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    def _mark_automatic_update_checked(self, _release) -> None:
        """Record the daily gate only after fetch and signature verification."""
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.config_mgr.set("app", "last_update_check", checked_at)

    def _finish_automatic_update_check(self, permission_token: str) -> None:
        permissions = getattr(self, "context_permission_service", None)
        if permissions is not None:
            permissions.clear_pending(
                PermissionResource.NETWORK,
                PermissionOperation.READ,
                handoff_token=permission_token,
            )
        self._clear_update_check_worker()

    def _clear_update_check_worker(self):
        self._update_check_worker = None

    def _install_verified_update(self, installer: str):
        if self.update_service is None:
            return
        answer = QMessageBox.question(
            None,
            "安装更新",
            "Pixkin 将先备份本地配置、聊天、角色和伙伴工坊任务，"
            "然后退出并启动已验证的安装器。\n\n现在安装吗？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = (
            user_data_dir()
            / "updates"
            / "backups"
            / f"pre-update-{VERSION}-{stamp}.pixkin-backup.zip"
        )
        try:
            self.local_data_backup_service.export(backup)
            self.update_service.launch_installer(
                installer,
                user_confirmed=True,
            )
        except Exception as exc:
            QMessageBox.critical(
                None,
                "更新未启动",
                f"{exc}\n\n现有程序和本地数据未被修改。",
            )
            return
        log_event(
            LOGGER,
            logging.INFO,
            component="update",
            operation="installer_launch",
            message="已备份本地数据并启动验证安装器",
            backup_name=backup.name,
        )
        self._quit_app()

    def _install_verified_rollback(self, installer: str):
        if self.update_service is None:
            return
        answer = QMessageBox.question(
            None,
            "回滚 Pixkin",
            "Pixkin 将先备份当前全部本地数据，然后退出并启动上一版本安装器。"
            "\n\n确认回滚吗？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = (
            user_data_dir()
            / "updates"
            / "backups"
            / f"pre-rollback-{VERSION}-{stamp}.pixkin-backup.zip"
        )
        try:
            self.local_data_backup_service.export(backup)
            self.update_service.launch_rollback(
                installer,
                user_confirmed=True,
            )
        except Exception as exc:
            QMessageBox.critical(
                None,
                "回滚未启动",
                f"{exc}\n\n现有程序和本地数据未被修改。",
            )
            return
        log_event(
            LOGGER,
            logging.INFO,
            component="update",
            operation="rollback_launch",
            message="已备份本地数据并启动回滚安装器",
            backup_name=backup.name,
        )
        self._quit_app()

    def _characters(self) -> CharacterService:
        """Return the shared service, including for lightweight test controllers."""
        service = getattr(self, "character_service", None)
        if service is None:
            service = CharacterService(self.package_manager)
            self.character_service = service
        return service

    def _migrate_api_key(self):
        migrations = (
            (
                "api",
                SecretStore.get_api_key,
                SecretStore.set_api_key,
                "对话",
            ),
            (
                "image_generation",
                SecretStore.get_image_api_key,
                SecretStore.set_image_api_key,
                "图像",
            ),
        )
        for section, read_secret, write_secret, label in migrations:
            plain_key = self.config_mgr.get(section, "api_key", "")
            if not plain_key:
                continue
            previous_key = read_secret()
            if not write_secret(plain_key):
                LOGGER.warning(
                    "配置文件中的%s API Key 无法迁移到 Windows 凭据管理器",
                    label,
                )
                continue
            if self.config_mgr.update_section(section, {"api_key": ""}):
                continue
            rollback_ok = write_secret(previous_key)
            LOGGER.warning(
                "%s API Key 迁移未提交：配置清理失败，凭据回滚%s",
                label,
                "成功" if rollback_ok else "失败",
            )
        if not self.config_mgr.get("api", "api_key", ""):
            if not SecretStore.migrate_legacy_api_key():
                LOGGER.warning("旧版 API Key 无法迁移到新的凭据目标")

    def _upgrade_legacy_character(self):
        characters = self._characters()
        active_id = self.config_mgr.get("character", "active_pack", "")
        if active_id not in LEGACY_DEFAULT_PACKAGE_IDS:
            return
        default_package = next(
            (
                package
                for package in characters.list_packages()
                if package.package_id == DEFAULT_PACKAGE_ID
            ),
            None,
        )
        if default_package is None:
            return
        try:
            characters.activate(DEFAULT_PACKAGE_ID)
            LOGGER.info("旧版默认角色已升级为 %s", default_package.package_id)
        except Exception as exc:
            LOGGER.warning("升级默认角色失败: %s", exc)

    def _ensure_builtin_characters(self):
        characters = self._characters()
        installed = {
            package.package_id: package
            for package in characters.list_packages()
        }
        active_id = self.config_mgr.get("character", "active_pack", "")
        for archive_name in BUILTIN_CHARACTER_ARCHIVES:
            builtin = resource_path(f"character-packs/{archive_name}")
            if not builtin.is_file():
                LOGGER.warning("缺少内置角色包：%s", builtin)
                continue
            if not self._is_trusted_official_archive(
                archive_name,
                builtin,
            ):
                LOGGER.error(
                    "内置角色包哈希不匹配，已拒绝安装：%s",
                    archive_name,
                )
                continue
            try:
                plan = characters.prepare_install(
                    builtin,
                    allow_builtin_replace=True,
                )
                current = installed.get(plan.package.package_id)
                if current and characters.package_matches_archive(
                    plan.package.package_id, builtin
                ):
                    continue
                target_exists = (characters.root / plan.package.package_id).exists()
                result = characters.install(
                    plan,
                    replace_confirmed=target_exists,
                    activate=active_id == plan.package.package_id,
                )
                installed[result.package.package_id] = result.package
                action = "更新" if target_exists else "安装"
                LOGGER.info("已%s内置角色 %s", action, result.package.package_id)
            except Exception as exc:
                LOGGER.warning("安装内置角色失败 %s: %s", archive_name, exc)

    def _update_installed_official_characters(self):
        """只升级用户已安装的官方可选角色，不在新用户目录中强制安装。"""
        characters = self._characters()
        installed = {
            package.package_id: package
            for package in characters.list_packages()
        }
        active_id = self.config_mgr.get("character", "active_pack", "")
        for archive_name in OPTIONAL_OFFICIAL_CHARACTER_ARCHIVES:
            archive = resource_path(f"character-packs/{archive_name}")
            if not archive.is_file():
                LOGGER.warning("缺少官方可选角色包：%s", archive)
                continue
            if not self._is_trusted_official_archive(
                archive_name,
                archive,
            ):
                LOGGER.error(
                    "官方角色包哈希不匹配，已拒绝更新：%s",
                    archive_name,
                )
                continue
            try:
                plan = characters.prepare_install(archive)
                current = installed.get(plan.package.package_id)
                if current is None:
                    continue
                if current.author not in OFFICIAL_CHARACTER_AUTHORS:
                    LOGGER.info(
                        "保留同 ID 的非官方角色 %s，未自动覆盖",
                        current.package_id,
                    )
                    continue
                if characters.package_matches_archive(
                    plan.package.package_id, archive
                ):
                    continue
                result = characters.install(
                    plan,
                    replace_confirmed=True,
                    activate=active_id == plan.package.package_id,
                )
                updated = result.package
                installed[updated.package_id] = updated
                LOGGER.info("已升级官方可选角色 %s", updated.package_id)
            except Exception as exc:
                LOGGER.warning(
                    "升级官方可选角色失败 %s: %s", archive_name, exc
                )

    def _is_trusted_official_archive(
        self,
        archive_name: str,
        archive,
    ) -> bool:
        trust = getattr(self, "_official_character_trust", None)
        if trust is None:
            try:
                trust = OfficialCharacterTrustStore(
                    resource_path(
                        "character-packs/official-sha256.json"
                    )
                )
            except OfficialCharacterTrustError as exc:
                LOGGER.error("%s", exc)
                return False
            self._official_character_trust = trust
        try:
            return trust.verify(archive_name, archive)
        except OSError as exc:
            LOGGER.error(
                "官方角色包无法校验（%s）",
                type(exc).__name__,
            )
            return False

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(create_tray_icon(), self.app)
        self.tray.setToolTip("Pixkin · 监听准备中")
        self.tray_menu = QMenu()
        self._apply_tray_theme()
        tray_menu = self.tray_menu
        open_chat = QAction("打开对话", self.app)
        open_chat.triggered.connect(self.pet_window._toggle_chat_window)
        settings = QAction("设置中心", self.app)
        settings.triggered.connect(self._open_settings)
        pet_lab = QAction("伙伴工坊 · 孵化新伙伴", self.app)
        pet_lab.triggered.connect(self._open_pet_lab)
        toggle_pet = QAction("显示 / 隐藏角色", self.app)
        toggle_pet.triggered.connect(self._toggle_pet_visibility)
        quit_action = QAction("退出程序", self.app)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(open_chat)
        tray_menu.addAction(pet_lab)
        tray_menu.addAction(settings)
        tray_menu.addAction(toggle_pet)
        self.live_status_menu = tray_menu.addMenu("监听状态")
        waiting = self.live_status_menu.addAction("等待首次检测")
        waiting.setEnabled(False)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _apply_tray_theme(self):
        if not hasattr(self, "tray_menu"):
            return
        if resolved_theme(self.config_mgr) == "dark":
            style = """
                QMenu {
                    background: #171F30; color: #DDE3EE;
                    border: 1px solid #344057; border-radius: 9px;
                    padding: 6px; font-size: 12px;
                }
                QMenu::item { padding: 7px 24px 7px 11px; border-radius: 6px; }
                QMenu::item:selected { background: #263149; color: #7BE0D0; }
                QMenu::separator { height: 1px; background: #344057; margin: 5px 8px; }
            """
        else:
            style = """
            QMenu {
                background: #FAF9FD; color: #302A43; border: 1px solid #DDD9E8;
                border-radius: 9px; padding: 6px; font-size: 12px;
            }
            QMenu::item { padding: 7px 24px 7px 11px; border-radius: 6px; }
            QMenu::item:selected { background: #EDE9FB; color: #5547C7; }
            QMenu::separator { height: 1px; background: #E7E3EF; margin: 5px 8px; }
            """
        self.tray_menu.setStyleSheet(style)

    def _on_system_theme_changed(self, _scheme):
        if configured_theme(self.config_mgr) != "system":
            return
        if hasattr(self, "pet_window"):
            self.pet_window.chat_window.apply_theme()
        self._apply_tray_theme()

    def _setup_voice_output(self):
        self._retire_voice_output_service()
        cfg = self.config_mgr.get("voice_output", {})
        if not isinstance(cfg, dict):
            cfg = {}
        enabled = bool(cfg.get("enabled", False))
        provider_name = str(cfg.get("provider", "windows_sapi"))
        voice = str(cfg.get("voice", ""))
        speed = float(cfg.get("speed", 1.0))
        base_url = str(cfg.get("base_url", "https://api.openai.com/v1"))
        model = str(cfg.get("model", "tts-1"))

        if not hasattr(self, "provider_catalog"):
            self.provider_catalog = build_builtin_provider_catalog()
        tts_registry = self.provider_catalog.get("tts")
        if tts_registry is None:
            raise RuntimeError("TTS provider registry is unavailable")
        if provider_name == "windows_sapi":
            provider = tts_registry.create("windows_sapi")
        else:
            permissions = getattr(self, "context_permission_service", None)
            if permissions is None:
                LOGGER.error("网络权限服务未初始化，禁用云端 TTS")
                enabled = False
            else:
                network_decision = permissions.decide(
                    PermissionResource.NETWORK,
                    PermissionOperation.WRITE,
                )
                if not network_decision.allowed:
                    LOGGER.info("云端 TTS 未启用：网络权限未允许")
                    enabled = False
            api_key = self._voice_api_key()
            provider = tts_registry.create(
                "openai_compatible",
                base_url=base_url,
                api_key=api_key,
                model=model,
            )

        self._voice_output_service = VoiceOutputService(
            provider,
            voice=voice if voice else None,
            speed=speed,
            enabled=enabled,
            permission_service=getattr(
                self, "context_permission_service", None
            ),
        )

    def _retire_voice_output_service(self) -> None:
        """Retire a service only after its worker has stopped using it."""
        service = getattr(self, "_voice_output_service", None)
        if service is None:
            return
        self._voice_output_service = None
        workers = [
            worker
            for worker in getattr(self, "_tts_playback_workers", [])
            if worker.isRunning() and worker.service is service
        ]
        if workers:
            for worker in workers:
                stop_playback = getattr(worker, "stop_playback", None)
                if callable(stop_playback):
                    stop_playback(cancel_request=True)
            self._retired_voice_output_services = list(
                getattr(self, "_retired_voice_output_services", [])
            )
            self._retired_voice_output_services.append(service)
            return
        worker = getattr(self, "_tts_playback_worker", None)
        if worker is not None and worker.isRunning():
            stop_playback = getattr(worker, "stop_playback", None)
            if callable(stop_playback):
                stop_playback(cancel_request=True)
            self._retired_voice_output_services = list(
                getattr(self, "_retired_voice_output_services", [])
            )
            self._retired_voice_output_services.append(service)
            return
        try:
            service.close()
        except Exception:
            pass

    def _close_retired_voice_output_services(self) -> None:
        workers = getattr(self, "_tts_playback_workers", [])
        active_services = {
            worker.service for worker in workers if worker.isRunning()
        }
        services = list(getattr(self, "_retired_voice_output_services", []))
        self._retired_voice_output_services = [
            service for service in services if service in active_services
        ]
        for service in services:
            if service in active_services:
                continue
            try:
                service.close()
            except Exception:
                pass

    def _setup_proactive_companion(self):
        if not hasattr(self, "event_bus"):
            self.event_bus = EventBus()
        if not hasattr(self, "context_permission_service"):
            self.context_permission_service = ContextPermissionService()
        proactive_cfg = self.config_mgr.get("proactive", {})
        if not isinstance(proactive_cfg, dict):
            proactive_cfg = {}
        policy = ProactivePolicyConfig(
            enabled=bool(proactive_cfg.get("enabled", False)),
            quiet_fullscreen=bool(proactive_cfg.get("quiet_fullscreen", True)),
            work_stretch_reminder=bool(
                proactive_cfg.get("work_stretch_reminder", True)
            ),
            work_stretch_interval_minutes=int(
                proactive_cfg.get("work_stretch_interval_minutes", 90)
            ),
            sleep_guard=bool(proactive_cfg.get("sleep_guard", False)),
            sleep_guard_hour=int(proactive_cfg.get("sleep_guard_hour", 23)),
            sleep_guard_minute=int(
                proactive_cfg.get("sleep_guard_minute", 30)
            ),
            min_prompt_interval_seconds=int(
                proactive_cfg.get("min_prompt_interval_seconds", 3600)
            ),
        )
        self.context_sensor = ContextSensorService(
            permission_service=getattr(
                self,
                "context_permission_service",
                None,
            )
        )
        self.perception_projector = PerceptionContextProjector()
        self.proactive_service = ProactiveCompanionService(policy)
        if not hasattr(self, "_proactive_event_subscription"):
            self._proactive_event_subscription = self.event_bus.subscribe(
                "companion.proactive",
                self._on_proactive_event,
            )
        if not hasattr(self, "_proactive_timer"):
            app = getattr(self, "app", None)
            self._proactive_timer = QTimer(app) if app is not None else QTimer()
            self._proactive_timer.timeout.connect(self._check_proactive_nudge)
        if policy.enabled:
            self._proactive_timer.start(30000)
        else:
            self._proactive_timer.stop()

    def _register_runtime_services(self):
        """Register constructed services at the composition root.

        Existing windows keep their compatibility attributes during the
        migration. New controllers receive this container explicitly instead
        of constructing providers or storage on demand.
        """
        services = {
            "config": self.config_mgr,
            "character": self.character_service,
            "chat_store": self.chat_store,
            "chat_session": self.chat_session,
            "memory": self.memory_service,
            "character_state": self.character_state_service,
            "tools": self.tool_registry,
            "permissions": self.context_permission_service,
            "events": self.event_bus,
            "actions": self.action_dispatcher,
            "speech": getattr(self, "_speech_controller", None),
            "plugins": self.plugin_host,
            "providers": self.provider_catalog,
            "updates": self.update_service,
        }
        for name, service in services.items():
            if service is not None:
                self.service_container.register(name, service, replace=True)

    def _on_proactive_event(self, event: EventEnvelope):
        """Render a policy-approved proactive event in the Qt thread."""
        if event.type != "companion.proactive":
            return
        pet_win = getattr(self, "pet_window", None)
        if pet_win is None:
            return
        payload = event.payload
        try:
            target_state = PetState(str(payload.get("target_state")))
        except ValueError:
            return
        pet_win.animator.request_state(
            target_state,
            duration_ms=3000,
            restore=True,
            complete_current=True,
        )
        state_service = getattr(self, "character_state_service", None)
        if state_service is not None:
            state_service.transition(
                getattr(self, "active_character_id", "default"),
                str(payload.get("kind") or "idle_tick"),
            )
        if getattr(self, "tray", None) is not None:
            self.tray.showMessage(
                "Pixkin 贴心关怀",
                str(payload.get("message") or ""),
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _load_context_permissions(self):
        """Load persisted context grants without ever granting by default."""
        if not hasattr(self, "context_permission_service"):
            self.context_permission_service = ContextPermissionService()
        values = self.config_mgr.get(
            "privacy",
            "context_permissions",
            {},
        )
        if not isinstance(values, dict):
            return
        for resource in PermissionResource:
            raw = values.get(resource.value)
            try:
                state = PermissionState(raw)
            except (TypeError, ValueError):
                continue
            if state in {
                PermissionState.ALLOW_ALWAYS,
                PermissionState.DENY,
                PermissionState.ASK,
            }:
                self.context_permission_service.set_state(resource, state)

    def _ask_context_permission(self, resource, operation) -> bool:
        """Ask once for an observation/action that is configured as ASK."""
        labels = {
            PermissionResource.WINDOW_METADATA: "前台窗口与进程名",
            PermissionResource.SYSTEM_STATE: "系统空闲时间",
            PermissionResource.CLIPBOARD: "剪贴板文本",
            PermissionResource.SCREEN: "屏幕截图",
            PermissionResource.PLUGIN: "第三方插件进程",
            PermissionResource.MCP: "MCP 外部工具",
            PermissionResource.MICROPHONE: "麦克风录音",
            PermissionResource.CAMERA: "摄像头",
            PermissionResource.FILESYSTEM: "文件访问",
            PermissionResource.NETWORK: "网络访问",
            PermissionResource.EXTERNAL_ACTION: "外部动作",
        }
        label = labels.get(resource, resource.value)
        answer = QMessageBox.question(
            getattr(self, "pet_window", None),
            "Pixkin 请求权限",
            f"允许 Pixkin 本次{operation.value}读取或使用“{label}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _check_proactive_nudge(self):
        if not getattr(self, "ready", False) or getattr(self, "_quitting", False):
            return
        if getattr(self, "ai_worker", None) and self.ai_worker.isRunning():
            return
        pet_win = getattr(self, "pet_window", None)
        if pet_win is None or getattr(pet_win, "_is_dragging", False):
            return

        sensor = getattr(self, "context_sensor", None)
        proactive = getattr(self, "proactive_service", None)
        if sensor is None or proactive is None:
            return

        # Background nudges must never open permission dialogs or capture
        # one-shot sensitive context.
        snapshot = sensor.poll(resolve_ask=False)
        self._last_context_snapshot = snapshot
        event = proactive.evaluate(snapshot)
        if event is not None:
            self.event_bus.publish(
                EventEnvelope(
                    type="companion.proactive",
                    source="proactive_companion",
                    payload={
                        "kind": event.kind,
                        "target_state": event.target_state.value,
                        "message": event.message,
                    },
                    priority=20,
                    sensitivity="private",
                )
            )

    def _setup_live_monitor(self):
        if self._quitting:
            return
        permissions = getattr(self, "context_permission_service", None)
        if permissions is None:
            self.tray.setToolTip("Pixkin · 开播监听未启用（权限服务不可用）")
            return
        if permissions.state(PermissionResource.NETWORK) is PermissionState.ASK:
            # The monitor is long-lived; a one-shot ASK approval must not be
            # stretched into an unbounded background network grant.  Users
            # can explicitly choose session/always access in Privacy Center.
            self.tray.setToolTip(
                "Pixkin · 开播监听未启用（需要会话级网络授权）"
            )
            return
        network_decision = permissions.decide(
            PermissionResource.NETWORK,
            PermissionOperation.READ,
        )
        if not network_decision.allowed:
            self.tray.setToolTip("Pixkin · 开播监听未启用（网络权限未允许）")
            return
        monitor_config = self.config_mgr.get("live_monitor", {})
        rooms = monitor_config.get("rooms", [])
        providers = dict(ADAPTERS)
        enabled_plugins = monitor_config.get(
            "enabled_adapter_plugins", []
        )
        if isinstance(enabled_plugins, list) and enabled_plugins:
            try:
                providers = LiveProviderRegistry(
                    ADAPTERS.values()
                ).load_enabled(enabled_plugins)
            except LiveProviderPluginError as exc:
                LOGGER.warning("直播适配器未加载：%s", exc)
                self.tray.showMessage(
                    "直播适配器未加载",
                    str(exc),
                    QSystemTrayIcon.MessageIcon.Warning,
                    5000,
                )
        self.live_providers = providers
        if not monitor_config.get("enabled", True) or not rooms:
            self.tray.setToolTip("Pixkin · 开播监听未启用")
            return
        router = LiveProviderRouter(providers.values())
        monitor = LiveMonitorThread(
            rooms=rooms,
            interval_seconds=monitor_config.get("interval_seconds", 60),
            notify_if_live_on_start=monitor_config.get(
                "notify_if_live_on_start", False
            ),
            quiet_start=monitor_config.get(
                "quiet_start", "22:00"
            ),
            quiet_end=monitor_config.get(
                "quiet_end", "08:00"
            ),
            repeat_reminder_minutes=monitor_config.get(
                "repeat_reminder_minutes", 0
            ),
            checker=router.check,
            checker_close=router.close,
        )
        self.live_monitor = monitor
        monitor.anchor_live_started.connect(
            self._on_anchor_live_started
        )
        monitor.room_status_changed.connect(
            self._on_room_status_changed
        )
        monitor.room_health_changed.connect(
            self._on_room_health_changed
        )
        monitor.cycle_completed.connect(
            self._on_monitor_cycle_completed
        )
        monitor.finished.connect(
            lambda finished_monitor=monitor: self._on_live_monitor_finished(
                finished_monitor
            )
        )
        monitor.start()
        enabled_count = len([r for r in rooms if r.get("enabled", True)])
        self.tray.setToolTip(
            f"Pixkin · 正在并发监听 {enabled_count} 个直播间"
        )

    def _restart_live_monitor(self):
        self._monitor_restart_pending = True
        monitor = self.live_monitor
        if monitor and monitor.isRunning():
            monitor.stop()
            self.tray.setToolTip("Pixkin · 正在重新加载开播监听")
            return
        if monitor:
            monitor.deleteLater()
        self.live_monitor = None
        self._monitor_restart_pending = False
        self._room_states.clear()
        self._setup_live_monitor()

    def _on_live_monitor_finished(self, monitor):
        if self.live_monitor is not monitor:
            return
        self.live_monitor = None
        monitor.deleteLater()
        if self._quitting:
            self._maybe_finish_quit()
            return
        if self._monitor_restart_pending:
            self._monitor_restart_pending = False
            self._room_states.clear()
            self._setup_live_monitor()

    def _on_room_status_changed(
        self, room_key: str, is_live: bool, title: str, error: str
    ):
        self._room_states[room_key] = {
            "is_live": is_live, "title": title, "error": error
        }

    def _on_room_health_changed(self, state):
        last_success = getattr(
            state,
            "last_success_wall_time",
            None,
        )
        self._room_states[state.room_key] = {
            "platform": state.platform,
            "is_live": state.is_live,
            "title": state.title,
            "error": state.error,
            "consecutive_failures": state.consecutive_failures,
            "retry_after_seconds": state.retry_after_seconds,
            "last_success_wall_time": last_success,
        }
        self._refresh_live_status_menu()

    def _refresh_live_status_menu(self):
        menu = getattr(self, "live_status_menu", None)
        if menu is None:
            return
        menu.clear()
        if not self._room_states:
            action = menu.addAction("等待首次检测")
            action.setEnabled(False)
            return
        for room_key, state in sorted(self._room_states.items()):
            trusted = state.get("last_success_wall_time")
            if state.get("error"):
                status = "检测失败"
            elif state.get("is_live") is True:
                status = "直播中"
            elif state.get("is_live") is False:
                status = "未开播"
            else:
                status = "尚无可信状态"
            last_success = (
                datetime.fromtimestamp(trusted)
                .astimezone()
                .strftime("%H:%M:%S")
                if trusted
                else "无"
            )
            failures = int(
                state.get("consecutive_failures", 0) or 0
            )
            retry = round(
                float(state.get("retry_after_seconds", 0) or 0)
            )
            detail = (
                f"{room_key} · {status} · 成功 {last_success}"
                f" · 连续失败 {failures}"
            )
            if failures:
                detail += f" · 约 {retry}s 后重试"
            action = menu.addAction(detail)
            action.setEnabled(False)

    def _on_monitor_cycle_completed(self, live_count: int, total: int):
        error_count = sum(
            1 for state in self._room_states.values() if state["error"]
        )
        backoff = max(
            (
                float(state.get("retry_after_seconds", 0) or 0)
                for state in self._room_states.values()
                if state.get("error")
            ),
            default=0,
        )
        suffix = (
            f" · {error_count} 个异常 · 最长 {round(backoff)}s 后重试"
            if error_count
            else ""
        )
        self.tray.setToolTip(
            f"Pixkin · 并发监听 {total} 个 · {live_count} 个直播中{suffix}"
        )

    def _on_anchor_live_started(
        self, platform: str, anchor_name: str, title: str
    ):
        self.event_bus.publish(
            EventEnvelope(
                type="stream.started",
                source=f"{platform}_trigger",
                payload={
                    "platform": platform,
                    "anchor_name": anchor_name,
                    "title": title,
                },
                priority=40,
                sensitivity="normal",
            )
        )

    def _on_stream_started_event(self, event: EventEnvelope):
        payload = event.payload
        self._render_anchor_live_started(
            str(payload.get("platform") or ""),
            str(payload.get("anchor_name") or ""),
            str(payload.get("title") or ""),
        )

    def _render_anchor_live_started(
        self, platform: str, anchor_name: str, title: str
    ):
        self.pet_window.play_contextual_alert(
            PetState.CELEBRATE_LIVE,
            duration_ms=3200,
        )
        platform_name = "B站" if platform == "bilibili" else "抖音"
        message = self.pet_window.chat_window.show_live_alert(
            platform_name, anchor_name, title
        )
        self._store_message(
            message["role"],
            message["content"],
            message.get("metadata"),
            message.get("created_at"),
        )
        self.pet_window._update_chat_position()
        self.tray.showMessage(
            f"{anchor_name} 开播啦",
            f"{platform_name} · {title}",
            QSystemTrayIcon.MessageIcon.Information,
            6000,
        )

    def _network_permission_allowed(self, *, handoff_token: str | None = None) -> bool:
        permissions = getattr(self, "context_permission_service", None)
        if permissions is None:
            LOGGER.error("网络权限服务未初始化，拒绝发送模型请求")
            return False
        network_decision = permissions.decide(
            PermissionResource.NETWORK,
            PermissionOperation.WRITE,
            handoff_token=handoff_token,
        )
        if network_decision.allowed:
            return True
        self.pet_window.chat_window.append_message(
            "system",
            "网络权限未允许，本条消息没有发送到模型接口。"
            "请在“隐私与审计”中授权网络访问后重试。",
        )
        return False

    def _clear_pending_network_permission(self, token: str | None = None) -> None:
        """Discard an ASK approval when its operation will not start/finish."""
        permissions = getattr(self, "context_permission_service", None)
        if permissions is not None:
            permissions.clear_pending(
                PermissionResource.NETWORK,
                PermissionOperation.WRITE,
                handoff_token=token,
            )

    def _on_user_send_message(self, user_text: str):
        if self.ai_worker and self.ai_worker.isRunning():
            return
        clean_user_text = str(user_text or "")
        permission_token = ContextPermissionService.new_handoff_token()
        if not self._network_permission_allowed(handoff_token=permission_token):
            return
        # Resolve context permissions and take the snapshot before the model
        # consent dialog.  The consent scope must describe the data that this
        # very request can actually include.
        sensor = getattr(self, "context_sensor", None)
        if sensor is not None:
            try:
                self._last_context_snapshot = sensor.poll()
            except Exception:
                LOGGER.debug("上下文采样失败", exc_info=True)
        if not self._ensure_model_privacy_notice(clean_user_text):
            self._clear_pending_network_permission(permission_token)
            self._last_context_snapshot = None
            return
        state_service = getattr(self, "character_state_service", None)
        if state_service is not None:
            state_service.transition(
                getattr(self, "active_character_id", "default"),
                "user_interaction",
            )
        self._store_message("user", clean_user_text)
        self._last_user_text = clean_user_text
        self.pet_window.chat_window.set_retry_available(False)
        self._start_ai_response(
            network_authorized=True,
            permission_token=permission_token,
        )

    def _start_ai_response(
        self,
        *,
        network_authorized: bool = False,
        permission_token: str | None = None,
    ):
        # The GUI send/retry path resolves ASK once before entering this
        # method. Direct callers retain the defensive permission check.
        if not network_authorized:
            permission_token = ContextPermissionService.new_handoff_token()
            if not self._network_permission_allowed(
                handoff_token=permission_token
            ):
                return
        self._ai_permission_token = permission_token
        self.pet_window.animator.set_state(PetState.LISTENING)
        self.pet_window.chat_window.set_busy(True)
        self.pet_window.chat_window.start_assistant_message()
        memory_service = getattr(self, "memory_service", None)
        self._ai_used_memories = (
            memory_service.used_for(
                user_id="local-profile",
                character_id=getattr(
                    self, "active_character_id", "default"
                ),
            )
            if memory_service is not None
            else []
        )
        prompt = str(
            self.config_mgr.get("pet", "system_prompt", "") or ""
        )
        prompt += MemoryService.prompt_fragment(
            self._ai_used_memories
        )
        prompt += getattr(self, "perception_projector", PerceptionContextProjector()).prompt_fragment(
            getattr(self, "_last_context_snapshot", None),
            query=getattr(self, "_last_user_text", ""),
        )
        prompt += preset_prompt(
            str(
                self.config_mgr.get(
                    "api", "preset", "balanced"
                )
            )
        )
        context = self.chat_session.context()
        self._ai_started_at = time.perf_counter()
        self._ai_input_text = "\n".join(
            str(message.get("content") or "")
            for message in context
        )
        chat_provider = None
        chat_api_key = self._chat_api_key()
        chat_registry = getattr(self, "provider_catalog", {}).get("chat")
        if chat_api_key and chat_registry is not None:
            chat_provider = chat_registry.create(
                "openai_compatible",
                api_key=chat_api_key,
                base_url=self.config_mgr.get("api", "base_url"),
            )
        self.ai_worker = AiWorkerThread(
            base_url=self.config_mgr.get("api", "base_url"),
            api_key=chat_api_key,
            model=self.config_mgr.get("api", "model"),
            system_prompt=prompt,
            messages=context,
            tool_registry=self.tool_registry,
            provider=chat_provider,
            execution_context=ToolExecutionContext(
                session_id=self.chat_session.session_id,
                character_id=getattr(self, "active_character_id", ""),
            ),
            permission_service=getattr(
                self, "context_permission_service", None
            ),
            permission_token=permission_token,
        )
        self._ai_session_id = self.chat_session.session_id
        self.ai_worker.chunk_received.connect(self._on_ai_chunk)
        self.ai_worker.tool_executing.connect(self._on_tool_executing)
        self.ai_worker.finished_response.connect(self._on_ai_finished)
        self.ai_worker.error_occurred.connect(self._on_ai_error)
        self.ai_worker.emotion_detected.connect(self._on_ai_emotion)
        worker = self.ai_worker
        worker.finished.connect(
            lambda finished_worker=worker: self._cleanup_ai_worker(
                finished_worker
            )
        )
        try:
            self.ai_worker.start()
        except Exception as exc:
            self._clear_pending_network_permission(
                getattr(self, "_ai_permission_token", None)
            )
            self.ai_worker = None
            self._on_ai_error(
                f"大模型后台任务启动失败（{type(exc).__name__}）。请重试。"
            )
            return
        QTimer.singleShot(
            450,
            lambda expected_worker=worker: self._show_thinking(
                expected_worker
            ),
        )

    def _retry_last_message(self):
        if self.ai_worker and self.ai_worker.isRunning():
            return
        if not getattr(self, "_last_user_text", ""):
            return
        self.pet_window.chat_window.set_retry_available(False)
        # ``_start_ai_response`` performs the single GUI-thread permission
        # check for retries; keeping it here avoids a duplicate ASK prompt.
        self._start_ai_response()

    def _stop_ai_generation(self):
        self._clear_pending_network_permission(
            getattr(self, "_ai_permission_token", None)
        )
        worker = getattr(self, "ai_worker", None)
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        self.pet_window.chat_window.cancel_stream()
        self.pet_window.chat_window.set_busy(False)
        self.pet_window.chat_window.set_retry_available(
            bool(getattr(self, "_last_user_text", ""))
        )
        self.pet_window.animator.request_state(
            PetState.IDLE,
            complete_current=True,
        )

    def _voice_api_key(self) -> str:
        session_secrets = getattr(self, "session_secrets", None)
        if session_secrets is not None:
            value = session_secrets.get_voice_api_key()
            if value:
                return value
        return SecretStore.get_voice_api_key()

    def _refresh_microphone_status(self):
        chat = self.pet_window.chat_window
        enabled = bool(
            self.config_mgr.get("voice_input", "enabled", False)
        )
        muted = bool(
            self.config_mgr.get("voice_input", "muted", False)
        )
        if not enabled:
            chat.set_microphone_status(
                "麦克风关闭 · 可在设置中启用按住说话",
                enabled=False,
            )
        elif muted:
            chat.set_microphone_status(
                "麦克风已静音 · 快捷键可解除",
                enabled=False,
            )
        else:
            chat.set_microphone_status(
                "麦克风待命 · 按住圆点才会录音",
                enabled=True,
            )

    def _ensure_voice_privacy_notice(self) -> bool:
        endpoint = str(
            self.config_mgr.get("voice_input", "base_url", "")
            or "未配置"
        )
        model = str(
            self.config_mgr.get("voice_input", "model", "")
            or ""
        )
        fingerprint = privacy_scope_fingerprint(
            endpoint=endpoint,
            model=model,
            data_scope="microphone_audio_for_transcription",
        )
        acknowledged = bool(
            self.config_mgr.get(
                "voice_input",
                "privacy_notice_acknowledged",
                False,
            )
        )
        stored_fingerprint = str(
            self.config_mgr.get(
                "voice_input",
                "privacy_notice_fingerprint",
                "",
            )
            or ""
        )
        if acknowledged and stored_fingerprint == fingerprint:
            return True
        answer = QMessageBox.question(
            self.pet_window.chat_window,
            "语音转写隐私确认",
            "只有按住说话期间才访问麦克风。松开后，本次录音会发送到"
            "独立语音端点：\n\n"
            f"{endpoint}\n\n"
            "Pixkin 不保存录音文件；服务商可能按其条款处理或保留请求。"
            "转写文本会先放入输入框，由你确认后再发送聊天。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        if not self.config_mgr.update_section(
            "voice_input",
            {
                "privacy_notice_acknowledged": True,
                "privacy_notice_fingerprint": fingerprint,
            },
        ):
            LOGGER.warning("语音隐私说明确认状态无法写入配置")
        return True

    def _start_voice_capture(self):
        if (
            not bool(
                self.config_mgr.get(
                    "voice_input", "enabled", False
                )
            )
            or bool(
                self.config_mgr.get(
                    "voice_input", "muted", False
                )
            )
        ):
            self._refresh_microphone_status()
            return
        worker = getattr(self, "_voice_worker", None)
        if worker is not None and worker.isRunning():
            return
        if not self._ensure_voice_privacy_notice():
            self._refresh_microphone_status()
            return
        permissions = getattr(self, "context_permission_service", None)
        if permissions is None:
            self.pet_window.chat_window.set_microphone_status(
                "麦克风权限服务不可用 · 请重启 Pixkin",
                enabled=True,
            )
            return
        decision = permissions.decide(
            PermissionResource.MICROPHONE,
        )
        if not decision.allowed:
            self.pet_window.chat_window.set_microphone_status(
                "麦克风权限未允许 · 请在隐私与审计中授权",
                enabled=True,
            )
            return
        speech = getattr(self, "_speech_controller", None)
        if speech is not None:
            speech.begin_listening()
        self.voice_recorder.start()

    def _finish_voice_capture(self):
        recorder = getattr(self, "voice_recorder", None)
        if recorder is not None:
            recorder.stop()

    def _on_voice_status(self, message: str):
        self.pet_window.chat_window.set_microphone_status(message)

    def _on_voice_error(self, message: str):
        self.pet_window.chat_window.set_microphone_status(
            f"语音不可用 · {message}",
            enabled=True,
        )

    def _transcribe_voice(self, wav_bytes: bytes):
        speech = getattr(self, "_speech_controller", None)
        if speech is not None:
            speech.begin_thinking()
        permissions = getattr(self, "context_permission_service", None)
        if permissions is None:
            if speech is not None:
                speech.finish()
            self._on_voice_error("网络权限服务不可用 · 请重启 Pixkin")
            return
        permission_token = ContextPermissionService.new_handoff_token()
        network_decision = permissions.decide(
            PermissionResource.NETWORK,
            PermissionOperation.WRITE,
            handoff_token=permission_token,
        )
        if not network_decision.allowed:
            if speech is not None:
                speech.finish()
            self._on_voice_error("网络权限未允许 · 请在隐私与审计中授权")
            return
        endpoint = VoiceEndpoint(
            str(
                self.config_mgr.get(
                    "voice_input", "base_url", ""
                )
            ),
            str(
                self.config_mgr.get(
                    "voice_input", "model", "whisper-1"
                )
            ),
        )
        worker = VoiceTranscriptionWorker(
            VoiceTranscriptionService(
                endpoint,
                permission_service=permissions,
                permission_token=permission_token,
            ),
            wav_bytes,
            self._voice_api_key(),
            parent=self.app,
        )
        self._voice_worker = worker
        worker.completed.connect(self._on_voice_text)
        worker.failed.connect(self._on_voice_error)
        worker.finished.connect(
            lambda expected=worker: self._clear_voice_worker(expected)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_voice_text(self, text: str):
        speech = getattr(self, "_speech_controller", None)
        if speech is not None:
            speech.finish()
        self.pet_window.chat_window.insert_voice_text(text)
        self.pet_window.chat_window.set_microphone_status(
            "转写完成 · 请确认输入框内容后发送"
        )

    def _clear_voice_worker(self, expected):
        if getattr(self, "_voice_worker", None) is expected:
            self._voice_worker = None

    def _interrupt_tts(self):
        workers = list(getattr(self, "_tts_playback_workers", []))
        if not workers:
            worker = getattr(self, "_tts_playback_worker", None)
            if worker is not None:
                workers = [worker]
        for worker in workers:
            if worker.isRunning():
                worker.stop_playback()

    def _on_speech_state(self, state: SpeechState):
        """Keep renderer state aligned with the provider-neutral speech FSM."""
        pet_window = getattr(self, "pet_window", None)
        if pet_window is None:
            return
        mapping = {
            SpeechState.IDLE: PetState.IDLE,
            SpeechState.LISTENING: PetState.LISTENING,
            SpeechState.THINKING: PetState.THINKING,
            SpeechState.TALKING: PetState.TALKING,
            SpeechState.INTERRUPTED: PetState.LISTENING,
        }
        pet_window.animator.request_state(
            mapping[state],
            complete_current=True,
        )
        if getattr(self, "_quitting", False):
            self._maybe_finish_quit()

    def _toggle_voice_mute(self):
        muted = not bool(
            self.config_mgr.get("voice_input", "muted", False)
        )
        self.config_mgr.set("voice_input", "muted", muted)
        recorder = getattr(self, "voice_recorder", None)
        if muted and recorder is not None:
            recorder.cancel()
        self._refresh_microphone_status()

    def _open_chat_hotkey(self):
        self.pet_window.show()
        self.pet_window.raise_()
        self.pet_window.chat_window.show()
        self.pet_window.chat_window.raise_()
        self.pet_window.chat_window.activateWindow()

    def _setup_global_hotkeys(self):
        manager = getattr(self, "hotkey_manager", None)
        if manager is None:
            return
        if not bool(
            self.config_mgr.get(
                "voice_input", "hotkeys_enabled", True
            )
        ):
            manager.close()
            return
        configured = self.config_mgr.get(
            "voice_input", "hotkeys", {}
        )
        if not isinstance(configured, dict):
            configured = {}
        defaults = {
            "toggle_pet": "Ctrl+Alt+P",
            "open_chat": "Ctrl+Alt+C",
            "stop_generation": "Ctrl+Alt+S",
            "mute_voice": "Ctrl+Alt+M",
        }
        callbacks = {
            "toggle_pet": self._toggle_pet_visibility,
            "open_chat": self._open_chat_hotkey,
            "stop_generation": self._stop_ai_generation,
            "mute_voice": self._toggle_voice_mute,
        }
        conflicts = manager.register({
            name: (
                str(configured.get(name, defaults[name])),
                callbacks[name],
            )
            for name in defaults
        })
        if conflicts and getattr(self, "tray", None) is not None:
            self.tray.showMessage(
                "部分全局快捷键未注册",
                "已被其他程序占用：" + "、".join(conflicts),
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

    def _model_privacy_data_scope(self) -> str:
        permissions = getattr(self, "context_permission_service", None)
        states = {}
        if permissions is not None:
            for resource in (
                PermissionResource.WINDOW_METADATA,
                PermissionResource.SYSTEM_STATE,
                PermissionResource.CLIPBOARD,
            ):
                try:
                    states[resource.value] = permissions.state(resource).value
                except (AttributeError, TypeError, ValueError):
                    continue
        return model_data_scope(states)

    def _model_privacy_context_labels(self) -> list[str]:
        permissions = getattr(self, "context_permission_service", None)
        if permissions is None:
            return []
        labels = (
            (PermissionResource.WINDOW_METADATA, "前台窗口与进程名"),
            (PermissionResource.SYSTEM_STATE, "系统空闲时间"),
            (PermissionResource.CLIPBOARD, "剪贴板文本或选中文本"),
        )
        result = []
        for resource, label in labels:
            try:
                state = permissions.state(resource)
            except (AttributeError, TypeError, ValueError):
                continue
            if state in {
                PermissionState.ALLOW_SESSION,
                PermissionState.ALLOW_ALWAYS,
            }:
                result.append(label)
        return result

    def _ensure_model_privacy_notice(self, _user_text: str = "") -> bool:
        if not getattr(self, "ready", False):
            return True
        endpoint = str(
            self.config_mgr.get("api", "base_url", "") or "未配置"
        )
        model = str(
            self.config_mgr.get("api", "model", "") or ""
        )
        fingerprint = privacy_scope_fingerprint(
            endpoint=endpoint,
            model=model,
            data_scope=self._model_privacy_data_scope(),
        )
        acknowledged = bool(
            self.config_mgr.get(
                "privacy",
                "model_notice_acknowledged",
                False,
            )
        )
        stored_fingerprint = str(
            self.config_mgr.get(
                "privacy",
                "model_notice_fingerprint",
                "",
            )
            or ""
        )
        if acknowledged and stored_fingerprint == fingerprint:
            return True
        context_labels = self._model_privacy_context_labels()
        context_notice = (
            "当前已授权的桌面上下文类别："
            + "、".join(context_labels)
            + "。\n\n"
            if context_labels
            else "当前未授权桌面上下文。\n\n"
        )
        answer = QMessageBox.question(
            self.pet_window.chat_window,
            "发送给模型前，请确认隐私范围",
            "Pixkin 会把当前角色系统提示词、你的消息，以及受上下文"
            "上限约束的近期对话发送到以下模型接口：\n\n"
            f"{endpoint}\n\n"
            + context_notice
            + "已启用的长期记忆也可能随相关请求发送。\n\n"
            "聊天历史默认保存在本机；你可以在“设置 → 隐私与审计”"
            "中改为不保存、7 天、30 天或永久。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        if not self.config_mgr.update_section(
            "privacy",
            {
                "model_notice_acknowledged": True,
                "model_notice_fingerprint": fingerprint,
            },
        ):
            LOGGER.warning("模型隐私说明确认状态无法写入配置")
        return True

    def _chat_api_key(self) -> str:
        session_secrets = getattr(self, "session_secrets", None)
        if session_secrets is not None:
            session_key = session_secrets.get_chat_api_key()
            if session_key:
                return session_key
        return SecretStore.get_api_key()

    def _show_thinking(self, expected_worker):
        if self.ai_worker is expected_worker and expected_worker.isRunning():
            self.pet_window.animator.request_state(
                PetState.THINKING, complete_current=True
            )

    def _on_ai_emotion(self, emotion):
        if not isinstance(emotion, PetState):
            return
        self.pet_window.animator.request_state(
            emotion,
            duration_ms=1600,
            restore=True,
            complete_current=True,
        )

    def _on_ai_chunk(self, chunk: str):
        speech = getattr(self, "_speech_controller", None)
        if speech is not None and speech.state is SpeechState.THINKING:
            speech.begin_talking()
        self.pet_window.animator.request_state(
            PetState.TALKING, complete_current=True
        )
        self.pet_window.chat_window.append_chunk(chunk)

    def _on_tool_executing(self, message: str):
        self.pet_window.animator.request_state(
            PetState.WORKING, complete_current=True
        )
        self.pet_window.chat_window.append_message("system", message)
        self._store_message(
            "system",
            message,
            session_id=getattr(self, "_ai_session_id", None),
        )

    def _on_ai_finished(self, final_text: str):
        used_memories = [
            {"id": item.id, "content": item.content}
            for item in getattr(self, "_ai_used_memories", [])
        ]
        config_manager = getattr(self, "config_mgr", None)
        started_at = float(getattr(self, "_ai_started_at", 0.0))
        estimate = estimate_chat(
            latency_seconds=max(
                0.0,
                time.perf_counter() - started_at
                if started_at
                else 0.0,
            ),
            input_text=getattr(self, "_ai_input_text", ""),
            output_text=final_text,
            input_cost_per_million=float(
                config_manager.get(
                    "api", "input_cost_per_million", 0.0
                ) if config_manager is not None else 0.0
            ),
            output_cost_per_million=float(
                config_manager.get(
                    "api", "output_cost_per_million", 0.0
                ) if config_manager is not None else 0.0
            ),
        )
        metadata = {
            "chat_metrics": estimate.metadata(),
        }
        if used_memories:
            metadata["used_memories"] = used_memories
        self._store_message(
            "assistant",
            final_text,
            metadata=metadata,
            session_id=getattr(self, "_ai_session_id", None),
        )
        self.pet_window.chat_window.complete_stream(metadata=metadata)
        self.pet_window.chat_window.set_busy(False)
        self.pet_window.chat_window.set_retry_available(False)
        self.pet_window.animator.request_state(
            PetState.SUCCESS,
            duration_ms=1400,
            restore=False,
            complete_current=True,
        )
        voice_service = getattr(self, "_voice_output_service", None)
        tts_authorized = True
        if (
            voice_service is not None
            and voice_service.enabled
            and final_text
            and not getattr(self, "_quitting", False)
        ):
            if not voice_service.provider.capabilities.offline:
                permissions = getattr(self, "context_permission_service", None)
                if permissions is None:
                    tts_authorized = False
                elif permissions.state(PermissionResource.NETWORK) is PermissionState.ASK:
                    tts_token = ContextPermissionService.new_handoff_token()
                    tts_decision = permissions.decide(
                        PermissionResource.NETWORK,
                        PermissionOperation.WRITE,
                        handoff_token=tts_token,
                    )
                    tts_authorized = tts_decision.allowed
                    if tts_authorized:
                        voice_service.set_permission_token(tts_token)
            if not tts_authorized:
                voice_service = None
        if (
            voice_service is not None
            and voice_service.enabled
            and final_text
            and not getattr(self, "_quitting", False)
        ):
            speech = getattr(self, "_speech_controller", None)
            if speech is not None:
                speech.begin_talking()
            worker = TtsPlaybackWorker(voice_service, final_text)
            self._tts_playback_worker = worker
            self._tts_playback_workers.append(worker)
            worker.finished.connect(worker.deleteLater)
            worker.finished.connect(
                lambda expected=worker: self._finish_tts_worker(expected)
            )
            worker.start()
        else:
            speech = getattr(self, "_speech_controller", None)
            if speech is not None:
                speech.finish()

    def _finish_tts_worker(self, expected_worker) -> None:
        """Finish TTS without clobbering a newer listening/interruption state."""
        workers = [
            worker
            for worker in getattr(self, "_tts_playback_workers", [])
            if worker is not expected_worker
        ]
        self._tts_playback_workers = workers
        is_current = getattr(self, "_tts_playback_worker", None) is expected_worker
        if is_current:
            self._tts_playback_worker = next(
                (
                    worker
                    for worker in reversed(workers)
                    if worker.isRunning()
                ),
                None,
            )
        speech = getattr(self, "_speech_controller", None)
        if (
            is_current
            and speech is not None
            and speech.state is SpeechState.TALKING
        ):
            speech.finish()
        self._close_retired_voice_output_services()
        self._maybe_finish_quit()

    def _on_ai_error(self, error_message: str):
        LOGGER.warning("AI 请求失败: %s", error_message)
        self._clear_pending_network_permission(
            getattr(self, "_ai_permission_token", None)
        )
        self.pet_window.chat_window.cancel_stream()
        self.pet_window.chat_window.append_message("error", error_message)
        self._store_message(
            "error",
            error_message,
            session_id=getattr(self, "_ai_session_id", None),
        )
        self.pet_window.chat_window.set_busy(False)
        self.pet_window.chat_window.set_retry_available(
            bool(getattr(self, "_last_user_text", ""))
        )
        self.pet_window.animator.request_state(
            PetState.FAILED,
            duration_ms=1900,
            restore=False,
            complete_current=True,
        )
        speech = getattr(self, "_speech_controller", None)
        if speech is not None:
            speech.finish()

    def _cleanup_ai_worker(self, worker):
        if self.ai_worker is worker:
            self.ai_worker = None
            self._ai_session_id = None
            self._ai_permission_token = None
            self._ai_used_memories = []
            self._ai_started_at = 0.0
            self._ai_input_text = ""
        worker.deleteLater()
        if getattr(self, "_quitting", False):
            self._maybe_finish_quit()

    def _store_message(
        self,
        role,
        content,
        metadata=None,
        created_at=None,
        session_id=None,
    ):
        service = getattr(self, "chat_session", None)
        if service is None:
            return None
        return service.add_message(
            role,
            content,
            metadata,
            created_at,
            session_id=session_id,
        )

    def _apply_chat_snapshot(self, snapshot: ChatSessionSnapshot):
        self.pet_window.chat_window.load_messages(snapshot.transcript)

    def _reset_chat_context(self):
        self._start_new_chat_session()

    def _cancel_ai_for_context_switch(self):
        self._last_user_text = ""
        self._clear_pending_network_permission(
            getattr(self, "_ai_permission_token", None)
        )
        permission_bridge = getattr(
            self, "tool_permission_bridge", None
        )
        if permission_bridge is not None:
            permission_bridge.cancel_pending()
        worker = getattr(self, "ai_worker", None)
        if worker is None or not worker.isRunning():
            return
        tts_workers = list(getattr(self, "_tts_playback_workers", []))
        if not tts_workers:
            tts_worker = getattr(self, "_tts_playback_worker", None)
            if tts_worker is not None:
                tts_workers = [tts_worker]
        for tts_worker in tts_workers:
            if tts_worker.isRunning():
                tts_worker.stop_playback(cancel_request=True)
        for signal, callback in (
            (worker.chunk_received, self._on_ai_chunk),
            (worker.tool_executing, self._on_tool_executing),
            (worker.finished_response, self._on_ai_finished),
            (worker.error_occurred, self._on_ai_error),
            (worker.emotion_detected, self._on_ai_emotion),
        ):
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError):
                pass
        worker.cancel()
        self.ai_worker = None
        self._ai_session_id = None
        self._ai_used_memories = []
        self._ai_started_at = 0.0
        self._ai_input_text = ""
        self.pet_window.chat_window.cancel_stream()
        self.pet_window.chat_window.set_busy(False)

    def _activate_chat_for_package(self, package):
        if package is None:
            return
        previous_id = getattr(self, "active_character_id", None)
        if previous_id and previous_id != package.package_id:
            self._cancel_ai_for_context_switch()
        self.active_character_id = package.package_id
        self.active_character_name = package.name
        service = getattr(self, "chat_session", None)
        if service is None:
            self.pet_window.chat_window.load_messages([])
            return
        snapshot = service.activate_character(
            package.package_id,
            package.name,
        )
        self._apply_chat_snapshot(snapshot)
        self._sync_history_window()

    def _reload_current_chat(self):
        service = getattr(self, "chat_session", None)
        if service is None:
            self.pet_window.chat_window.load_messages([])
            return
        previous_session_id = service.session_id
        snapshot = service.activate_character(
            self.active_character_id,
            self.active_character_name,
        )
        if (
            previous_session_id
            and previous_session_id != snapshot.session_id
        ):
            self._cancel_ai_for_context_switch()
        self._apply_chat_snapshot(snapshot)
        self._sync_history_window()

    def _start_new_chat_session(self):
        service = getattr(self, "chat_session", None)
        if service is None:
            self.pet_window.chat_window.load_messages([])
            return
        self._cancel_ai_for_context_switch()
        self._apply_chat_snapshot(service.start_new_session())
        self._sync_history_window()

    def _sync_history_window(self):
        window = getattr(self, "_history_window", None)
        if window is None:
            return
        try:
            window.set_context(
                self.active_character_id,
                self.active_character_name,
                self.chat_session.session_id,
            )
        except RuntimeError:
            self._history_window = None

    def _open_history(self):
        current = getattr(self, "_history_window", None)
        if current is not None:
            try:
                current.set_context(
                    self.active_character_id,
                    self.active_character_name,
                    self.chat_session.session_id,
                )
                current.show()
                current.raise_()
                current.activateWindow()
                return current
            except RuntimeError:
                self._history_window = None
        dialog = HistoryWindow(
            self.chat_store,
            self.active_character_id,
            self.active_character_name,
            self.chat_session.session_id,
            self.config_mgr,
        )
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.history_changed.connect(self._reload_current_chat)
        dialog.new_session_requested.connect(
            self._start_new_chat_session
        )
        dialog.destroyed.connect(
            lambda _object=None, expected=dialog:
            self._clear_history_window(expected)
        )
        self._history_window = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _clear_history_window(self, expected):
        if getattr(self, "_history_window", None) is expected:
            self._history_window = None

    def _open_settings(self):
        current = getattr(self, "_settings_window", None)
        if current is not None:
            try:
                if current.isMinimized():
                    current.showNormal()
                else:
                    current.show()
                current.raise_()
                current.activateWindow()
                return current
            except RuntimeError:
                self._settings_window = None

        previous_prompt = self.config_mgr.get("pet", "system_prompt", "")
        dialog = SettingsWindow(
            self.config_mgr,
            self.package_manager,
            character_service=self._characters(),
            chat_store=getattr(self, "chat_store", None),
            tool_audit_store=getattr(
                self, "tool_audit_store", None
            ),
            session_secret_store=getattr(
                self, "session_secrets", None
            ),
            diagnostic_bundle_service=getattr(
                self, "diagnostic_bundle_service", None
            ),
            local_data_backup_service=getattr(
                self, "local_data_backup_service", None
            ),
            update_service=getattr(self, "update_service", None),
            update_manifest_urls=getattr(
                self,
                "update_manifest_urls",
                self.config_mgr.get(
                    "app", "update_manifest_urls", {}
                ),
            ),
            memory_service=getattr(self, "memory_service", None),
            active_character_id=getattr(
                self, "active_character_id", "default"
            ),
            context_permission_service=getattr(
                self, "context_permission_service", None
            ),
            provider_catalog=getattr(self, "provider_catalog", {}),
            live_providers=getattr(
                self, "live_providers", ADAPTERS
            ),
        )
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._settings_window = dialog
        dialog.history_changed.connect(self._reload_current_chat)
        dialog.install_update_requested.connect(
            self._install_verified_update
        )
        dialog.rollback_update_requested.connect(
            self._install_verified_rollback
        )
        dialog.preview_character_action.connect(
            self._preview_character_action
        )
        dialog.finished.connect(
            lambda result, expected=dialog, prompt=previous_prompt:
            self._on_settings_finished(expected, result, prompt)
        )
        dialog.destroyed.connect(
            lambda _object=None, expected=dialog:
            self._clear_settings_window(expected)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _preview_character_action(self, state_name: str):
        try:
            state = PetState(str(state_name))
        except ValueError:
            return
        self.pet_window.animator.request_state(
            state,
            duration_ms=1800,
            restore=True,
            complete_current=True,
        )

    def _clear_settings_window(self, expected):
        if getattr(self, "_settings_window", None) is expected:
            self._settings_window = None

    def _on_settings_finished(self, dialog, result, previous_prompt):
        self._clear_settings_window(dialog)
        if dialog.character_changed:
            package = self._characters().get_active()
            if package:
                self.pet_window.set_character(package)
                self._activate_chat_for_package(package)
        if int(result) != int(QDialog.DialogCode.Accepted):
            return
        chat_session = getattr(self, "chat_session", None)
        if chat_session is not None:
            chat_session.set_retention(
                int(
                    self.config_mgr.get(
                        "privacy",
                        "history_retention_days",
                        -1,
                    )
                )
            )
        package = self._characters().get_active()
        self.pet_window.set_character(package)
        if not dialog.character_changed:
            self._activate_chat_for_package(package)
            prompt_changed = (
                self.config_mgr.get("pet", "system_prompt", "")
                != previous_prompt
            )
            if prompt_changed:
                self._start_new_chat_session()
        self.pet_window.chat_window.set_user_profile(
            self.config_mgr.get("user", "display_name", "我"),
            self.config_mgr.get("user", "avatar_path", ""),
        )
        self.pet_window.apply_config()
        self._apply_tray_theme()
        self._setup_global_hotkeys()
        self._refresh_microphone_status()
        self._load_context_permissions()
        for resource in dialog.privacy_panel.context_session_permissions():
            self.context_permission_service.set_state(
                resource,
                PermissionState.ALLOW_SESSION,
                session_only=True,
            )
        self._restart_live_monitor()
        self._setup_voice_output()
        self._setup_proactive_companion()
        try:
            set_start_with_windows(
                bool(self.config_mgr.get("app", "start_with_windows", False))
            )
        except Exception as exc:
            LOGGER.warning("设置开机自启失败: %s", exc)
            self.tray.showMessage(
                "开机自启设置失败", str(exc),
                QSystemTrayIcon.MessageIcon.Warning, 4000
            )
        self.tray.showMessage(
            "设置已应用", "角色、监听和桌面行为已经更新。",
            QSystemTrayIcon.MessageIcon.Information, 2200
        )

    def _open_pet_lab(self):
        dialog = PetLabWindow(
            self.config_mgr,
            self.package_manager,
            character_service=self._characters(),
            context_permission_service=getattr(
                self, "context_permission_service", None
            ),
            session_secret_store=getattr(
                self, "session_secrets", None
            ),
            provider_catalog=getattr(self, "provider_catalog", {}),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        package = dialog.generated_package or self._characters().get_active()
        if package:
            self.pet_window.set_character(package)
            self._activate_chat_for_package(package)
            self.pet_window.play_contextual_alert(
                PetState.ALERTING_IMPORTANT,
                duration_ms=2600,
            )
            self.pet_window.show()
            self.pet_window.raise_()
            self.tray.showMessage(
                "新伙伴孵化成功",
                f"{package.name} 已经成为你的桌面伙伴。",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )

    def _toggle_pet_visibility(self):
        if self.pet_window.isVisible():
            self.pet_window.chat_window.hide()
            self.pet_window.hide()
        else:
            self.pet_window.show()
            self.pet_window.raise_()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.pet_window._toggle_chat_window()

    def _quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self._monitor_restart_pending = False
        log_event(
            LOGGER,
            logging.INFO,
            component="application",
            operation="shutdown",
            message="正在退出",
        )
        session_secrets = getattr(self, "session_secrets", None)
        if session_secrets is not None:
            session_secrets.clear()
        permission_bridge = getattr(
            self, "tool_permission_bridge", None
        )
        if permission_bridge is not None:
            permission_bridge.close()
        hotkey_manager = getattr(self, "hotkey_manager", None)
        if hotkey_manager is not None:
            hotkey_manager.close()
        voice_recorder = getattr(self, "voice_recorder", None)
        if voice_recorder is not None:
            voice_recorder.cancel()
        self._clear_pending_network_permission()
        self.pet_window.save_position()
        if self.live_monitor and self.live_monitor.isRunning():
            self.live_monitor.stop()
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
        update_worker = getattr(self, "_update_check_worker", None)
        if update_worker is not None and update_worker.isRunning():
            update_worker.requestInterruption()
        voice_worker = getattr(self, "_voice_worker", None)
        if voice_worker is not None and voice_worker.isRunning():
            voice_worker.requestInterruption()
        proactive_timer = getattr(self, "_proactive_timer", None)
        if proactive_timer is not None:
            proactive_timer.stop()
        tts_workers = list(getattr(self, "_tts_playback_workers", []))
        if not tts_workers:
            tts_worker = getattr(self, "_tts_playback_worker", None)
            if tts_worker is not None:
                tts_workers = [tts_worker]
        for tts_worker in tts_workers:
            if not tts_worker.isRunning():
                continue
            stop_playback = getattr(tts_worker, "stop_playback", None)
            if callable(stop_playback):
                stop_playback(cancel_request=True)
            else:
                tts_worker.requestInterruption()
        self._retire_voice_output_service()
        kernel = getattr(self, "kernel", None)
        if kernel is not None:
            kernel.stop()
        self.tray.hide()
        self._maybe_finish_quit()

    def _maybe_finish_quit(self):
        if not self._quitting or self._quit_finalized:
            return
        if self.live_monitor and self.live_monitor.isRunning():
            return
        if self.ai_worker and self.ai_worker.isRunning():
            return
        update_worker = getattr(self, "_update_check_worker", None)
        if update_worker is not None and update_worker.isRunning():
            return
        voice_worker = getattr(self, "_voice_worker", None)
        if voice_worker is not None and voice_worker.isRunning():
            return
        tts_workers = list(getattr(self, "_tts_playback_workers", []))
        if not tts_workers:
            tts_worker = getattr(self, "_tts_playback_worker", None)
            if tts_worker is not None:
                tts_workers = [tts_worker]
        if any(worker.isRunning() for worker in tts_workers):
            return
        self._close_retired_voice_output_services()
        voice_service = getattr(self, "_voice_output_service", None)
        if voice_service is not None:
            try:
                voice_service.close()
            except Exception:
                pass
            self._voice_output_service = None
        self._quit_finalized = True
        memory_service = getattr(self, "memory_service", None)
        if memory_service is not None:
            close = getattr(memory_service, "close", None)
            if callable(close):
                close()
        self._instance_lock.unlock()
        self.app.quit()

    def run(self) -> int:
        if not self.ready:
            return 0
        return self.app.exec()


def install_exception_hook():
    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        report_path = None
        try:
            report_path = CrashReportStore().save_exception(
                exc_type,
                exc_value,
                details,
            )
        except OSError as store_error:
            LOGGER.error(
                "崩溃摘要写入失败（%s）",
                type(store_error).__name__,
            )
        log_event(
            LOGGER,
            logging.CRITICAL,
            component="application",
            operation="unhandled_exception",
            error_category="unexpected",
            message="未捕获异常",
            exception_type=getattr(
                exc_type,
                "__name__",
                str(exc_type),
            ),
        )
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None,
                "Pixkin 遇到问题",
                "程序发生异常，已生成本地脱敏崩溃摘要。"
                "只有你在“设置 → 隐私与审计”主动导出时，"
                "诊断包才会离开本机。\n\n"
                + (
                    f"摘要：{report_path.name}\n"
                    if report_path is not None
                    else ""
                )
                + CrashReportStore._sanitize(str(exc_value)),
            )

    def handle_threading_exception(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        details = "".join(
            traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            )
        )
        try:
            CrashReportStore().save_exception(
                args.exc_type,
                args.exc_value,
                details,
            )
        except OSError as store_error:
            LOGGER.error(
                "后台线程崩溃摘要写入失败（%s）",
                type(store_error).__name__,
            )
        thread_name = getattr(args.thread, "name", "unknown_thread")
        log_event(
            LOGGER,
            logging.ERROR,
            component="threading",
            operation="unhandled_thread_exception",
            error_category="unexpected",
            message=f"后台线程未捕获异常: {thread_name}",
            exception_type=getattr(
                args.exc_type,
                "__name__",
                str(args.exc_type),
            ),
        )

    sys.excepthook = handle_exception
    import threading
    threading.excepthook = handle_threading_exception


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    configure_logging()
    install_exception_hook()
    app_instance = DesktopPetApp()
    sys.exit(app_instance.run())
