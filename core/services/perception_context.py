"""Permission-aware, relevance-filtered context projection for prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from core.services.context_sensor_service import DesktopContextSnapshot


@dataclass(frozen=True)
class ContextBudget:
    """Hard limits applied before desktop context reaches a model endpoint."""

    max_chars: int = 4_000
    max_clipboard_chars: int = 3_000
    include_window: bool = True
    include_system: bool = True
    include_screen: bool = False

    def __post_init__(self) -> None:
        if self.max_chars < 64:
            raise ValueError("context budget must be at least 64 characters")
        if self.max_clipboard_chars < 0:
            raise ValueError("clipboard budget cannot be negative")


class PerceptionContextProjector:
    """Select only context relevant to a user query.

    Clipboard text is considered relevant for debugging, code, traceback and
    "what did I copy" questions. Screenshots are opt-in and represented by a
    small marker instead of embedding binary data into a text prompt.
    """

    _CLIPBOARD_HINTS = re.compile(
        r"(报错|错误|异常|traceback|exception|error|代码|日志|复制|剪贴板|clipboard|debug)",
        re.IGNORECASE,
    )

    def __init__(self, budget: ContextBudget | None = None):
        self.budget = budget or ContextBudget()

    def project(
        self,
        snapshot: DesktopContextSnapshot,
        *,
        query: str = "",
        requested: Iterable[str] | None = None,
    ) -> dict[str, str | float | bool]:
        requested_set = {str(item).strip().lower() for item in (requested or ())}
        query_text = str(query or "")
        include_clipboard = (
            "clipboard" in requested_set
            or "剪贴板" in requested_set
            or bool(snapshot.clipboard_text and self._CLIPBOARD_HINTS.search(query_text))
        )
        include_selected = (
            "selected_text" in requested_set
            or "选中文本" in requested_set
            or bool(snapshot.selected_text and self._CLIPBOARD_HINTS.search(query_text))
        )
        include_screen = self.budget.include_screen and (
            "screen" in requested_set or "截图" in requested_set
        )

        fields: dict[str, str | float | bool] = {}
        if self.budget.include_window and (snapshot.process_name or snapshot.active_window_title):
            fields.update(
                {
                    "active_app": snapshot.process_name,
                    "window_title": snapshot.active_window_title,
                    "fullscreen": snapshot.is_fullscreen,
                }
            )
        if self.budget.include_system:
            fields["idle_seconds"] = round(max(0.0, float(snapshot.idle_seconds)), 1)
        if include_clipboard:
            fields["clipboard"] = snapshot.clipboard_text[: self.budget.max_clipboard_chars]
        if include_selected and snapshot.selected_text:
            fields["selected_text"] = snapshot.selected_text[: self.budget.max_clipboard_chars]
        if snapshot.system_state:
            fields["system_state"] = snapshot.system_state[:200]
        if include_screen and snapshot.screen_snapshot:
            fields["screen_snapshot_available"] = True

        # Apply a deterministic character budget while keeping field order.
        result: dict[str, str | float | bool] = {}
        used = 0
        for key, value in fields.items():
            rendered = f"{key}={value}"
            if used + len(rendered) > self.budget.max_chars:
                remaining = self.budget.max_chars - used
                if remaining > len(key) + 4 and isinstance(value, str):
                    available = max(0, remaining - len(key) - 3)
                    result[key] = value[:available] + "…"
                break
            result[key] = value
            used += len(rendered) + 1
        return result

    def prompt_fragment(
        self,
        snapshot: DesktopContextSnapshot | None,
        *,
        query: str = "",
        requested: Iterable[str] | None = None,
    ) -> str:
        if snapshot is None:
            return ""
        values = self.project(snapshot, query=query, requested=requested)
        if not values:
            return ""
        lines = ["\n[仅在授权范围内获取的桌面上下文]"]
        for key, value in values.items():
            lines.append(f"- {key}: {value}")
        lines.append("[/桌面上下文]")
        return "\n".join(lines) + "\n"


__all__ = ["ContextBudget", "PerceptionContextProjector"]
