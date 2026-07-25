import logging
import sys
import traceback

from PyQt6.QtCore import Qt, QLockFile, QTimer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QPainter, QPixmap
)

from core.ai_engine import AiWorkerThread
from core.app_logging import configure_logging
from core.character_package import (
    DEFAULT_PACKAGE_ID, CharacterPackageManager
)
from core.config import ConfigManager
from core.live_monitor import LiveMonitorThread
from core.paths import resource_path, user_data_dir
from core.pet_animator import PetState
from core.secrets import SecretStore
from core.tool_registry import ToolRegistry
from core.version import VERSION
from core.windows_integration import set_start_with_windows
from ui.onboarding_window import FirstRunWindow
from ui.pet_lab_window import PetLabWindow
from ui.pet_window import PetWindow
from ui.settings_window import SettingsWindow
from ui.theme import configured_theme, resolved_theme


LOGGER = logging.getLogger("desktop_pet.main")
CHAT_HISTORY_MAX_MESSAGES = 40
CHAT_HISTORY_CHAR_BUDGET = 24000
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

        self.config_mgr = ConfigManager()
        try:
            self.app.styleHints().colorSchemeChanged.connect(
                self._on_system_theme_changed
            )
        except AttributeError:
            pass
        self._migrate_api_key()
        self.package_manager = CharacterPackageManager(self.config_mgr)
        self._ensure_builtin_characters()
        self._update_installed_official_characters()
        self._upgrade_legacy_character()
        active_package = self.package_manager.get_active()
        if active_package is None:
            onboarding = FirstRunWindow(self.package_manager)
            if onboarding.exec() != QDialog.DialogCode.Accepted:
                self._instance_lock.unlock()
                return
            active_package = self.package_manager.get_active()
        if active_package is None:
            QMessageBox.critical(
                None, "角色加载失败", "没有可用的角色包，程序无法继续启动。"
            )
            self._instance_lock.unlock()
            return

        self.tool_registry = ToolRegistry()
        self.pet_window = PetWindow(self.config_mgr, active_package)
        self.chat_history_list = []
        self.ai_worker = None
        self.live_monitor = None
        self._room_states = {}

        self._setup_tray()
        self.pet_window.chat_window.send_message_signal.connect(
            self._on_user_send_message
        )
        self.pet_window.chat_window.settings_requested.connect(
            self._open_settings
        )
        self.pet_window.chat_window.pet_lab_requested.connect(
            self._open_pet_lab
        )
        self._setup_live_monitor()
        self.pet_window.show()
        self.ready = True
        LOGGER.info(
            "Pixkin 启动完成，角色=%s", active_package.package_id
        )

    def _migrate_api_key(self):
        plain_key = self.config_mgr.get("api", "api_key", "")
        if not plain_key:
            if not SecretStore.migrate_legacy_api_key():
                LOGGER.warning("旧版 API Key 无法迁移到新的凭据目标")
            return
        if SecretStore.set_api_key(plain_key):
            if not self.config_mgr.update_section("api", {"api_key": ""}):
                LOGGER.warning("API Key 已迁移，但无法清除配置文件中的明文副本")
        else:
            LOGGER.warning("配置文件中的 API Key 无法迁移到 Windows 凭据管理器")

    def _upgrade_legacy_character(self):
        active_id = self.config_mgr.get("character", "active_pack", "")
        if active_id not in LEGACY_DEFAULT_PACKAGE_IDS:
            return
        default_package = next(
            (
                package
                for package in self.package_manager.list_packages()
                if package.package_id == DEFAULT_PACKAGE_ID
            ),
            None,
        )
        if default_package is None:
            return
        try:
            self.package_manager.activate(DEFAULT_PACKAGE_ID)
            LOGGER.info("旧版默认角色已升级为 %s", default_package.package_id)
        except Exception as exc:
            LOGGER.warning("升级默认角色失败: %s", exc)

    def _ensure_builtin_characters(self):
        installed = {
            package.package_id: package
            for package in self.package_manager.list_packages()
        }
        active_id = self.config_mgr.get("character", "active_pack", "")
        for archive_name in BUILTIN_CHARACTER_ARCHIVES:
            builtin = resource_path(f"character-packs/{archive_name}")
            if not builtin.is_file():
                LOGGER.warning("缺少内置角色包：%s", builtin)
                continue
            try:
                inspected = self.package_manager.inspect_zip(str(builtin))
                current = installed.get(inspected.package_id)
                if current and self.package_manager.package_matches_zip(
                    inspected.package_id, str(builtin)
                ):
                    continue
                target_exists = (
                    self.package_manager.root / inspected.package_id
                ).exists()
                self.package_manager.import_zip(
                    str(builtin),
                    replace=target_exists,
                    activate=active_id == inspected.package_id,
                    allow_builtin_replace=True,
                )
                installed[inspected.package_id] = inspected
                action = "更新" if target_exists else "安装"
                LOGGER.info("已%s内置角色 %s", action, inspected.package_id)
            except Exception as exc:
                LOGGER.warning("安装内置角色失败 %s: %s", archive_name, exc)

    def _update_installed_official_characters(self):
        """只升级用户已安装的官方可选角色，不在新用户目录中强制安装。"""
        installed = {
            package.package_id: package
            for package in self.package_manager.list_packages()
        }
        active_id = self.config_mgr.get("character", "active_pack", "")
        for archive_name in OPTIONAL_OFFICIAL_CHARACTER_ARCHIVES:
            archive = resource_path(f"character-packs/{archive_name}")
            if not archive.is_file():
                LOGGER.warning("缺少官方可选角色包：%s", archive)
                continue
            try:
                inspected = self.package_manager.inspect_zip(str(archive))
                current = installed.get(inspected.package_id)
                if current is None:
                    continue
                if current.author not in OFFICIAL_CHARACTER_AUTHORS:
                    LOGGER.info(
                        "保留同 ID 的非官方角色 %s，未自动覆盖",
                        current.package_id,
                    )
                    continue
                if self.package_manager.package_matches_zip(
                    inspected.package_id, str(archive)
                ):
                    continue
                updated = self.package_manager.import_zip(
                    str(archive),
                    replace=True,
                    activate=active_id == inspected.package_id,
                )
                installed[updated.package_id] = updated
                LOGGER.info("已升级官方可选角色 %s", updated.package_id)
            except Exception as exc:
                LOGGER.warning(
                    "升级官方可选角色失败 %s: %s", archive_name, exc
                )

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

    def _setup_live_monitor(self):
        if self._quitting:
            return
        monitor_config = self.config_mgr.get("live_monitor", {})
        rooms = monitor_config.get("rooms", [])
        if not monitor_config.get("enabled", True) or not rooms:
            self.tray.setToolTip("Pixkin · 开播监听未启用")
            return
        monitor = LiveMonitorThread(
            rooms=rooms,
            interval_seconds=monitor_config.get("interval_seconds", 60),
            notify_if_live_on_start=monitor_config.get(
                "notify_if_live_on_start", False
            ),
        )
        self.live_monitor = monitor
        monitor.anchor_live_started.connect(
            self._on_anchor_live_started
        )
        monitor.room_status_changed.connect(
            self._on_room_status_changed
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

    def _on_monitor_cycle_completed(self, live_count: int, total: int):
        error_count = sum(
            1 for state in self._room_states.values() if state["error"]
        )
        suffix = f" · {error_count} 个异常" if error_count else ""
        self.tray.setToolTip(
            f"Pixkin · 并发监听 {total} 个 · {live_count} 个直播中{suffix}"
        )

    def _on_anchor_live_started(
        self, platform: str, anchor_name: str, title: str
    ):
        self.pet_window.play_contextual_alert(
            PetState.CELEBRATE_LIVE,
            duration_ms=3200,
        )
        platform_name = "B站" if platform == "bilibili" else "抖音"
        message = (
            f"你关注的主播【{anchor_name}】在{platform_name}开播啦！\n{title}"
        )
        self.pet_window.chat_window.show_alert_bubble("开播啦！", message)
        self.pet_window._update_chat_position()
        self.tray.showMessage(
            f"{anchor_name} 开播啦",
            f"{platform_name} · {title}",
            QSystemTrayIcon.MessageIcon.Information,
            6000,
        )

    def _on_user_send_message(self, user_text: str):
        if self.ai_worker and self.ai_worker.isRunning():
            return
        self.chat_history_list.append({"role": "user", "content": user_text})
        self._trim_chat_history()
        self.pet_window.animator.set_state(PetState.LISTENING)
        self.pet_window.chat_window.set_busy(True)
        self.pet_window.chat_window.start_assistant_message()
        self.ai_worker = AiWorkerThread(
            base_url=self.config_mgr.get("api", "base_url"),
            api_key=SecretStore.get_api_key()
            or self.config_mgr.get("api", "api_key", ""),
            model=self.config_mgr.get("api", "model"),
            system_prompt=self.config_mgr.get("pet", "system_prompt"),
            messages=list(self.chat_history_list),
            tool_registry=self.tool_registry,
        )
        self.ai_worker.chunk_received.connect(self._on_ai_chunk)
        self.ai_worker.tool_executing.connect(self._on_tool_executing)
        self.ai_worker.finished_response.connect(self._on_ai_finished)
        self.ai_worker.error_occurred.connect(self._on_ai_error)
        worker = self.ai_worker
        worker.finished.connect(
            lambda finished_worker=worker: self._cleanup_ai_worker(
                finished_worker
            )
        )
        self.ai_worker.start()
        QTimer.singleShot(
            450,
            lambda expected_worker=worker: self._show_thinking(
                expected_worker
            ),
        )

    def _show_thinking(self, expected_worker):
        if self.ai_worker is expected_worker and expected_worker.isRunning():
            self.pet_window.animator.request_state(
                PetState.THINKING, complete_current=True
            )

    def _on_ai_chunk(self, chunk: str):
        self.pet_window.animator.request_state(
            PetState.TALKING, complete_current=True
        )
        self.pet_window.chat_window.append_chunk(chunk)

    def _on_tool_executing(self, message: str):
        self.pet_window.animator.request_state(
            PetState.WORKING, complete_current=True
        )
        self.pet_window.chat_window.append_message("system", message)

    def _on_ai_finished(self, final_text: str):
        self.chat_history_list.append(
            {"role": "assistant", "content": final_text}
        )
        self._trim_chat_history()
        self.pet_window.chat_window.complete_stream()
        self.pet_window.chat_window.set_busy(False)
        self.pet_window.animator.request_state(
            PetState.SUCCESS,
            duration_ms=1400,
            restore=False,
            complete_current=True,
        )

    def _on_ai_error(self, error_message: str):
        LOGGER.warning("AI 请求失败: %s", error_message)
        self.pet_window.chat_window.cancel_stream()
        self.pet_window.chat_window.append_message("error", error_message)
        self.pet_window.chat_window.set_busy(False)
        self.pet_window.animator.request_state(
            PetState.FAILED,
            duration_ms=1900,
            restore=False,
            complete_current=True,
        )

    def _cleanup_ai_worker(self, worker):
        if self.ai_worker is worker:
            self.ai_worker = None
        worker.deleteLater()
        if getattr(self, "_quitting", False):
            self._maybe_finish_quit()

    def _trim_chat_history(self):
        """保留最近的完整上下文，避免请求无限增长直至超过模型限制。"""
        if not self.chat_history_list:
            return
        latest = self.chat_history_list[-1]
        latest_content = str(latest.get("content") or "")
        if len(latest_content) > CHAT_HISTORY_CHAR_BUDGET:
            latest["content"] = latest_content[-CHAT_HISTORY_CHAR_BUDGET:]

        total_chars = sum(
            len(str(message.get("content") or ""))
            for message in self.chat_history_list
        )
        while len(self.chat_history_list) > 1 and (
            len(self.chat_history_list) > CHAT_HISTORY_MAX_MESSAGES
            or total_chars > CHAT_HISTORY_CHAR_BUDGET
        ):
            removed = self.chat_history_list.pop(0)
            total_chars -= len(str(removed.get("content") or ""))
        while (
            self.chat_history_list
            and self.chat_history_list[0].get("role") != "user"
        ):
            self.chat_history_list.pop(0)

    def _reset_chat_context(self):
        self.chat_history_list.clear()
        self.pet_window.chat_window.append_message(
            "system", "角色或人设已更新，对话上下文已重新开始。"
        )

    def _open_settings(self):
        previous_prompt = self.config_mgr.get("pet", "system_prompt", "")
        dialog = SettingsWindow(self.config_mgr, self.package_manager)
        result = dialog.exec()
        if dialog.character_changed:
            package = self.package_manager.get_active()
            if package:
                self.pet_window.set_character(package)
        prompt_changed = (
            self.config_mgr.get("pet", "system_prompt", "") != previous_prompt
        )
        if dialog.character_changed or prompt_changed:
            self._reset_chat_context()
        if result != QDialog.DialogCode.Accepted:
            return
        package = self.package_manager.get_active()
        self.pet_window.set_character(package)
        self.pet_window.apply_config()
        self._apply_tray_theme()
        self._restart_live_monitor()
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
        dialog = PetLabWindow(self.config_mgr, self.package_manager)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        package = dialog.generated_package or self.package_manager.get_active()
        if package:
            self._reset_chat_context()
            self.pet_window.set_character(package)
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
        LOGGER.info("正在退出")
        self.pet_window.save_position()
        if self.live_monitor and self.live_monitor.isRunning():
            self.live_monitor.stop()
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
        self.tray.hide()
        self._maybe_finish_quit()

    def _maybe_finish_quit(self):
        if not self._quitting or self._quit_finalized:
            return
        if self.live_monitor and self.live_monitor.isRunning():
            return
        if self.ai_worker and self.ai_worker.isRunning():
            return
        self._quit_finalized = True
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
        LOGGER.critical("未捕获异常:\n%s", details)
        QMessageBox.critical(
            None,
            "Pixkin 遇到问题",
            "程序发生异常，详细信息已写入日志目录。\n\n"
            f"{exc_value}",
        )

    sys.excepthook = handle_exception


if __name__ == "__main__":
    configure_logging()
    install_exception_hook()
    app_instance = DesktopPetApp()
    sys.exit(app_instance.run())
