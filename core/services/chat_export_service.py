"""Atomic chat-history export independent from dialog and widget concerns."""

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


DEFAULT_ROLE_LABELS = {
    "user": "我",
    "assistant": "伙伴",
    "system": "工具",
    "error": "错误",
    "alert": "提醒",
}


class ChatExportService:
    """Render and atomically persist filtered chat history."""

    @classmethod
    def export(
        cls,
        messages: Sequence[Mapping[str, Any]],
        destination,
        *,
        day_label: str,
        json_format: bool = False,
    ) -> Path:
        target = Path(destination)
        content = (
            cls.render_json(messages)
            if json_format
            else cls.render_markdown(messages, day_label=day_label)
        )
        cls._atomic_write_text(target, content)
        return target

    @staticmethod
    def render_json(messages: Sequence[Mapping[str, Any]]) -> str:
        return json.dumps(messages, ensure_ascii=False, indent=2)

    @staticmethod
    def render_markdown(
        messages: Sequence[Mapping[str, Any]],
        *,
        day_label: str,
    ) -> str:
        lines = [f"# Pixkin 聊天记录 · {day_label}", ""]
        for item in messages:
            role = str(item.get("role") or "system")
            who = DEFAULT_ROLE_LABELS.get(role, role)
            if role == "assistant":
                who = str(item.get("character_name") or who)
            timestamp = str(item.get("created_at") or "").replace("T", " ")[:19]
            lines.extend(
                [
                    f"## {timestamp} · {who}",
                    "",
                    str(item.get("content") or ""),
                    "",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _atomic_write_text(destination: Path, content: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
