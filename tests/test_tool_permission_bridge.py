import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from core.services.tool_permission_service import (
    ToolPermissionLevel,
    ToolPermissionRequest,
)
from ui.tool_permission_bridge import ToolPermissionBridge


def permission_request() -> ToolPermissionRequest:
    return ToolPermissionRequest(
        tool_name="open_url",
        level=ToolPermissionLevel.EXTERNAL_ACTION,
        side_effect="在默认浏览器打开外部网页",
        arguments={"url": "https://example.com"},
    )


class ToolPermissionBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_thread_confirmation_is_one_shot(self):
        requests = []
        bridge = ToolPermissionBridge(
            ask_user=lambda request: (
                requests.append(request) is None,
                False,
            )
        )
        allowed = bridge.confirm(permission_request())

        self.assertTrue(allowed)
        self.assertEqual(len(requests), 1)
        bridge.close()

    def test_worker_confirmation_dialog_runs_on_gui_thread(self):
        result = []
        dialog_threads = []

        def answer(_request):
            dialog_threads.append(QThread.currentThread())
            return True, False

        bridge = ToolPermissionBridge(
            timeout_seconds=1.0,
            ask_user=answer,
        )
        worker = threading.Thread(
            target=lambda: result.append(
                bridge.confirm(permission_request())
            )
        )
        worker.start()
        deadline = time.monotonic() + 1.0
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            worker.join(0.01)
        worker.join(0.2)

        self.assertEqual(result, [True])
        self.assertEqual(dialog_threads, [self.app.thread()])
        bridge.close()

    def test_cancel_pending_denies_without_disabling_bridge(self):
        bridge = ToolPermissionBridge(timeout_seconds=1.0)
        result = []
        worker = threading.Thread(
            target=lambda: result.append(
                bridge.confirm(permission_request())
            )
        )
        worker.start()
        deadline = time.monotonic() + 0.5
        while not bridge._pending and time.monotonic() < deadline:
            time.sleep(0.005)

        bridge.cancel_pending()
        worker.join(0.5)

        self.assertEqual(result, [False])
        bridge._ask_user_callback = lambda _request: (True, False)
        self.assertTrue(bridge.confirm(permission_request()))
        bridge.close()

    def test_close_releases_waiter_and_denies_future_requests(self):
        bridge = ToolPermissionBridge(timeout_seconds=1.0)
        result = []
        worker = threading.Thread(
            target=lambda: result.append(
                bridge.confirm(permission_request())
            )
        )
        worker.start()
        deadline = time.monotonic() + 0.5
        while not bridge._pending and time.monotonic() < deadline:
            time.sleep(0.005)

        bridge.close()
        worker.join(0.5)

        self.assertEqual(result, [False])
        self.assertFalse(bridge.confirm(permission_request()))

    def test_worker_timeout_defaults_to_deny(self):
        bridge = ToolPermissionBridge(timeout_seconds=0.02)
        result = []
        worker = threading.Thread(
            target=lambda: result.append(
                bridge.confirm(permission_request())
            )
        )

        worker.start()
        worker.join(0.5)

        self.assertEqual(result, [False])
        bridge.close()

    def test_session_decision_is_scoped_to_exact_sanitized_arguments(self):
        prompts = []

        def answer(request):
            prompts.append(request)
            return True, True

        bridge = ToolPermissionBridge(ask_user=answer)
        first = permission_request()
        second = ToolPermissionRequest(
            tool_name=first.tool_name,
            level=first.level,
            side_effect=first.side_effect,
            arguments={"url": "https://other.example"},
        )

        self.assertTrue(bridge.confirm(first))
        self.assertTrue(bridge.confirm(first))
        self.assertTrue(bridge.confirm(second))

        self.assertEqual(len(prompts), 2)
        bridge.close()
