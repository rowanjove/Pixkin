import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.chat_history_store import ChatHistoryStore
from core.services.local_data_backup_service import (
    LocalDataBackupError,
    LocalDataBackupService,
)


class LocalDataBackupTests(unittest.TestCase):
    def _seed(self, root: Path):
        (root / "config.json").write_text(
            '{"schema_version":1,"name":"original"}',
            encoding="utf-8",
        )
        (root / "characters" / "role").mkdir(parents=True)
        (root / "characters" / "role" / "character.md").write_text(
            "role-data",
            encoding="utf-8",
        )
        (root / "pet-lab" / "runs" / "run-1").mkdir(parents=True)
        (root / "pet-lab" / "runs" / "run-1" / "run.json").write_text(
            '{"status":"pending"}',
            encoding="utf-8",
        )
        (root / "profile").mkdir()
        (root / "profile" / "avatar.png").write_bytes(b"avatar")
        (root / "plugins").mkdir()
        (root / "plugins" / "plugin.sqlite3").write_bytes(b"plugin-db")
        (root / "audit").mkdir()
        (root / "audit" / "tool-audit.json").write_text(
            '{"schema_version":1,"events":[]}',
            encoding="utf-8",
        )
        (root / "crashes").mkdir()
        (root / "crashes" / "crash.json").write_text(
            '{"schema_version":1}',
            encoding="utf-8",
        )
        (root / "memories.json").write_text(
            '{"schema_version":1,"enabled":true,"records":[]}',
            encoding="utf-8",
        )
        connection = sqlite3.connect(root / "memory.sqlite3")
        try:
            connection.execute("CREATE TABLE marker(value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('memory-v2')")
            connection.commit()
        finally:
            connection.close()
        chat = ChatHistoryStore(root / "chat-history.sqlite3")
        session = chat.create_session("role", "角色")
        chat.add_message(session, "user", "backup-chat")
        return chat

    def test_export_is_whitelisted_and_hash_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chat = self._seed(root)
            service = LocalDataBackupService(root, chat_store=chat)

            target = service.export(root / "export" / "backup.zip")
            manifest = service.inspect(target)

            self.assertTrue(manifest["contains_private_user_data"])
            self.assertIn("config.json", manifest["files"])
            self.assertIn("chat-history.sqlite3", manifest["files"])
            self.assertIn("memories.json", manifest["files"])
            self.assertIn("memory.sqlite3", manifest["files"])
            self.assertIn(
                "characters/role/character.md",
                manifest["files"],
            )
            self.assertIn(
                "pet-lab/runs/run-1/run.json",
                manifest["files"],
            )
            self.assertIn("plugins/plugin.sqlite3", manifest["files"])
            self.assertIn("audit/tool-audit.json", manifest["files"])
            self.assertIn("crashes/crash.json", manifest["files"])

    def test_restore_is_staged_then_applied_with_previous_data_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chat = self._seed(root)
            service = LocalDataBackupService(root, chat_store=chat)
            archive = service.export(root / "backup.zip")
            service.stage_restore(archive)
            (root / "config.json").write_text(
                '{"schema_version":1,"name":"changed"}',
                encoding="utf-8",
            )
            (root / "characters" / "role" / "character.md").write_text(
                "changed-role",
                encoding="utf-8",
            )
            stale = root / "characters" / "created-after-backup"
            stale.mkdir()
            (stale / "character.md").write_text(
                "must-not-survive",
                encoding="utf-8",
            )

            previous = service.apply_pending_restore()

            self.assertIsNotNone(previous)
            self.assertFalse(service.marker_path.exists())
            self.assertEqual(
                json.loads(
                    (root / "config.json").read_text(encoding="utf-8")
                )["name"],
                "original",
            )
            self.assertEqual(
                (root / "characters" / "role" / "character.md")
                .read_text(encoding="utf-8"),
                "role-data",
            )
            self.assertEqual(
                ChatHistoryStore(
                    root / "chat-history.sqlite3"
                ).list_messages()[0]["content"],
                "backup-chat",
            )
            self.assertTrue(
                (previous / "config.json").is_file()
            )
            self.assertFalse(
                (root / "characters" / "created-after-backup").exists()
            )
            self.assertTrue(
                (
                    previous
                    / "characters"
                    / "created-after-backup"
                    / "character.md"
                ).is_file()
            )
            connection = sqlite3.connect(root / "memory.sqlite3")
            try:
                self.assertEqual(
                    connection.execute("SELECT value FROM marker").fetchone()[0],
                    "memory-v2",
                )
            finally:
                connection.close()

    def test_tampered_archive_is_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chat = self._seed(root)
            service = LocalDataBackupService(root, chat_store=chat)
            archive = service.export(root / "backup.zip")
            with zipfile.ZipFile(archive) as source:
                documents = {
                    name: source.read(name)
                    for name in source.namelist()
                }
            documents["config.json"] = b'{"tampered":true}'
            with zipfile.ZipFile(archive, "w") as target:
                for name, content in documents.items():
                    target.writestr(name, content)

            with self.assertRaises(LocalDataBackupError):
                service.stage_restore(archive)

            self.assertFalse(service.marker_path.exists())

    def test_marker_cannot_escape_staging_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.mkdir(exist_ok=True)
            service = LocalDataBackupService(root)
            service.marker_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "restore_id": "bad",
                        "stage": str(root.parent),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(LocalDataBackupError):
                service.apply_pending_restore()

    def test_manifest_non_object_is_rejected_as_domain_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = LocalDataBackupService(root)
            archive = root / "malformed.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("manifest.json", b"[]")
            with self.assertRaisesRegex(
                LocalDataBackupError,
                "根节点",
            ):
                service.inspect(archive)

    def test_malformed_marker_root_is_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = LocalDataBackupService(root)
            service.marker_path.write_text("[]", encoding="utf-8")
            with self.assertRaises(LocalDataBackupError):
                service.apply_pending_restore()

    def test_marker_restore_id_and_schema_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = LocalDataBackupService(root)
            stage = service.staging_root / ("a" * 32)
            (stage / "data").mkdir(parents=True)
            (stage / "manifest.json").write_text(
                json.dumps({"schema_version": 1, "files": {}}),
                encoding="utf-8",
            )
            service.marker_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "restore_id": "../escape",
                        "stage": str(stage),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(LocalDataBackupError):
                service.apply_pending_restore()

            service.marker_path.write_text(
                json.dumps(
                    {
                        "schema_version": 999,
                        "restore_id": "a" * 32,
                        "stage": str(stage),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(LocalDataBackupError):
                service.apply_pending_restore()
