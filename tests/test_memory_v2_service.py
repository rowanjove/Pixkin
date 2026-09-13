import json
import tempfile
import unittest
from pathlib import Path

from core.services.memory_v2_service import MemoryV2Service, MemoryV2StoreError


class MemoryV2Tests(unittest.TestCase):
    def test_legacy_json_migrates_once_and_preserves_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "memories.json"
            legacy.write_text(json.dumps({
                "schema_version": 1,
                "enabled": True,
                "records": [{
                    "id": "legacy-1",
                    "user_id": "u",
                    "character_id": "c",
                    "content": "用户喜欢 PyQt",
                    "enabled": True,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }],
            }, ensure_ascii=False), encoding="utf-8")
            db = root / "memory.sqlite3"
            service = MemoryV2Service(db, legacy_json_path=legacy)
            self.assertEqual(service.list(user_id="u", character_id="c")[0].id, "legacy-1")
            self.assertEqual(service.search("PyQt")[0].content, "用户喜欢 PyQt")
            service.close()
            again = MemoryV2Service(db, legacy_json_path=legacy)
            self.assertEqual(len(again.list()), 1)
            again.close()

    def test_supersession_and_disable_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MemoryV2Service(Path(directory) / "memory.sqlite3")
            old = service.add(user_id="u", character_id="c", content="住在北京")
            new = service.supersede(old.id, content="已经搬到上海")
            records = {record.id: record for record in service.list(include_disabled=True)}
            self.assertFalse(records[old.id].enabled)
            self.assertEqual(service.used_for(user_id="u", character_id="c")[0].content, "已经搬到上海")
            service.delete(new.id)
            self.assertEqual(service.used_for(user_id="u", character_id="c"), [])
            service.close()

    def test_non_active_status_cannot_remain_prompt_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MemoryV2Service(Path(directory) / "memory.sqlite3")
            record = service.add(
                user_id="u",
                character_id="c",
                content="只允许在本地查看",
            )
            updated = service.update(record.id, status="deleted")
            self.assertFalse(updated.enabled)
            self.assertEqual(updated.status, "deleted")
            self.assertEqual(
                service.used_for(user_id="u", character_id="c"),
                [],
            )
            service.close()

    def test_future_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            import sqlite3
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
            connection.close()
            with self.assertRaises(MemoryV2StoreError):
                MemoryV2Service(path)

    def test_malformed_legacy_json_is_reported_as_store_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "memories.json"
            legacy.write_text("[]", encoding="utf-8")
            with self.assertRaises(MemoryV2StoreError):
                MemoryV2Service(root / "memory.sqlite3", legacy_json_path=legacy)

    def test_physical_delete_cleans_fts_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            service = MemoryV2Service(path)
            try:
                item = service.add(user_id="u", character_id="c", content="likes Python programming")
                self.assertEqual(len(service.search("Python")), 1)
                # 执行物理底层 DELETE
                with service._connection:
                    service._connection.execute("DELETE FROM memories WHERE id=?", (item.id,))
                # 确认 FTS 表已被触发器同步清理
                fts_count = service._connection.execute(
                    "SELECT count(*) FROM memory_fts WHERE memory_id=?", (item.id,)
                ).fetchone()[0]
                self.assertEqual(fts_count, 0)
            finally:
                service.close()

    def test_concurrent_multithreaded_read_write(self):
        import concurrent.futures

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            service = MemoryV2Service(path)
            try:
                def writer(idx):
                    return service.add(
                        user_id="u",
                        character_id="c",
                        content=f"并发写入记忆 {idx}",
                    )

                def reader(_):
                    return service.search("并发")

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    write_futures = [pool.submit(writer, i) for i in range(20)]
                    read_futures = [pool.submit(reader, i) for i in range(20)]
                    concurrent.futures.wait(write_futures + read_futures)

                self.assertEqual(len(service.list(user_id="u", character_id="c")), 20)
            finally:
                service.close()

    def test_search_wildcard_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            service = MemoryV2Service(path)
            try:
                service.add(user_id="u", character_id="c", content="准确率达到了 100% 很高")
                service.add(user_id="u", character_id="c", content="准确率达到了 1000 很高")
                # "100%" 触发 FTS5 OperationalError 进入 LIKE fallback，转义后应只匹配含有真实 "%" 的记录
                results = service.search("100%")
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].content, "准确率达到了 100% 很高")
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
