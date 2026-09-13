import unittest

from core.runtime.actions import Action, ActionDispatcher, ActionType
from core.runtime.events import EventBus, EventEnvelope
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionResource,
    PermissionState,
)
from core.runtime.service_container import ServiceContainer
from core.services.context_sensor_service import ContextSensorService


class RuntimeCoreTests(unittest.TestCase):
    def test_event_bus_is_ordered_and_isolates_failures(self):
        bus = EventBus()
        calls = []
        bus.subscribe("demo", lambda event: calls.append(("ok", event.payload["x"])))

        def broken(_event):
            raise RuntimeError("boom")

        bus.subscribe("demo", broken)
        report = bus.publish(EventEnvelope("demo", "test", {"x": 1}))
        self.assertEqual(calls, [("ok", 1)])
        self.assertEqual((report.delivered, report.failed), (1, 1))

    def test_event_payload_isolated_from_caller_mutation(self):
        payload = {"state": "idle"}
        event = EventEnvelope("state.changed", "test", payload)
        payload["state"] = "talking"
        self.assertEqual(event.payload["state"], "idle")
        with self.assertRaises(TypeError):
            event.payload["state"] = "broken"

    def test_permission_service_defaults_to_deny_and_supports_session_grant(self):
        permissions = ContextPermissionService()
        resource = PermissionResource.WINDOW_METADATA
        self.assertFalse(permissions.decide(resource).allowed)
        permissions.set_state(resource, PermissionState.ALLOW_SESSION, session_only=True)
        self.assertTrue(permissions.decide(resource).allowed)
        permissions.clear_session()
        self.assertFalse(permissions.decide(resource).allowed)

    def test_ask_permission_uses_callback_without_persisting_grant(self):
        calls = []
        permissions = ContextPermissionService(
            ask_callback=lambda resource, operation: calls.append(
                (resource, operation)
            ) or True
        )
        decision = permissions.decide(
            PermissionResource.CLIPBOARD,
        )
        self.assertFalse(decision.allowed)

        permissions.set_state(PermissionResource.CLIPBOARD, PermissionState.ASK)
        self.assertTrue(permissions.decide(PermissionResource.CLIPBOARD).allowed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            permissions.state(PermissionResource.CLIPBOARD),
            PermissionState.ASK,
        )
        self.assertFalse(
            permissions.decide(
                PermissionResource.CLIPBOARD,
                resolve_ask=False,
                consume_pending=True,
            ).allowed
        )

    def test_ask_grant_can_be_consumed_by_worker_without_repeating_callback(self):
        calls = []
        permissions = ContextPermissionService(
            ask_callback=lambda resource, operation: calls.append(
                (resource, operation)
            ) or True
        )
        permissions.set_state(
            PermissionResource.NETWORK,
            PermissionState.ASK,
        )
        token = permissions.new_handoff_token()

        self.assertTrue(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="write",
                handoff_token=token,
            ).allowed
        )
        self.assertTrue(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="write",
                resolve_ask=False,
                consume_pending=True,
                handoff_token=token,
            ).allowed
        )
        self.assertFalse(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="write",
                resolve_ask=False,
                consume_pending=True,
                handoff_token=token,
            ).allowed
        )
        self.assertEqual(len(calls), 1)

    def test_ask_resolution_is_fail_closed_off_callback_thread(self):
        import threading

        calls = []
        permissions = ContextPermissionService(
            ask_callback=lambda resource, operation: calls.append(
                (resource, operation)
            ) or True
        )
        permissions.set_state(PermissionResource.NETWORK, PermissionState.ASK)
        result = []

        def resolve_from_worker():
            result.append(
                permissions.decide(
                    PermissionResource.NETWORK,
                    operation="write",
                ).allowed
            )

        thread = threading.Thread(target=resolve_from_worker)
        thread.start()
        thread.join()

        self.assertEqual(result, [False])
        self.assertEqual(calls, [])

    def test_pending_ask_grant_is_cleared_when_operation_is_aborted(self):
        permissions = ContextPermissionService(
            ask_callback=lambda _resource, _operation: True
        )
        permissions.set_state(PermissionResource.NETWORK, PermissionState.ASK)
        token = permissions.new_handoff_token()

        self.assertTrue(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="write",
                handoff_token=token,
            ).allowed
        )
        permissions.clear_pending(
            PermissionResource.NETWORK,
            operation="write",
            handoff_token=token,
        )

        self.assertFalse(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="write",
                resolve_ask=False,
                consume_pending=True,
                handoff_token=token,
            ).allowed
        )

    def test_pending_ask_grant_is_bound_to_handoff_token(self):
        permissions = ContextPermissionService(
            ask_callback=lambda _resource, _operation: True
        )
        permissions.set_state(PermissionResource.NETWORK, PermissionState.ASK)
        token = permissions.new_handoff_token()
        wrong_token = permissions.new_handoff_token()
        self.assertTrue(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="read",
                handoff_token=token,
            ).allowed
        )
        self.assertFalse(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="read",
                resolve_ask=False,
                consume_pending=True,
                handoff_token=wrong_token,
            ).allowed
        )
        self.assertTrue(
            permissions.decide(
                PermissionResource.NETWORK,
                operation="read",
                resolve_ask=False,
                consume_pending=True,
                handoff_token=token,
            ).allowed
        )
    def test_context_sensor_does_not_call_denied_resolvers(self):
        permissions = ContextPermissionService()
        calls = []
        sensor = ContextSensorService(
            get_window_info=lambda: calls.append("window") or ("secret", "x.exe", False),
            get_idle_seconds=lambda: calls.append("idle") or 5.0,
            time_fn=lambda: 12.0,
            permission_service=permissions,
        )
        snapshot = sensor.poll()
        self.assertEqual(calls, [])
        self.assertEqual(snapshot.active_window_title, "")
        self.assertEqual(snapshot.idle_seconds, 0.0)

        permissions.set_state(PermissionResource.WINDOW_METADATA, PermissionState.ALLOW_SESSION, session_only=True)
        permissions.set_state(PermissionResource.SYSTEM_STATE, PermissionState.ALLOW_SESSION, session_only=True)
        sensor.poll()
        self.assertEqual(calls, ["window", "idle"])

    def test_action_dispatch_requires_permission_when_declared(self):
        permissions = ContextPermissionService()
        dispatcher = ActionDispatcher(permissions)
        dispatcher.register(ActionType.OPEN_WINDOW, lambda _action: "opened")
        action = Action(
            ActionType.OPEN_WINDOW,
            permission_resource=PermissionResource.EXTERNAL_ACTION,
        )
        self.assertEqual(dispatcher.dispatch(action).status, "denied")
        permissions.set_state(PermissionResource.EXTERNAL_ACTION, PermissionState.ALLOW_SESSION, session_only=True)
        self.assertEqual(dispatcher.dispatch(action).status, "success")

    def test_side_effect_action_without_declaration_is_default_denied(self):
        permissions = ContextPermissionService()
        dispatcher = ActionDispatcher(permissions)
        dispatcher.register(ActionType.OPEN_WINDOW, lambda _action: "opened")
        self.assertEqual(
            dispatcher.dispatch(Action(ActionType.OPEN_WINDOW)).status,
            "denied",
        )

    def test_service_container_rolls_back_started_services(self):
        events = []

        class Service:
            def __init__(self, name, fail=False):
                self.name = name
                self.fail = fail

            def start(self):
                events.append("start:" + self.name)
                if self.fail:
                    raise RuntimeError(self.name)

            def stop(self):
                events.append("stop:" + self.name)

        container = ServiceContainer()
        container.register("first", Service("first"))
        container.register("second", Service("second", fail=True))
        with self.assertRaises(RuntimeError):
            container.start()
        self.assertEqual(events, ["start:first", "start:second", "stop:first"])

    def test_service_container_closes_services_without_stop_hook(self):
        events = []

        class Closable:
            def start(self):
                events.append("start")

            def close(self):
                events.append("close")

        container = ServiceContainer()
        container.register("closable", Closable())
        container.start()
        container.stop()
        self.assertEqual(events, ["start", "close"])


if __name__ == "__main__":
    unittest.main()
