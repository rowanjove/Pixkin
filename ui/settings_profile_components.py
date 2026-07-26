"""User profile controls shared by the settings window."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def profile_avatar_pixmap(path: str | Path, size: int) -> QPixmap:
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
    clip = QPainterPath()
    clip.addEllipse(0, 0, size, size)
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return canvas


@dataclass(frozen=True)
class UserProfileInput:
    display_name: str
    avatar_source: str


class UserProfileEditor(QWidget):
    """Collect a normalized display name and optional local avatar source."""

    def __init__(
        self,
        *,
        display_name: str = "我",
        avatar_source: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._avatar_source = str(avatar_source or "")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.avatar_preview = QLabel()
        self.avatar_preview.setObjectName("userAvatarPreview")
        self.avatar_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_preview.setFixedSize(56, 56)
        layout.addWidget(self.avatar_preview)

        fields = QVBoxLayout()
        fields.setSpacing(5)
        label = QLabel("昵称")
        label.setObjectName("muted")
        self.name_input = QLineEdit(
            str(display_name or "我")
        )
        self.name_input.setMaxLength(20)
        self.name_input.setPlaceholderText("显示在右侧聊天气泡上方")
        self.name_input.textChanged.connect(
            self._refresh_text_avatar
        )
        fields.addWidget(label)
        fields.addWidget(self.name_input)
        layout.addLayout(fields, 1)

        choose = QPushButton("选择头像")
        choose.setObjectName("secondary")
        choose.clicked.connect(self.choose_avatar)
        clear = QPushButton("使用文字头像")
        clear.setObjectName("link")
        clear.clicked.connect(self.clear_avatar)
        layout.addWidget(choose)
        layout.addWidget(clear)
        self.set_avatar_source(self._avatar_source)

    @property
    def avatar_source(self) -> str:
        return self._avatar_source

    def values(self) -> UserProfileInput:
        return UserProfileInput(
            display_name=(
                self.name_input.text().strip()[:20] or "我"
            ),
            avatar_source=self._avatar_source,
        )

    def set_avatar_source(self, path: str | Path):
        self._avatar_source = str(path or "")
        pixmap = profile_avatar_pixmap(self._avatar_source, 54)
        if pixmap.isNull():
            self.avatar_preview.setPixmap(QPixmap())
            self.avatar_preview.setText(
                (self.name_input.text().strip() or "我")[:2]
            )
        else:
            self.avatar_preview.clear()
            self.avatar_preview.setPixmap(pixmap)

    def choose_avatar(self):
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
                self,
                "无法使用头像",
                "该文件不是受支持或可读取的图片。",
            )
            return
        self.set_avatar_source(selected)

    def clear_avatar(self):
        self.set_avatar_source("")

    def _refresh_text_avatar(self):
        if not self._avatar_source:
            self.set_avatar_source("")
