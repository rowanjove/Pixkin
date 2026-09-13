import time
import tempfile
import unittest
from pathlib import Path

from core.services.tool_permission_service import ToolPermissionLevel
from core.tool_registry import ToolExecutionContext, ToolRegistry


def _slow_write(path: str) -> str:
    time.sleep(0.3)
    Path(path).write_text("side effect", encoding="utf-8")
    return "done"


class ToolRegistryRuntimeTests(unittest.TestCase):
    def test_isolated_timeout_kills_handler_before_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "side-effect.txt"
            registry = ToolRegistry()
            registry.register_tool(
                "isolated_slow",
                "slow",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                _slow_write,
                timeout_seconds=0.05,
            )
            result = registry.execute_tool(
                "isolated_slow",
                {"path": str(target)},
            )
            self.assertIn("timed out", result)
            time.sleep(0.4)
            self.assertFalse(target.exists())

    def test_side_effect_tool_requires_serializable_handler(self):
        registry = ToolRegistry()
        with self.assertRaises(ValueError):
            registry.register_tool(
                "unsafe_external",
                "external",
                {"type": "object"},
                lambda: "done",
                permission_level=ToolPermissionLevel.EXTERNAL_ACTION,
            )

    def test_timeout_is_audited_and_context_is_recorded(self):
        registry = ToolRegistry()
        registry.register_tool(
            "slow_test",
            "slow",
            {"type": "object", "properties": {}, "required": []},
            lambda: time.sleep(0.3),
            timeout_seconds=0.1,
        )
        result = registry.execute_tool(
            "slow_test",
            {},
            context=ToolExecutionContext(
                session_id="session", character_id="char", plugin_id="plugin"
            ),
        )
        self.assertIn("timed out", result)
        event = registry.permission_service.events()[-1]
        self.assertEqual(event.result_status, "timeout")
        self.assertEqual(event.session_id, "session")
        self.assertEqual(event.character_id, "char")
        self.assertEqual(event.plugin_id, "plugin")

    def test_duplicate_tool_names_are_rejected(self):
        registry = ToolRegistry()
        with self.assertRaises(ValueError):
            registry.register_tool(
                "get_current_time", "duplicate", {"type": "object"}, lambda: "x",
                permission_level=ToolPermissionLevel.READ_ONLY,
            )

    def test_open_application_unauthorized_app_is_rejected(self):
        registry = ToolRegistry()
        result = registry._tool_open_application("cmd.exe")
        self.assertIn("未授权或不支持打开该应用", result)

    def test_open_application_executes_allowed_app_or_handles_oserror(self):
        from unittest.mock import patch
        registry = ToolRegistry()
        with patch("subprocess.Popen") as mock_popen:
            result = registry._tool_open_application("notepad")
            self.assertIn("已为您成功启动应用", result)
            mock_popen.assert_called_once()
            called_cmd = mock_popen.call_args[0][0]
            self.assertTrue(str(called_cmd).lower().endswith("notepad.exe"))

        with patch("subprocess.Popen", side_effect=OSError("Access denied")):
            result = registry._tool_open_application("calc")
            self.assertIn("启动应用 'calc' 失败: Access denied", result)


if __name__ == "__main__":
    unittest.main()
