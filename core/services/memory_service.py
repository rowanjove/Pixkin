"""Local, explicitly confirmed long-term memory for chat prompts."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.privacy import redact_text


class MemoryStoreError(ValueError):
    """Raised when a memory operation would violate the storage contract."""


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    user_id: str
    character_id: str
    content: str
    enabled: bool
    created_at: str
    updated_at: str


class MemoryService:
    """Atomic, bounded JSON memory store with local candidate extraction."""

    SCHEMA_VERSION = 1
    MAX_RECORDS = 200
    MAX_CONTENT_CHARS = 500
    MAX_INJECTED_RECORDS = 20
    MAX_INJECTED_CHARS = 4000
    _CANDIDATE_PATTERNS = (
        re.compile(r"(?:请记住|记住)[：:，,\s]*(.+)", re.I),
        re.compile(r"(我叫|我的名字是)[：:，,\s]*([^。！？!?\n]{1,80})"),
        re.compile(
            r"(我(?:很)?喜欢|我不喜欢|我希望|我通常)"
            r"([^。！？!?\n]{1,160})"
        ),
    )

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._enabled = True
        self._records: list[MemoryRecord] = []
        self._load()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        candidate = bool(enabled)
        self._save(enabled=candidate, records=self._records)
        self._enabled = candidate

    def list(
        self,
        *,
        user_id: str | None = None,
        character_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[MemoryRecord]:
        result = self._records
        if user_id is not None:
            result = [
                item for item in result if item.user_id == str(user_id)
            ]
        if character_id is not None:
            result = [
                item
                for item in result
                if item.character_id == str(character_id)
            ]
        if not include_disabled:
            result = [item for item in result if item.enabled]
        return list(result)

    def add(
        self,
        *,
        user_id: str,
        character_id: str,
        content: str,
    ) -> MemoryRecord:
        clean = self._validate_content(content)
        scope = (str(user_id), str(character_id))
        for item in self._records:
            if (
                (item.user_id, item.character_id) == scope
                and item.content.casefold() == clean.casefold()
            ):
                return item
        if len(self._records) >= self.MAX_RECORDS:
            raise MemoryStoreError("长期记忆已达到 200 条上限")
        timestamp = self._now()
        record = MemoryRecord(
            id=uuid.uuid4().hex,
            user_id=scope[0],
            character_id=scope[1],
            content=clean,
            enabled=True,
            created_at=timestamp,
            updated_at=timestamp,
        )
        candidate = [*self._records, record]
        self._save(enabled=self._enabled, records=candidate)
        self._records = candidate
        return record

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        enabled: bool | None = None,
    ) -> MemoryRecord:
        index = self._index(memory_id)
        current = self._records[index]
        updated = MemoryRecord(
            id=current.id,
            user_id=current.user_id,
            character_id=current.character_id,
            content=(
                current.content
                if content is None
                else self._validate_content(content)
            ),
            enabled=current.enabled if enabled is None else bool(enabled),
            created_at=current.created_at,
            updated_at=self._now(),
        )
        candidate = list(self._records)
        candidate[index] = updated
        self._save(enabled=self._enabled, records=candidate)
        self._records = candidate
        return updated

    def delete(self, memory_id: str) -> bool:
        try:
            index = self._index(memory_id)
        except KeyError:
            return False
        candidate = list(self._records)
        candidate.pop(index)
        self._save(enabled=self._enabled, records=candidate)
        self._records = candidate
        return True

    def used_for(
        self,
        *,
        user_id: str,
        character_id: str,
    ) -> list[MemoryRecord]:
        if not self._enabled:
            return []
        selected: list[MemoryRecord] = []
        characters = 0
        for item in reversed(
            self.list(
                user_id=user_id,
                character_id=character_id,
                include_disabled=False,
            )
        ):
            if len(selected) >= self.MAX_INJECTED_RECORDS:
                break
            extra = len(item.content)
            if selected and characters + extra > self.MAX_INJECTED_CHARS:
                break
            selected.append(item)
            characters += extra
        return list(reversed(selected))

    @staticmethod
    def prompt_fragment(records: Iterable[MemoryRecord]) -> str:
        values = list(records)
        if not values:
            return ""
        lines = "\n".join(f"- {item.content}" for item in values)
        return (
            "\n\n以下是用户逐条确认、仅保存在本机的长期记忆。"
            "只在相关时自然使用，不要声称记得未列出的内容：\n"
            f"{lines}"
        )

    @classmethod
    def extract_candidates(cls, messages: Iterable[object]) -> list[str]:
        """Extract local candidates; this method never persists them."""

        candidates: list[str] = []
        seen: set[str] = set()
        for message in messages:
            if isinstance(message, dict):
                if str(message.get("role") or "") != "user":
                    continue
                text = str(message.get("content") or "")
            else:
                text = str(message or "")
            for pattern_index, pattern in enumerate(
                cls._CANDIDATE_PATTERNS
            ):
                for match in pattern.finditer(text):
                    candidate = (
                        match.group(0)
                        if pattern_index
                        else match.group(1)
                    )
                    candidate = cls._normalize_candidate(candidate)
                    key = candidate.casefold()
                    if candidate and key not in seen:
                        seen.add(key)
                        candidates.append(candidate)
        return candidates

    @classmethod
    def _normalize_candidate(cls, value: str) -> str:
        clean = " ".join(str(value).strip(" ：:，,。.!！?？\t").split())
        if not clean or len(clean) > cls.MAX_CONTENT_CHARS:
            return ""
        if redact_text(clean) != clean:
            return ""
        return clean

    @classmethod
    def _validate_content(cls, content: str) -> str:
        clean = cls._normalize_candidate(content)
        if not clean:
            raise MemoryStoreError(
                "记忆不能为空、不能包含凭据，且不能超过 500 字"
            )
        return clean

    def _index(self, memory_id: str) -> int:
        for index, item in enumerate(self._records):
            if item.id == str(memory_id):
                return index
        raise KeyError(memory_id)

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("schema_version") != self.SCHEMA_VERSION:
                raise MemoryStoreError("长期记忆版本不兼容")
            records = document.get("records")
            if not isinstance(records, list) or len(records) > self.MAX_RECORDS:
                raise MemoryStoreError("长期记忆文件无效")
            loaded = []
            for value in records:
                if not isinstance(value, dict):
                    raise MemoryStoreError("长期记忆条目无效")
                record = MemoryRecord(
                    id=str(value["id"]),
                    user_id=str(value["user_id"]),
                    character_id=str(value["character_id"]),
                    content=self._validate_content(value["content"]),
                    enabled=bool(value.get("enabled", True)),
                    created_at=str(value["created_at"]),
                    updated_at=str(value["updated_at"]),
                )
                loaded.append(record)
            self._enabled = bool(document.get("enabled", True))
            self._records = loaded
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise MemoryStoreError("长期记忆文件无法读取") from exc

    def _save(
        self,
        *,
        enabled: bool | None = None,
        records: list[MemoryRecord] | None = None,
    ) -> None:
        saved_enabled = self._enabled if enabled is None else enabled
        saved_records = self._records if records is None else records
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "enabled": saved_enabled,
            "records": [asdict(item) for item in saved_records],
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".memories-",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    document,
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
