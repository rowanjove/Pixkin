"""Measure repeatable Pixkin source and packaged runtime performance."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

from core.character_package import CharacterPackageManager
from core.chat_history_store import ChatHistoryStore
from core.config import ConfigManager
from core.providers.live import LiveStatus
from core.services.chat_service import ChatSessionService
from core.services.live_service import LiveService
from core.services.performance_baseline_service import (
    PerformanceBaselineService,
)
from core.version import VERSION


def timed_ms(callback, repeats: int = 1) -> float:
    samples = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 3)


def source_metrics() -> dict[str, dict]:
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        config_path = base / "config.json"
        ConfigManager(str(config_path))
        config_load = timed_ms(
            lambda: ConfigManager(str(config_path)),
            repeats=20,
        )

        store = ChatHistoryStore(base / "history.sqlite3")
        session = store.create_session("benchmark", "基准角色")
        for index in range(1000):
            store.add_message(
                session,
                "user" if index % 2 == 0 else "assistant",
                f"message-{index}-" + ("x" * 80),
            )
        chat_query = timed_ms(
            lambda: store.list_messages(session_id=session),
            repeats=10,
        )
        chat_session = ChatSessionService(store)
        messages = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": "x" * 300,
            }
            for index in range(1000)
        ]
        context_trim = timed_ms(
            lambda: chat_session.trim_context(messages),
            repeats=20,
        )

        rooms = [
            {
                "enabled": True,
                "platform": "fake",
                "room_id": str(index),
            }
            for index in range(20)
        ]

        def live_cycle():
            service = LiveService(
                rooms,
                jitter_source=lambda: 0.5,
            )
            for room in service.due_rooms(0):
                service.record_success(
                    room,
                    LiveStatus(False),
                    now=0,
                )
            service.summary()

        live_cycle_ms = timed_ms(live_cycle, repeats=100)

        manager = CharacterPackageManager(
            ConfigManager(str(base / "character-config.json")),
            base / "characters",
        )
        package_path = ROOT / "character-packs" / "shanshan.zip"
        full_validation = timed_ms(
            lambda: manager.inspect_zip(str(package_path)),
            repeats=5,
        )
        package = manager.inspect_zip(str(package_path))
        state_count = len(package.animations)

        with zipfile.ZipFile(package_path) as archive:
            atlas_data = archive.read("spritesheet.webp")
        atlas = Image.open(io.BytesIO(atlas_data)).convert("RGBA")
        rectangles = [
            (column * 192, row * 208, (column + 1) * 192, (row + 1) * 208)
            for row in range(9)
            for column in range(8)
        ]

        def crop_frames():
            for index in range(500):
                atlas.crop(rectangles[index % len(rectangles)])

        animation_frame_ms = timed_ms(crop_frames, repeats=5) / 500

    return {
        "config_load_ms": metric(config_load, "ms"),
        "chat_1000_query_ms": metric(chat_query, "ms"),
        "chat_1000_trim_ms": metric(context_trim, "ms"),
        "live_20_rooms_cycle_ms": metric(live_cycle_ms, "ms"),
        "full_character_validation_ms": metric(
            full_validation,
            "ms",
        ),
        "animation_frame_crop_ms": metric(
            round(animation_frame_ms, 4),
            "ms/frame",
        ),
        "full_character_state_count": metric(
            state_count,
            "states",
            lower_is_better=False,
        ),
    }


def metric(
    value: float,
    unit: str,
    *,
    lower_is_better: bool = True,
) -> dict:
    return {
        "value": value,
        "unit": unit,
        "lower_is_better": lower_is_better,
    }


def packaged_metrics(executable: Path, duration_seconds: float) -> dict:
    executable = executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    measurement_seconds = max(2.0, duration_seconds)
    warmup_seconds = 2.0
    with tempfile.TemporaryDirectory() as directory:
        runtime_root = Path(directory)
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(runtime_root)
        environment["PIXKIN_PERF_TEST_SECONDS"] = str(
            warmup_seconds + measurement_seconds
        )
        environment["QT_QPA_PLATFORM"] = "offscreen"
        started = time.perf_counter()
        process = subprocess.Popen(
            [str(executable)],
            cwd=executable.parent,
            env=environment,
        )
        handle = _open_process(process.pid)
        peak_working_set = 0
        warmup_deadline = started + warmup_seconds
        while process.poll() is None and time.perf_counter() < warmup_deadline:
            peak_working_set = max(
                peak_working_set,
                _working_set_bytes(handle),
            )
            time.sleep(0.1)
        measurement_started = time.perf_counter()
        cpu_start = _process_cpu_seconds(handle)
        while process.poll() is None:
            peak_working_set = max(
                peak_working_set,
                _working_set_bytes(handle),
            )
            time.sleep(0.1)
        measurement_elapsed = time.perf_counter() - measurement_started
        cpu_end = _process_cpu_seconds(handle)
        ctypes.windll.kernel32.CloseHandle(handle)
        if process.returncode != 0:
            raise RuntimeError(
                f"performance executable exited {process.returncode}"
            )
        log_path = runtime_root / "Pixkin" / "logs" / "pixkin.log"
        startup_ms = _startup_ms(log_path)
    cpu_percent = max(
        0.0,
        (cpu_end - cpu_start) / measurement_elapsed * 100,
    )
    return {
        "cold_start_ms": metric(startup_ms, "ms"),
        "idle_cpu_percent": metric(
            round(cpu_percent, 3),
            "%",
        ),
        "peak_working_set_mb": metric(
            round(peak_working_set / 1024 / 1024, 3),
            "MiB",
        ),
    }


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _open_process(pid: int):
    handle = ctypes.windll.kernel32.OpenProcess(
        0x0400 | 0x0010,
        False,
        pid,
    )
    if not handle:
        raise OSError("OpenProcess failed")
    return handle


def _working_set_bytes(handle) -> int:
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        handle,
        ctypes.byref(counters),
        counters.cb,
    ):
        return 0
    return int(counters.WorkingSetSize)


def _process_cpu_seconds(handle) -> float:
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return 0.0

    def seconds(value):
        ticks = (value.dwHighDateTime << 32) | value.dwLowDateTime
        return ticks / 10_000_000

    return seconds(kernel) + seconds(user)


def _startup_ms(log_path: Path) -> float:
    for line in log_path.read_text(encoding="utf-8").splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if event.get("operation") == "startup":
            return float(event["fields"]["startup_ms"])
    raise RuntimeError("startup metric missing from packaged log")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--threshold-percent", type=float, default=10.0)
    arguments = parser.parse_args()

    metrics = source_metrics()
    if arguments.executable:
        metrics.update(
            packaged_metrics(
                arguments.executable,
                arguments.duration,
            )
        )
    baseline = {
        "schema_version": 1,
        "app_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "mode": (
                "source+packaged"
                if arguments.executable
                else "source"
            ),
        },
        "metrics": metrics,
    }
    PerformanceBaselineService.write(arguments.output, baseline)
    if arguments.compare:
        previous = PerformanceBaselineService.read(arguments.compare)
        regressions = PerformanceBaselineService.compare(
            baseline,
            previous,
            threshold_percent=arguments.threshold_percent,
        )
        if regressions:
            print(json.dumps(regressions, ensure_ascii=False, indent=2))
            return 2
    print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
