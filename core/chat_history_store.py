import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.paths import user_data_dir


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class ChatHistoryStore:
    """按角色和会话保存聊天历史的本地 SQLite 存储。"""

    def __init__(self, database_path=None):
        self.database_path = Path(
            database_path or (user_data_dir() / "chat-history.sqlite3")
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    character_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_session_per_character
                ON sessions(character_id)
                WHERE is_active = 1;
                CREATE INDEX IF NOT EXISTS sessions_by_character
                ON sessions(character_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS messages_by_session
                ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS messages_by_date
                ON messages(created_at);
                """
            )

    def get_or_create_active_session(
        self, character_id: str, character_name: str
    ) -> str:
        character_id = str(character_id or "default")
        character_name = str(character_name or "角色")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id FROM sessions
                WHERE character_id = ? AND is_active = 1
                ORDER BY updated_at DESC LIMIT 1
                """,
                (character_id,),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE sessions SET character_name = ? WHERE id = ?",
                    (character_name, row["id"]),
                )
                return row["id"]
        return self.create_session(character_id, character_name)

    def create_session(
        self, character_id: str, character_name: str
    ) -> str:
        session_id = uuid.uuid4().hex
        timestamp = _now_iso()
        with self._connection() as connection:
            connection.execute(
                "UPDATE sessions SET is_active = 0 WHERE character_id = ?",
                (str(character_id or "default"),),
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    id, character_id, character_name,
                    created_at, updated_at, is_active
                ) VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    session_id,
                    str(character_id or "default"),
                    str(character_name or "角色"),
                    timestamp,
                    timestamp,
                ),
            )
        return session_id

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata=None,
        created_at=None,
    ) -> dict:
        timestamp = created_at or _now_iso()
        metadata = metadata if isinstance(metadata, dict) else {}
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    session_id, role, content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(role),
                    str(content),
                    json.dumps(metadata, ensure_ascii=False),
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (timestamp, session_id),
            )
        return {
            "id": cursor.lastrowid,
            "session_id": session_id,
            "role": str(role),
            "content": str(content),
            "metadata": metadata,
            "created_at": timestamp,
        }

    def session_messages(self, session_id: str) -> list:
        return self.list_messages(session_id=session_id)

    def context_messages(self, session_id: str) -> list:
        return [
            {"role": item["role"], "content": item["content"]}
            for item in self.session_messages(session_id)
            if item["role"] in {"user", "assistant"}
        ]

    def list_messages(
        self,
        *,
        session_id=None,
        character_id=None,
        day=None,
    ) -> list:
        conditions = []
        values = []
        if session_id:
            conditions.append("m.session_id = ?")
            values.append(session_id)
        if character_id:
            conditions.append("s.character_id = ?")
            values.append(character_id)
        if day:
            conditions.append("substr(m.created_at, 1, 10) = ?")
            values.append(str(day))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    m.id, m.session_id, m.role, m.content,
                    m.metadata_json, m.created_at,
                    s.character_id, s.character_name
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                {where}
                ORDER BY m.created_at ASC, m.id ASC
                """,
                values,
            ).fetchall()
        result = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            result.append({
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": metadata,
                "created_at": row["created_at"],
                "character_id": row["character_id"],
                "character_name": row["character_name"],
            })
        return result

    def list_dates(self, character_id=None) -> list:
        values = []
        where = ""
        if character_id:
            where = "WHERE s.character_id = ?"
            values.append(character_id)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    substr(m.created_at, 1, 10) AS day,
                    COUNT(*) AS message_count
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                {where}
                GROUP BY day
                ORDER BY day DESC
                """,
                values,
            ).fetchall()
        return [
            {"day": row["day"], "message_count": row["message_count"]}
            for row in rows
        ]

    def delete_session(self, session_id: str) -> int:
        with self._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
        return count

    def delete_date(self, day: str, character_id=None) -> int:
        conditions = ["substr(created_at, 1, 10) = ?"]
        values = [str(day)]
        if character_id:
            conditions.append(
                "session_id IN (SELECT id FROM sessions WHERE character_id = ?)"
            )
            values.append(character_id)
        with self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM messages WHERE {' AND '.join(conditions)}",
                values,
            )
            connection.execute(
                """
                DELETE FROM sessions
                WHERE NOT EXISTS (
                    SELECT 1 FROM messages WHERE messages.session_id = sessions.id
                ) AND is_active = 0
                """
            )
        return max(0, cursor.rowcount)

    def clear_all(self) -> int:
        with self._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            connection.execute("DELETE FROM sessions")
        return count
