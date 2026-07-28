import unittest

from core.providers.live import (
    LiveProvider,
    LiveProviderCapabilities,
    LiveProviderHealth,
    LiveProviderRouter,
    LiveStatus,
)
from core.services.live_service import LiveService


ROOM = {
    "enabled": True,
    "platform": "fake",
    "room_id": "42",
    "anchor_name": "小主播",
}


class FakeLiveProvider(LiveProvider):
    platform = "fake"

    def __init__(self, status: LiveStatus):
        self.status = status
        self.closed = False

    @property
    def capabilities(self):
        return LiveProviderCapabilities()

    def check(self, room):
        return self.status

    def health_check(self):
        return LiveProviderHealth(True, "ok")

    def close(self):
        self.closed = True


class LiveServiceTests(unittest.TestCase):
    def test_provider_router_uses_platform_contract_and_closes_provider(self):
        provider = FakeLiveProvider(LiveStatus(True, "画画", "主播"))
        router = LiveProviderRouter([provider])

        self.assertTrue(router.check(ROOM).is_live)
        router.close()

        self.assertTrue(provider.closed)

    def test_notifies_only_for_a_trusted_offline_to_live_transition(self):
        service = LiveService(
            [ROOM],
            notify_if_live_on_start=False,
            jitter_source=lambda: 0.5,
        )

        first = service.record_success(
            ROOM, LiveStatus(False), now=0
        )
        started = service.record_success(
            ROOM, LiveStatus(True, "开播"), now=60
        )
        repeated = service.record_success(
            ROOM, LiveStatus(True, "开播"), now=120
        )

        self.assertFalse(first.should_notify)
        self.assertTrue(started.should_notify)
        self.assertFalse(repeated.should_notify)

    def test_failure_preserves_last_trusted_live_state(self):
        service = LiveService(
            [ROOM],
            interval_seconds=30,
            jitter_source=lambda: 0.5,
        )
        service.record_success(
            ROOM,
            LiveStatus(True, "可信标题", "可信主播"),
            now=10,
            wall_time=1000,
        )

        failed = service.record_failure(
            ROOM, RuntimeError("临时失败"), now=40
        )
        state = failed.state

        self.assertTrue(state.is_live)
        self.assertEqual(state.title, "可信标题")
        self.assertEqual(state.last_success_at, 10)
        self.assertEqual(state.last_success_wall_time, 1000)
        self.assertEqual(state.consecutive_failures, 1)
        self.assertEqual(state.retry_after_seconds, 30)
        self.assertEqual(service.summary().live_count, 1)
        self.assertEqual(service.summary().error_count, 1)

    def test_each_room_uses_independent_exponential_backoff(self):
        other = {**ROOM, "room_id": "84"}
        service = LiveService(
            [ROOM, other],
            interval_seconds=20,
            max_backoff_seconds=70,
            jitter_source=lambda: 0.5,
        )

        first = service.record_failure(ROOM, RuntimeError("1"), now=0)
        second = service.record_failure(ROOM, RuntimeError("2"), now=20)
        third = service.record_failure(ROOM, RuntimeError("3"), now=60)

        self.assertEqual(first.state.retry_after_seconds, 20)
        self.assertEqual(second.state.retry_after_seconds, 40)
        self.assertEqual(third.state.retry_after_seconds, 70)
        self.assertEqual(service.due_rooms(0), [other])
        self.assertEqual(service.seconds_until_next_check(0), 0.05)

    def test_success_clears_failure_and_restores_normal_interval(self):
        service = LiveService(
            [ROOM],
            interval_seconds=25,
            jitter_source=lambda: 0.5,
        )
        service.record_failure(ROOM, RuntimeError("失败"), now=0)

        recovered = service.record_success(
            ROOM, LiveStatus(False, "休息中"), now=25
        )

        self.assertEqual(recovered.state.error, "")
        self.assertEqual(recovered.state.consecutive_failures, 0)
        self.assertEqual(recovered.state.next_check_at, 50)

    def test_repeat_reminders_and_quiet_pending_are_explicit(self):
        service = LiveService(
            [ROOM],
            repeat_reminder_minutes=1,
            quiet_start="00:00",
            quiet_end="00:00",
        )
        service.record_success(
            ROOM, LiveStatus(False), now=0, wall_time=1_700_000_000
        )
        quiet = service.record_success(
            ROOM,
            LiveStatus(True, "开播"),
            now=60,
            wall_time=1_700_000_060,
        )
        self.assertFalse(quiet.should_notify)
        self.assertTrue(quiet.state.notification_pending)

        service.quiet_start = None
        service.quiet_end = None
        delivered = service.record_success(
            ROOM,
            LiveStatus(True, "开播"),
            now=120,
            wall_time=1_700_000_120,
        )
        repeated = service.record_success(
            ROOM,
            LiveStatus(True, "仍在直播"),
            now=180,
            wall_time=1_700_000_180,
        )

        self.assertTrue(delivered.should_notify)
        self.assertFalse(delivered.state.notification_pending)
        self.assertTrue(repeated.should_notify)

    def test_room_groups_are_preserved_in_trusted_state(self):
        grouped = {**ROOM, "group": "画师"}
        service = LiveService([grouped])

        outcome = service.record_success(
            grouped,
            LiveStatus(False),
            now=0,
        )

        self.assertEqual(outcome.state.group, "画师")


if __name__ == "__main__":
    unittest.main()
