import unittest
from datetime import datetime

from core.pet_animator import PetState
from core.services.context_sensor_service import (
    ContextSensorService,
    DesktopContextSnapshot,
)
from core.services.proactive_companion_service import (
    ProactiveCompanionService,
    ProactivePolicyConfig,
)
from core.runtime.permissions import ContextPermissionService, PermissionResource, PermissionState
from core.services.perception_context import ContextBudget, PerceptionContextProjector


class ContextAndProactiveServiceTests(unittest.TestCase):
    def test_context_sensor_poll_with_custom_resolvers(self):
        permissions = ContextPermissionService()
        permissions.set_state(
            PermissionResource.WINDOW_METADATA,
            PermissionState.ALLOW_SESSION,
        )
        permissions.set_state(
            PermissionResource.SYSTEM_STATE,
            PermissionState.ALLOW_SESSION,
        )
        sensor = ContextSensorService(
            get_window_info=lambda: ("Visual Studio Code", "Code.exe", False),
            get_idle_seconds=lambda: 12.5,
            time_fn=lambda: 1000.0,
            permission_service=permissions,
        )
        snapshot = sensor.poll()
        self.assertEqual(snapshot.active_window_title, "Visual Studio Code")
        self.assertEqual(snapshot.process_name, "Code.exe")
        self.assertEqual(snapshot.idle_seconds, 12.5)
        self.assertFalse(snapshot.is_fullscreen)
        self.assertEqual(snapshot.timestamp, 1000.0)

    def test_permission_gates_clipboard_and_screen_capture(self):
        permissions = ContextPermissionService()
        calls = []
        sensor = ContextSensorService(
            get_window_info=lambda: ("Editor", "code.exe", False),
            get_idle_seconds=lambda: 2.0,
            get_clipboard_text=lambda: calls.append("clipboard") or "traceback",
            get_screen_snapshot=lambda: calls.append("screen") or b"PNG",
            permission_service=permissions,
            time_fn=lambda: 1.0,
        )
        denied = sensor.poll()
        self.assertEqual(denied.clipboard_text, "")
        self.assertEqual(denied.screen_snapshot, b"")
        permissions.set_state(PermissionResource.CLIPBOARD, PermissionState.ALLOW_SESSION)
        permissions.set_state(PermissionResource.SCREEN, PermissionState.ALLOW_SESSION)
        allowed = sensor.poll()
        self.assertEqual(allowed.clipboard_text, "traceback")
        self.assertEqual(allowed.screen_snapshot, b"")
        self.assertEqual(calls, ["clipboard"])

        captured = sensor.capture_screen_snapshot()
        self.assertEqual(captured, b"PNG")
        self.assertEqual(calls, ["clipboard", "screen"])

    def test_context_sensor_defaults_to_deny_and_poll_never_captures_screen(self):
        calls = []
        sensor = ContextSensorService(
            get_window_info=lambda: calls.append("window") or ("secret", "x.exe", False),
            get_screen_snapshot=lambda: calls.append("screen") or b"PNG",
        )

        snapshot = sensor.poll()

        self.assertEqual(snapshot.active_window_title, "")
        self.assertEqual(snapshot.screen_snapshot, b"")
        self.assertEqual(calls, [])

    def test_background_poll_does_not_resolve_ask_or_read(self):
        permission_calls = []
        resolver_calls = []
        permissions = ContextPermissionService(
            ask_callback=lambda resource, operation: permission_calls.append(
                (resource, operation)
            ) or True
        )
        permissions.set_state(
            PermissionResource.WINDOW_METADATA,
            PermissionState.ASK,
        )
        sensor = ContextSensorService(
            get_window_info=lambda: resolver_calls.append("window")
            or ("private", "secret.exe", False),
            permission_service=permissions,
        )

        snapshot = sensor.poll(resolve_ask=False)

        self.assertEqual(snapshot.active_window_title, "")
        self.assertEqual(permission_calls, [])
        self.assertEqual(resolver_calls, [])

    def test_context_projector_only_includes_relevant_clipboard_with_budget(self):
        snapshot = DesktopContextSnapshot(
            "Editor", "code.exe", 12.0, False, 1.0,
            clipboard_text="Traceback: " + "x" * 500,
        )
        projector = PerceptionContextProjector(
            ContextBudget(max_chars=120, max_clipboard_chars=80)
        )
        self.assertNotIn(
            "clipboard", projector.project(snapshot, query="今天天气怎么样")
        )
        values = projector.project(snapshot, query="这个 traceback 怎么回事")
        self.assertLessEqual(
            sum(len(f"{key}={value}") + 1 for key, value in values.items()),
            120,
        )
        self.assertIn("clipboard", values)

    def test_proactive_service_disabled(self):
        cfg = ProactivePolicyConfig(enabled=False)
        service = ProactiveCompanionService(cfg)
        snap = DesktopContextSnapshot("test", "test.exe", 0.0, False, 100.0)
        self.assertIsNone(service.evaluate(snap))

    def test_proactive_service_fullscreen_suppression(self):
        cfg = ProactivePolicyConfig(enabled=True, quiet_fullscreen=True)
        service = ProactiveCompanionService(cfg)
        snap = DesktopContextSnapshot("Game", "game.exe", 0.0, True, 100.0)
        self.assertIsNone(service.evaluate(snap))

    def test_proactive_service_work_stretch_reminder(self):
        cfg = ProactivePolicyConfig(
            enabled=True,
            work_stretch_reminder=True,
            work_stretch_interval_minutes=1,  # 60s for testing
            min_prompt_interval_seconds=100,
            sleep_guard=False,
        )
        service = ProactiveCompanionService(cfg)

        # First tick at t=0
        snap1 = DesktopContextSnapshot("Editor", "code.exe", 5.0, False, 0.0)
        self.assertIsNone(service.evaluate(snap1))

        # Second tick at t=30s
        snap2 = DesktopContextSnapshot("Editor", "code.exe", 5.0, False, 30.0)
        self.assertIsNone(service.evaluate(snap2))

        # Third tick at t=65s (exceeds 60s threshold)
        snap3 = DesktopContextSnapshot("Editor", "code.exe", 5.0, False, 65.0)
        event = service.evaluate(snap3)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.kind, "stretch")
        self.assertEqual(event.target_state, PetState.STRETCH)
        self.assertIn("喝杯水", event.message)

    def test_proactive_service_idle_resets_fatigue(self):
        cfg = ProactivePolicyConfig(
            enabled=True,
            work_stretch_reminder=True,
            work_stretch_interval_minutes=1,
        )
        service = ProactiveCompanionService(cfg)

        # Work for 50s
        service.evaluate(DesktopContextSnapshot("Editor", "code.exe", 5.0, False, 0.0))
        service.evaluate(DesktopContextSnapshot("Editor", "code.exe", 5.0, False, 50.0))

        # User leaves desk for 6 minutes (360s idle)
        service.evaluate(DesktopContextSnapshot("ScreenSaver", "", 360.0, False, 410.0))

        # Work for another 20s (total continuous is now only 20s, not 50+20)
        event = service.evaluate(DesktopContextSnapshot("Editor", "code.exe", 5.0, False, 430.0))
        self.assertIsNone(event)

    def test_proactive_service_sleep_guard(self):
        cfg = ProactivePolicyConfig(
            enabled=True,
            sleep_guard=True,
            sleep_guard_hour=23,
            sleep_guard_minute=30,
        )

        mock_dt = datetime(2026, 9, 3, 23, 45, 0)
        service = ProactiveCompanionService(cfg, now_fn=lambda: mock_dt)

        snap = DesktopContextSnapshot("Editor", "code.exe", 10.0, False, 1000.0)
        event = service.evaluate(snap)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.kind, "sleep_guard")
        self.assertEqual(event.target_state, PetState.SLEEP)
        self.assertIn("夜深啦", event.message)

        # Second evaluate on the same night should be suppressed
        event2 = service.evaluate(DesktopContextSnapshot("Editor", "code.exe", 10.0, False, 1010.0))
        self.assertIsNone(event2)


if __name__ == "__main__":
    unittest.main()
