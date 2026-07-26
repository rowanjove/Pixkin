import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.chat_history_store import (
    ChatDatabaseCorruptError,
    ChatHistoryStore,
)
from core.services.chat_database_recovery_service import (
    ChatDatabaseRecoveryService,
)


class ChatDatabaseReliabilityTests(unittest.TestCase):
    def test_existing_unversioned_database_is_backed_up_before_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE legacy_note (value TEXT)"
            )
            connection.execute(
                "INSERT INTO legacy_note VALUES ('keep')"
            )
            connection.commit()
            connection.close()

            store = ChatHistoryStore(path)

            backups = list(
                (path.parent / "backups").glob(
                    "chat-pre-migration-*.sqlite3"
                )
            )
            self.assertEqual(len(backups), 1)
            backup = sqlite3.connect(backups[0])
            try:
                value = backup.execute(
                    "SELECT value FROM legacy_note"
                ).fetchone()[0]
            finally:
                backup.close()
            self.assertEqual(value, "keep")
            self.assertEqual(store.schema_version, 1)

    def test_corrupt_database_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            path.write_bytes(b"not a sqlite database")

            with self.assertRaises(ChatDatabaseCorruptError):
                ChatHistoryStore(path)

            self.assertEqual(
                path.read_bytes(),
                b"not a sqlite database",
            )

    def test_readonly_export_and_verified_rebuild_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / "history.sqlite3"
            store = ChatHistoryStore(path)
            session = store.create_session("role", "角色")
            store.add_message(session, "user", "recover me")
            recovery = ChatDatabaseRecoveryService(path)

            export = recovery.export_readonly(base / "recovered.json")
            backup = recovery.backup_and_remove(base / "backups")

            payload = json.loads(export.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["messages"][0]["content"],
                "recover me",
            )
            self.assertTrue(backup.is_file())
            self.assertFalse(path.exists())
            rebuilt = ChatHistoryStore(path)
            self.assertEqual(rebuilt.list_messages(), [])

    def test_integrity_check_and_atomic_manual_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store = ChatHistoryStore(base / "history.sqlite3")
            session = store.create_session("role", "角色")
            store.add_message(session, "user", "backup")

            healthy, detail = store.check_integrity()
            backup = store.backup(base / "manual" / "chat.sqlite3")

            self.assertTrue(healthy)
            self.assertEqual(detail, "ok")
            self.assertEqual(
                ChatHistoryStore(backup).list_messages()[0]["content"],
                "backup",
            )
