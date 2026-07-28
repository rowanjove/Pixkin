"""Thread-safe bridge from tool workers to a Qt confirmation dialog."""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QMessageBox

from core.services.tool_permission_service import ToolPermissionRequest


@dataclass
class _PendingConfirmation:
    request: ToolPermissionRequest
    completed: threading.Event = field(default_factory=threading.Event)
    allowed: bool = False
    cancelled: bool = False


class ToolPermissionBridge(QObject):
    """Show L1 tool prompts on the GUI thread and return one-shot decisions."""

    confirmation_requested = pyqtSignal(object)

    def __init__(
        self,
        *,
        timeout_seconds: float = 120.0,
        ask_user: Callable[
            [ToolPermissionRequest], tuple[bool, bool]
        ] | None = None,
    ):
        super().__init__()
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self._lock = threading.Lock()
        self._pending: dict[int, _PendingConfirmation] = {}
        self._session_decisions: dict[str, bool] = {}
        self._closed = False
        self._ask_user_callback = ask_user or self._ask_user
        self.confirmation_requested.connect(
            self._show_confirmation,
            Qt.ConnectionType.QueuedConnection,
        )

    def confirm(self, request: ToolPermissionRequest) -> bool:
        """Ask once, blocking only the calling worker thread."""
        decision_key = self._decision_key(request)
        with self._lock:
            if self._closed:
                return False
            remembered = self._session_decisions.get(decision_key)
            if remembered is not None:
                return remembered
        if QThread.currentThread() == self.thread():
            with self._lock:
                if self._closed:
                    return False
            allowed, remember = self._ask_user_callback(request)
            if remember:
                with self._lock:
                    if not self._closed:
                        self._session_decisions[decision_key] = allowed
            return allowed

        pending = _PendingConfirmation(request)
        pending_id = id(pending)
        with self._lock:
            if self._closed:
                return False
            self._pending[pending_id] = pending
        self.confirmation_requested.emit(pending)
        completed = pending.completed.wait(self.timeout_seconds)
        with self._lock:
            self._pending.pop(pending_id, None)
            if not completed:
                pending.cancelled = True
            return bool(completed and not pending.cancelled and pending.allowed)

    def cancel_pending(self) -> None:
        """Deny current prompts without disabling future confirmations."""
        with self._lock:
            pending = tuple(self._pending.values())
            self._pending.clear()
            for item in pending:
                item.cancelled = True
                item.allowed = False
                item.completed.set()

    def close(self) -> None:
        """Permanently deny new prompts and release waiting workers."""
        with self._lock:
            self._closed = True
            self._session_decisions.clear()
        self.cancel_pending()

    def _show_confirmation(self, pending: object) -> None:
        if not isinstance(pending, _PendingConfirmation):
            return
        pending_id = id(pending)
        with self._lock:
            if (
                self._closed
                or pending.cancelled
                or self._pending.get(pending_id) is not pending
            ):
                return
        allowed, remember = self._ask_user_callback(pending.request)
        with self._lock:
            if (
                self._closed
                or pending.cancelled
                or self._pending.get(pending_id) is not pending
            ):
                return
            if remember:
                self._session_decisions[
                    self._decision_key(pending.request)
                ] = allowed
            pending.allowed = allowed
            pending.completed.set()

    @staticmethod
    def _decision_key(request: ToolPermissionRequest) -> str:
        fingerprint = (
            request.authorization_fingerprint
            or ToolPermissionBridge._legacy_fingerprint(request)
        )
        return (
            f"{request.tool_name}\0{request.side_effect}\0{fingerprint}"
        )

    @staticmethod
    def _legacy_fingerprint(request: ToolPermissionRequest) -> str:
        return json.dumps(
            dict(request.arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _ask_user(
        request: ToolPermissionRequest,
    ) -> tuple[bool, bool]:
        arguments = json.dumps(
            dict(request.arguments),
            ensure_ascii=False,
            indent=2,
        )
        if len(arguments) > 1200:
            arguments = arguments[:1197] + "..."
        message = (
            "AI 请求执行一次外部动作。\n\n"
            f"工具：{request.tool_name}\n"
            f"影响：{request.side_effect or '外部动作'}\n"
            f"参数：\n{arguments}\n\n"
            "只允许本次执行；拒绝不会影响当前对话。"
        )
        buttons = (
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        dialog = QMessageBox(
            QMessageBox.Icon.Question,
            "允许 Pixkin 执行外部动作？",
            message,
            buttons,
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        remember = QCheckBox("本次会话记住此工具与参数的决定")
        dialog.setCheckBox(remember)
        result = dialog.exec()
        allowed = result == int(QMessageBox.StandardButton.Yes)
        return allowed, remember.isChecked()
