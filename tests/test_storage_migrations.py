import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.chat_history_store import ChatHistoryStore
from core.config import ConfigManager
from core.storage.migrations import (
    CHAT_MIGRATIONS,
    CHAT_SCHEMA_VERSION,
    CONFIG_SCHEMA_VERSION,
    FutureSchemaVersionError,
    StorageMigrationError,
)


class ConfigMigrationTests(unittest.TestCase):
    def test_unversioned_config_is_migrated_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({
                    "api": {
                        "base_url": "https://api.deepseek.com/v1",
                        "model": "deepseek-chat",
                    },
                    "pet": {"scale": 1.3},
                }),
                encoding="utf-8",
            )

            config = ConfigManager(str(path))
            persisted = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(
                config.schema_version,
                CONFIG_SCHEMA_VERSION,
            )
            self.assertEqual(
                persisted["schema_version"],
                CONFIG_SCHEMA_VERSION,
            )
            self.assertEqual(
                persisted["api"]["model"],
                "deepseek-v4-flash",
            )
            self.assertEqual(config.get("pet", "scale"), 1.3)
            self.assertIn("live_monitor", persisted)

    def test_current_config_does_not_run_migration_or_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            ConfigManager(str(path))
            original = path.read_bytes()

            with patch.object(
                ConfigManager,
                "save_config",
                autospec=True,
                return_value=True,
            ) as save:
                loaded = ConfigManager(str(path))

            save.assert_not_called()
            self.assertEqual(
                loaded.schema_version,
                CONFIG_SCHEMA_VERSION,
            )
            self.assertEqual(path.read_bytes(), original)

    def test_future_config_version_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = json.dumps({
                "schema_version": CONFIG_SCHEMA_VERSION + 1,
                "future": {"keep": True},
            })
            path.write_text(original, encoding="utf-8")

            with self.assertRaises(FutureSchemaVersionError):
                ConfigManager(str(path))

            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_invalid_config_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"schema_version": "new"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                StorageMigrationError,
                "必须是整数",
            ):
                ConfigManager(str(path))

    def test_failed_migration_write_keeps_legacy_file_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = json.dumps({"pet": {"scale": 1.4}})
            path.write_text(original, encoding="utf-8")

            with patch(
                "core.config.os.replace",
                side_effect=OSError("locked"),
            ):
                config = ConfigManager(str(path))

            self.assertEqual(config.get("pet", "scale"), 1.4)
            self.assertEqual(
                config.schema_version,
                CONFIG_SCHEMA_VERSION,
            )
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(
                list(Path(directory).glob(".pixkin-config-*.tmp")),
                [],
            )


class ChatDatabaseMigrationTests(unittest.TestCase):
    @staticmethod
    def _create_legacy_database(path: Path):
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    character_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                        ON DELETE CASCADE
                );
                INSERT INTO sessions VALUES (
                    'legacy-session', 'shanshan', '山山',
                    '2026-01-01T00:00:00', '2026-01-01T00:00:00', 1
                );
                INSERT INTO messages (
                    session_id, role, content, metadata_json, created_at
                ) VALUES (
                    'legacy-session', 'user', '旧消息', '{}',
                    '2026-01-01T00:00:00'
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

    def test_unversioned_chat_database_preserves_existing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            self._create_legacy_database(path)

            store = ChatHistoryStore(path)

            self.assertEqual(store.schema_version, CHAT_SCHEMA_VERSION)
            messages = store.list_messages()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["content"], "旧消息")
            connection = sqlite3.connect(path)
            try:
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'index'"
                    )
                }
            finally:
                connection.close()
            self.assertIn("messages_by_session", indexes)
            self.assertIn("one_active_session_per_character", indexes)

    def test_chat_database_migration_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            first = ChatHistoryStore(path)
            session_id = first.get_or_create_active_session(
                "shanshan",
                "山山",
            )
            first.add_message(session_id, "user", "保留")

            second = ChatHistoryStore(path)

            self.assertEqual(second.schema_version, CHAT_SCHEMA_VERSION)
            self.assertEqual(
                [item["content"] for item in second.list_messages()],
                ["保留"],
            )

    def test_future_chat_database_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    f"PRAGMA user_version = {CHAT_SCHEMA_VERSION + 1}"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(FutureSchemaVersionError):
                ChatHistoryStore(path)

            connection = sqlite3.connect(path)
            try:
                version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(version, CHAT_SCHEMA_VERSION + 1)
            self.assertEqual(tables, [])

    def test_failed_chat_migration_rolls_back_ddl_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"

            def fail_after_ddl(connection):
                connection.execute(
                    "CREATE TABLE migration_sentinel (id INTEGER)"
                )
                raise StorageMigrationError("simulated failure")

            with patch.dict(CHAT_MIGRATIONS, {0: fail_after_ddl}):
                with self.assertRaisesRegex(
                    StorageMigrationError,
                    "simulated failure",
                ):
                    ChatHistoryStore(path)

            connection = sqlite3.connect(path)
            try:
                version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                sentinel = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name = 'migration_sentinel'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(version, 0)
            self.assertIsNone(sentinel)
