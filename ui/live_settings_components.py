"""Reusable settings controls for livestream room configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from core.providers.live import ADAPTERS


PLATFORM_LABELS = {
    "bilibili": "B站",
    "douyin": "抖音",
}


@dataclass(frozen=True)
class LiveRoomConfig:
    enabled: bool
    platform: str
    room_id: str
    anchor_name: str
    group: str = "默认"

    def as_dict(self) -> dict[str, Any]:
        value = {
            "enabled": self.enabled,
            "platform": self.platform,
            "room_id": self.room_id,
            "anchor_name": self.anchor_name,
        }
        if self.group != "默认":
            value["group"] = self.group
        return value


class LiveRoomEditor(QWidget):
    """Own the live-room table, supported platforms, and normalized output."""

    def __init__(
        self,
        rooms: Iterable[Mapping[str, object]] = (),
        providers=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.providers = providers or ADAPTERS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["启用", "平台", "房间号 / 链接", "主播名称", "分组"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(0, 64)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(1, 104)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setMinimumHeight(230)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        add = QPushButton("添加直播间")
        add.setObjectName("secondary")
        add.clicked.connect(lambda: self.add_room({}))
        remove = QPushButton("删除选中")
        remove.setObjectName("danger")
        remove.clicked.connect(self.remove_selected)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout.addLayout(buttons)

        for room in rooms:
            self.add_room(room)

    @staticmethod
    def available_platforms(
        providers=None,
    ) -> list[tuple[str, str, str]]:
        platforms = []
        for platform_id, provider in (providers or ADAPTERS).items():
            capabilities = provider.capabilities
            if not capabilities.status_check:
                continue
            note = (
                "支持直播状态检测"
                + (
                    "与安全链接校验"
                    if capabilities.safe_url_validation
                    else ""
                )
            )
            platforms.append(
                (
                    PLATFORM_LABELS.get(platform_id, platform_id),
                    platform_id,
                    note,
                )
            )
        return platforms

    def add_room(self, room: Mapping[str, object]):
        row = self.table.rowCount()
        self.table.insertRow(row)
        enabled = QCheckBox()
        enabled.setChecked(bool(room.get("enabled", True)))
        enabled_wrap = QWidget()
        enabled_layout = QHBoxLayout(enabled_wrap)
        enabled_layout.setContentsMargins(9, 0, 0, 0)
        enabled_layout.addWidget(enabled)
        enabled_layout.addStretch()

        platform = QComboBox()
        platform.setMinimumWidth(86)
        for label, platform_id, note in self.available_platforms(
            self.providers
        ):
            platform.addItem(label, platform_id)
            platform.setItemData(
                platform.count() - 1,
                note,
                role=Qt.ItemDataRole.ToolTipRole,
            )
        index = platform.findData(room.get("platform", "bilibili"))
        platform.setCurrentIndex(max(0, index))

        room_id = QLineEdit(
            str(room.get("room_id") or room.get("room_url") or "")
        )
        room_id.setPlaceholderText("房间号或直播间链接")
        anchor = QLineEdit(str(room.get("anchor_name") or ""))
        anchor.setPlaceholderText("提醒中显示的名称")
        group = QLineEdit(str(room.get("group") or "默认"))
        group.setPlaceholderText("默认")
        self.table.setCellWidget(row, 0, enabled_wrap)
        self.table.setCellWidget(row, 1, platform)
        self.table.setCellWidget(row, 2, room_id)
        self.table.setCellWidget(row, 3, anchor)
        self.table.setCellWidget(row, 4, group)
        self.table.setRowHeight(row, 45)

    def remove_selected(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def room_configs(self) -> list[LiveRoomConfig]:
        rooms = []
        for row in range(self.table.rowCount()):
            enabled_wrap = self.table.cellWidget(row, 0)
            enabled = enabled_wrap.findChild(QCheckBox)
            platform = self.table.cellWidget(row, 1)
            room_id_input = self.table.cellWidget(row, 2)
            anchor_input = self.table.cellWidget(row, 3)
            group_input = self.table.cellWidget(row, 4)
            if not isinstance(platform, QComboBox):
                continue
            if not isinstance(room_id_input, QLineEdit):
                continue
            if not isinstance(anchor_input, QLineEdit):
                continue
            if not isinstance(group_input, QLineEdit):
                continue
            room_id = room_id_input.text().strip()
            if not room_id:
                continue
            rooms.append(
                LiveRoomConfig(
                    enabled=bool(enabled and enabled.isChecked()),
                    platform=str(
                        platform.currentData() or "bilibili"
                    ),
                    room_id=room_id,
                    anchor_name=(
                        anchor_input.text().strip()
                        or "关注的主播"
                    ),
                    group=group_input.text().strip()[:30] or "默认",
                )
            )
        return rooms

    def values(self) -> list[dict[str, Any]]:
        return [room.as_dict() for room in self.room_configs()]
