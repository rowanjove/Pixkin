import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.app_logging import configure_logging
from core.chat_history_store import ChatHistoryStore
from core.config import ConfigManager
from core.providers.chat.openai_compatible import (
    OpenAICompatibleChatProvider,
)
from core.secrets import (
    CHAT_TARGET, LEGACY_TARGET, SecretStore
)
from core.services.tool_permission_service import ToolPermissionService
from core.tool_registry import ToolRegistry


class ConfigManagerTests(unittest.TestCase):
    def test_default_chat_model_is_deepseek_v4_flash(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            self.assertEqual(
                config.get("api", "model"), "deepseek-v4-flash"
            )

    def test_legacy_deepseek_model_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({
                "api": {
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                }
            }), encoding="utf-8")

            config = ConfigManager(str(path))

            self.assertEqual(
                config.get("api", "model"), "deepseek-v4-flash"
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["api"]["model"],
                "deepseek-v4-flash",
            )

    def test_missing_defaults_are_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"pet": {"scale": 1.2}}), encoding="utf-8")
            config = ConfigManager(str(path))
        self.assertEqual(config.get("pet", "scale"), 1.2)
        self.assertTrue(config.get("pet", "edge_dock_enabled"))
        self.assertEqual(
            config.get("pet", "edge_sides"),
            {"left": True, "right": True, "top": True, "bottom": False},
        )

    def test_update_section_persists_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = ConfigManager(str(path))
            self.assertTrue(config.update_section("pet", {"scale": 1.4, "peek_size": 50}))
            reloaded = ConfigManager(str(path))
            self.assertEqual(reloaded.get("pet", "scale"), 1.4)
            self.assertEqual(reloaded.get("pet", "peek_size"), 50)

    def test_failed_atomic_save_rolls_back_memory_and_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = ConfigManager(str(path))
            original = path.read_text(encoding="utf-8")
            with patch("core.config.os.replace", side_effect=OSError("locked")):
                saved = config.update_section("pet", {"scale": 1.5})
            self.assertFalse(saved)
            self.assertEqual(config.get("pet", "scale"), 1.0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse(list(Path(directory).glob(".pixkin-config-*.tmp")))

    def test_corrupt_config_is_backed_up_before_future_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            corrupt = b'{"broken":'
            path.write_bytes(corrupt)

            config = ConfigManager(str(path))
            self.assertTrue(config.set("pet", "scale", 1.25))

            backups = list(
                Path(directory).glob(
                    "config.json.corrupt-*.bak"
                )
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), corrupt)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))[
                    "pet"
                ]["scale"],
                1.25,
            )


class LoggingTests(unittest.TestCase):
    def test_desktop_pet_children_write_to_configured_log(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = logging.getLogger("desktop_pet")
            previous_handlers = list(logger.handlers)
            for handler in previous_handlers:
                logger.removeHandler(handler)
            try:
                with patch(
                    "core.app_logging.logs_dir", return_value=Path(directory)
                ):
                    configured = configure_logging()
                logging.getLogger("desktop_pet.main").error("recorded-message")
                for handler in configured.handlers:
                    handler.flush()
                content = (Path(directory) / "pixkin.log").read_text(
                    encoding="utf-8"
                )
                self.assertIn("recorded-message", content)
            finally:
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()
                for handler in previous_handlers:
                    logger.addHandler(handler)


class SecretStoreTests(unittest.TestCase):
    @staticmethod
    def _fake_win32cred(delete):
        return SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CRED_PERSIST_LOCAL_MACHINE=2,
            CredDelete=delete,
        )

    def test_credential_delete_failure_is_reported(self):
        def fail_delete(*_args):
            raise OSError(5, "access denied")

        fake = self._fake_win32cred(fail_delete)
        with patch.dict(sys.modules, {"win32cred": fake}):
            self.assertFalse(SecretStore._write("test", "", ""))

    def test_missing_credential_counts_as_deleted(self):
        def missing(*_args):
            raise OSError(1168, "not found")

        fake = self._fake_win32cred(missing)
        with patch.dict(sys.modules, {"win32cred": fake}):
            self.assertTrue(SecretStore._write("test", "", ""))

    def test_credential_write_passes_unicode_blob_to_pywin32(self):
        written = {}

        def write(credential, flags):
            written.update(credential)
            written["flags"] = flags

        fake = SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CRED_PERSIST_LOCAL_MACHINE=2,
            CredWrite=write,
        )
        with patch.dict(sys.modules, {"win32cred": fake}):
            self.assertTrue(
                SecretStore._write("test", "sk-测试", "Pixkin test")
            )

        self.assertEqual(written["CredentialBlob"], "sk-测试")
        self.assertIsInstance(written["CredentialBlob"], str)
        self.assertEqual(written["TargetName"], "test")
        self.assertEqual(written["flags"], 0)

    def test_clearing_chat_key_removes_new_and_legacy_targets(self):
        values = {
            CHAT_TARGET: "new-secret",
            LEGACY_TARGET: "legacy-secret",
        }

        def read(target):
            return values.get(target, "")

        def write(target, value, _comment):
            if value:
                values[target] = value
            else:
                values.pop(target, None)
            return True

        with patch.object(SecretStore, "_read", side_effect=read), patch.object(
            SecretStore, "_write", side_effect=write
        ):
            self.assertTrue(SecretStore.set_api_key(""))
            self.assertEqual(SecretStore.get_api_key(), "")
        self.assertEqual(values, {})

    def test_legacy_key_is_migrated_and_removed(self):
        values = {LEGACY_TARGET: "legacy-secret"}

        def read(target):
            return values.get(target, "")

        def write(target, value, _comment):
            if value:
                values[target] = value
            else:
                values.pop(target, None)
            return True

        with patch.object(SecretStore, "_read", side_effect=read), patch.object(
            SecretStore, "_write", side_effect=write
        ):
            self.assertTrue(SecretStore.migrate_legacy_api_key())
        self.assertEqual(values, {CHAT_TARGET: "legacy-secret"})


class AiStreamingTests(unittest.TestCase):
    def test_streaming_text_and_fragmented_tool_call_are_assembled(self):
        class FakeStream(list):
            closed = False

            def close(self):
                self.closed = True

        def chunk(content=None, calls=None):
            delta = SimpleNamespace(content=content, tool_calls=calls or [])
            return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

        stream = FakeStream([
            chunk("你好"),
            chunk(calls=[
                SimpleNamespace(
                    index=0,
                    id="call-1",
                    type="function",
                    function=SimpleNamespace(
                        name="get_current_", arguments="{"
                    ),
                )
            ]),
            chunk(calls=[
                SimpleNamespace(
                    index=0,
                    id=None,
                    type=None,
                    function=SimpleNamespace(name="time", arguments="}"),
                )
            ]),
        ])
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_request: stream)
            )
        )
        provider = OpenAICompatibleChatProvider(
            api_key="test",
            base_url="https://example.test/v1",
            client=client,
        )

        chunks = []
        result = provider.stream_completion(
            model="test",
            messages=[],
            tools=[],
            on_chunk=chunks.append,
            is_cancelled=lambda: False,
        )

        self.assertEqual(result.text, "你好")
        self.assertEqual(chunks, ["你好"])
        self.assertEqual(result.tool_calls[0]["id"], "call-1")
        self.assertEqual(
            result.tool_calls[0]["function"]["name"],
            "get_current_time",
        )
        self.assertEqual(
            result.tool_calls[0]["function"]["arguments"],
            "{}",
        )
        self.assertTrue(stream.closed)


class ToolRegistryTests(unittest.TestCase):
    def test_parameterless_tools_execute(self):
        registry = ToolRegistry()
        self.assertNotIn("Error", registry.execute_tool("get_current_time", {}))
        self.assertIn("OS:", registry.execute_tool("get_system_info", {}))

    def test_unknown_tool_is_rejected(self):
        registry = ToolRegistry()
        self.assertIn("not found", registry.execute_tool("missing_tool", {}))

    def test_calculator_accepts_arithmetic_and_rejects_code(self):
        registry = ToolRegistry()
        self.assertEqual(
            registry.execute_tool(
                "calculate_expression", {"expression": "(12 + 8) * 2"}
            ),
            "(12 + 8) * 2 = 40",
        )
        rejected = registry.execute_tool(
            "calculate_expression",
            {"expression": "__import__('os').system('whoami')"},
        )
        self.assertIn("Error executing tool", rejected)

    def test_open_url_restricts_protocols(self):
        registry = ToolRegistry(
            ToolPermissionService(confirm_external=lambda _request: True)
        )
        with patch.object(
            registry, "_execute_isolated", return_value="已使用默认浏览器打开：https://example.com/path"
        ) as execute_isolated:
            result = registry.execute_tool(
                "open_url", {"url": "https://example.com/path"}
            )
        self.assertIn("已使用默认浏览器打开", result)
        execute_isolated.assert_called_once()
        with patch("core.tool_registry.webbrowser.open", return_value=True) as open_url:
            self.assertIn(
                "已使用默认浏览器打开",
                registry._tool_open_url("https://example.com/path"),
            )
        open_url.assert_called_once_with("https://example.com/path", new=2)
        self.assertIn(
            "Error executing tool",
            registry.execute_tool("open_url", {"url": "file:///secret.txt"}),
        )


class ChatHistoryStoreTests(unittest.TestCase):
    def test_characters_keep_independent_active_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            shanshan = store.get_or_create_active_session(
                "shanshan", "山山"
            )
            store.add_message(shanshan, "user", "山山的消息")
            linlin = store.get_or_create_active_session(
                "linlin", "凛凛"
            )
            store.add_message(linlin, "user", "凛凛的消息")

            self.assertEqual(
                store.get_or_create_active_session("shanshan", "山山"),
                shanshan,
            )
            self.assertEqual(
                [item["content"] for item in store.session_messages(shanshan)],
                ["山山的消息"],
            )
            self.assertEqual(
                [item["content"] for item in store.session_messages(linlin)],
                ["凛凛的消息"],
            )

    def test_new_session_preserves_old_history_and_context_filters_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            first = store.create_session("shanshan", "山山")
            store.add_message(first, "user", "你好")
            store.add_message(first, "system", "正在使用工具")
            store.add_message(first, "assistant", "你好呀")
            second = store.create_session("shanshan", "山山")

            self.assertNotEqual(first, second)
            self.assertEqual(
                store.context_messages(first),
                [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好呀"},
                ],
            )
            self.assertEqual(store.session_messages(second), [])
            self.assertEqual(
                store.get_or_create_active_session("shanshan", "山山"),
                second,
            )

    def test_history_can_be_deleted_by_day_without_touching_other_role(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatHistoryStore(Path(directory) / "history.sqlite3")
            first = store.create_session("shanshan", "山山")
            second = store.create_session("linlin", "凛凛")
            store.add_message(
                first, "user", "山山今天", created_at="2026-07-25T10:00:00+08:00"
            )
            store.add_message(
                first, "user", "山山昨天", created_at="2026-07-24T10:00:00+08:00"
            )
            store.add_message(
                second, "user", "凛凛今天", created_at="2026-07-25T11:00:00+08:00"
            )

            self.assertEqual(
                store.delete_date("2026-07-25", "shanshan"), 1
            )
            remaining = store.list_messages()
            self.assertEqual(
                {item["content"] for item in remaining},
                {"山山昨天", "凛凛今天"},
            )


if __name__ == "__main__":
    unittest.main()
