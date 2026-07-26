import random
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRectF, QTimer
from PyQt6.QtGui import (
    QAction, QColor, QFont, QMouseEvent, QPainter, QPainterPath,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPushButton, QScrollArea, QTextBrowser,
    QVBoxLayout, QWidget,
)

from core.paths import resource_path, user_data_dir
from core.services.extension_service import (
    ExtensionManifestError,
    ExtensionService,
)
from ui.chat_message_components import (
    MessageRow,
    WelcomeMessageCard,
    avatar_pixmap as _avatar_pixmap,
    friendly_day_label,
    render_transcript_html,
)
from ui.theme import resolved_theme


MIDNIGHT = "#101624"
SURFACE = "#171F30"
SURFACE_RAISED = "#202A3D"
INK = "#F6F4FF"
MUTED = "#98A2B8"
MINT = "#7BE0D0"
CORAL = "#FF775C"
VIOLET = "#7357FF"


class BubbleShell(QFrame):
    """无边框圆角对话壳。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("bubbleShell")
        self.background_color = MIDNIGHT
        self.border_color = QColor(255, 255, 255, 22)

    def set_theme(self, theme: str):
        self.background_color = "#F7F9FC" if theme == "light" else MIDNIGHT
        self.border_color = (
            QColor(32, 43, 62, 35)
            if theme == "light"
            else QColor(255, 255, 255, 22)
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 26, 26)
        painter.fillPath(path, QColor(self.background_color))
        painter.setPen(self.border_color)
        painter.drawPath(path)


class ChatBubbleWindow(QWidget):
    """Pixkin 悬浮对话气泡：头像、消息气泡、工具与直播提醒。"""

    send_message_signal = pyqtSignal(str)
    retry_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    pet_lab_requested = pyqtSignal()
    history_requested = pyqtSignal()
    new_session_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    push_to_talk_pressed = pyqtSignal()
    push_to_talk_released = pyqtSignal()

    def __init__(self, config_manager=None, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.setWindowTitle("Pixkin · 对话")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setMinimumSize(400, 520)
        self.resize(430, 600)

        self._messages = []
        self._message_rows = []
        self._stream_index = None
        self._stream_row = None
        self._drag_origin = QPoint()
        self._window_origin = QPoint()
        self._character_name = "山山"
        self._avatar_path = resource_path("assets/pixkin/pip-avatar.png")
        self._user_name = (
            str(config_manager.get("user", "display_name", "我")).strip()
            if config_manager is not None
            else "我"
        ) or "我"
        self._user_avatar_path = (
            config_manager.get("user", "avatar_path", "")
            if config_manager is not None
            else ""
        )
        self._available_tool_names = set()
        self._focus_after_response = False
        self._init_ui()
        self._stream_update_timer = QTimer(self)
        self._stream_update_timer.setSingleShot(True)
        self._stream_update_timer.setInterval(16)
        self._stream_update_timer.timeout.connect(
            self._flush_stream_update
        )
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._scroll_to_bottom)
        self._render_messages()

    def _init_ui(self):
        self._base_style = """
            QFrame#bubbleShell { background: transparent; border: none; }
            QLabel { color: #F6F4FF; }
            QLabel#brand {
                color: #F6F4FF; font-size: 16px; font-weight: 900;
                letter-spacing: 2px;
            }
            QLabel#characterName { color: #AAB4C8; font-size: 11px; }
            QLabel#statusDot { color: #7BE0D0; font-size: 15px; }
            QPushButton#headerButton {
                color: #AAB4C8; background: #202A3D; border: none;
                border-radius: 15px; font-size: 11px; font-weight: 700;
                padding: 0 10px;
            }
            QPushButton#headerButton:hover { background: #2A3650; color: white; }
            QScrollArea { border: none; background: transparent; }
            QWidget#messagesHost { background: transparent; }
            QScrollBar:vertical {
                background: transparent; width: 7px; margin: 3px 0;
            }
            QScrollBar::handle:vertical {
                background: #3B4660; border-radius: 3px; min-height: 34px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0; background: none;
            }
            QFrame#assistantBubble {
                background: #202A3D; border: 1px solid rgba(255,255,255,14);
                border-radius: 17px;
            }
            QFrame#userBubble {
                background: #7357FF; border: 1px solid #8A74FF;
                border-radius: 17px;
            }
            QLabel#messageAuthor {
                color: #7BE0D0; font-size: 9px; font-weight: 900;
                letter-spacing: 1px;
            }
            QLabel#userMessageAuthor {
                color: #D9D3FF; font-size: 9px; font-weight: 800;
            }
            QLabel#messageText { color: #F6F4FF; font-size: 13px; }
            QLabel#userAvatar {
                color: #121827; background: #7BE0D0; border-radius: 17px;
                font-size: 8px; font-weight: 900;
            }
            QLabel#toolPill {
                color: #AEB8CC; background: #1C2536; border: 1px solid #303C55;
                border-radius: 12px; padding: 6px 11px; font-size: 10px;
            }
            QLabel#dateDivider {
                color: #718097; font-size: 9px; padding: 5px 8px;
            }
            QLabel#errorPill {
                color: #FFC1B4; background: #38231F; border: 1px solid #6B3A31;
                border-radius: 12px; padding: 7px 11px; font-size: 10px;
            }
            QFrame#alertCard {
                background: #251E27; border: 1px solid #70435C;
                border-radius: 18px;
            }
            QLabel#liveBadge {
                color: #FFF7F5; background: #F05268; border-radius: 7px;
                padding: 3px 7px; font-size: 8px; font-weight: 900;
            }
            QLabel#platformBadge {
                color: #FFB9C6; background: #3A2733; border-radius: 7px;
                padding: 3px 7px; font-size: 9px; font-weight: 750;
            }
            QLabel#alertAnchor {
                color: #FFF6F8; font-size: 13px; font-weight: 850;
            }
            QLabel#alertTitle { color: #E7CED8; font-size: 11px; }
            QLabel#alertMeta { color: #9E7988; font-size: 9px; }
            QFrame#composer {
                background: #202A3D; border: 1px solid #35415A;
                border-radius: 20px;
            }
            QLineEdit {
                color: #F7F5FF; background: transparent; border: none;
                padding: 9px 3px; font-size: 13px;
            }
            QLineEdit { placeholder-text-color: #8995AA; }
            QLineEdit:disabled { color: #778197; }
            QPushButton#toolButton {
                color: #7BE0D0; background: #2A354B; border: none;
                border-radius: 15px; font-size: 16px; font-weight: 500;
            }
            QPushButton#toolButton:hover { background: #34425D; }
            QPushButton#voiceButton {
                color: #AEB8CC; background: #2A354B; border: none;
                border-radius: 15px; font-size: 14px;
            }
            QPushButton#voiceButton:pressed {
                color: #101624; background: #FFB7A7;
            }
            QPushButton#sendButton {
                color: #101624; background: #7BE0D0; border: none;
                border-radius: 15px; font-size: 16px; font-weight: 900;
            }
            QPushButton#sendButton:hover { background: #95EBDD; }
            QPushButton#sendButton:disabled { background: #465266; color: #788297; }
            QLabel#finePrint { color: #667187; font-size: 9px; }
            QFrame#welcomeCard {
                background: #171F30; border: 1px solid #2E394F;
                border-radius: 20px;
            }
            QLabel#welcomeTitle { color: white; font-size: 18px; font-weight: 850; }
            QLabel#welcomeCopy { color: #98A2B8; font-size: 11px; }
            QPushButton#suggestion {
                color: #C9D1E1; background: #202A3D; border: 1px solid #35415A;
                border-radius: 13px; padding: 7px 10px; font-size: 10px;
            }
            QPushButton#suggestion:hover {
                color: white; border-color: #7BE0D0; background: #253148;
            }
            QPushButton#memoryDetailsButton,
            QPushButton#editResendButton {
                color: #7BE0D0; background: transparent; border: none;
                text-align: left; padding: 3px 0; font-size: 9px;
            }
            QLabel#memoryDetails, QLabel#chatMetrics {
                color: #98A2B8; font-size: 9px;
            }
            QMenu {
                background: #171F30; color: #DDE3EE; border: 1px solid #344057;
                border-radius: 9px; padding: 6px;
            }
            QMenu::item { padding: 8px 18px; border-radius: 6px; }
            QMenu::item:selected { background: #263149; color: #7BE0D0; }
            QMenu::item:disabled { color: #657086; }
        """

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        self.shell = BubbleShell()
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(44)
        shadow.setColor(QColor(3, 8, 18, 135))
        shadow.setOffset(0, 12)
        self.shell.setGraphicsEffect(shadow)
        root.addWidget(self.shell)

        card = QVBoxLayout(self.shell)
        card.setContentsMargins(20, 18, 20, 20)
        card.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(9)
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(42, 42)
        self.avatar_label.setPixmap(_avatar_pixmap(self._avatar_path, 42))
        title_stack = QVBoxLayout()
        title_stack.setSpacing(0)
        self.brand_label = QLabel(self._character_name)
        self.brand_label.setObjectName("brand")
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        dot = QLabel("●")
        dot.setObjectName("statusDot")
        self.status_label = QLabel("待命")
        self.status_label.setObjectName("characterName")
        status_row.addWidget(dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        title_stack.addWidget(self.brand_label)
        title_stack.addLayout(status_row)

        studio_btn = QPushButton("伙伴工坊")
        studio_btn.setObjectName("headerButton")
        studio_btn.setFixedHeight(30)
        studio_btn.clicked.connect(self.pet_lab_requested)
        settings_btn = QPushButton("•••")
        settings_btn.setObjectName("headerButton")
        settings_btn.setFixedSize(38, 30)
        settings_btn.setToolTip("设置")
        settings_btn.clicked.connect(self.settings_requested)
        close_btn = QPushButton("×")
        close_btn.setObjectName("headerButton")
        close_btn.setFixedSize(32, 30)
        close_btn.clicked.connect(self.hide)

        header.addWidget(self.avatar_label)
        header.addLayout(title_stack)
        header.addStretch()
        header.addWidget(studio_btn)
        header.addWidget(settings_btn)
        header.addWidget(close_btn)
        card.addLayout(header)

        self.separator = QFrame()
        self.separator.setFixedHeight(1)
        card.addWidget(self.separator)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.messages_host = QWidget()
        self.messages_host.setObjectName("messagesHost")
        self.messages_layout = QVBoxLayout(self.messages_host)
        self.messages_layout.setContentsMargins(1, 5, 5, 5)
        self.messages_layout.setSpacing(4)
        self.scroll.setWidget(self.messages_host)
        card.addWidget(self.scroll, 1)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_row = QHBoxLayout(composer)
        composer_row.setContentsMargins(7, 4, 7, 4)
        composer_row.setSpacing(7)
        self.tool_btn = QPushButton("+")
        self.tool_btn.setObjectName("toolButton")
        self.tool_btn.setFixedSize(30, 30)
        self.tool_btn.setToolTip("快捷工具")
        self.tool_btn.clicked.connect(self._show_tool_menu)
        self.voice_btn = QPushButton("●")
        self.voice_btn.setObjectName("voiceButton")
        self.voice_btn.setFixedSize(30, 30)
        self.voice_btn.setToolTip("按住说话 · 松开停止")
        self.voice_btn.pressed.connect(self.push_to_talk_pressed)
        self.voice_btn.released.connect(self.push_to_talk_released)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("问问山山，或让她帮你做点什么…")
        self.input_field.returnPressed.connect(self._on_send_click)
        self.send_btn = QPushButton("↑")
        self.send_btn.setObjectName("sendButton")
        self.send_btn.setFixedSize(30, 30)
        self.send_btn.clicked.connect(self._on_send_click)
        self.retry_btn = QPushButton("↻")
        self.retry_btn.setObjectName("sendButton")
        self.retry_btn.setFixedSize(30, 30)
        self.retry_btn.setToolTip("重试上一条消息")
        self.retry_btn.clicked.connect(self.retry_requested)
        self.retry_btn.hide()
        self.stop_btn = QPushButton("■")
        self.stop_btn.setObjectName("sendButton")
        self.stop_btn.setFixedSize(30, 30)
        self.stop_btn.setToolTip("停止生成")
        self.stop_btn.clicked.connect(self.stop_requested)
        self.stop_btn.hide()
        composer_row.addWidget(self.tool_btn)
        composer_row.addWidget(self.voice_btn)
        composer_row.addWidget(self.input_field, 1)
        composer_row.addWidget(self.retry_btn)
        composer_row.addWidget(self.stop_btn)
        composer_row.addWidget(self.send_btn)
        card.addWidget(composer)

        self.mic_status_label = QLabel(
            "麦克风关闭 · 按住圆点才会录音"
        )
        self.mic_status_label.setObjectName("finePrint")
        self.mic_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.addWidget(self.mic_status_label)
        fine_print = QLabel("AI 可调用已启用的本地工具 · 按回车发送")
        fine_print.setObjectName("finePrint")
        fine_print.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.addWidget(fine_print)

        # 兼容旧调用与安全转义测试；不参与可见界面。
        self.chat_history = QTextBrowser(self)
        self.chat_history.hide()
        self.apply_theme()

    def apply_theme(self):
        theme = (
            resolved_theme(self.config_manager)
            if self.config_manager is not None
            else "dark"
        )
        light = """
            QLabel { color: #172033; }
            QLabel#brand { color: #172033; }
            QLabel#characterName { color: #596579; }
            QPushButton#headerButton {
                color: #46566A; background: #E8EDF4;
            }
            QPushButton#headerButton:hover { background: #DDE5EE; color: #172033; }
            QScrollBar::handle:vertical { background: #C2CCDA; }
            QFrame#assistantBubble {
                background: #FFFFFF; border-color: #D9E1EC;
            }
            QLabel#messageAuthor { color: #16897D; }
            QLabel#userMessageAuthor { color: #E8E4FF; }
            QLabel#messageText { color: #172033; }
            QFrame#userBubble { background: #6654E8; border-color: #7868EC; }
            QFrame#userBubble QLabel#messageText { color: white; }
            QLabel#userAvatar { color: #14322D; background: #78DDD0; }
            QLabel#toolPill {
                color: #46566A; background: #E9EEF5; border-color: #D5DEE9;
            }
            QLabel#dateDivider { color: #667085; }
            QLabel#errorPill {
                color: #A43B29; background: #FFF0EC; border-color: #F2C9BF;
            }
            QFrame#alertCard {
                background: #FFF4F6; border-color: #E8BCC7;
            }
            QLabel#liveBadge { color: white; background: #E84E64; }
            QLabel#platformBadge { color: #A43D54; background: #F9E2E8; }
            QLabel#alertAnchor { color: #522332; }
            QLabel#alertTitle { color: #754556; }
            QLabel#alertMeta { color: #A0707F; }
            QFrame#composer {
                background: #FFFFFF; border-color: #CBD5E1;
            }
            QLineEdit { color: #172033; }
            QLineEdit { placeholder-text-color: #596579; }
            QPushButton#toolButton { color: #16897D; background: #E8EDF4; }
            QPushButton#sendButton { color: #10241F; background: #6ED8C9; }
            QLabel#finePrint { color: #596579; }
            QFrame#welcomeCard {
                background: #FFFFFF; border-color: #D9E1EC;
            }
            QLabel#welcomeTitle { color: #172033; }
            QLabel#welcomeCopy { color: #596579; }
            QPushButton#suggestion {
                color: #344054; background: #F1F4F8; border-color: #D8E0EA;
            }
            QMenu {
                background: #FFFFFF; color: #344054; border-color: #D7DFEA;
            }
            QMenu::item:selected { background: #EAF7F5; color: #16897D; }
        """
        self.setStyleSheet(self._base_style + (light if theme == "light" else ""))
        self.shell.set_theme(theme)
        self.separator.setStyleSheet(
            "background:#DDE4ED; border:none;"
            if theme == "light"
            else "background:#293349; border:none;"
        )

    def set_character(self, name: str, preview_path=None):
        self._character_name = name or "山山"
        candidate = Path(preview_path) if preview_path else None
        if candidate and candidate.is_file():
            self._avatar_path = candidate
        else:
            self._avatar_path = resource_path("assets/pixkin/pip-avatar.png")
        self.avatar_label.setPixmap(_avatar_pixmap(self._avatar_path, 42))
        self.brand_label.setText(self._character_name)
        self.setWindowTitle(f"{self._character_name} · 对话")
        self.input_field.setPlaceholderText(
            f"问问{self._character_name}，或让它帮你做点什么…"
        )
        self._render_messages()

    def set_user_profile(self, name: str, avatar_path=None):
        self._user_name = str(name or "").strip()[:20] or "我"
        candidate = Path(avatar_path) if avatar_path else None
        self._user_avatar_path = (
            str(candidate)
            if (
                candidate
                and candidate.is_file()
                and not QPixmap(str(candidate)).isNull()
            )
            else ""
        )
        self._render_messages()

    def set_tool_schemas(self, schemas):
        self._available_tool_names = {
            function.get("name")
            for schema in schemas or []
            if isinstance(schema, dict)
            and isinstance((function := schema.get("function")), dict)
            and function.get("name")
        }
        self.tool_btn.setToolTip(
            f"快捷工具 · {len(self._available_tool_names)} 项能力"
            if self._available_tool_names
            else "快捷工具"
        )

    def append_message(self, role: str, content: str, metadata=None):
        self._stream_update_timer.stop()
        if self._stream_index is not None:
            stream_item = self._messages[self._stream_index]
            if not stream_item["content"] and role != "assistant":
                self._messages.pop(self._stream_index)
        self._messages.append({
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            ),
        })
        self._stream_index = None
        self._render_messages()
        return self._messages[-1]

    def start_assistant_message(self):
        self._stream_update_timer.stop()
        self._messages.append({
            "role": "assistant",
            "content": "",
            "metadata": {},
            "created_at": datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            ),
        })
        self._stream_index = len(self._messages) - 1
        self._render_messages()

    def load_messages(self, messages):
        self._stream_update_timer.stop()
        self._stream_index = None
        self._stream_row = None
        self._messages = [
            {
                "role": str(item.get("role") or "system"),
                "content": str(item.get("content") or ""),
                "metadata": (
                    item.get("metadata")
                    if isinstance(item.get("metadata"), dict)
                    else {}
                ),
                "created_at": str(item.get("created_at") or ""),
            }
            for item in messages or []
        ]
        self._render_messages()

    def message_snapshot(self):
        return [dict(item) for item in self._messages]

    def append_chunk(self, chunk: str):
        if self._stream_index is None:
            self.start_assistant_message()
        self._messages[self._stream_index]["content"] += chunk
        if not self._stream_update_timer.isActive():
            self._stream_update_timer.start()

    def complete_stream(self, metadata=None):
        self._stream_update_timer.stop()
        self._flush_stream_update()
        stream_row = self._stream_row
        if (
            self._stream_index is not None
            and isinstance(metadata, dict)
        ):
            self._messages[self._stream_index]["metadata"] = dict(metadata)
            if stream_row is not None:
                stream_row.set_metadata(metadata)
        self._stream_index = None
        self._stream_row = None
        self._render_transcript()
        self.messages_layout.invalidate()
        self.messages_host.updateGeometry()
        self._scroll_timer.stop()
        self._scroll_to_bottom()
        self._schedule_scroll_to_bottom()

    def cancel_stream(self):
        self._stream_update_timer.stop()
        if self._stream_index is not None:
            if not self._messages[self._stream_index]["content"]:
                self._messages.pop(self._stream_index)
            self._stream_index = None
            self._render_messages()

    def set_busy(self, busy: bool):
        if busy:
            self._focus_after_response = (
                self.isActiveWindow() or self.input_field.hasFocus()
            )
        self.input_field.setDisabled(busy)
        self.send_btn.setDisabled(busy)
        self.tool_btn.setDisabled(busy)
        self.voice_btn.setDisabled(busy)
        self.retry_btn.setDisabled(busy)
        self.stop_btn.setVisible(busy)
        self.send_btn.setVisible(not busy)
        state = "思考中…" if busy else "待命"
        self.status_label.setText(state)
        if not busy and self._focus_after_response:
            QTimer.singleShot(0, self._restore_input_focus)

    def set_microphone_status(
        self,
        text: str,
        *,
        enabled: bool = True,
    ):
        self.mic_status_label.setText(str(text))
        self.voice_btn.setEnabled(
            bool(enabled) and self.input_field.isEnabled()
        )

    def insert_voice_text(self, text: str):
        value = str(text or "").strip()
        if not value:
            return
        current = self.input_field.text().strip()
        self.input_field.setText(
            f"{current} {value}".strip() if current else value
        )
        self.input_field.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_retry_available(self, available: bool) -> None:
        self.retry_btn.setVisible(bool(available))

    def show_alert_bubble(self, title: str, text: str):
        clean = text.replace("<br/>", "\n").replace("<b>", "").replace("</b>", "")
        self.append_message(
            "alert",
            f"{title}\n{clean}",
            {
                "headline": title,
                "body": clean,
                "meta": "刚刚收到提醒",
            },
        )
        self.show()
        self.raise_()

    def show_live_alert(
        self, platform_name: str, anchor_name: str, title: str
    ):
        live_title = str(title or "").strip() or "主播正在直播"
        message = self.append_message(
            "alert",
            f"{anchor_name} 开播了\n{live_title}",
            {
                "platform": platform_name,
                "headline": f"{anchor_name} 开播了",
                "body": live_title,
                "meta": "刚刚检测到开播 · 点击桌宠继续对话",
            },
        )
        self.show()
        self.raise_()
        return message

    def _restore_input_focus(self):
        self._focus_after_response = False
        if not self.isVisible() or not self.input_field.isEnabled():
            return
        active = QApplication.activeWindow()
        if active not in (None, self):
            return
        self.input_field.setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear_message_widgets(self):
        self._message_rows = []
        self._stream_row = None
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _welcome_widget(self):
        return WelcomeMessageCard(
            self._character_name,
            self._avatar_path,
            self._send_prompt,
        )

    def _render_messages(self):
        self._clear_message_widgets()
        if not self._messages:
            self.messages_layout.addStretch()
            self.messages_layout.addWidget(self._welcome_widget())
            self.messages_layout.addStretch()
        else:
            previous_day = None
            for index, item in enumerate(self._messages):
                created_at = str(item.get("created_at") or "")
                day = created_at[:10] if len(created_at) >= 10 else ""
                if day and day != previous_day:
                    divider = QLabel(self._friendly_day(day))
                    divider.setObjectName("dateDivider")
                    divider.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.messages_layout.addWidget(divider)
                    previous_day = day
                message = MessageRow(
                    item["role"],
                    item["content"] or "•••",
                    self._avatar_path,
                    self._character_name,
                    self._user_avatar_path,
                    self._user_name,
                    item.get("metadata"),
                    self._edit_and_resend,
                )
                self._message_rows.append(message)
                self.messages_layout.addWidget(message)
                if index == self._stream_index:
                    self._stream_row = message
            self.messages_layout.addStretch()
        self._render_transcript()
        self._schedule_scroll_to_bottom()

    def _edit_and_resend(self, content: str):
        if not self.input_field.isEnabled():
            return
        self.input_field.setText(str(content))
        self.input_field.setFocus(Qt.FocusReason.OtherFocusReason)
        self.input_field.selectAll()

    @staticmethod
    def _friendly_day(day: str) -> str:
        return friendly_day_label(day)

    def _flush_stream_update(self):
        if self._stream_index is None:
            return
        if not 0 <= self._stream_index < len(self._messages):
            return
        if self._stream_row is None:
            self._render_messages()
            return
        content = self._messages[self._stream_index]["content"]
        self._stream_row.set_content(content)
        self.messages_layout.invalidate()
        self.messages_host.updateGeometry()
        self._schedule_scroll_to_bottom()

    def _render_transcript(self):
        self.chat_history.setHtml(render_transcript_html(self._messages))

    def _scroll_to_bottom(self):
        self.messages_layout.activate()
        self.messages_host.adjustSize()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _schedule_scroll_to_bottom(self):
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def closeEvent(self, event):
        self._stream_update_timer.stop()
        self._scroll_timer.stop()
        super().closeEvent(event)

    def _build_tool_menu(self):
        menu = QMenu(self)
        heading = QAction(
            f"✨ 和{self._character_name}玩点什么",
            menu,
        )
        heading.setEnabled(False)
        menu.addAction(heading)
        menu.addSeparator()

        for label, mode, payload in (
            ("💭 开启一段新话题", "new_session", None),
            ("🕰 打开聊天时光胶囊", "history", None),
            (
                "🎲 来个随机脑洞",
                "random",
                (
                    "给我一个一分钟就能玩的奇怪脑洞挑战。",
                    "我们来玩一句话世界观接龙，你先开始。",
                    "随机选两个完全无关的东西，帮我发明一个新玩意。",
                    "给今天设计一个荒诞但可完成的小任务。",
                ),
            ),
        ):
            self._add_menu_action(menu, label, mode, payload)
        for extension in self._gameplay_extensions():
            self._add_menu_action(
                menu,
                f"{extension.icon} {extension.name}",
                "send",
                extension.prompt,
            )
        self._add_menu_action(
            menu,
            "⚙ 编辑玩法与插件…",
            "settings",
            None,
        )

        toolbox = menu.addMenu(
            f"🧰 实用百宝箱 · {len(self._available_tool_names)} 项"
        )
        shortcuts = (
            (
                "list_available_tools", "看看百宝箱里有什么",
                "请调用 list_available_tools 工具，介绍目前可用的能力。", False,
            ),
            (
                "get_current_time", "查询当前时间",
                "请调用 get_current_time 工具告诉我当前时间。", False,
            ),
            (
                "get_system_info", "查看系统信息",
                "请调用 get_system_info 工具查看这台电脑的系统信息。", False,
            ),
            (
                "get_disk_usage", "查看磁盘空间",
                "请调用 get_disk_usage 工具查看磁盘空间。", False,
            ),
            (
                "calculate_expression", "神算子：计算算式…",
                "请使用 calculate_expression 工具计算：", True,
            ),
            (
                "open_url", "传送门：打开网页…",
                "请使用 open_url 工具打开这个网页：https://", True,
            ),
            (
                "open_application", "打开计算器",
                "请调用 open_application 工具打开计算器。", False,
            ),
            (
                "open_application", "打开记事本",
                "请调用 open_application 工具打开记事本。", False,
            ),
            (
                "open_application", "打开画图",
                "请调用 open_application 工具打开画图。", False,
            ),
            (
                "open_application", "打开文件管理器",
                "请调用 open_application 工具打开文件资源管理器。", False,
            ),
        )
        added = False
        for tool_name, label, prompt, prefill in shortcuts:
            if tool_name not in self._available_tool_names:
                continue
            self._add_menu_action(
                toolbox,
                label,
                "prefill" if prefill else "send",
                prompt,
            )
            added = True
        if not added:
            unavailable = QAction("AI 工具尚未连接", toolbox)
            unavailable.setEnabled(False)
            toolbox.addAction(unavailable)
        menu.addSeparator()
        self._add_menu_action(menu, "🥚 去伙伴工坊孵化新朋友", "studio", None)
        return menu

    def _add_menu_action(self, menu, label, mode, payload):
        action = QAction(label, menu)
        action.setData((mode, payload))
        action.triggered.connect(self._on_tool_action_triggered)
        menu.addAction(action)
        return action

    def _show_tool_menu(self):
        menu = self._build_tool_menu()
        menu.exec(self.tool_btn.mapToGlobal(self.tool_btn.rect().bottomLeft()))

    def _on_tool_action_triggered(self):
        action = self.sender()
        if not isinstance(action, QAction):
            return
        mode, prompt = action.data() or (None, None)
        if mode == "prefill":
            self._prefill_prompt(prompt)
        elif mode == "send":
            self._send_prompt(prompt)
        elif mode == "random":
            self._send_prompt(random.choice(prompt))
        elif mode == "history":
            self.history_requested.emit()
        elif mode == "new_session":
            self.new_session_requested.emit()
        elif mode == "studio":
            self.pet_lab_requested.emit()
        elif mode == "settings":
            self.settings_requested.emit()

    def _gameplay_extensions(self):
        config = {}
        plugin_root = user_data_dir() / "plugins"
        if self.config_manager is not None:
            raw = self.config_manager.get("extensions", default={})
            if isinstance(raw, dict):
                config = raw
            config_path = getattr(
                self.config_manager, "config_path", ""
            )
            if config_path:
                plugin_root = Path(config_path).resolve().parent / "plugins"
        service = ExtensionService(plugin_root)
        try:
            return service.available_gameplay(
                config.get("gameplay"),
                config.get("enabled_plugins", []),
            )
        except ExtensionManifestError:
            return ExtensionService(plugin_root).available_gameplay(
                None,
                [],
            )

    def _prefill_prompt(self, value: str):
        if not self.input_field.isEnabled():
            return
        self.input_field.setText(value)
        self.input_field.setFocus(Qt.FocusReason.OtherFocusReason)
        self.input_field.setCursorPosition(len(value))

    def _send_prompt(self, value: str):
        if not self.input_field.isEnabled():
            return
        self.input_field.setText(value)
        self._on_send_click()

    def _on_send_click(self):
        text = self.input_field.text().strip()
        if text and self.input_field.isEnabled():
            self.append_message("user", text)
            self.input_field.clear()
            self.send_message_signal.emit(text)

    def showEvent(self, event):
        super().showEvent(event)
        self.input_field.setFocus()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 88:
            self._drag_origin = event.globalPosition().toPoint()
            self._window_origin = self.pos()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton and not self._drag_origin.isNull():
            self.move(
                self._window_origin
                + event.globalPosition().toPoint()
                - self._drag_origin
            )
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_origin = QPoint()
        super().mouseReleaseEvent(event)
