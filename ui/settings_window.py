from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QInputDialog,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout, QWidget
)

from core.character_package import (
    BUILTIN_PACKAGE_IDS,
    CharacterPackageError,
    CharacterPackageManager,
)
from core.config import ConfigManager
from core.paths import resource_path, user_data_dir
from core.privacy import model_data_scope, privacy_scope_fingerprint
from core.secrets import SecretStore
from core.services.character_service import CharacterService
from core.services.user_profile_service import (
    UserProfileError,
    UserProfileService,
)
from core.services.session_secret_store import SessionSecretStore
from core.services.memory_service import MemoryService
from core.services.global_hotkey_service import parse_hotkey, HotkeyError
from core.services.character_ecosystem_service import (
    CharacterCatalogService,
    CharacterPackageInspector,
)
from core.services.character_trust_service import OfficialCharacterTrustStore
from core.services.live_service import LiveService
from core.services.extension_service import ExtensionManifestError
from core.runtime.permissions import ContextPermissionService
from core.version import VERSION
from ui.settings_character_components import CharacterManagerPanel
from ui.live_settings_components import LiveRoomEditor
from ui.model_settings_components import ModelSettingsPanel
from ui.pet_lab_window import PetLabWindow
from ui.privacy_settings_components import PrivacySettingsPanel
from ui.settings_profile_components import UserProfileEditor
from ui.theme import configured_theme, resolved_theme
from ui.update_settings_components import UpdateSettingsPanel
from ui.memory_settings_components import MemorySettingsPanel
from ui.voice_input_components import VoiceSettingsPanel
from ui.character_ecosystem_components import (
    CharacterBehaviorEditor,
    CharacterPackageInspectorDialog,
)
from ui.extension_settings_components import ExtensionSettingsPanel
from ui.proactive_settings_components import ProactiveSettingsPanel
from ui.voice_output_components import VoiceOutputSettingsPanel


class SettingsWindow(QDialog):
    """带侧栏的产品级设置中心。"""

    history_changed = pyqtSignal()
    install_update_requested = pyqtSignal(str)
    rollback_update_requested = pyqtSignal(str)
    preview_character_action = pyqtSignal(str)

    def __init__(
        self,
        config_manager: ConfigManager,
        package_manager: CharacterPackageManager = None,
        parent=None,
        character_service: CharacterService = None,
        user_profile_service: UserProfileService = None,
        chat_store=None,
        tool_audit_store=None,
        session_secret_store: SessionSecretStore = None,
        diagnostic_bundle_service=None,
        local_data_backup_service=None,
        update_service=None,
        update_manifest_urls=None,
        memory_service: MemoryService = None,
        active_character_id: str = "",
        live_providers=None,
        provider_catalog=None,
        context_permission_service: ContextPermissionService = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.package_manager = package_manager or CharacterPackageManager(config_manager)
        self.character_service = character_service or CharacterService(
            self.package_manager
        )
        self.user_profile_service = (
            user_profile_service or UserProfileService()
        )
        self.chat_store = chat_store
        self.tool_audit_store = tool_audit_store
        self.session_secret_store = (
            session_secret_store or SessionSecretStore()
        )
        self.diagnostic_bundle_service = diagnostic_bundle_service
        self.local_data_backup_service = local_data_backup_service
        self.update_service = update_service
        self.update_manifest_urls = update_manifest_urls or {}
        self.memory_service = memory_service
        self.active_character_id = str(active_character_id or "default")
        self.live_providers = live_providers
        self.provider_catalog = provider_catalog or {}
        self.context_permission_service = context_permission_service
        self.character_changed = False
        self._user_avatar_source_value = str(
            self.config_manager.get("user", "avatar_path", "") or ""
        )
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.setWindowTitle("Pixkin · 设置中心")
        self.setMinimumSize(920, 650)
        self.resize(1040, 760)
        self._theme_choice = configured_theme(self.config_manager)
        self.apply_theme(self._theme_choice)
        self._build_ui()

    @property
    def _user_avatar_source(self) -> str:
        if hasattr(self, "user_profile_editor"):
            return self.user_profile_editor.avatar_source
        return self._user_avatar_source_value

    @_user_avatar_source.setter
    def _user_avatar_source(self, value):
        self._user_avatar_source_value = str(value or "")
        if hasattr(self, "user_profile_editor"):
            self.user_profile_editor.set_avatar_source(value)

    @staticmethod
    def _style(theme="dark"):
        base = """
            QDialog { background: #0D1320; color: #F6F4FF; font-size: 13px; }
            QLabel { color: #E7ECF5; }
            QFrame#shell { background: #151E2E; border: 1px solid #28334A; border-radius: 22px; }
            QFrame#sidebar { background: #101624; border-radius: 18px; }
            QLabel#brand { color: #7BE0D0; font-size: 18px; font-weight: 900; letter-spacing: 2px; }
            QLabel#version { color: #738097; font-size: 10px; }
            QListWidget {
                background: transparent; border: none; color: #9BA7BB;
                outline: none; font-size: 13px;
            }
            QListWidget::item { padding: 9px 13px; margin: 2px 0; border-radius: 9px; }
            QListWidget::item:selected { background: #7357FF; color: white; }
            QListWidget::item:hover:!selected { background: #202A3D; color: white; }
            QListWidget#characterList {
                background: #111827; border: 1px solid #303B53;
                border-radius: 11px; padding: 4px;
            }
            QListWidget#characterList::item {
                padding: 4px 8px; margin: 2px 0; border-radius: 8px;
            }
            QLabel#pageTitle { color: #F7F5FF; font-size: 24px; font-weight: 850; }
            QLabel#pageHint { color: #8F9AAF; font-size: 12px; }
            QFrame#card { background: #171F30; border: 1px solid #2D3850; border-radius: 14px; }
            QLabel#cardTitle { color: #F1EFFF; font-size: 14px; font-weight: 750; }
            QLabel#muted { color: #8B96AA; font-size: 11px; }
            QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background: #202A3D; border: 1px solid #35415B; border-radius: 8px;
                color: #F7F5FF; padding: 7px 9px; selection-background-color: #7357FF;
            }
            QLineEdit, QTextEdit { placeholder-text-color: #8995AA; }
            QComboBox QAbstractItemView {
                background: #202A3D; color: #F7F5FF;
                border: 1px solid #45526D;
                selection-background-color: #7357FF;
                selection-color: #FFFFFF;
                outline: none;
            }
            QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
            QDoubleSpinBox:focus, QComboBox:focus {
                background: #222D42; border: 1px solid #7BE0D0;
            }
            QCheckBox { color: #BEC7D7; spacing: 8px; }
            QLabel:disabled, QCheckBox:disabled { color: #758196; }
            QTableWidget {
                background: #171F30; alternate-background-color: #1B2435;
                border: 1px solid #303B53; border-radius: 10px;
                gridline-color: #2C374C; color: #DDE3EE;
            }
            QHeaderView::section {
                background: #202A3D; color: #A9B3C5; border: none;
                border-bottom: 1px solid #35415B; padding: 8px; font-weight: 700;
            }
            QPushButton {
                border: none; border-radius: 9px; padding: 9px 16px; font-weight: 650;
            }
            QPushButton#primary { background: #7BE0D0; color: #101624; }
            QPushButton#primary:hover { background: #95EBDD; }
            QPushButton#secondary { background: #263249; color: #DDE3EE; }
            QPushButton#secondary:hover { background: #303D57; }
            QPushButton#danger { background: #35231F; color: #FFB7A7; }
            QPushButton#danger:hover { background: #442A25; }
            QPushButton#link { background: transparent; color: #7BE0D0; padding: 5px 7px; }
            QPushButton:disabled { background: #202838; color: #6F7B90; }
            QLabel#characterPreview {
                background: #202A3D; color: #8792A8; border-radius: 18px;
                font-size: 10px;
            }
            QLabel#userAvatarPreview {
                background: #263249; color: #7BE0D0; border: 1px solid #3A4863;
                border-radius: 27px; font-size: 10px; font-weight: 850;
            }
            QScrollArea#settingsPageScroll {
                background: transparent; border: none;
            }
            QScrollArea#settingsPageScroll > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent; width: 10px; margin: 4px 0;
            }
            QScrollBar::handle:vertical {
                background: #3B4760; border-radius: 5px; min-height: 34px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0; background: transparent;
            }
        """
        if theme != "light":
            return base
        return base + """
            QDialog { background: #EEF2F7; color: #1B2433; }
            QLabel { color: #263244; }
            QFrame#shell { background: #FFFFFF; border-color: #D7DFEA; }
            QFrame#sidebar { background: #F3F6FA; }
            QLabel#brand { color: #198F82; }
            QLabel#version, QLabel#pageHint, QLabel#muted { color: #667085; }
            QListWidget { color: #526174; }
            QListWidget::item:selected { background: #6654E8; color: white; }
            QListWidget::item:hover:!selected { background: #E6EBF3; color: #202939; }
            QListWidget#characterList {
                background: #FFFFFF; border-color: #CBD5E1;
            }
            QLabel#pageTitle, QLabel#cardTitle { color: #172033; }
            QFrame#card { background: #F8FAFC; border-color: #DCE3ED; }
            QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background: #FFFFFF; border-color: #C8D2E0; color: #172033;
            }
            QLineEdit, QTextEdit { placeholder-text-color: #667085; }
            QComboBox QAbstractItemView {
                background: #FFFFFF; color: #172033; border-color: #C8D2E0;
                selection-background-color: #6654E8; selection-color: white;
            }
            QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
            QDoubleSpinBox:focus, QComboBox:focus {
                background: #FFFFFF; border-color: #27A99A;
            }
            QCheckBox { color: #344054; }
            QLabel:disabled, QCheckBox:disabled { color: #667085; }
            QTableWidget {
                background: #FFFFFF; alternate-background-color: #F6F8FB;
                border-color: #D7DFEA; gridline-color: #E4E9F0; color: #263244;
            }
            QHeaderView::section {
                background: #EDF1F6; color: #526174; border-bottom-color: #D7DFEA;
            }
            QPushButton#primary { background: #6ED8C9; color: #10241F; }
            QPushButton#secondary { background: #E8EDF4; color: #344054; }
            QPushButton#secondary:hover { background: #DCE3EC; }
            QPushButton#danger { background: #FFF0EC; color: #B43D28; }
            QPushButton#link { color: #16897D; }
            QPushButton:disabled { background: #E5EAF1; color: #667085; }
            QLabel#characterPreview { background: #E8EDF4; color: #667085; }
            QLabel#userAvatarPreview {
                background: #E7EEF5; color: #16897D; border-color: #CAD5E2;
            }
            QScrollBar::handle:vertical { background: #B9C4D3; }
        """

    def apply_theme(self, requested=None):
        self._theme_choice = requested or configured_theme(self.config_manager)
        self.setStyleSheet(
            self._style(resolved_theme(self.config_manager, self._theme_choice))
        )

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        shell = QFrame()
        shell.setObjectName("shell")
        outer.addWidget(shell)
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(8, 8, 8, 8)
        shell_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 22, 16, 18)
        brand = QLabel("PIXKIN")
        brand.setObjectName("brand")
        version = QLabel(f"设置中心 · {VERSION}")
        version.setObjectName("version")
        side_layout.addWidget(brand)
        side_layout.addWidget(version)
        side_layout.addSpacing(24)
        self.navigation = QListWidget()
        for text in (
            "大模型",
            "角色与人设",
            "角色行为",
            "开播监听",
            "桌面行为",
            "语音与快捷键",
            "长期记忆",
            "隐私与审计",
            "玩法与插件",
            "更新",
        ):
            self.navigation.addItem(QListWidgetItem(text))
        self.navigation.setUniformItemSizes(True)
        self.navigation.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.navigation.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        side_layout.addWidget(self.navigation, 1)
        local_tip = QLabel("所有设置保存在本机")
        local_tip.setObjectName("version")
        side_layout.addWidget(local_tip)
        shell_layout.addWidget(sidebar)

        content = QFrame()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 24, 26, 20)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._scroll_page(self._build_model_page()))
        self.stack.addWidget(self._scroll_page(self._build_character_page()))
        self.stack.addWidget(
            self._scroll_page(self._build_character_behavior_page())
        )
        self.stack.addWidget(self._scroll_page(self._build_live_page()))
        self.stack.addWidget(self._scroll_page(self._build_desktop_page()))
        self.stack.addWidget(self._scroll_page(self._build_voice_page()))
        self.stack.addWidget(self._scroll_page(self._build_memory_page()))
        self.stack.addWidget(self._scroll_page(self._build_privacy_page()))
        self.stack.addWidget(self._scroll_page(self._build_extensions_page()))
        self.stack.addWidget(self._scroll_page(self._build_update_page()))
        content_layout.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存并应用")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        content_layout.addLayout(footer)
        shell_layout.addWidget(content, 1)

        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)

    @staticmethod
    def _scroll_page(page: QWidget) -> QScrollArea:
        page.setObjectName("settingsPage")
        scroll = QScrollArea()
        scroll.setObjectName("settingsPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _page_header(self, title, hint):
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        hint_label = QLabel(hint)
        hint_label.setObjectName("pageHint")
        hint_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(hint_label)
        return wrapper

    def _card(self, title, hint=""):
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("cardTitle")
        layout.addWidget(label)
        if hint:
            helper = QLabel(hint)
            helper.setObjectName("muted")
            helper.setWordWrap(True)
            layout.addWidget(helper)
        return frame, layout

    def _build_model_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "大模型", "连接 OpenAI Chat Completions 兼容服务，API Key 使用 Windows 凭据管理器保存。"
        ))
        card, card_layout = self._card("连接配置")
        self.model_settings_panel = ModelSettingsPanel(
            self.config_manager,
            self.session_secret_store,
            provider_registry=self.provider_catalog.get("chat"),
            permission_service=self.context_permission_service,
        )
        for name in (
            "api_url_input",
            "api_key_input",
            "session_api_key_cb",
            "api_model_input",
        ):
            setattr(
                self,
                name,
                getattr(self.model_settings_panel, name),
            )
        card_layout.addWidget(self.model_settings_panel)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _build_character_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._page_header(
            "角色与人设", "管理、切换和重命名角色；山山、凛凛与 Pip 三个内置角色始终保留。"
        ))

        profile_card, profile_layout = self._card("我的聊天资料")
        self.user_profile_editor = UserProfileEditor(
            display_name=str(
                self.config_manager.get(
                    "user", "display_name", "我"
                )
                or "我"
            ),
            avatar_source=self._user_avatar_source,
        )
        self.user_avatar_preview = (
            self.user_profile_editor.avatar_preview
        )
        self.user_name_input = self.user_profile_editor.name_input
        profile_layout.addWidget(self.user_profile_editor)
        layout.addWidget(profile_card)

        card, card_layout = self._card(
            "角色列表", "选择角色后可设为当前、重命名或删除。"
        )
        self.character_panel = CharacterManagerPanel()
        self.character_list = self.character_panel.character_list
        self.character_preview = self.character_panel.character_preview
        self.character_name = self.character_panel.character_name
        self.character_meta = self.character_panel.character_meta
        self.character_description = (
            self.character_panel.character_description
        )
        self.activate_character_btn = self.character_panel.activate_btn
        self.rename_character_btn = self.character_panel.rename_btn
        self.delete_character_btn = self.character_panel.delete_btn
        self.activate_character_btn.clicked.connect(
            self._activate_selected_character
        )
        self.rename_character_btn.clicked.connect(
            self._rename_selected_character
        )
        self.delete_character_btn.clicked.connect(
            self._delete_selected_character
        )
        self.character_panel.import_btn.clicked.connect(
            self._import_character
        )
        self.character_panel.inspect_btn.clicked.connect(
            self._inspect_character_archive
        )
        self.character_panel.hatch_btn.clicked.connect(
            self._open_pet_lab
        )
        card_layout.addWidget(self.character_panel)
        layout.addWidget(card)

        prompt_card, prompt_layout = self._card(
            "当前角色提示词", "切换角色时会自动载入对应人设，也可以在这里覆盖。"
        )
        self.prompt_input = QTextEdit()
        self.prompt_input.setMinimumHeight(76)
        self.prompt_input.setPlainText(
            self.config_manager.get("pet", "system_prompt", "")
        )
        prompt_layout.addWidget(self.prompt_input)
        layout.addWidget(prompt_card, 1)
        self._refresh_character_list()
        return page

    def _build_character_behavior_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "角色行为编辑器",
            "按角色覆盖随机动作权重、冷却与贴边露出，并可即时预览。",
        ))
        catalog_card, catalog_layout = self._card(
            "角色目录",
            "官方目录条目按版本和兼容性筛选；第三方目录只提供指纹，"
            "不会让作者声明自动获得信任。",
        )
        catalog = CharacterCatalogService(
            resource_path("character-packs/catalog.json")
        )
        updates = catalog.available_updates(
            self.character_service.list_packages()
        )
        catalog_status = QLabel(
            f"已验证 {len(catalog.entries)} 个目录条目 · "
            + (
                f"{len(updates)} 个已安装角色可更新"
                if updates
                else "已安装角色暂无兼容更新"
            )
        )
        catalog_status.setObjectName("muted")
        catalog_layout.addWidget(catalog_status)
        layout.addWidget(catalog_card)
        card, card_layout = self._card(
            "当前选中角色",
            "权重为 0 表示不加入随机动作；双击动作行只做本地预览。",
        )
        self.behavior_editor = CharacterBehaviorEditor(
            self.config_manager.get(
                "character_behavior_overrides", default={}
            )
        )
        self.behavior_editor.preview_requested.connect(
            self.preview_character_action
        )
        self.behavior_editor.set_package(self._selected_package())
        card_layout.addWidget(self.behavior_editor)
        layout.addWidget(card, 1)
        return page

    def _build_live_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._page_header(
            "开播监听",
            "B 站和抖音房间会在同一轮中并发查询，任何一个平台变慢都不会阻塞其他房间。",
        ))

        options, options_layout = self._card("监听策略")
        option_row = QHBoxLayout()
        self.live_enable_cb = QCheckBox("开启后台监听")
        self.live_enable_cb.setChecked(
            bool(self.config_manager.get("live_monitor", "enabled", True))
        )
        self.notify_start_cb = QCheckBox("启动时已在直播也提醒")
        self.notify_start_cb.setChecked(
            bool(self.config_manager.get(
                "live_monitor", "notify_if_live_on_start", False
            ))
        )
        self.live_interval_input = QSpinBox()
        self.live_interval_input.setRange(15, 3600)
        self.live_interval_input.setValue(
            int(self.config_manager.get("live_monitor", "interval_seconds", 60))
        )
        self.live_interval_input.setSuffix(" 秒")
        option_row.addWidget(self.live_enable_cb)
        option_row.addWidget(self.notify_start_cb)
        option_row.addStretch()
        option_row.addWidget(QLabel("检测间隔"))
        option_row.addWidget(self.live_interval_input)
        options_layout.addLayout(option_row)
        policy_row = QHBoxLayout()
        self.quiet_start_input = QLineEdit(
            str(
                self.config_manager.get(
                    "live_monitor", "quiet_start", "22:00"
                )
            )
        )
        self.quiet_start_input.setFixedWidth(72)
        self.quiet_end_input = QLineEdit(
            str(
                self.config_manager.get(
                    "live_monitor", "quiet_end", "08:00"
                )
            )
        )
        self.quiet_end_input.setFixedWidth(72)
        self.repeat_reminder_input = QSpinBox()
        self.repeat_reminder_input.setRange(0, 1440)
        self.repeat_reminder_input.setValue(
            int(
                self.config_manager.get(
                    "live_monitor",
                    "repeat_reminder_minutes",
                    0,
                )
            )
        )
        self.repeat_reminder_input.setSuffix(" 分钟")
        policy_row.addWidget(QLabel("免打扰"))
        policy_row.addWidget(self.quiet_start_input)
        policy_row.addWidget(QLabel("至"))
        policy_row.addWidget(self.quiet_end_input)
        policy_row.addStretch()
        policy_row.addWidget(QLabel("直播中重复提醒"))
        policy_row.addWidget(self.repeat_reminder_input)
        options_layout.addLayout(policy_row)
        layout.addWidget(options)

        room_card, room_layout = self._card(
            "直播间", "可同时添加多个 B 站与抖音直播间；房间号一栏也支持粘贴完整链接。"
        )
        self.live_room_editor = LiveRoomEditor(
            self.config_manager.get("live_monitor", "rooms", []),
            providers=self.live_providers,
        )
        self.rooms_table = self.live_room_editor.table
        room_layout.addWidget(self.live_room_editor)
        layout.addWidget(room_card, 1)
        return page

    def _build_desktop_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "桌面行为", "调整尺寸、贴边探头和 Windows 启动方式。"
        ))
        card, card_layout = self._card("挂件外观")
        form = QFormLayout()
        form.setSpacing(11)
        self.scale_input = QDoubleSpinBox()
        self.scale_input.setRange(0.7, 1.6)
        self.scale_input.setSingleStep(0.1)
        self.scale_input.setValue(
            float(self.config_manager.get("pet", "scale", 1.0))
        )
        self.scale_input.setSuffix(" ×")
        self.peek_input = QSpinBox()
        self.peek_input.setRange(36, 64)
        self.peek_input.setValue(
            int(self.config_manager.get("pet", "peek_size", 45))
        )
        self.peek_input.setSuffix(" px")
        self.theme_input = QComboBox()
        self.theme_input.addItem("跟随 Windows", "system")
        self.theme_input.addItem("深色", "dark")
        self.theme_input.addItem("浅色", "light")
        theme_index = self.theme_input.findData(self._theme_choice)
        self.theme_input.setCurrentIndex(max(0, theme_index))
        self.theme_input.currentIndexChanged.connect(
            lambda: self.apply_theme(self.theme_input.currentData())
        )
        form.addRow("界面主题", self.theme_input)
        form.addRow("角色尺寸", self.scale_input)
        form.addRow("贴边露出", self.peek_input)
        card_layout.addLayout(form)
        self.topmost_cb = QCheckBox("始终显示在其他窗口上方")
        self.topmost_cb.setChecked(
            bool(self.config_manager.get("pet", "topmost", True))
        )
        self.edge_dock_cb = QCheckBox("角色触碰或拖到屏幕边缘时自动隐藏并探头")
        self.edge_dock_cb.setChecked(
            bool(self.config_manager.get("pet", "edge_dock_enabled", True))
        )
        edge_sides = self.config_manager.get("pet", "edge_sides", {})
        if not isinstance(edge_sides, dict):
            edge_sides = {}
        self.edge_side_checks = {}
        edge_side_row = QHBoxLayout()
        edge_side_row.setSpacing(14)
        for side, label, default in (
            ("left", "左边", True),
            ("right", "右边", True),
            ("top", "上边", True),
            ("bottom", "下边", False),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(bool(edge_sides.get(side, default)))
            self.edge_side_checks[side] = checkbox
            edge_side_row.addWidget(checkbox)
        edge_side_row.addStretch()
        self.edge_hover_cb = QCheckBox("悬停时多探出一点")
        self.edge_hover_cb.setChecked(
            bool(self.config_manager.get("pet", "edge_hover_enabled", True))
        )
        self.return_edge_after_alert_cb = QCheckBox("提醒结束后回到原贴边位置")
        self.return_edge_after_alert_cb.setChecked(
            bool(self.config_manager.get(
                "pet", "return_to_edge_after_alert", True
            ))
        )
        card_layout.addWidget(self.topmost_cb)
        card_layout.addWidget(self.edge_dock_cb)
        card_layout.addLayout(edge_side_row)
        card_layout.addWidget(self.edge_hover_cb)
        card_layout.addWidget(self.return_edge_after_alert_cb)
        layout.addWidget(card)

        startup_card, startup_layout = self._card(
            "Windows", "开机自启使用当前用户注册表，不需要管理员权限。"
        )
        self.startup_cb = QCheckBox("登录 Windows 后自动启动小助理")
        self.startup_cb.setChecked(
            bool(self.config_manager.get("app", "start_with_windows", False))
        )
        startup_layout.addWidget(self.startup_cb)
        layout.addWidget(startup_card)

        proactive_card, proactive_layout = self._card(
            "主动关怀与桌面感知",
            "在本地感知前台专注状态，非侵入式提供适时的疲劳与深夜陪伴提醒。",
        )
        self.proactive_panel = ProactiveSettingsPanel(self.config_manager)
        proactive_layout.addWidget(self.proactive_panel)
        layout.addWidget(proactive_card)

        layout.addStretch()
        return page

    def _build_voice_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "语音与快捷键",
            "默认不访问麦克风；按住说话、独立转写端点和快捷键均可关闭。",
        ))
        card, card_layout = self._card(
            "输入与全局操作",
            "全局快捷键注册冲突时会保持未注册，不会覆盖其他程序。",
        )
        self.voice_panel = VoiceSettingsPanel(
            self.config_manager,
            self.session_secret_store,
        )
        card_layout.addWidget(self.voice_panel)
        layout.addWidget(card)

        tts_card, tts_card_layout = self._card(
            "桌宠语音发声 (TTS)",
            "回复时同步发音，支持 Windows 原生免配置离线音色与云端端点。",
        )
        self.voice_output_panel = VoiceOutputSettingsPanel(
            self.config_manager,
            provider_registry=self.provider_catalog.get("tts"),
            session_secrets=self.session_secret_store,
            permission_service=self.context_permission_service,
        )
        tts_card_layout.addWidget(self.voice_output_panel)
        layout.addWidget(tts_card)

        layout.addStretch()
        return page

    def _build_memory_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "可控长期记忆",
            "候选只在本机提取，逐条确认后才保存；每次回复会显示实际使用项。",
        ))
        card, card_layout = self._card(
            "当前角色的记忆",
            "记忆不默认上传到其他服务；只有启用且属于当前用户和角色的条目"
            "会随相关模型请求发送。",
        )
        recent = (
            self.chat_store.list_messages(
                character_id=self.active_character_id
            )[-100:]
            if self.chat_store is not None
            else []
        )
        service = self.memory_service or MemoryService(
            user_data_dir() / "memories.json"
        )
        self.memory_panel = MemorySettingsPanel(
            service,
            user_id="local-profile",
            character_id=self.active_character_id,
            recent_messages=recent,
        )
        card_layout.addWidget(self.memory_panel)
        layout.addWidget(card, 1)
        return page

    def _build_privacy_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "隐私与审计",
            "控制聊天保留期限，并查看本机脱敏工具审计。",
        ))
        card, card_layout = self._card(
            "本地数据",
            "选择“不保存”会在应用设置生效时清理已有历史，"
            "之后仅保留当前运行期间的上下文。",
        )
        self.privacy_panel = PrivacySettingsPanel(
            retention_days=int(
                self.config_manager.get(
                    "privacy",
                    "history_retention_days",
                    -1,
                )
            ),
            chat_store=self.chat_store,
            audit_store=self.tool_audit_store,
            diagnostic_service=self.diagnostic_bundle_service,
            local_backup_service=self.local_data_backup_service,
            config_manager=self.config_manager,
            context_permission_service=self.context_permission_service,
        )
        self.privacy_panel.history_changed.connect(
            self.history_changed.emit
        )
        card_layout.addWidget(self.privacy_panel)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _build_update_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "安装与更新",
            "签名清单只描述固定字段；下载和安装始终需要用户确认。",
        ))
        card, card_layout = self._card(
            "应用更新",
            "便携版与安装版使用不同更新路径，更新私钥不会随应用分发。",
        )
        self.update_panel = UpdateSettingsPanel(
            service=self.update_service,
            manifest_urls=self.update_manifest_urls,
            channel=str(
                self.config_manager.get(
                    "app", "update_channel", "stable"
                )
            ),
            automatic_updates=bool(
                self.config_manager.get(
                    "app", "automatic_updates", True
                )
            ),
            permission_service=self.context_permission_service,
        )
        self.update_panel.install_requested.connect(
            self.install_update_requested.emit
        )
        self.update_panel.rollback_requested.connect(
            self.rollback_update_requested.emit
        )
        card_layout.addWidget(self.update_panel)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _build_extensions_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 4)
        layout.setSpacing(14)
        layout.addWidget(self._page_header(
            "玩法与插件",
            "编辑聊天加号菜单中的互动玩法，或通过安全的 JSON 清单"
            "安装第三方玩法插件。",
        ))
        card, card_layout = self._card(
            "扩展管理",
            "插件采用声明式清单，只能增加提示词玩法，不会执行第三方代码。",
        )
        config = self.config_manager.get("extensions", default={})
        if not isinstance(config, dict):
            config = {}
        self.extension_panel = ExtensionSettingsPanel(
            config,
            Path(self.config_manager.config_path).resolve().parent
            / "plugins",
        )
        card_layout.addWidget(self.extension_panel)
        layout.addWidget(card)
        return page

    def _add_room_row(self, room):
        self.live_room_editor.add_room(room)

    def _remove_room_row(self):
        self.live_room_editor.remove_selected()

    def _collect_rooms(self):
        return self.live_room_editor.values()

    def _import_character(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入角色包", "", "Pixkin 角色包 (*.zip)"
        )
        if not path:
            return
        try:
            plan = self.character_service.prepare_install(path)
            if not plan.can_install:
                raise CharacterPackageError(
                    "内置角色不能被第三方角色包覆盖。"
                )
            rights = plan.package.rights
            license_name = str(
                rights.get("license", "未声明")
                if isinstance(rights, dict)
                else "未声明"
            )
            replace_note = (
                "\n此角色已安装，继续会覆盖现有版本。"
                if plan.requires_confirmation
                else ""
            )
            answer = QMessageBox.question(
                self,
                "确认安装第三方角色包",
                f"角色：{plan.package.name}\n"
                f"作者：{plan.package.author}\n"
                f"许可声明：{license_name}\n"
                f"资源文件：{plan.archive_file_count} 个\n"
                f"SHA-256：{plan.archive_sha256[:16]}…"
                f"{replace_note}\n\n"
                "第三方作者声明不会被 Pixkin 自动信任；"
                "确认来源可信后再安装。",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            replace = plan.requires_confirmation
            package = self.character_service.install(
                plan,
                replace_confirmed=replace,
            ).package
        except CharacterPackageError as exc:
            QMessageBox.warning(self, "角色包无法导入", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        if package.system_prompt:
            self.prompt_input.setPlainText(package.system_prompt)
        self.character_changed = True
        self._refresh_character_list(package.package_id)

    def _inspect_character_archive(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "检查角色包",
            "",
            "Pixkin 角色包 (*.zip)",
        )
        if not path:
            return
        try:
            inspector = CharacterPackageInspector(
                self.character_service,
                OfficialCharacterTrustStore(
                    resource_path(
                        "character-packs/official-sha256.json"
                    )
                ),
            )
            inspection = inspector.inspect(path)
        except Exception as exc:
            QMessageBox.warning(self, "角色包检查失败", str(exc))
            return
        CharacterPackageInspectorDialog(
            inspection,
            self,
        ).exec()

    def _open_pet_lab(self):
        dialog = PetLabWindow(
            self.config_manager,
            self.package_manager,
            self,
            character_service=self.character_service,
            context_permission_service=self.context_permission_service,
            session_secret_store=self.session_secret_store,
            provider_catalog=self.provider_catalog,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        package = dialog.generated_package
        if package and package.system_prompt:
            self.prompt_input.setPlainText(package.system_prompt)
            self.character_changed = True
        self._refresh_character_list(
            package.package_id if package else None
        )

    def _refresh_character_list(self, selected_id=None):
        active = self.character_service.get_active()
        active_id = active.package_id if active else ""
        self.character_panel.refresh(
            self.character_service.list_packages(),
            active_id=active_id,
            selected_id=selected_id,
        )

    def _selected_package(self):
        return self.character_panel.selected_package()

    def _on_character_selected(self, current, _previous):
        self.character_panel.show_details(
            self._selected_package() if current else None
        )
        if hasattr(self, "behavior_editor"):
            self.behavior_editor.set_package(
                self._selected_package() if current else None
            )

    def _show_character_details(self, package):
        self.character_panel.show_details(package)

    def _activate_selected_character(self):
        package = self._selected_package()
        if not package:
            return
        try:
            package = self.character_service.activate(package.package_id)
        except CharacterPackageError as exc:
            QMessageBox.warning(self, "无法切换角色", str(exc))
            return
        if package.system_prompt:
            self.prompt_input.setPlainText(package.system_prompt)
        self.character_changed = True
        self._refresh_character_list(package.package_id)

    def _rename_selected_character(self):
        package = self._selected_package()
        if not package:
            return
        name, accepted = QInputDialog.getText(
            self,
            "重命名角色",
            "新的角色名字：",
            QLineEdit.EchoMode.Normal,
            package.name,
        )
        if not accepted:
            return
        try:
            renamed = self.character_service.rename(
                package.package_id, name
            )
        except CharacterPackageError as exc:
            QMessageBox.warning(self, "无法重命名", str(exc))
            return
        active = self.character_service.get_active()
        if active and active.package_id == renamed.package_id:
            self.prompt_input.setPlainText(renamed.system_prompt)
            self.character_changed = True
        self._refresh_character_list(renamed.package_id)

    def _delete_selected_character(self):
        package = self._selected_package()
        if not package:
            return
        if package.package_id in BUILTIN_PACKAGE_IDS:
            QMessageBox.information(
                self, "内置角色不可删除",
                "山山、凛凛与 Pip 是 Pixkin 的内置角色，会一直保留。"
            )
            return
        answer = QMessageBox.question(
            self,
            "删除角色",
            f"确定删除“{package.name}”吗？\n该角色的本地图片和人设文件会被移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.character_service.delete(package.package_id)
        except CharacterPackageError as exc:
            QMessageBox.warning(self, "无法删除角色", str(exc))
            return
        active = result.active_package
        if result.switched_to_fallback:
            self.character_changed = True
        if active and active.system_prompt:
            self.prompt_input.setPlainText(active.system_prompt)
        self._refresh_character_list(
            active.package_id if active else None
        )

    def _toggle_key(self, button, checked):
        self.api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        button.setText("隐藏" if checked else "显示")

    def _set_user_avatar_preview(self, path):
        self.user_profile_editor.set_avatar_source(path)

    def _choose_user_avatar(self):
        self.user_profile_editor.choose_avatar()

    def _clear_user_avatar(self):
        self.user_profile_editor.clear_avatar()

    def _persist_user_avatar(self):
        if not self._user_avatar_source:
            return True, ""
        try:
            destination = self.user_profile_service.persist_avatar(
                self._user_avatar_source,
                user_data_dir() / "profile",
            )
        except UserProfileError as exc:
            QMessageBox.warning(
                self,
                "头像保存失败",
                str(exc),
            )
            return False, ""
        return True, destination

    def _save(self):
        base_url = self.api_url_input.text().strip()
        model = self.api_model_input.text().strip()
        if not base_url or not model:
            self.navigation.setCurrentRow(0)
            QMessageBox.warning(self, "还差一点", "请填写接口地址和模型名称。")
            return

        update_channel, automatic_updates = self.update_panel.settings()
        voice = self.voice_panel.values()
        try:
            LiveService._parse_clock(
                self.quiet_start_input.text()
            )
            LiveService._parse_clock(
                self.quiet_end_input.text()
            )
        except ValueError as exc:
            self.navigation.setCurrentRow(3)
            QMessageBox.warning(self, "提醒策略无效", str(exc))
            return
        if voice.enabled:
            try:
                self.voice_panel.build_service().endpoint.transcription_url()
            except Exception as exc:
                self.navigation.setCurrentRow(5)
                QMessageBox.warning(self, "语音设置无效", str(exc))
                return
        if voice.hotkeys_enabled:
            try:
                for shortcut in voice.hotkeys.values():
                    parse_hotkey(shortcut)
            except HotkeyError as exc:
                self.navigation.setCurrentRow(5)
                QMessageBox.warning(self, "快捷键无效", str(exc))
                return
        try:
            extension_values = self.extension_panel.values()
        except ExtensionManifestError as exc:
            self.navigation.setCurrentRow(8)
            QMessageBox.warning(self, "扩展设置无效", str(exc))
            return

        # All user input is validated before any persistent state changes.
        user_name = self.user_name_input.text().strip()[:20] or "我"
        profile_dir = user_data_dir() / "profile"
        existing_avatar_files = (
            {path.resolve() for path in profile_dir.glob("user-avatar-*")}
            if profile_dir.is_dir()
            else set()
        )
        avatar_ok, user_avatar_path = self._persist_user_avatar()
        if not avatar_ok:
            self.navigation.setCurrentRow(1)
            return
        new_avatar_path = (
            Path(user_avatar_path).resolve()
            if user_avatar_path
            else None
        )

        def cleanup_new_avatar() -> None:
            if (
                new_avatar_path is not None
                and new_avatar_path not in existing_avatar_files
                and new_avatar_path.parent == profile_dir.resolve()
            ):
                new_avatar_path.unlink(missing_ok=True)

        api_key = self.api_key_input.text().strip()
        previous_api_key = SecretStore.get_api_key()
        previous_session_key = (
            self.session_secret_store.get_chat_api_key()
        )
        session_only = self.session_api_key_cb.isChecked()
        credential_saved = False
        previous_voice_key = SecretStore.get_voice_api_key()
        previous_session_voice_key = (
            self.session_secret_store.get_voice_api_key()
        )
        voice_credential_saved = False

        def rollback_credentials() -> bool:
            rollback_ok = True
            if session_only:
                self.session_secret_store.set_chat_api_key(
                    previous_session_key
                )
            elif credential_saved:
                rollback_ok = SecretStore.set_api_key(previous_api_key)
            if voice.session_only:
                self.session_secret_store.set_voice_api_key(
                    previous_session_voice_key
                )
            elif voice_credential_saved:
                rollback_ok = (
                    SecretStore.set_voice_api_key(previous_voice_key)
                    and rollback_ok
                )
            return rollback_ok

        if session_only:
            self.session_secret_store.set_chat_api_key(api_key)
        else:
            credential_saved = SecretStore.set_api_key(api_key)
            credential_available = bool(
                credential_saved
                or (
                    api_key
                    and previous_api_key
                    and previous_api_key == api_key
                )
            )
            if not api_key and not credential_saved:
                restored = SecretStore.set_api_key(previous_api_key)
                cleanup_new_avatar()
                QMessageBox.critical(
                    self,
                    "API Key 清除失败",
                    "Windows 凭据管理器中的 API Key 无法删除。"
                    + (
                        "原设置已恢复。"
                        if restored
                        else "原设置也无法完全恢复，请立即检查凭据管理器。"
                    ),
                )
                return
            if (
                api_key
                and not credential_saved
                and previous_api_key
                and previous_api_key != api_key
            ):
                cleanup_new_avatar()
                QMessageBox.critical(
                    self,
                    "API Key 保存失败",
                    "Windows 凭据管理器仍保留旧 API Key；"
                    "为避免旧凭据覆盖新设置，本次修改没有应用。",
                )
                return
            if api_key and not credential_available:
                cleanup_new_avatar()
                QMessageBox.critical(
                    self,
                    "API Key 保存失败",
                    "Windows 凭据管理器不可用。为避免明文密钥写入配置文件，"
                    "本次修改没有应用；请修复凭据管理器后重试。",
                )
                return

        if voice.session_only:
            self.session_secret_store.set_voice_api_key(voice.api_key)
        elif voice.api_key != previous_voice_key:
            voice_credential_saved = SecretStore.set_voice_api_key(
                voice.api_key
            )
            if not voice_credential_saved:
                rollback_credentials()
                cleanup_new_avatar()
                QMessageBox.critical(
                    self,
                    "语音 API Key 保存失败",
                    "独立语音凭据无法写入 Windows 凭据管理器；"
                    "本次设置没有应用。",
                )
                return

        previous_memory_enabled = self.memory_panel.service.enabled
        desired_memory_enabled = (
            self.memory_panel.enable_input.isChecked()
        )
        memory_changed = (
            previous_memory_enabled != desired_memory_enabled
        )
        try:
            if memory_changed:
                self.memory_panel.service.set_enabled(
                    desired_memory_enabled
                )
        except (OSError, ValueError) as exc:
            rollback_credentials()
            cleanup_new_avatar()
            QMessageBox.critical(
                self,
                "长期记忆设置保存失败",
                f"{exc}\n本次设置没有应用。",
            )
            return

        model_fingerprint = privacy_scope_fingerprint(
            endpoint=base_url,
            model=model,
            data_scope=model_data_scope(
                self.privacy_panel.effective_context_permissions()
            ),
        )
        previous_model_fingerprint = str(
            self.config_manager.get(
                "privacy",
                "model_notice_fingerprint",
                "",
            )
            or ""
        )
        preserve_model_notice = bool(
            self.config_manager.get(
                "privacy",
                "model_notice_acknowledged",
                False,
            )
        ) and previous_model_fingerprint == model_fingerprint
        voice_fingerprint = privacy_scope_fingerprint(
            endpoint=voice.base_url,
            model=voice.model,
            data_scope="microphone_audio_for_transcription",
        )
        previous_voice_fingerprint = str(
            self.config_manager.get(
                "voice_input",
                "privacy_notice_fingerprint",
                "",
            )
            or ""
        )
        preserve_voice_notice = bool(
            self.config_manager.get(
                "voice_input",
                "privacy_notice_acknowledged",
                False,
            )
        ) and previous_voice_fingerprint == voice_fingerprint

        config_saved = self.config_manager.update_sections({
            "api": {
                "base_url": base_url,
                "model": model,
                "api_key": "",
                "preset": self.model_settings_panel.preset_input.currentData(),
                "input_cost_per_million": (
                    self.model_settings_panel.input_cost.value()
                ),
                "output_cost_per_million": (
                    self.model_settings_panel.output_cost.value()
                ),
            },
            "pet": {
                "system_prompt": self.prompt_input.toPlainText().strip(),
                "scale": self.scale_input.value(),
                "peek_size": self.peek_input.value(),
                "topmost": self.topmost_cb.isChecked(),
                "edge_dock_enabled": self.edge_dock_cb.isChecked(),
                "edge_sides": {
                    side: checkbox.isChecked()
                    for side, checkbox in self.edge_side_checks.items()
                },
                "edge_hover_enabled": self.edge_hover_cb.isChecked(),
                "return_to_edge_after_alert": (
                    self.return_edge_after_alert_cb.isChecked()
                ),
            },
            "live_monitor": {
                "enabled": self.live_enable_cb.isChecked(),
                "notify_if_live_on_start": self.notify_start_cb.isChecked(),
                "interval_seconds": self.live_interval_input.value(),
                "quiet_start": self.quiet_start_input.text().strip(),
                "quiet_end": self.quiet_end_input.text().strip(),
                "repeat_reminder_minutes": (
                    self.repeat_reminder_input.value()
                ),
                "rooms": self._collect_rooms(),
            },
            "user": {
                "display_name": user_name,
                "avatar_path": user_avatar_path,
            },
            "app": {
                "start_with_windows": self.startup_cb.isChecked(),
                "theme": self.theme_input.currentData(),
                "update_channel": update_channel,
                "automatic_updates": automatic_updates,
            },
            "privacy": {
                "history_retention_days": (
                    self.privacy_panel.retention_days()
                ),
                "model_notice_acknowledged": preserve_model_notice,
                "model_notice_fingerprint": (
                    model_fingerprint if preserve_model_notice else ""
                ),
                "context_permissions": self.privacy_panel.context_permissions(),
            },
            "voice_input": {
                "enabled": voice.enabled,
                "muted": voice.muted,
                "base_url": voice.base_url,
                "model": voice.model,
                "privacy_notice_acknowledged": preserve_voice_notice,
                "privacy_notice_fingerprint": (
                    voice_fingerprint if preserve_voice_notice else ""
                ),
                "hotkeys_enabled": voice.hotkeys_enabled,
                "hotkeys": voice.hotkeys,
            },
            "voice_output": {
                "enabled": self.voice_output_panel.values().enabled,
                "provider": self.voice_output_panel.values().provider,
                "voice": self.voice_output_panel.values().voice,
                "speed": self.voice_output_panel.values().speed,
                "base_url": self.voice_output_panel.values().base_url,
                "model": self.voice_output_panel.values().model,
            },
            "proactive": {
                "enabled": self.proactive_panel.values().enabled,
                "quiet_fullscreen": self.proactive_panel.values().quiet_fullscreen,
                "work_stretch_reminder": self.proactive_panel.values().work_stretch_reminder,
                "work_stretch_interval_minutes": self.proactive_panel.values().work_stretch_interval_minutes,
                "sleep_guard": self.proactive_panel.values().sleep_guard,
                "sleep_guard_hour": 23,
                "sleep_guard_minute": 30,
                "min_prompt_interval_seconds": 3600,
            },
            "character_behavior_overrides": (
                self.behavior_editor.values()
            ),
            "extensions": extension_values,
        })
        if not config_saved:
            rollback_ok = rollback_credentials()
            if memory_changed:
                try:
                    self.memory_panel.service.set_enabled(
                        previous_memory_enabled
                    )
                except (OSError, ValueError):
                    rollback_ok = False
            cleanup_new_avatar()
            QMessageBox.critical(
                self,
                "设置保存失败",
                (
                    "配置文件无法写入，API Key 与其他设置均已回滚。"
                    if rollback_ok
                    else "配置文件无法写入，且 API Key 回滚失败；"
                    "请立即检查 Windows 凭据管理器。"
                )
                + "请检查磁盘空间和目录权限。",
            )
            return
        if not session_only:
            self.session_secret_store.set_chat_api_key("")
        if not voice.session_only:
            self.session_secret_store.set_voice_api_key("")
        self.accept()
