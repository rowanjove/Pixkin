"""Read-only export and recoverable rebuild for a damaged chat database."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path


class ChatDatabaseRecoveryError(RuntimeError):
    pass


class ChatDatabaseRecoveryService:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()

    def export_readonly(self, destination: str | Path) -> Path:
        target = Path(destination)
        uri = f"{self.database_path.as_uri()}?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=2,
            )
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT
                        m.role, m.content, m.metadata_json, m.created_at,
                        s.id AS session_id, s.character_id,
                        s.character_name
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    ORDER BY m.created_at, m.id
                    """
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise ChatDatabaseRecoveryError(
                "损坏数据库中没有可只读导出的完整消息记录"
            ) from exc
        payload = {
            "schema_version": 1,
            "source": "damaged-chat-database",
            "messages": [dict(row) for row in rows],
        }
        self._atomic_json(target, payload)
        return target

    def backup_and_remove(self, backup_root: str | Path) -> Path:
        if not self.database_path.is_file():
            raise ChatDatabaseRecoveryError("聊天数据库文件不存在")
        root = Path(backup_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        destination = root / (
            "chat-corrupt-"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + ".sqlite3"
        )
        if destination.exists():
            destination = root / (
                destination.stem
                + f"-{os.getpid()}"
                + destination.suffix
            )
        shutil.copy2(self.database_path, destination)
        if self._digest(destination) != self._digest(self.database_path):
            destination.unlink(missing_ok=True)
            raise ChatDatabaseRecoveryError(
                "损坏数据库备份校验失败，未执行重建"
            )
        self.database_path.unlink()
        return destination

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _atomic_json(destination: Path, payload: dict) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
