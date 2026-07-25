import html
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRectF, QTimer
from PyQt6.QtGui import (
    QAction, QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QTextBrowser,
    QVBoxLayout, QWidget,
)

from core.paths import resource_path
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


def _avatar_pixmap(path, size: int) -> QPixmap:
    source = QPixmap(str(path)) if path else QPixmap()
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#273248"))
    painter.drawEllipse(0, 0, size, size)
    if not source.isNull():
        scaled = source.scaled(
            size - 4, size - 4,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.setClipPath(
            _circle_path(size - 4, 2)
        )
        painter.drawPixmap(
            (size - scaled.width()) // 2,
            (size - scaled.height()) // 2,
            scaled,
        )
    painter.end()
    return result


def _user_avatar_pixmap(path, size: int) -> QPixmap:
    source = QPixmap(str(path)) if path else QPixmap()
    if source.isNull():
        return QPixmap()
    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(_circle_path(size))
    painter.drawPixmap(0, 0, scaled.copy(x, y, size, size))
    painter.end()
    return result


def _circle_path(diameter: int, offset: int = 0):
    path = QPainterPath()
    path.addEllipse(offset, offset, diameter, diameter)
    return path


class MessageRow(QWidget):
    def __init__(
        self,
        role: str,
        content: str,
        avatar_path=None,
        author_name="山山",
        user_avatar_path=None,
        user_name="我",
        metadata=None,
        parent=None,
    ):
        super().__init__(parent)
        metadata = metadata or {}
        self.text_label = None
        self.author_label = None
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 4, 2, 4)
        row.setSpacing(9)

        if role in {"system", "error"}:
            pill = QLabel(content)
            pill.setTextFormat(Qt.TextFormat.PlainText)
            pill.setWordWrap(True)
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pill.setObjectName("errorPill" if role == "error" else "toolPill")
            pill.setMaximumWidth(330)
            self.text_label = pill
            row.addStretch()
            row.addWidget(pill)
            row.addStretch()
            return

        if role == "alert":
            card = QFrame()
            card.setObjectName("alertCard")
            card.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 13, 15, 13)
            card_layout.setSpacing(7)
            badge_row = QHBoxLayout()
            badge_row.setSpacing(7)
            live_badge = QLabel("● LIVE")
            live_badge.setObjectName("liveBadge")
            platform_badge = QLabel(metadata.get("platform") or "直播提醒")
            platform_badge.setObjectName("platformBadge")
            badge_row.addWidget(live_badge)
            badge_row.addWidget(platform_badge)
            badge_row.addStretch()
            headline = QLabel(metadata.get("headline") or "关注的主播开播了")
            headline.setObjectName("alertAnchor")
            headline.setTextFormat(Qt.TextFormat.PlainText)
            text = QLabel(metadata.get("body") or content)
            text.setObjectName("alertTitle")
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setWordWrap(True)
            text.setMinimumHeight(
                max(
                    36,
                    QFontMetrics(text.font()).boundingRect(
                        0, 0, 300, 1000,
                        int(Qt.TextFlag.TextWordWrap),
                        text.text(),
                    ).height(),
                )
            )
            meta = QLabel(metadata.get("meta") or "刚刚检测到开播")
            meta.setObjectName("alertMeta")
            self.text_label = text
            card_layout.addLayout(badge_row)
            card_layout.addWidget(headline)
            card_layout.addWidget(text)
            card_layout.addWidget(meta)
            row.addWidget(card)
            return

        avatar = QLabel()
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if role == "user":
            avatar.setObjectName("userAvatar")
            user_source = (
                QPixmap(str(user_avatar_path)) if user_avatar_path else QPixmap()
            )
            if user_source.isNull():
                avatar.setText((user_name or "我")[:2])
            else:
                avatar.setPixmap(_user_avatar_pixmap(user_avatar_path, 34))
        else:
            avatar.setPixmap(_avatar_pixmap(avatar_path, 34))

        bubble = QFrame()
        bubble.setObjectName("userBubble" if role == "user" else "assistantBubble")
        bubble.setMaximumWidth(306)
        bubble.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(13, 10, 13, 10)
        bubble_layout.setSpacing(4)
        author = QLabel(user_name if role == "user" else author_name)
        author.setObjectName(
            "userMessageAuthor" if role == "user" else "messageAuthor"
        )
        if role == "user":
            author.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.author_label = author
        bubble_layout.addWidget(author)
        text = QLabel(content or "•••")
        text.setObjectName("messageText")
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        text.setWordWrap(True)
        self.text_label = text
        bubble_layout.addWidget(text)

        if role == "user":
            row.addStretch()
            row.addWidget(bubble)
            row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        else:
            row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(bubble)
            row.addStretch()

    def set_content(self, content: str):
        if self.text_label is None:
            return
        self.text_label.setText(content or "•••")
        self.text_label.updateGeometry()
        self.updateGeometry()


class ChatBubbleWindow(QWidget):
    """Pixkin 悬浮对话气泡：头像、消息气泡、工具与直播提醒。"""

    send_message_signal = pyqtSignal(str)
    settings_requested = pyqtSignal()
    pet_lab_requested = pyqtSignal()

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
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("问问山山，或让她帮你做点什么…")
        self.input_field.returnPressed.connect(self._on_send_click)
        self.send_btn = QPushButton("↑")
        self.send_btn.setObjectName("sendButton")
        self.send_btn.setFixedSize(30, 30)
        self.send_btn.clicked.connect(self._on_send_click)
        composer_row.addWidget(self.tool_btn)
        composer_row.addWidget(self.input_field, 1)
        composer_row.addWidget(self.send_btn)
        card.addWidget(composer)

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
        })
        self._stream_index = None
        self._render_messages()

    def start_assistant_message(self):
        self._stream_update_timer.stop()
        self._messages.append({"role": "assistant", "content": ""})
        self._stream_index = len(self._messages) - 1
        self._render_messages()

    def append_chunk(self, chunk: str):
        if self._stream_index is None:
            self.start_assistant_message()
        self._messages[self._stream_index]["content"] += chunk
        if not self._stream_update_timer.isActive():
            self._stream_update_timer.start()

    def complete_stream(self):
        self._stream_update_timer.stop()
        self._flush_stream_update()
        self._stream_index = None
        self._stream_row = None
        self._render_transcript()
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
        state = "思考中…" if busy else "待命"
        self.status_label.setText(state)
        if not busy and self._focus_after_response:
            QTimer.singleShot(0, self._restore_input_focus)

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
        self.append_message(
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
        card = QFrame()
        card.setObjectName("welcomeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 20, 18, 18)
        layout.setSpacing(8)
        avatar = QLabel()
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setPixmap(_avatar_pixmap(self._avatar_path, 78))
        title = QLabel(f"你好，我是 {self._character_name}")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copy = QLabel("你的桌面 AI 小伙伴。\n能聊天、调用工具，也能孵化新的卡通角色。")
        copy.setObjectName("welcomeCopy")
        copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copy.setWordWrap(True)
        layout.addWidget(avatar)
        layout.addWidget(title)
        layout.addWidget(copy)
        for text, prompt in (
            ("你能做什么？", "介绍一下你能调用的工具"),
            ("打开计算器", "请打开计算器"),
            ("现在几点？", "现在几点了？"),
        ):
            button = QPushButton(text)
            button.setObjectName("suggestion")
            button.clicked.connect(
                lambda _checked=False, value=prompt: self._send_prompt(value)
            )
            layout.addWidget(button)
        return card

    def _render_messages(self):
        self._clear_message_widgets()
        if not self._messages:
            self.messages_layout.addStretch()
            self.messages_layout.addWidget(self._welcome_widget())
            self.messages_layout.addStretch()
        else:
            for index, item in enumerate(self._messages):
                message = MessageRow(
                    item["role"],
                    item["content"] or "•••",
                    self._avatar_path,
                    self._character_name,
                    self._user_avatar_path,
                    self._user_name,
                    item.get("metadata"),
                )
                self._message_rows.append(message)
                self.messages_layout.addWidget(message)
                if index == self._stream_index:
                    self._stream_row = message
            self.messages_layout.addStretch()
        self._render_transcript()
        self._schedule_scroll_to_bottom()

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
        parts = ["<div>"]
        for item in self._messages:
            safe = html.escape(item["content"] or "•••").replace("\n", "<br>")
            parts.append(f"<p><b>{html.escape(item['role'])}</b>: {safe}</p>")
        parts.append("</div>")
        self.chat_history.setHtml("".join(parts))

    def _scroll_to_bottom(self):
        self.messages_layout.activate()
        self.messages_host.adjustSize()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(
            0,
            lambda target=bar: target.setValue(target.maximum()),
        )

    def _schedule_scroll_to_bottom(self):
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _build_tool_menu(self):
        menu = QMenu(self)
        heading = QAction(
            f"AI 工具 · {len(self._available_tool_names)} 项已连接",
            menu,
        )
        heading.setEnabled(False)
        menu.addAction(heading)
        menu.addSeparator()

        shortcuts = (
            (
                "list_available_tools", "查看全部工具",
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
                "calculate_expression", "计算算式…",
                "请使用 calculate_expression 工具计算：", True,
            ),
            (
                "open_url", "打开网页…",
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
            action = QAction(label, menu)
            action.setData(("prefill" if prefill else "send", prompt))
            action.triggered.connect(self._on_tool_action_triggered)
            menu.addAction(action)
            added = True
        if not added:
            unavailable = QAction("AI 工具尚未连接", menu)
            unavailable.setEnabled(False)
            menu.addAction(unavailable)
        menu.addSeparator()
        studio = QAction("孵化新伙伴", menu)
        studio.triggered.connect(self.pet_lab_requested)
        menu.addAction(studio)
        return menu

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
