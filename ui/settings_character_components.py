"""Character list and detail controls for the settings window."""

from __future__ import annotations

from typing import Any, Iterable

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.character_package import (
    BUILTIN_PACKAGE_IDS,
    DEFAULT_PACKAGE_ID,
)


BUILTIN_PACKAGE_ORDER = ("shanshan", "linlin", "pip")
REPLACED_LEGACY_PACKAGES = {
    "default-assistant": "shanshan",
    "pixkin-pip": "pip",
}


def fitted_character_pixmap(path: Any, size: int) -> QPixmap:
    """Scale by non-transparent content so mixed canvases align."""
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
        source = source.copy(
            left,
            top,
            right - left + 1,
            bottom - top + 1,
        )

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


class CharacterManagerPanel(QWidget):
    """Render installed packages and enforce built-in UI protections."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._packages: dict[str, Any] = {}
        self._active_id = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
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
            self._on_selected
        )
        layout.addWidget(self.character_list)

        detail = QVBoxLayout()
        detail.setSpacing(6)
        preview_row = QHBoxLayout()
        self.character_preview = QLabel("暂无\n图片")
        self.character_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.character_preview.setFixedSize(82, 82)
        self.character_preview.setObjectName("characterPreview")
        meta = QVBoxLayout()
        self.character_name = QLabel("请选择角色")
        self.character_name.setObjectName("cardTitle")
        self.character_meta = QLabel("")
        self.character_meta.setObjectName("muted")
        self.character_description = QLabel("")
        self.character_description.setObjectName("muted")
        self.character_description.setWordWrap(True)
        meta.addWidget(self.character_name)
        meta.addWidget(self.character_meta)
        meta.addWidget(self.character_description)
        meta.addStretch()
        preview_row.addWidget(self.character_preview)
        preview_row.addLayout(meta, 1)
        detail.addLayout(preview_row)

        selected_actions = QHBoxLayout()
        self.activate_btn = QPushButton("设为当前")
        self.activate_btn.setObjectName("primary")
        self.rename_btn = QPushButton("重命名")
        self.rename_btn.setObjectName("secondary")
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("danger")
        selected_actions.addWidget(self.activate_btn)
        selected_actions.addWidget(self.rename_btn)
        selected_actions.addWidget(self.delete_btn)
        selected_actions.addStretch()
        detail.addLayout(selected_actions)

        add_actions = QHBoxLayout()
        self.import_btn = QPushButton("＋ 导入 ZIP")
        self.import_btn.setObjectName("secondary")
        self.inspect_btn = QPushButton("检查 ZIP")
        self.inspect_btn.setObjectName("secondary")
        self.hatch_btn = QPushButton("伙伴工坊")
        self.hatch_btn.setObjectName("secondary")
        add_actions.addWidget(self.import_btn)
        add_actions.addWidget(self.inspect_btn)
        add_actions.addWidget(self.hatch_btn)
        add_actions.addStretch()
        detail.addLayout(add_actions)
        layout.addLayout(detail, 1)
        self.show_details(None)

    def refresh(
        self,
        packages: Iterable[Any],
        *,
        active_id: str = "",
        selected_id: str | None = None,
    ):
        all_packages = list(packages)
        package_ids = {
            package.package_id for package in all_packages
        }
        visible = [
            package
            for package in all_packages
            if not (
                package.package_id in REPLACED_LEGACY_PACKAGES
                and REPLACED_LEGACY_PACKAGES[
                    package.package_id
                ]
                in package_ids
            )
        ]
        builtin_rank = {
            package_id: index
            for index, package_id in enumerate(
                BUILTIN_PACKAGE_ORDER
            )
        }
        ordered = sorted(
            visible,
            key=lambda package: (
                0 if package.package_id in builtin_rank else 1,
                builtin_rank.get(package.package_id, 0),
                package.name.casefold(),
                package.package_id,
            ),
        )
        self._packages = {
            package.package_id: package for package in ordered
        }
        self._active_id = active_id
        target_id = selected_id or active_id
        self.character_list.blockSignals(True)
        self.character_list.clear()
        selected_row = 0
        for row, package in enumerate(ordered):
            badges = self._badges(package)
            subtitle = " · ".join(badges) or package.package_id
            item = QListWidgetItem(
                f"{package.name}\n{subtitle}"
            )
            item.setSizeHint(QSize(250, 54))
            item.setData(
                Qt.ItemDataRole.UserRole,
                package.package_id,
            )
            if package.preview and package.preview.is_file():
                item.setIcon(
                    QIcon(
                        fitted_character_pixmap(
                            package.preview,
                            44,
                        )
                    )
                )
            self.character_list.addItem(item)
            if package.package_id == target_id:
                selected_row = row
        self.character_list.blockSignals(False)
        if ordered:
            self.character_list.setCurrentRow(selected_row)
            self.show_details(self.selected_package())
        else:
            self.show_details(None)

    def selected_package(self):
        item = self.character_list.currentItem()
        if item is None:
            return None
        package_id = str(
            item.data(Qt.ItemDataRole.UserRole) or ""
        )
        return self._packages.get(package_id)

    def show_details(self, package: Any | None):
        self.character_preview.clear()
        self.character_preview.setText("暂无\n图片")
        if package is None:
            self.character_name.setText("没有已安装角色")
            self.character_meta.clear()
            self.character_description.clear()
            self.activate_btn.setDisabled(True)
            self.rename_btn.setDisabled(True)
            self.delete_btn.setDisabled(True)
            return
        is_active = package.package_id == self._active_id
        self.character_name.setText(package.name)
        badges = self._badges(package)
        badge_text = (
            f"  ·  {' · '.join(badges)}" if badges else ""
        )
        self.character_meta.setText(
            f"版本 {package.version}  ·  {package.author}{badge_text}"
        )
        self.character_description.setText(package.description)
        self.activate_btn.setDisabled(is_active)
        protected = package.package_id in BUILTIN_PACKAGE_IDS
        self.rename_btn.setDisabled(protected)
        self.rename_btn.setToolTip(
            "内置角色不可重命名"
            if protected
            else "重命名选中的角色"
        )
        self.delete_btn.setDisabled(protected)
        self.delete_btn.setToolTip(
            "内置角色不可删除"
            if protected
            else "删除选中的角色"
        )
        if package.preview and package.preview.is_file():
            pixmap = fitted_character_pixmap(
                package.preview,
                84,
            )
            if not pixmap.isNull():
                self.character_preview.setPixmap(pixmap)

    def _on_selected(self, current, _previous):
        self.show_details(
            self.selected_package() if current else None
        )

    def _badges(self, package: Any) -> list[str]:
        badges = []
        if package.package_id == DEFAULT_PACKAGE_ID:
            badges.append("默认角色")
        elif package.package_id in BUILTIN_PACKAGE_IDS:
            badges.append("内置角色")
        if package.package_id == self._active_id:
            badges.append("当前使用")
        return badges
