import html
import json
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextBrowser,
    QVBoxLayout,
)

from ui.theme import resolved_theme


ROLE_LABELS = {
    "user": "我",
    "assistant": "伙伴",
    "system": "工具",
    "error": "错误",
    "alert": "提醒",
}


class HistoryWindow(QDialog):
    """聊天时光胶囊：按角色和日期浏览、导出及清理历史。"""

    history_changed = pyqtSignal()
    new_session_requested = pyqtSignal()

    def __init__(
        self,
        store,
        character_id: str,
        character_name: str,
        session_id: str,
        config_manager=None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.character_id = character_id
        self.character_name = character_name
        self.session_id = session_id
        self.config_manager = config_manager
        self.setWindowTitle("Pixkin · 聊天时光胶囊")
        self.setMinimumSize(760, 560)
        self.resize(820, 620)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self._build_ui()
        self._apply_theme()
        self.refresh()

    def set_context(
        self, character_id: str, character_name: str, session_id: str
    ):
        self.character_id = character_id
        self.character_name = character_name
        self.session_id = session_id
        self.scope_combo.setItemText(0, f"当前角色 · {character_name}")
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        title = QLabel("聊天时光胶囊")
        title.setObjectName("historyTitle")
        hint = QLabel("每个角色拥有独立记忆。按日期回看，也可以导出留念。")
        hint.setObjectName("historyHint")
        root.addWidget(title)
        root.addWidget(hint)

        toolbar = QHBoxLayout()
        self.scope_combo = QComboBox()
        self.scope_combo.addItem(
            f"当前角色 · {self.character_name}", "character"
        )
        self.scope_combo.addItem("全部角色", "all")
        self.scope_combo.currentIndexChanged.connect(self.refresh)
        new_topic = QPushButton("✨ 开启新话题")
        new_topic.setObjectName("primary")
        new_topic.clicked.connect(self.new_session_requested)
        export = QPushButton("导出所见记录")
        export.setObjectName("secondary")
        export.clicked.connect(self._export_visible)
        toolbar.addWidget(self.scope_combo)
        toolbar.addStretch()
        toolbar.addWidget(new_topic)
        toolbar.addWidget(export)
        root.addLayout(toolbar)

        content = QHBoxLayout()
        content.setSpacing(14)
        date_card = QFrame()
        date_card.setObjectName("historyCard")
        date_layout = QVBoxLayout(date_card)
        date_layout.setContentsMargins(12, 12, 12, 12)
        date_title = QLabel("日期")
        date_title.setObjectName("sectionTitle")
        self.date_list = QListWidget()
        self.date_list.setObjectName("historyDates")
        self.date_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.date_list.currentItemChanged.connect(self._render_selected)
        date_layout.addWidget(date_title)
        date_layout.addWidget(self.date_list)
        date_card.setFixedWidth(190)

        transcript_card = QFrame()
        transcript_card.setObjectName("historyCard")
        transcript_layout = QVBoxLayout(transcript_card)
        transcript_layout.setContentsMargins(14, 12, 14, 12)
        self.transcript_title = QLabel("记录")
        self.transcript_title.setObjectName("sectionTitle")
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        self.empty_hint = QLabel("")
        self.empty_hint.setObjectName("historyHint")
        transcript_layout.addWidget(self.transcript_title)
        transcript_layout.addWidget(self.transcript, 1)
        transcript_layout.addWidget(self.empty_hint)
        content.addWidget(date_card)
        content.addWidget(transcript_card, 1)
        root.addLayout(content, 1)

        danger_row = QHBoxLayout()
        self.clear_session_btn = QPushButton("清空本次对话")
        self.clear_session_btn.setObjectName("danger")
        self.clear_session_btn.clicked.connect(self._clear_session)
        self.clear_day_btn = QPushButton("清空所选日期")
        self.clear_day_btn.setObjectName("danger")
        self.clear_day_btn.clicked.connect(self._clear_day)
        clear_all = QPushButton("清空全部历史")
        clear_all.setObjectName("danger")
        clear_all.clicked.connect(self._clear_all)
        close = QPushButton("完成")
        close.setObjectName("secondary")
        close.clicked.connect(self.accept)
        danger_row.addWidget(self.clear_session_btn)
        danger_row.addWidget(self.clear_day_btn)
        danger_row.addWidget(clear_all)
        danger_row.addStretch()
        danger_row.addWidget(close)
        root.addLayout(danger_row)

    def _apply_theme(self):
        light = (
            self.config_manager is None
            or resolved_theme(self.config_manager) == "light"
        )
        if light:
            self.setStyleSheet(
                """
                QDialog { background: #EEF2F7; color: #172033; }
                QLabel { color: #263244; }
                QLabel#historyTitle { font-size: 23px; font-weight: 850; }
                QLabel#historyHint { color: #667085; }
                QLabel#sectionTitle { font-size: 13px; font-weight: 800; }
                QFrame#historyCard {
                    background: white; border: 1px solid #D7DFEA;
                    border-radius: 14px;
                }
                QListWidget, QTextBrowser, QComboBox {
                    background: #F8FAFC; color: #263244;
                    border: 1px solid #D7DFEA; border-radius: 9px;
                    padding: 7px;
                }
                QListWidget::item { padding: 9px; border-radius: 7px; }
                QListWidget::item:selected { background: #6654E8; color: white; }
                QPushButton {
                    border: none; border-radius: 9px;
                    padding: 9px 14px; font-weight: 700;
                }
                QPushButton#primary { background: #6ED8C9; color: #10241F; }
                QPushButton#secondary { background: #E2E8F0; color: #344054; }
                QPushButton#danger { background: #FFF0EC; color: #B43D28; }
                QPushButton:disabled { color: #98A2B3; background: #E8EDF3; }
                """
            )
            return
        self.setStyleSheet(
            """
            QDialog { background: #0D1320; color: #F6F4FF; }
            QLabel { color: #E7ECF5; }
            QLabel#historyTitle { font-size: 23px; font-weight: 850; }
            QLabel#historyHint { color: #8F9AAF; }
            QLabel#sectionTitle { font-size: 13px; font-weight: 800; }
            QFrame#historyCard {
                background: #151E2E; border: 1px solid #2D3850;
                border-radius: 14px;
            }
            QListWidget, QTextBrowser, QComboBox {
                background: #111827; color: #E7ECF5;
                border: 1px solid #303B53; border-radius: 9px;
                padding: 7px;
            }
            QListWidget::item { padding: 9px; border-radius: 7px; }
            QListWidget::item:selected { background: #7357FF; color: white; }
            QPushButton {
                border: none; border-radius: 9px;
                padding: 9px 14px; font-weight: 700;
            }
            QPushButton#primary { background: #7BE0D0; color: #101624; }
            QPushButton#secondary { background: #263249; color: #DDE3EE; }
            QPushButton#danger { background: #35231F; color: #FFB7A7; }
            QPushButton:disabled { color: #657086; background: #202838; }
            """
        )

    def _selected_character_id(self):
        return (
            self.character_id
            if self.scope_combo.currentData() == "character"
            else None
        )

    def _selected_day(self):
        item = self.date_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        selected_day = self._selected_day()
        dates = self.store.list_dates(self._selected_character_id())
        self.date_list.blockSignals(True)
        self.date_list.clear()
        all_item = QListWidgetItem("全部日期")
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self.date_list.addItem(all_item)
        target_row = 0
        for row, entry in enumerate(dates, start=1):
            day = entry["day"]
            try:
                label = datetime.strptime(day, "%Y-%m-%d").strftime(
                    "%Y年%m月%d日"
                )
            except ValueError:
                label = day
            item = QListWidgetItem(
                f"{label}\n{entry['message_count']} 条消息"
            )
            item.setData(Qt.ItemDataRole.UserRole, day)
            self.date_list.addItem(item)
            if day == selected_day:
                target_row = row
        self.date_list.blockSignals(False)
        self.date_list.setCurrentRow(target_row)
        self._render_selected()

    def _visible_messages(self):
        return self.store.list_messages(
            character_id=self._selected_character_id(),
            day=self._selected_day(),
        )

    def _render_selected(self, *_args):
        messages = self._visible_messages()
        day = self._selected_day()
        self.transcript_title.setText(day or "全部记录")
        self.clear_day_btn.setEnabled(bool(day))
        if not messages:
            self.transcript.clear()
            self.empty_hint.setText("这里还没有消息。去和小伙伴聊点什么吧。")
            return
        self.empty_hint.clear()
        blocks = [
            """
            <style>
            body { font-family: "Microsoft YaHei UI"; }
            .entry { margin: 0 0 13px 0; }
            .meta { color: #8792A8; font-size: 11px; }
            .content { margin-top: 3px; white-space: pre-wrap; }
            </style>
            """
        ]
        for item in messages:
            timestamp = item["created_at"].replace("T", " ")[:19]
            who = ROLE_LABELS.get(item["role"], item["role"])
            if item["role"] == "assistant":
                who = item["character_name"]
            blocks.append(
                '<div class="entry">'
                f'<div class="meta">{html.escape(timestamp)} · '
                f'{html.escape(who)}</div>'
                f'<div class="content">{html.escape(item["content"])}</div>'
                "</div>"
            )
        self.transcript.setHtml("".join(blocks))

    def _export_visible(self):
        messages = self._visible_messages()
        if not messages:
            QMessageBox.information(
                self, "没有可导出的记录", "当前筛选范围内还没有消息。"
            )
            return
        day = self._selected_day() or "全部日期"
        destination, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出聊天记录",
            f"Pixkin-聊天记录-{day}.md",
            "Markdown (*.md);;JSON (*.json)",
        )
        if not destination:
            return
        target = Path(destination)
        try:
            if "JSON" in selected_filter or target.suffix.lower() == ".json":
                target.write_text(
                    json.dumps(messages, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                lines = [f"# Pixkin 聊天记录 · {day}", ""]
                for item in messages:
                    who = ROLE_LABELS.get(item["role"], item["role"])
                    if item["role"] == "assistant":
                        who = item["character_name"]
                    timestamp = item["created_at"].replace("T", " ")[:19]
                    lines.extend([
                        f"## {timestamp} · {who}",
                        "",
                        item["content"],
                        "",
                    ])
                target.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(
            self, "导出完成", f"聊天记录已保存到：\n{target}"
        )

    def _clear_session(self):
        answer = QMessageBox.question(
            self,
            "清空本次对话",
            "确定永久删除当前这一次对话吗？其他历史会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_session(self.session_id)
        self.history_changed.emit()
        self.refresh()

    def _clear_day(self):
        day = self._selected_day()
        if not day:
            return
        scope = (
            f"角色“{self.character_name}”"
            if self._selected_character_id()
            else "全部角色"
        )
        answer = QMessageBox.question(
            self,
            "清空所选日期",
            f"确定永久删除 {scope} 在 {day} 的消息吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_date(day, self._selected_character_id())
        self.history_changed.emit()
        self.refresh()

    def _clear_all(self):
        answer = QMessageBox.question(
            self,
            "清空全部历史",
            "确定永久删除所有角色的全部聊天历史吗？此操作无法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.clear_all()
        self.history_changed.emit()
        self.refresh()
