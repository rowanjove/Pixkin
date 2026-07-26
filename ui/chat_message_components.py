"""Reusable visual components and pure formatting rules for chat messages."""

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def _nonnegative_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _nonnegative_float(value) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


@dataclass(frozen=True)
class ChatMessageView:
    """Normalized, immutable input consumed by message renderers."""

    role: str
    content: str
    metadata: Mapping[str, Any]
    created_at: str

    @classmethod
    def from_mapping(cls, message: Mapping[str, Any]) -> "ChatMessageView":
        metadata = message.get("metadata")
        return cls(
            role=str(message.get("role") or "system"),
            content=str(message.get("content") or ""),
            metadata=metadata if isinstance(metadata, Mapping) else {},
            created_at=str(message.get("created_at") or ""),
        )

    @property
    def display_content(self) -> str:
        return self.content or "•••"

    @property
    def day(self) -> str:
        return self.created_at[:10] if len(self.created_at) >= 10 else ""


def _circle_path(diameter: int, offset: int = 0) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(offset, offset, diameter, diameter)
    return path


def avatar_pixmap(path, size: int) -> QPixmap:
    """Render a character avatar with the existing circular fallback."""

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
            size - 4,
            size - 4,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.setClipPath(_circle_path(size - 4, 2))
        painter.drawPixmap(
            (size - scaled.width()) // 2,
            (size - scaled.height()) // 2,
            scaled,
        )
    painter.end()
    return result


def user_avatar_pixmap(path, size: int) -> QPixmap:
    """Crop a user image to a centered circular avatar."""

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


def friendly_day_label(day: str, *, today: date | None = None) -> str:
    """Return the localized divider label for an ISO calendar day."""

    current_day = today or datetime.now().astimezone().date()
    try:
        value = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return day
    if value == current_day:
        return "今天"
    if (current_day - value).days == 1:
        return "昨天"
    return value.strftime("%Y年%m月%d日")


def render_transcript_html(messages: Sequence[Mapping[str, Any]]) -> str:
    """Render the hidden compatibility transcript with strict HTML escaping."""

    parts = ["<div>"]
    for raw_message in messages:
        message = ChatMessageView.from_mapping(raw_message)
        safe_content = html.escape(message.display_content).replace("\n", "<br>")
        parts.append(
            f"<p><b>{html.escape(message.role)}</b>: {safe_content}</p>"
        )
    parts.append("</div>")
    return "".join(parts)


class MessageRow(QWidget):
    """Render one chat, status, error, or live-alert message."""

    def __init__(
        self,
        role: str,
        content: str,
        avatar_path=None,
        author_name="山山",
        user_avatar_path=None,
        user_name="我",
        metadata=None,
        edit_resend: Callable[[str], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        metadata = metadata or {}
        self._role = role
        self._bubble_layout = None
        self._metadata_rendered: set[str] = set()
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
                        0,
                        0,
                        300,
                        1000,
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
                avatar.setPixmap(user_avatar_pixmap(user_avatar_path, 34))
        else:
            avatar.setPixmap(avatar_pixmap(avatar_path, 34))

        bubble = QFrame()
        bubble.setObjectName("userBubble" if role == "user" else "assistantBubble")
        bubble.setMaximumWidth(306)
        bubble.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(13, 10, 13, 10)
        bubble_layout.setSpacing(4)
        self._bubble_layout = bubble_layout
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
        self.set_metadata(metadata)
        if role == "user" and edit_resend is not None:
            edit_button = QPushButton("编辑并重发")
            edit_button.setObjectName("editResendButton")
            edit_button.clicked.connect(
                lambda _checked=False, value=content: edit_resend(value)
            )
            bubble_layout.addWidget(edit_button)

        if role == "user":
            row.addStretch()
            row.addWidget(bubble)
            row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        else:
            row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(bubble)
            row.addStretch()

    def set_metadata(self, metadata) -> None:
        if (
            self._role != "assistant"
            or self._bubble_layout is None
            or not isinstance(metadata, Mapping)
        ):
            return
        metrics = metadata.get("chat_metrics")
        if (
            "chat_metrics" not in self._metadata_rendered
            and isinstance(metrics, Mapping)
        ):
            latency = _nonnegative_int(metrics.get("latency_ms"))
            input_tokens = _nonnegative_int(metrics.get("input_tokens"))
            output_tokens = _nonnegative_int(
                metrics.get("output_tokens")
            )
            cost = _nonnegative_float(metrics.get("estimated_cost"))
            metrics_label = QLabel(
                f"约 {latency / 1000:.1f}s · "
                f"{input_tokens}+{output_tokens} tokens · "
                f"估算 ¥/计费单位 {cost:.6f}"
            )
            metrics_label.setObjectName("chatMetrics")
            metrics_label.setToolTip(
                "Token 与费用为本地估算；实际账单以模型服务商为准"
            )
            metrics_label.setWordWrap(True)
            self._bubble_layout.addWidget(metrics_label)
            self._metadata_rendered.add("chat_metrics")
        used_memories = metadata.get("used_memories")
        if (
            "used_memories" not in self._metadata_rendered
            and isinstance(used_memories, Sequence)
            and not isinstance(used_memories, (str, bytes))
        ):
            contents = [
                str(item.get("content") or "").strip()
                for item in used_memories
                if isinstance(item, Mapping)
                and str(item.get("content") or "").strip()
            ]
            if contents:
                details = QLabel(
                    "\n".join(f"• {item}" for item in contents)
                )
                details.setObjectName("memoryDetails")
                details.setTextFormat(Qt.TextFormat.PlainText)
                details.setWordWrap(True)
                details.setVisible(False)
                button = QPushButton(
                    f"本次使用 {len(contents)} 条记忆"
                )
                button.setObjectName("memoryDetailsButton")
                button.setCheckable(True)
                button.setToolTip(
                    "只显示本次实际发送给模型的已确认记忆"
                )
                button.toggled.connect(details.setVisible)
                self._bubble_layout.addWidget(button)
                self._bubble_layout.addWidget(details)
                self.memory_details_button = button
                self.memory_details_label = details
            self._metadata_rendered.add("used_memories")

    def set_content(self, content: str):
        if self.text_label is None:
            return
        self.text_label.setText(content or "•••")
        self.text_label.updateGeometry()
        self.updateGeometry()


class WelcomeMessageCard(QFrame):
    """Empty-state card with prompt shortcuts."""

    def __init__(
        self,
        character_name: str,
        avatar_path,
        send_prompt: Callable[[str], None],
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("welcomeCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 18)
        layout.setSpacing(8)
        avatar = QLabel()
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setPixmap(avatar_pixmap(avatar_path, 78))
        title = QLabel(f"你好，我是 {character_name}")
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
                lambda _checked=False, value=prompt: send_prompt(value)
            )
            layout.addWidget(button)
