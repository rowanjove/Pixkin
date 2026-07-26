import io
import logging
import tempfile
import unittest
from pathlib import Path

from core.app_logging import SecretRedactionFilter
from core.chat_history_store import ChatHistoryStore
from core.services.chat_service import ChatSessionService


class PrivacyRedactionTests(unittest.TestCase):
    def test_chat_database_redacts_key_but_runtime_context_keeps_message(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            service = ChatSessionService(store)
            service.activate_character("role", "角色")
            secret = "sk-project-secretvalue12345"

            service.add_message(
                "user",
                f"临时使用 {secret}",
                {"api_key": secret},
            )

            self.assertIn(secret, service.context()[0]["content"])
            persisted = store.list_messages()[0]
            self.assertNotIn(secret, persisted["content"])
            self.assertEqual(
                persisted["metadata"]["api_key"],
                "<redacted-secret>",
            )
            self.assertNotIn(
                secret,
                Path(store.database_path).read_bytes().decode(
                    "utf-8",
                    errors="ignore",
                ),
            )

    def test_logging_filter_redacts_message_and_arguments(self):
        output = io.StringIO()
        logger = logging.Logger("privacy-test")
        handler = logging.StreamHandler(output)
        handler.addFilter(SecretRedactionFilter())
        logger.addHandler(handler)
        secret = "Bearer abcdefghijklmnop"

        logger.warning("provider failed: %s", secret)

        rendered = output.getvalue()
        self.assertNotIn("abcdefghijklmnop", rendered)
        self.assertIn("<redacted-secret>", rendered)

    def test_token_metrics_are_not_mistaken_for_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            service = ChatSessionService(store)
            service.activate_character("role", "角色")

            service.add_message(
                "assistant",
                "完成",
                {
                    "chat_metrics": {
                        "input_tokens": 128,
                        "output_tokens": 64,
                        "max_tokens": 4096,
                    },
                    "access_token": "Bearer abcdefghijklmnop",
                },
            )

            metadata = store.list_messages()[0]["metadata"]
            self.assertEqual(metadata["chat_metrics"]["input_tokens"], 128)
            self.assertEqual(metadata["chat_metrics"]["output_tokens"], 64)
            self.assertEqual(metadata["chat_metrics"]["max_tokens"], 4096)
            self.assertEqual(
                metadata["access_token"],
                "<redacted-secret>",
            )
