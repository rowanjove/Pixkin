import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from core.live_monitor import LiveMonitorThread
from core.live_platforms import (
    BilibiliLiveAdapter,
    DouyinLiveAdapter,
    LivePlatformError,
    LiveStatus,
)


class LiveMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bilibili_room_id_accepts_url(self):
        self.assertEqual(
            BilibiliLiveAdapter._room_id(
                "https://live.bilibili.com/123456?live_from=1"
            ),
            "123456",
        )

    def test_douyin_rejects_untrusted_hosts_before_request(self):
        with self.assertRaisesRegex(LivePlatformError, "受信任"):
            DouyinLiveAdapter._validated_douyin_url(
                "http://127.0.0.1/v.douyin.com/123"
            )
        self.assertEqual(
            DouyinLiveAdapter._validated_douyin_url(
                "https://v.douyin.com/abc"
            ),
            "https://v.douyin.com/abc",
        )

    def test_douyin_redirects_cannot_leave_trusted_domains(self):
        session = SimpleNamespace(
            get=lambda *_args, **_kwargs: SimpleNamespace(
                is_redirect=True,
                is_permanent_redirect=False,
                headers={"Location": "http://127.0.0.1/private"},
            )
        )
        with self.assertRaisesRegex(LivePlatformError, "受信任"):
            DouyinLiveAdapter._follow_safe_redirects(
                session, "https://v.douyin.com/abc"
            )

    def test_live_cookie_is_domain_scoped_not_a_global_header(self):
        with DouyinLiveAdapter._session(
            "sessionid=secret", ".douyin.com"
        ) as session:
            self.assertNotIn("Cookie", session.headers)
            cookie = next(iter(session.cookies))
            self.assertEqual(cookie.name, "sessionid")
            self.assertEqual(cookie.domain, ".douyin.com")

    def test_rooms_are_checked_concurrently(self):
        rooms = [
            {"enabled": True, "platform": "bilibili", "room_id": str(index)}
            for index in range(3)
        ]

        def slow_check(room):
            time.sleep(0.22)
            return LiveStatus(False)

        monitor = LiveMonitorThread(rooms, interval_seconds=15)
        finished = []
        monitor.cycle_completed.connect(
            lambda live, total: (finished.append(total), monitor.stop())
        )
        started = time.monotonic()
        with patch("core.live_monitor.check_live_room", side_effect=slow_check):
            monitor.start()
            deadline = time.monotonic() + 3
            while not finished and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            monitor.stop()
            monitor.wait(2000)
        elapsed = time.monotonic() - started
        self.assertEqual(finished, [3])
        self.assertLess(elapsed, 0.55)


if __name__ == "__main__":
    unittest.main()
