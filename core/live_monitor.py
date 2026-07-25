import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from PyQt6.QtCore import QThread, pyqtSignal

from core.live_platforms import check_live_room


LOGGER = logging.getLogger("desktop_pet.live_monitor")


class LiveMonitorThread(QThread):
    """每一轮并发查询所有直播间，不让慢平台阻塞其他平台。"""

    anchor_live_started = pyqtSignal(str, str, str)
    room_status_changed = pyqtSignal(str, bool, str, str)
    cycle_completed = pyqtSignal(int, int)

    def __init__(
        self,
        rooms: List[Dict[str, str]],
        interval_seconds: int = 60,
        notify_if_live_on_start: bool = False,
    ):
        super().__init__()
        self.rooms = [dict(room) for room in rooms if room.get("enabled", True)]
        self.interval_seconds = max(15, int(interval_seconds))
        self.notify_if_live_on_start = notify_if_live_on_start
        self._stop_event = threading.Event()
        self._last_status: Dict[str, bool] = {}

    def stop(self):
        self._stop_event.set()

    def run(self):
        if not self.rooms:
            return
        workers = min(8, max(1, len(self.rooms)))
        pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="live-check"
        )
        try:
            while not self._stop_event.is_set():
                futures = {
                    pool.submit(check_live_room, room): room
                    for room in self.rooms
                }
                live_count = 0
                for future in as_completed(futures):
                    if self._stop_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    room = futures[future]
                    key = self._room_key(room)
                    try:
                        status = future.result()
                        if status.is_live:
                            live_count += 1
                        previous = self._last_status.get(key)
                        should_notify = status.is_live and (
                            previous is False
                            or (previous is None and self.notify_if_live_on_start)
                        )
                        if should_notify:
                            self.anchor_live_started.emit(
                                str(room.get("platform", "")),
                                status.anchor_name
                                or str(room.get("anchor_name") or "关注的主播"),
                                status.title or "正在直播",
                            )
                        self._last_status[key] = status.is_live
                        self.room_status_changed.emit(
                            key, status.is_live, status.title, ""
                        )
                    except Exception as exc:
                        LOGGER.warning("直播间 %s 检测失败: %s", key, exc)
                        self.room_status_changed.emit(
                            key, False, "", str(exc)
                        )
                if self._stop_event.is_set():
                    break
                self.cycle_completed.emit(live_count, len(self.rooms))
                self._stop_event.wait(self.interval_seconds)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    @staticmethod
    def _room_key(room: Dict[str, str]) -> str:
        return (
            f"{room.get('platform', '')}:"
            f"{room.get('room_id') or room.get('room_url') or ''}"
        )
