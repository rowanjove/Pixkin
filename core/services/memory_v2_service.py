"""SQLite-backed Memory 2.0 with an idempotent legacy JSON migration."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.services.memory_service import MemoryRecord, MemoryService, MemoryStoreError


class MemoryV2StoreError(MemoryStoreError):
    """Raised when the versioned Memory 2.0 store cannot be used safely."""


class MemoryV2Service:
    """A bounded, local-only memory store with FTS5 search and version links."""

    SCHEMA_VERSION = 1
    MAX_RECORDS = 2000
    MAX_CONTENT_CHARS = 1000
    MAX_INJECTED_RECORDS = 20
    MAX_INJECTED_CHARS = 4000
    MEMORY_KINDS = frozenset({"profile", "episodic", "semantic", "relationship", "task"})

    def __init__(
        self,
        path: str | Path,
        *,
        legacy_json_path: str | Path | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        if str(self.path) != ":memory:":
            try:
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = NORMAL")
            except sqlite3.DatabaseError:
                pass
        self._closed = False
        try:
            self._migrate_schema()
            self._enabled = self._read_enabled()
            if legacy_json_path is not None:
                self._migrate_legacy_if_needed(Path(legacy_json_path))
        except Exception:
            self._connection.close()
            raise

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        value = bool(enabled)
        with self._lock:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO memory_meta(key, value) VALUES('enabled', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("1" if value else "0",),
                )
            self._enabled = value

    def list(
        self,
        *,
        user_id: str | None = None,
        character_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[str | int] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(str(user_id))
        if character_id is not None:
            clauses.append("character_id = ?")
            params.append(str(character_id))
        if not include_disabled:
            clauses.append("enabled = 1")
        query = "SELECT * FROM memories"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at ASC, id ASC"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
            return [self._record(row) for row in rows]

    def add(
        self,
        *,
        user_id: str,
        character_id: str,
        content: str,
        kind: str = "profile",
        source_type: str = "explicit",
        importance: float = 0.5,
    ) -> MemoryRecord:
        clean = self._validate_content(content)
        normalized_kind = str(kind or "profile").strip().lower()
        if normalized_kind not in self.MEMORY_KINDS:
            raise MemoryV2StoreError("不支持的记忆类型")
        scope = (str(user_id), str(character_id))
        with self._lock:
            existing = self._connection.execute(
                "SELECT * FROM memories WHERE user_id=? AND character_id=? "
                "AND lower(content)=lower(?) AND status='active' LIMIT 1",
                (*scope, clean),
            ).fetchone()
            if existing is not None:
                return self._record(existing)
            count = int(self._connection.execute("SELECT count(*) FROM memories").fetchone()[0])
            if count >= self.MAX_RECORDS:
                raise MemoryV2StoreError("长期记忆已达到 2000 条上限")
            now = self._now()
            memory_id = uuid.uuid4().hex
            with self._connection:
                self._connection.execute(
                    "INSERT INTO memories(id,user_id,character_id,kind,content,enabled,"
                    "status,importance,source_type,source_id,created_at,updated_at,"
                    "last_used_at,supersedes_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        memory_id,
                        scope[0],
                        scope[1],
                        normalized_kind,
                        clean,
                        1,
                        "active",
                        max(0.0, min(1.0, float(importance))),
                        str(source_type or "explicit")[:40],
                        "",
                        now,
                        now,
                        "",
                        "",
                    ),
                )
                self._insert_version(memory_id, clean, now)
            return self._record_by_id(memory_id)

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        enabled: bool | None = None,
        status: str | None = None,
    ) -> MemoryRecord:
        with self._lock:
            current = self._record_by_id(memory_id)
            clean = current.content if content is None else self._validate_content(content)
            next_status = self._connection.execute(
                "SELECT status FROM memories WHERE id=?", (str(memory_id),)
            ).fetchone()[0]
            if status is not None:
                next_status = str(status)
                if next_status not in {"active", "disabled", "superseded", "deleted"}:
                    raise MemoryV2StoreError("记忆状态无效")
                if next_status in {"disabled", "superseded", "deleted"}:
                    next_enabled = False
                else:
                    next_enabled = True if enabled is None else bool(enabled)
            elif enabled is not None:
                next_status = "active" if enabled else "disabled"
                next_enabled = bool(enabled)
            else:
                next_enabled = bool(current.enabled)
            now = self._now()
            with self._connection:
                self._connection.execute(
                    "UPDATE memories SET content=?, enabled=?, status=?, updated_at=? WHERE id=?",
                    (clean, int(next_enabled), next_status, now, str(memory_id)),
                )
                if clean != current.content:
                    self._insert_version(str(memory_id), clean, now)
            return self._record_by_id(memory_id)

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            current = self._connection.execute(
                "SELECT id FROM memories WHERE id=?", (str(memory_id),)
            ).fetchone()
            if current is None:
                return False
            with self._connection:
                self._connection.execute(
                    "UPDATE memories SET enabled=0, status='deleted', updated_at=? WHERE id=?",
                    (self._now(), str(memory_id)),
                )
            return True

    def supersede(self, old_id: str, *, content: str, kind: str = "profile") -> MemoryRecord:
        with self._lock:
            old = self._record_by_id(old_id)
            new = self.add(
                user_id=old.user_id,
                character_id=old.character_id,
                content=content,
                kind=kind,
                source_type="supersession",
            )
            # ``add`` is intentionally idempotent.  Superseding with identical
            # content therefore becomes a safe no-op instead of self-superseding
            # the existing row and creating a misleading version link.
            if new.id == old.id:
                return old
            with self._connection:
                self._connection.execute(
                    "UPDATE memories SET status='superseded', enabled=0, updated_at=? WHERE id=?",
                    (self._now(), old.id),
                )
                self._connection.execute(
                    "UPDATE memories SET supersedes_id=? WHERE id=?",
                    (old.id, new.id),
                )
                self._connection.execute(
                    "INSERT OR IGNORE INTO memory_links(memory_id,related_id,relation) VALUES(?,?,?)",
                    (new.id, old.id, "supersedes"),
                )
            return self._record_by_id(new.id)

    def search(self, query: str, *, user_id: str | None = None, character_id: str | None = None) -> list[MemoryRecord]:
        clean = str(query or "").strip()[:200]
        if not clean:
            return []
        clauses = ["memory_fts MATCH ?"]
        params: list[str] = [clean.replace('"', " ")]
        if user_id is not None:
            clauses.append("m.user_id = ?")
            params.append(str(user_id))
        if character_id is not None:
            clauses.append("m.character_id = ?")
            params.append(str(character_id))
        sql = (
            "SELECT m.* FROM memory_fts f JOIN memories m ON m.id=f.memory_id "
            "WHERE m.enabled=1 AND m.status='active' AND " + " AND ".join(clauses) +
            " ORDER BY bm25(memory_fts), m.updated_at DESC LIMIT 50"
        )
        with self._lock:
            try:
                rows = self._connection.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                escaped = (
                    clean.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                rows = self._connection.execute(
                    "SELECT * FROM memories WHERE enabled=1 AND status='active' "
                    "AND content LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT 50",
                    (f"%{escaped}%",),
                ).fetchall()
            return [self._record(row) for row in rows]

    def used_for(self, *, user_id: str, character_id: str) -> list[MemoryRecord]:
        with self._lock:
            if not self._enabled:
                return []
            records = list(reversed(self.list(
                user_id=user_id,
                character_id=character_id,
                include_disabled=False,
            )))
            records = [record for record in records if record.status == "active"]
            selected: list[MemoryRecord] = []
            total = 0
            for record in records:
                if len(selected) >= self.MAX_INJECTED_RECORDS:
                    break
                content_length = len(record.content)
                if content_length > self.MAX_INJECTED_CHARS:
                    continue
                if total + content_length > self.MAX_INJECTED_CHARS:
                    break
                selected.append(record)
                total += content_length
            if selected:
                now = self._now()
                with self._connection:
                    self._connection.executemany(
                        "UPDATE memories SET last_used_at=? WHERE id=?",
                        [(now, record.id) for record in selected],
                    )
            return list(reversed(selected))

    @staticmethod
    def prompt_fragment(records: Iterable[MemoryRecord]) -> str:
        return MemoryService.prompt_fragment(records)

    @staticmethod
    def extract_candidates(messages: Iterable[object]) -> list[str]:
        return MemoryService.extract_candidates(messages)

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        with self._lock:
            try:
                backup_connection = sqlite3.connect(temporary)
                with backup_connection:
                    self._connection.backup(backup_connection)
                backup_connection.close()
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return target

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _migrate_schema(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > self.SCHEMA_VERSION:
            raise MemoryV2StoreError(
                f"记忆数据库版本 {version} 高于当前支持的 {self.SCHEMA_VERSION}"
            )
        if version == 0:
            try:
                with self._connection:
                    self._connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS memory_meta(
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS memories(
                            id TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            character_id TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            content TEXT NOT NULL,
                            enabled INTEGER NOT NULL DEFAULT 1,
                            status TEXT NOT NULL DEFAULT 'active',
                            importance REAL NOT NULL DEFAULT 0.5,
                            source_type TEXT NOT NULL DEFAULT 'explicit',
                            source_id TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            last_used_at TEXT NOT NULL DEFAULT '',
                            supersedes_id TEXT NOT NULL DEFAULT ''
                        );
                        CREATE INDEX IF NOT EXISTS memories_scope
                            ON memories(user_id, character_id, enabled, status, updated_at);
                        CREATE TABLE IF NOT EXISTS memory_versions(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            memory_id TEXT NOT NULL,
                            content TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                        );
                        CREATE TABLE IF NOT EXISTS memory_links(
                            memory_id TEXT NOT NULL,
                            related_id TEXT NOT NULL,
                            relation TEXT NOT NULL,
                            PRIMARY KEY(memory_id, related_id, relation)
                        );
                        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                            memory_id UNINDEXED,
                            content,
                            tokenize='unicode61'
                        );
                        CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
                            INSERT INTO memory_fts(memory_id, content) VALUES(new.id, new.content);
                        END;
                        CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE OF content ON memories BEGIN
                            DELETE FROM memory_fts WHERE memory_id=old.id;
                            INSERT INTO memory_fts(memory_id, content) VALUES(new.id, new.content);
                        END;
                        CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
                            DELETE FROM memory_fts WHERE memory_id=old.id;
                        END;
                        PRAGMA user_version = 1;
                        """
                    )
            except sqlite3.Error as exc:
                raise MemoryV2StoreError("记忆数据库迁移失败") from exc
        else:
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
                            DELETE FROM memory_fts WHERE memory_id=old.id;
                        END;
                        """
                    )
            except sqlite3.Error as exc:
                raise MemoryV2StoreError("记忆数据库更新触发器失败") from exc

    def _migrate_legacy_if_needed(self, legacy_path: Path) -> None:
        marker = self._connection.execute(
            "SELECT value FROM memory_meta WHERE key='legacy_migrated'"
        ).fetchone()
        if marker is not None or not legacy_path.is_file():
            return
        try:
            document = json.loads(legacy_path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise MemoryV2StoreError("旧记忆文件必须是 JSON 对象")
            if document.get("schema_version") != MemoryService.SCHEMA_VERSION:
                raise MemoryV2StoreError("旧记忆文件版本不兼容")
            records = document.get("records", [])
            if not isinstance(records, list):
                raise MemoryV2StoreError("旧记忆文件条目无效")
            if len(records) > self.MAX_RECORDS:
                raise MemoryV2StoreError("旧记忆文件超过 2000 条上限")
            with self._connection:
                self._enabled = bool(document.get("enabled", True))
                self._connection.execute(
                    "INSERT INTO memory_meta(key,value) VALUES('enabled',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("1" if self._enabled else "0",),
                )
                for raw in records:
                    if not isinstance(raw, dict):
                        raise MemoryV2StoreError("旧记忆条目无效")
                    memory_id = str(raw.get("id") or uuid.uuid4().hex)
                    content = self._validate_content(raw.get("content"))
                    created = str(raw.get("created_at") or self._now())
                    updated = str(raw.get("updated_at") or created)
                    self._connection.execute(
                        "INSERT OR IGNORE INTO memories(id,user_id,character_id,kind,content,enabled,status,importance,source_type,source_id,created_at,updated_at,last_used_at,supersedes_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (memory_id, str(raw.get("user_id") or ""), str(raw.get("character_id") or ""), "profile", content, int(bool(raw.get("enabled", True))), "active" if raw.get("enabled", True) else "disabled", 0.5, "legacy_json", "", created, updated, "", ""),
                    )
                    self._insert_version(memory_id, content, updated)
                self._connection.execute(
                    "INSERT INTO memory_meta(key,value) VALUES('legacy_migrated',?)",
                    (self._now(),),
                )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MemoryV2StoreError("旧记忆文件无法读取") from exc

    def _insert_version(self, memory_id: str, content: str, created_at: str) -> None:
        self._connection.execute(
            "INSERT INTO memory_versions(memory_id,content,created_at) VALUES(?,?,?)",
            (memory_id, content, created_at),
        )

    def _record_by_id(self, memory_id: str) -> MemoryRecord:
        row = self._connection.execute(
            "SELECT * FROM memories WHERE id=?", (str(memory_id),)
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            character_id=str(row["character_id"]),
            content=str(row["content"]),
            enabled=bool(row["enabled"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            kind=str(row["kind"]),
            source_type=str(row["source_type"]),
            status=str(row["status"]),
            importance=float(row["importance"]),
            last_used_at=str(row["last_used_at"]),
        )

    def _read_enabled(self) -> bool:
        row = self._connection.execute(
            "SELECT value FROM memory_meta WHERE key='enabled'"
        ).fetchone()
        return row is None or row[0] == "1"

    @classmethod
    def _validate_content(cls, content: object) -> str:
        clean = str(content or "").strip()
        if not clean or len(clean) > cls.MAX_CONTENT_CHARS:
            raise MemoryV2StoreError(
                f"记忆内容不能为空且不能超过 {cls.MAX_CONTENT_CHARS} 字"
            )
        return clean

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
