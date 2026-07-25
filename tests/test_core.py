import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.ai_engine import AiWorkerThread
from core.app_logging import configure_logging
from core.config import ConfigManager
from core.secrets import (
    CHAT_TARGET, LEGACY_TARGET, SecretStore
)
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
        worker = AiWorkerThread(
            base_url="https://example.test/v1",
            api_key="test",
            model="test",
            system_prompt="test",
            messages=[],
            tool_registry=ToolRegistry(),
        )

        text, calls = worker._stream_completion(client, {"stream": True})

        self.assertEqual(text, "你好")
        self.assertEqual(calls[0]["id"], "call-1")
        self.assertEqual(calls[0]["function"]["name"], "get_current_time")
        self.assertEqual(calls[0]["function"]["arguments"], "{}")
        self.assertTrue(stream.closed)


class ToolRegistryTests(unittest.TestCase):
    def test_parameterless_tools_execute(self):
        registry = ToolRegistry()
        self.assertNotIn("Error", registry.execute_tool("get_current_time", {}))
        self.assertIn("OS:", registry.execute_tool("get_system_info", {}))

    def test_unknown_tool_is_rejected(self):
        registry = ToolRegistry()
        self.assertIn("not found", registry.execute_tool("missing_tool", {}))


if __name__ == "__main__":
    unittest.main()
