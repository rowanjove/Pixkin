import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Mapping

from PyQt6.QtCore import QThread, pyqtSignal

from core.live_platforms import check_live_room
from core.services.live_service import LiveService


LOGGER = logging.getLogger("desktop_pet.live_monitor")


class LiveMonitorThread(QThread):
    """每一轮并发查询所有直播间，不让慢平台阻塞其他平台。"""

    anchor_live_started = pyqtSignal(str, str, str)
    room_status_changed = pyqtSignal(str, bool, str, str)
    room_health_changed = pyqtSignal(object)
    cycle_completed = pyqtSignal(int, int)

    def __init__(
        self,
        rooms: List[Dict[str, str]],
        interval_seconds: int = 60,
        notify_if_live_on_start: bool = False,
        service: LiveService | None = None,
        quiet_start: str = "",
        quiet_end: str = "",
        repeat_reminder_minutes: int = 0,
        checker=None,
        checker_close=None,
    ):
        super().__init__()
        self.rooms = [dict(room) for room in rooms if room.get("enabled", True)]
        self.interval_seconds = max(15, int(interval_seconds))
        self.notify_if_live_on_start = notify_if_live_on_start
        self._stop_event = threading.Event()
        self.service = service
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end
        self.repeat_reminder_minutes = repeat_reminder_minutes
        self._checker = checker
        self._checker_close = checker_close

    def stop(self):
        self._stop_event.set()

    def run(self):
        if not self.rooms:
            return
        service = self.service or LiveService(
            self.rooms,
            interval_seconds=self.interval_seconds,
            notify_if_live_on_start=self.notify_if_live_on_start,
            checker=self._checker or check_live_room,
            quiet_start=self.quiet_start,
            quiet_end=self.quiet_end,
            repeat_reminder_minutes=self.repeat_reminder_minutes,
        )
        self.service = service
        workers = min(8, max(1, len(self.rooms)))
        pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="live-check"
        )
        try:
            while not self._stop_event.is_set():
                due_rooms = service.due_rooms(time.monotonic())
                futures = {
                    pool.submit(service.check, room): room
                    for room in due_rooms
                }
                for future in as_completed(futures):
                    if self._stop_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    room = futures[future]
                    key = self._room_key(room)
                    try:
                        status = future.result()
                        outcome = service.record_success(
                            room,
                            status,
                            now=time.monotonic(),
                            wall_time=time.time(),
                        )
                        if outcome.should_notify:
                            self.anchor_live_started.emit(
                                str(room.get("platform", "")),
                                outcome.state.anchor_name
                                or str(room.get("anchor_name") or "关注的主播"),
                                outcome.state.title or "正在直播",
                            )
                        self.room_status_changed.emit(
                            key,
                            bool(outcome.state.is_live),
                            outcome.state.title,
                            "",
                        )
                        self.room_health_changed.emit(outcome.state)
                    except Exception as exc:
                        LOGGER.warning("直播间 %s 检测失败: %s", key, exc)
                        outcome = service.record_failure(
                            room,
                            exc,
                            now=time.monotonic(),
                        )
                        self.room_status_changed.emit(
                            key,
                            bool(outcome.state.is_live),
                            outcome.state.title,
                            outcome.state.error,
                        )
                        self.room_health_changed.emit(outcome.state)
                if self._stop_event.is_set():
                    break
                summary = service.summary()
                self.cycle_completed.emit(
                    summary.live_count, summary.total_count
                )
                wait_seconds = service.seconds_until_next_check(
                    time.monotonic()
                )
                self._stop_event.wait(wait_seconds)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            if self._checker_close is not None:
                self._checker_close()

    @staticmethod
    def _room_key(room: Mapping[str, object]) -> str:
        return LiveService.room_key(room)
