import hashlib
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QInputDialog,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QTableWidget, QTextEdit, QVBoxLayout, QWidget
)

from core.character_package import (
    BUILTIN_PACKAGE_IDS,
    DEFAULT_PACKAGE_ID,
    CharacterPackageError,
    CharacterPackageManager,
)
from core.config import ConfigManager
from core.paths import user_data_dir
from core.secrets import SecretStore
from core.version import VERSION
from ui.pet_lab_window import PetLabWindow
from ui.theme import configured_theme, resolved_theme


BUILTIN_PACKAGE_ORDER = ("shanshan", "linlin", "pip")
REPLACED_LEGACY_PACKAGES = {
    "default-assistant": "shanshan",
    "pixkin-pip": "pip",
}


def _fitted_character_pixmap(path, size: int) -> QPixmap:
    """按非透明内容统一缩放预览，避免不同画布尺寸把列表排乱。"""
    source = QPixmap(str(path)) if path else QPixmap()
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    if source.isNull():
        return canvas

    image = source.toImage()
    left, top = image.width(), image.height()
    right = bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 8:
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
    if right >= left and bottom >= top:
        source = source.copy(left, top, right - left + 1, bottom - top + 1)

    fitted = source.scaled(
        size - 4,
        size - 4,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.drawPixmap(
        (size - fitted.width()) // 2,
        (size - fitted.height()) // 2,
        fitted,
    )
    painter.end()
    return canvas


def _profile_avatar_pixmap(path, size: int) -> QPixmap:
    source = QPixmap(str(path)) if path else QPixmap()
    if source.isNull():
        return QPixmap()
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    cropped = scaled.copy(x, y, size, size)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return canvas


class SettingsWindow(QDialog):
    """带侧栏的产品级设置中心。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        package_manager: CharacterPackageManager = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.package_manager = package_manager or CharacterPackageManager(config_manager)
        self.character_changed = False
        self._user_avatar_source = str(
            self.config_manager.get("user", "avatar_path", "") or ""
        )
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setWindowTitle("Pixkin · 设置中心")
        self.setMinimumSize(820, 620)
        self.resize(880, 680)
        self._theme_choice = configured_theme(self.config_manager)
        self.apply_theme(self._theme_choice)
        self._build_ui()

    @staticmethod
    def _style(theme="dark"):
        base = """
            QDialog { background: #0D1320; color: #F6F4FF; }
            QLabel { color: #E7ECF5; }
            QFrame#shell { background: #151E2E; border: 1px solid #28334A; border-radius: 22px; }
            QFrame#sidebar { background: #101624; border-radius: 18px; }
            QLabel#brand { color: #7BE0D0; font-size: 18px; font-weight: 900; letter-spacing: 2px; }
            QLabel#version { color: #738097; font-size: 10px; }
            QListWidget {
                background: transparent; border: none; color: #9BA7BB;
                outline: none; font-size: 13px;
            }
            QListWidget::item { padding: 11px 13px; margin: 3px 0; border-radius: 9px; }
            QListWidget::item:selected { background: #7357FF; color: white; }
            QListWidget::item:hover:!selected { background: #202A3D; color: white; }
            QListWidget#characterList {
                background: #111827; border: 1px solid #303B53;
                border-radius: 11px; padding: 4px;
            }
            QListWidget#characterList::item {
                padding: 4px 8px; margin: 2px 0; border-radius: 8px;
            }
            QLabel#pageTitle { color: #F7F5FF; font-size: 23px; font-weight: 850; }
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
        sidebar.setFixedWidth(190)
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
        for text in ("大模型", "角色与人设", "开播监听", "桌面行为"):
            self.navigation.addItem(QListWidgetItem(text))
        side_layout.addWidget(self.navigation)
        side_layout.addStretch()
        local_tip = QLabel("所有设置保存在本机")
        local_tip.setObjectName("version")
        side_layout.addWidget(local_tip)
        shell_layout.addWidget(sidebar)

        content = QFrame()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 24, 26, 20)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_model_page())
        self.stack.addWidget(self._build_character_page())
        self.stack.addWidget(self._build_live_page())
        self.stack.addWidget(self._build_desktop_page())
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
        form = QFormLayout()
        form.setSpacing(11)
        self.api_url_input = QLineEdit(
            self.config_manager.get("api", "base_url", "")
        )
        self.api_url_input.setPlaceholderText("https://api.example.com/v1")
        stored_key = SecretStore.get_api_key() or self.config_manager.get(
            "api", "api_key", ""
        )
        self.api_key_input = QLineEdit(stored_key)
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-...")
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_input, 1)
        reveal = QPushButton("显示")
        reveal.setObjectName("link")
        reveal.setCheckable(True)
        reveal.toggled.connect(lambda checked: self._toggle_key(reveal, checked))
        key_row.addWidget(reveal)
        self.api_model_input = QLineEdit(
            self.config_manager.get("api", "model", "")
        )
        form.addRow("接口地址", self.api_url_input)
        form.addRow("API Key", key_row)
        form.addRow("模型名称", self.api_model_input)
        card_layout.addLayout(form)
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
        profile_row = QHBoxLayout()
        profile_row.setSpacing(12)
        self.user_avatar_preview = QLabel()
        self.user_avatar_preview.setObjectName("userAvatarPreview")
        self.user_avatar_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_avatar_preview.setFixedSize(56, 56)
        profile_row.addWidget(self.user_avatar_preview)

        profile_fields = QVBoxLayout()
        profile_fields.setSpacing(5)
        profile_label = QLabel("昵称")
        profile_label.setObjectName("muted")
        self.user_name_input = QLineEdit(
            str(self.config_manager.get("user", "display_name", "我") or "我")
        )
        self.user_name_input.setMaxLength(20)
        self.user_name_input.setPlaceholderText("显示在右侧聊天气泡上方")
        profile_fields.addWidget(profile_label)
        profile_fields.addWidget(self.user_name_input)
        profile_row.addLayout(profile_fields, 1)

        choose_avatar = QPushButton("选择头像")
        choose_avatar.setObjectName("secondary")
        choose_avatar.clicked.connect(self._choose_user_avatar)
        clear_avatar = QPushButton("使用文字头像")
        clear_avatar.setObjectName("link")
        clear_avatar.clicked.connect(self._clear_user_avatar)
        profile_row.addWidget(choose_avatar)
        profile_row.addWidget(clear_avatar)
        profile_layout.addLayout(profile_row)
        layout.addWidget(profile_card)
        if self._user_avatar_source:
            self._set_user_avatar_preview(self._user_avatar_source)
        else:
            self.user_avatar_preview.setText(
                (self.user_name_input.text().strip() or "我")[:2]
            )

        card, card_layout = self._card(
            "角色列表", "选择角色后可设为当前、重命名或删除。"
        )
        manager_row = QHBoxLayout()
        manager_row.setSpacing(16)
        self.character_list = QListWidget()
        self.character_list.setObjectName("characterList")
        self.character_list.setIconSize(QSize(44, 44))
        self.character_list.setFixedWidth(270)
        self.character_list.setFixedHeight(178)
        self.character_list.setUniformItemSizes(True)
        self.character_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.character_list.currentItemChanged.connect(
            self._on_character_selected
        )
        manager_row.addWidget(self.character_list)

        detail = QVBoxLayout()
        detail.setSpacing(6)
        preview_row = QHBoxLayout()
        self.character_preview = QLabel("暂无\n图片")
        self.character_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.character_preview.setFixedSize(82, 82)
        self.character_preview.setObjectName("characterPreview")
        meta_stack = QVBoxLayout()
        self.character_name = QLabel("请选择角色")
        self.character_name.setObjectName("cardTitle")
        self.character_meta = QLabel("")
        self.character_meta.setObjectName("muted")
        self.character_description = QLabel("")
        self.character_description.setObjectName("muted")
        self.character_description.setWordWrap(True)
        meta_stack.addWidget(self.character_name)
        meta_stack.addWidget(self.character_meta)
        meta_stack.addWidget(self.character_description)
        meta_stack.addStretch()
        preview_row.addWidget(self.character_preview)
        preview_row.addLayout(meta_stack, 1)
        detail.addLayout(preview_row)

        selected_actions = QHBoxLayout()
        self.activate_character_btn = QPushButton("设为当前")
        self.activate_character_btn.setObjectName("primary")
        self.activate_character_btn.clicked.connect(
            self._activate_selected_character
        )
        self.rename_character_btn = QPushButton("重命名")
        self.rename_character_btn.setObjectName("secondary")
        self.rename_character_btn.clicked.connect(
            self._rename_selected_character
        )
        self.delete_character_btn = QPushButton("删除")
        self.delete_character_btn.setObjectName("danger")
        self.delete_character_btn.clicked.connect(
            self._delete_selected_character
        )
        selected_actions.addWidget(self.activate_character_btn)
        selected_actions.addWidget(self.rename_character_btn)
        selected_actions.addWidget(self.delete_character_btn)
        selected_actions.addStretch()
        detail.addLayout(selected_actions)

        add_actions = QHBoxLayout()
        import_btn = QPushButton("＋ 导入 ZIP")
        import_btn.setObjectName("secondary")
        import_btn.clicked.connect(self._import_character)
        hatch_btn = QPushButton("伙伴工坊")
        hatch_btn.setObjectName("secondary")
        hatch_btn.clicked.connect(self._open_pet_lab)
        add_actions.addWidget(import_btn)
        add_actions.addWidget(hatch_btn)
        add_actions.addStretch()
        detail.addLayout(add_actions)
        manager_row.addLayout(detail, 1)
        card_layout.addLayout(manager_row)
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
        layout.addWidget(options)

        room_card, room_layout = self._card(
            "直播间", "可同时添加多个 B 站与抖音直播间；房间号一栏也支持粘贴完整链接。"
        )
        self.rooms_table = QTableWidget(0, 4)
        self.rooms_table.setHorizontalHeaderLabels(
            ["启用", "平台", "房间号 / 链接", "主播名称"]
        )
        self.rooms_table.verticalHeader().setVisible(False)
        self.rooms_table.setAlternatingRowColors(True)
        self.rooms_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.rooms_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.rooms_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.rooms_table.setColumnWidth(0, 64)
        self.rooms_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self.rooms_table.setColumnWidth(1, 104)
        self.rooms_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.rooms_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.rooms_table.setMinimumHeight(230)
        for room in self.config_manager.get("live_monitor", "rooms", []):
            self._add_room_row(room)
        room_layout.addWidget(self.rooms_table)
        buttons = QHBoxLayout()
        add = QPushButton("添加直播间")
        add.setObjectName("secondary")
        add.clicked.connect(lambda: self._add_room_row({}))
        remove = QPushButton("删除选中")
        remove.setObjectName("danger")
        remove.clicked.connect(self._remove_room_row)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        room_layout.addLayout(buttons)
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
        layout.addStretch()
        return page

    def _add_room_row(self, room):
        row = self.rooms_table.rowCount()
        self.rooms_table.insertRow(row)
        enabled = QCheckBox()
        enabled.setChecked(bool(room.get("enabled", True)))
        enabled_wrap = QWidget()
        enabled_layout = QHBoxLayout(enabled_wrap)
        enabled_layout.setContentsMargins(9, 0, 0, 0)
        enabled_layout.addWidget(enabled)
        enabled_layout.addStretch()
        platform = QComboBox()
        platform.setMinimumWidth(86)
        platform.addItem("B站", "bilibili")
        platform.addItem("抖音", "douyin")
        index = platform.findData(room.get("platform", "bilibili"))
        platform.setCurrentIndex(max(0, index))
        room_id = QLineEdit(
            str(room.get("room_id") or room.get("room_url") or "")
        )
        room_id.setPlaceholderText("房间号或直播间链接")
        anchor = QLineEdit(str(room.get("anchor_name") or ""))
        anchor.setPlaceholderText("提醒中显示的名称")
        self.rooms_table.setCellWidget(row, 0, enabled_wrap)
        self.rooms_table.setCellWidget(row, 1, platform)
        self.rooms_table.setCellWidget(row, 2, room_id)
        self.rooms_table.setCellWidget(row, 3, anchor)
        self.rooms_table.setRowHeight(row, 45)

    def _remove_room_row(self):
        row = self.rooms_table.currentRow()
        if row >= 0:
            self.rooms_table.removeRow(row)

    def _collect_rooms(self):
        rooms = []
        for row in range(self.rooms_table.rowCount()):
            enabled_wrap = self.rooms_table.cellWidget(row, 0)
            enabled = enabled_wrap.findChild(QCheckBox)
            platform = self.rooms_table.cellWidget(row, 1)
            room_id = self.rooms_table.cellWidget(row, 2).text().strip()
            anchor = self.rooms_table.cellWidget(row, 3).text().strip()
            if room_id:
                rooms.append({
                    "enabled": enabled.isChecked(),
                    "platform": platform.currentData(),
                    "room_id": room_id,
                    "anchor_name": anchor or "关注的主播",
                })
        return rooms

    def _import_character(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入角色包", "", "Pixkin 角色包 (*.zip)"
        )
        if not path:
            return
        replace = False
        try:
            inspected = self.package_manager.inspect_zip(path)
            installed = {
                package.package_id for package in self.package_manager.list_packages()
            }
            if inspected.package_id in installed:
                answer = QMessageBox.question(
                    self,
                    "覆盖角色包",
                    f"角色“{inspected.name}”已经安装，是否覆盖更新？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                replace = True
            package = self.package_manager.import_zip(path, replace=replace)
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

    def _open_pet_lab(self):
        dialog = PetLabWindow(self.config_manager, self.package_manager, self)
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
        active = self.package_manager.get_active()
        active_id = active.package_id if active else ""
        target_id = selected_id or active_id
        all_packages = self.package_manager.list_packages()
        package_ids = {package.package_id for package in all_packages}
        visible_packages = [
            package
            for package in all_packages
            if not (
                package.package_id in REPLACED_LEGACY_PACKAGES
                and REPLACED_LEGACY_PACKAGES[package.package_id] in package_ids
            )
        ]
        builtin_rank = {
            package_id: index
            for index, package_id in enumerate(BUILTIN_PACKAGE_ORDER)
        }
        packages = sorted(
            visible_packages,
            key=lambda package: (
                0 if package.package_id in builtin_rank else 1,
                builtin_rank.get(package.package_id, 0),
                package.name.casefold(),
                package.package_id,
            ),
        )
        self.character_list.blockSignals(True)
        self.character_list.clear()
        selected_row = 0
        for row, package in enumerate(packages):
            badges = []
            if package.package_id == DEFAULT_PACKAGE_ID:
                badges.append("默认角色")
            elif package.package_id in BUILTIN_PACKAGE_IDS:
                badges.append("内置角色")
            if package.package_id == active_id:
                badges.append("当前使用")
            subtitle = " · ".join(badges) or package.package_id
            item = QListWidgetItem(f"{package.name}\n{subtitle}")
            item.setSizeHint(QSize(250, 54))
            item.setData(Qt.ItemDataRole.UserRole, package.package_id)
            if package.preview and package.preview.is_file():
                item.setIcon(QIcon(
                    _fitted_character_pixmap(package.preview, 44)
                ))
            self.character_list.addItem(item)
            if package.package_id == target_id:
                selected_row = row
        self.character_list.blockSignals(False)
        if packages:
            self.character_list.setCurrentRow(selected_row)
            self._on_character_selected(
                self.character_list.currentItem(), None
            )
        else:
            self._show_character_details(None)

    def _selected_package(self):
        item = self.character_list.currentItem()
        if not item:
            return None
        package_id = item.data(Qt.ItemDataRole.UserRole)
        return next(
            (
                package
                for package in self.package_manager.list_packages()
                if package.package_id == package_id
            ),
            None,
        )

    def _on_character_selected(self, current, _previous):
        package = self._selected_package() if current else None
        self._show_character_details(package)

    def _show_character_details(self, package):
        self.character_preview.clear()
        self.character_preview.setText("暂无\n图片")
        if not package:
            self.character_name.setText("没有已安装角色")
            self.character_meta.clear()
            self.character_description.clear()
            self.activate_character_btn.setDisabled(True)
            self.rename_character_btn.setDisabled(True)
            self.delete_character_btn.setDisabled(True)
            return
        active = self.package_manager.get_active()
        is_active = bool(active and active.package_id == package.package_id)
        self.character_name.setText(package.name)
        badges = []
        if package.package_id == DEFAULT_PACKAGE_ID:
            badges.append("默认角色")
        elif package.package_id in BUILTIN_PACKAGE_IDS:
            badges.append("内置角色")
        if is_active:
            badges.append("当前使用")
        badge_text = f"  ·  {' · '.join(badges)}" if badges else ""
        self.character_meta.setText(
            f"版本 {package.version}  ·  {package.author}{badge_text}"
        )
        self.character_description.setText(package.description)
        self.activate_character_btn.setDisabled(is_active)
        self.rename_character_btn.setDisabled(
            package.package_id in BUILTIN_PACKAGE_IDS
        )
        self.rename_character_btn.setToolTip(
            "内置角色不可重命名"
            if package.package_id in BUILTIN_PACKAGE_IDS
            else "重命名选中的角色"
        )
        self.delete_character_btn.setDisabled(
            package.package_id in BUILTIN_PACKAGE_IDS
        )
        self.delete_character_btn.setToolTip(
            "内置角色不可删除"
            if package.package_id in BUILTIN_PACKAGE_IDS
            else "删除选中的角色"
        )
        if package.preview and package.preview.is_file():
            pixmap = _fitted_character_pixmap(package.preview, 84)
            if not pixmap.isNull():
                self.character_preview.setPixmap(pixmap)

    def _activate_selected_character(self):
        package = self._selected_package()
        if not package:
            return
        try:
            package = self.package_manager.activate(package.package_id)
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
            renamed = self.package_manager.rename_package(
                package.package_id, name
            )
        except CharacterPackageError as exc:
            QMessageBox.warning(self, "无法重命名", str(exc))
            return
        active = self.package_manager.get_active()
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
            fallback = self.package_manager.delete_package(package.package_id)
        except CharacterPackageError as exc:
            QMessageBox.warning(self, "无法删除角色", str(exc))
            return
        active = fallback or self.package_manager.get_active()
        if fallback:
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
        pixmap = _profile_avatar_pixmap(path, 54)
        if pixmap.isNull():
            self.user_avatar_preview.setPixmap(QPixmap())
            name = self.user_name_input.text().strip() or "我"
            self.user_avatar_preview.setText(name[:2])
        else:
            self.user_avatar_preview.clear()
            self.user_avatar_preview.setPixmap(pixmap)

    def _choose_user_avatar(self):
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择聊天头像",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)",
        )
        if not selected:
            return
        if QPixmap(selected).isNull():
            QMessageBox.warning(
                self, "无法使用头像", "该文件不是受支持或可读取的图片。"
            )
            return
        self._user_avatar_source = selected
        self._set_user_avatar_preview(selected)

    def _clear_user_avatar(self):
        self._user_avatar_source = ""
        self._set_user_avatar_preview("")

    def _persist_user_avatar(self):
        if not self._user_avatar_source:
            return True, ""
        source = Path(self._user_avatar_source)
        if not source.is_file() or QPixmap(str(source)).isNull():
            QMessageBox.warning(
                self, "头像保存失败", "所选头像已不存在或无法读取，请重新选择。"
            )
            return False, ""
        suffix = source.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            suffix = ".png"
        try:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            destination = (
                user_data_dir()
                / "profile"
                / f"user-avatar-{digest}{suffix}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except OSError as exc:
            QMessageBox.warning(
                self, "头像保存失败", f"无法复制头像到本地资料目录：{exc}"
            )
            return False, ""
        return True, str(destination)

    def _save(self):
        base_url = self.api_url_input.text().strip()
        model = self.api_model_input.text().strip()
        if not base_url or not model:
            self.navigation.setCurrentRow(0)
            QMessageBox.warning(self, "还差一点", "请填写接口地址和模型名称。")
            return

        user_name = self.user_name_input.text().strip()[:20] or "我"
        avatar_ok, user_avatar_path = self._persist_user_avatar()
        if not avatar_ok:
            self.navigation.setCurrentRow(1)
            return

        api_key = self.api_key_input.text().strip()
        previous_api_key = SecretStore.get_api_key()
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
            QMessageBox.critical(
                self,
                "API Key 保存失败",
                "Windows 凭据管理器仍保留旧 API Key；为避免旧凭据覆盖新设置，"
                "本次修改没有应用。",
            )
            return
        config_saved = self.config_manager.update_sections({
            "api": {
                "base_url": base_url,
                "model": model,
                "api_key": "" if credential_available else api_key,
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
                "rooms": self._collect_rooms(),
            },
            "user": {
                "display_name": user_name,
                "avatar_path": user_avatar_path,
            },
            "app": {
                "start_with_windows": self.startup_cb.isChecked(),
                "theme": self.theme_input.currentData(),
            },
        })
        if not config_saved:
            rollback_ok = True
            if credential_saved:
                rollback_ok = SecretStore.set_api_key(previous_api_key)
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
        if api_key and not credential_available:
            QMessageBox.warning(
                self,
                "凭据保存受限",
                "Windows 凭据管理器不可用，API Key 已回退保存到本地配置文件。",
            )
        self.accept()
