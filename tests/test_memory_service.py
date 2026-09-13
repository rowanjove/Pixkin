import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.services.memory_service import MemoryService, MemoryStoreError


class MemoryServiceTests(unittest.TestCase):
    def test_candidates_are_local_only_deduplicated_and_secret_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            service = MemoryService(path)

            candidates = service.extract_candidates(
                [
                    {"role": "assistant", "content": "我喜欢假的"},
                    {"role": "user", "content": "我叫小林。我喜欢无糖咖啡。"},
                    {"role": "user", "content": "请记住 我喜欢无糖咖啡"},
                    {"role": "user", "content": "请记住 sk-abcdefghijk"},
                ]
            )

            self.assertIn("我叫小林", candidates)
            self.assertIn("我喜欢无糖咖啡", candidates)
            self.assertNotIn("sk-abcdefghijk", candidates)
            self.assertFalse(path.exists())

    def test_crud_scope_disable_and_prompt_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            service = MemoryService(path)
            first = service.add(
                user_id="user-a",
                character_id="cat-a",
                content="喜欢无糖咖啡",
            )
            service.add(
                user_id="user-a",
                character_id="cat-b",
                content="另一个角色才知道",
            )

            used = service.used_for(
                user_id="user-a",
                character_id="cat-a",
            )

            self.assertEqual([item.id for item in used], [first.id])
            self.assertIn(
                "喜欢无糖咖啡",
                MemoryService.prompt_fragment(used),
            )
            service.update(first.id, content="喜欢淡咖啡", enabled=False)
            self.assertEqual(
                service.used_for(
                    user_id="user-a",
                    character_id="cat-a",
                ),
                [],
            )
            service.update(first.id, enabled=True)
            service.set_enabled(False)
            self.assertEqual(
                service.used_for(
                    user_id="user-a",
                    character_id="cat-a",
                ),
                [],
            )
            self.assertTrue(service.delete(first.id))
            self.assertFalse(service.delete(first.id))

    def test_store_is_atomic_reloadable_bounded_and_rejects_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            service = MemoryService(path)
            service.add(
                user_id="user",
                character_id="character",
                content="偏好中文回答",
            )

            reloaded = MemoryService(path)

            self.assertEqual(
                reloaded.list()[0].content,
                "偏好中文回答",
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))[
                    "schema_version"
                ],
                MemoryService.SCHEMA_VERSION,
            )
            with self.assertRaises(MemoryStoreError):
                reloaded.add(
                    user_id="user",
                    character_id="character",
                    content="token Bearer abcdefghijk",
                )

    def test_failed_save_does_not_mutate_in_memory_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MemoryService(
                Path(directory) / "memories.json"
            )
            with patch.object(
                service,
                "_save",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(OSError):
                    service.add(
                        user_id="user",
                        character_id="character",
                        content="不应被保留",
                    )
                with self.assertRaises(OSError):
                    service.set_enabled(False)

            self.assertEqual(service.list(), [])
            self.assertTrue(service.enabled)

    def test_non_object_and_invalid_utf8_are_domain_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(MemoryStoreError, "根节点"):
                MemoryService(path)

            path.write_bytes(b"\xff")
            with self.assertRaises(MemoryStoreError):
                MemoryService(path)


if __name__ == "__main__":
    unittest.main()
