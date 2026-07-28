"""Settings controls for explicit, local long-term memory."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.services.memory_service import MemoryService, MemoryStoreError


class MemorySettingsPanel(QWidget):
    """View, edit, delete and explicitly confirm candidate memories."""

    def __init__(
        self,
        service: MemoryService,
        *,
        user_id: str,
        character_id: str,
        recent_messages=None,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.user_id = str(user_id)
        self.character_id = str(character_id)
        self.recent_messages = list(recent_messages or [])
        self.enable_input = QCheckBox(
            "允许当前用户与角色使用已确认的长期记忆"
        )
        self.enable_input.setChecked(service.enabled)
        self.memory_list = QListWidget()
        self.memory_list.setObjectName("memoryList")
        self.memory_list.currentItemChanged.connect(self._selected)
        self.edit_input = QLineEdit()
        self.edit_input.setPlaceholderText("选择一条记忆后可编辑")
        self.candidate_list = QListWidget()
        self.candidate_list.setObjectName("memoryCandidateList")
        self._build()
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        layout.addWidget(self.enable_input)
        scope = QLabel(
            "记忆按本机用户与当前角色分区；关闭后不会向模型注入任何记忆。"
        )
        scope.setObjectName("muted")
        scope.setWordWrap(True)
        layout.addWidget(scope)
        layout.addWidget(QLabel("已确认记忆"))
        layout.addWidget(self.memory_list, 1)
        layout.addWidget(self.edit_input)
        actions = QHBoxLayout()
        save = QPushButton("保存修改")
        save.setObjectName("secondary")
        save.clicked.connect(self._save_edit)
        toggle = QPushButton("启用 / 停用")
        toggle.setObjectName("secondary")
        toggle.clicked.connect(self._toggle_selected)
        delete = QPushButton("删除")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete_selected)
        actions.addWidget(save)
        actions.addWidget(toggle)
        actions.addWidget(delete)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(QLabel("本机候选（尚未保存）"))
        candidate_hint = QLabel(
            "只从“请记住 / 我叫 / 我喜欢 / 我希望”等明确表达中提取；"
            "选择后点击确认保存，不会自动写入。"
        )
        candidate_hint.setObjectName("muted")
        candidate_hint.setWordWrap(True)
        layout.addWidget(candidate_hint)
        layout.addWidget(self.candidate_list, 1)
        candidate_actions = QHBoxLayout()
        extract = QPushButton("从最近聊天提取")
        extract.setObjectName("secondary")
        extract.clicked.connect(self.extract_candidates)
        confirm = QPushButton("确认保存所选候选")
        confirm.setObjectName("primary")
        confirm.clicked.connect(self.confirm_candidate)
        candidate_actions.addWidget(extract)
        candidate_actions.addWidget(confirm)
        candidate_actions.addStretch()
        layout.addLayout(candidate_actions)

    def apply_settings(self):
        self.service.set_enabled(self.enable_input.isChecked())

    def refresh(self):
        selected_id = self._selected_id()
        self.memory_list.clear()
        for record in self.service.list(
            user_id=self.user_id,
            character_id=self.character_id,
        ):
            state = "启用" if record.enabled else "停用"
            item = QListWidgetItem(f"[{state}] {record.content}")
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            self.memory_list.addItem(item)
            if record.id == selected_id:
                self.memory_list.setCurrentItem(item)
        if self.memory_list.count() and self.memory_list.currentRow() < 0:
            self.memory_list.setCurrentRow(0)

    def extract_candidates(self):
        existing = {
            item.content.casefold()
            for item in self.service.list(
                user_id=self.user_id,
                character_id=self.character_id,
            )
        }
        self.candidate_list.clear()
        for content in self.service.extract_candidates(
            self.recent_messages
        ):
            if content.casefold() not in existing:
                self.candidate_list.addItem(QListWidgetItem(content))
        if self.candidate_list.count():
            self.candidate_list.setCurrentRow(0)

    def confirm_candidate(self):
        item = self.candidate_list.currentItem()
        if item is None:
            return
        try:
            self.service.add(
                user_id=self.user_id,
                character_id=self.character_id,
                content=item.text(),
            )
        except MemoryStoreError as exc:
            QMessageBox.warning(self, "记忆未保存", str(exc))
            return
        self.candidate_list.takeItem(self.candidate_list.row(item))
        self.refresh()

    def _selected_id(self):
        item = self.memory_list.currentItem()
        return (
            str(item.data(Qt.ItemDataRole.UserRole))
            if item is not None
            else ""
        )

    def _selected(self, current, _previous):
        if current is None:
            self.edit_input.clear()
            return
        memory_id = self._selected_id()
        record = next(
            (
                item
                for item in self.service.list(
                    user_id=self.user_id,
                    character_id=self.character_id,
                )
                if item.id == memory_id
            ),
            None,
        )
        self.edit_input.setText(record.content if record else "")

    def _save_edit(self):
        memory_id = self._selected_id()
        if not memory_id:
            return
        try:
            self.service.update(
                memory_id,
                content=self.edit_input.text(),
            )
        except MemoryStoreError as exc:
            QMessageBox.warning(self, "记忆未修改", str(exc))
            return
        self.refresh()

    def _toggle_selected(self):
        memory_id = self._selected_id()
        if not memory_id:
            return
        record = next(
            item
            for item in self.service.list(
                user_id=self.user_id,
                character_id=self.character_id,
            )
            if item.id == memory_id
        )
        self.service.update(memory_id, enabled=not record.enabled)
        self.refresh()

    def _delete_selected(self):
        memory_id = self._selected_id()
        if not memory_id:
            return
        self.service.delete(memory_id)
        self.refresh()
