import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.services.tool_audit_store import ToolAuditStore
from core.services.tool_permission_service import (
    ToolAuditEvent,
    ToolPermissionLevel,
    ToolPermissionService,
)


def audit_event(index: int, arguments=None) -> ToolAuditEvent:
    return ToolAuditEvent(
        timestamp=float(index),
        tool_name=f"tool_{index}",
        level=ToolPermissionLevel.READ_ONLY,
        arguments=arguments or {"index": index},
        authorization="automatic",
        result_status="success",
        duration_ms=index,
    )


class ToolAuditStoreTests(unittest.TestCase):
    def test_append_is_durable_and_reloadable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit" / "tool-audit.json"
            store = ToolAuditStore(path)

            store.append(audit_event(1))
            reloaded = ToolAuditStore(path)

            self.assertEqual(len(reloaded.events()), 1)
            self.assertEqual(reloaded.events()[0].tool_name, "tool_1")
            self.assertEqual(reloaded.events()[0].arguments, {"index": 1})

    def test_store_keeps_only_newest_events_within_caps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool-audit.json"
            store = ToolAuditStore(path, max_events=2, max_bytes=4096)

            for index in range(4):
                store.append(audit_event(index))

            self.assertEqual(
                [event.tool_name for event in store.events()],
                ["tool_2", "tool_3"],
            )
            self.assertLessEqual(path.stat().st_size, 4096)

    def test_atomic_replace_failure_preserves_file_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool-audit.json"
            store = ToolAuditStore(path)
            store.append(audit_event(1))
            original = path.read_bytes()

            with patch(
                "core.services.tool_audit_store.os.replace",
                side_effect=OSError("locked"),
            ):
                with self.assertRaises(OSError):
                    store.append(audit_event(2))

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(
                [event.tool_name for event in store.events()],
                ["tool_1"],
            )
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_sanitized_event_does_not_persist_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool-audit.json"
            store = ToolAuditStore(path)
            service = ToolPermissionService(
                clock=lambda: 1.0,
                audit_sink=store.append,
            )
            request, decision = service.authorize(
                "read",
                ToolPermissionLevel.READ_ONLY,
                {
                    "api_key": "secret-value",
                    "url": "https://user:pass@example.com/a?token=hidden",
                },
                "",
            )

            service.record(
                request,
                decision,
                result_status="success",
                duration_ms=4,
            )

            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-value", raw)
            self.assertNotIn("hidden", raw)
            self.assertNotIn("user:pass", raw)
            payload = json.loads(raw)
            self.assertEqual(
                payload["events"][0]["arguments"]["api_key"],
                "<redacted>",
            )

    def test_audit_write_failure_does_not_break_tool_result(self):
        def fail(_event):
            raise OSError("disk unavailable")

        service = ToolPermissionService(audit_sink=fail)
        request, decision = service.authorize(
            "read",
            ToolPermissionLevel.READ_ONLY,
            {},
            "",
        )

        event = service.record(
            request,
            decision,
            result_status="success",
            duration_ms=1,
        )

        self.assertEqual(event.result_status, "success")
        self.assertEqual(len(service.events()), 1)

    def test_clear_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool-audit.json"
            store = ToolAuditStore(path)
            store.append(audit_event(1))

            store.clear()

            self.assertEqual(store.events(), ())
            self.assertEqual(ToolAuditStore(path).events(), ())

    def test_export_is_atomic_and_reloadable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store = ToolAuditStore(base / "tool-audit.json")
            store.append(audit_event(1))

            target = store.export(base / "exports" / "audit.json")

            self.assertEqual(
                ToolAuditStore(target).events()[0].tool_name,
                "tool_1",
            )

    def test_corrupt_root_is_quarantined_and_rebuilt_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool-audit.json"
            path.write_bytes(b"[]")

            store = ToolAuditStore(path)

            self.assertEqual(store.events(), ())
            self.assertFalse(path.exists())
            self.assertEqual(
                len(list(path.parent.glob("tool-audit.json.corrupt-*"))),
                1,
            )

    def test_invalid_utf8_is_quarantined_and_rebuilt_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool-audit.json"
            path.write_bytes(b"\xff")

            store = ToolAuditStore(path)

            self.assertEqual(store.events(), ())
            self.assertFalse(path.exists())
