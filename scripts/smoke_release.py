"""Launch a packaged Pixkin executable and verify a clean startup/exit."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START_MARKER = "Pixkin 启动完成"
EXIT_MARKER = "正在退出"


def smoke_executable(executable: Path, timeout_seconds: int = 60) -> Path:
    executable = executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Packaged executable not found: {executable}")
    if executable.suffix.lower() != ".exe":
        raise ValueError(f"Smoke target must be an .exe file: {executable}")

    runtime_root = (
        ROOT / "artifacts" / "exe-smoke" / uuid.uuid4().hex
    ).resolve()
    runtime_root.mkdir(parents=True, exist_ok=False)

    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(runtime_root)
    environment["PIXKIN_SMOKE_TEST"] = "1"
    environment["QT_QPA_PLATFORM"] = "offscreen"

    process = subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        env=environment,
    )
    try:
        return_code = process.wait(timeout=max(5, int(timeout_seconds)))
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise RuntimeError(
            f"Pixkin smoke timed out after {timeout_seconds}s; "
            f"runtime data: {runtime_root}"
        ) from exc

    log_path = runtime_root / "Pixkin" / "logs" / "pixkin.log"
    if return_code != 0:
        raise RuntimeError(
            f"Pixkin smoke exited with code {return_code}; "
            f"runtime data: {runtime_root}"
        )
    if not log_path.is_file():
        raise RuntimeError(
            f"Pixkin smoke did not create its log; runtime data: {runtime_root}"
        )

    log_text = log_path.read_text(encoding="utf-8")
    missing = [
        marker
        for marker in (START_MARKER, EXIT_MARKER)
        if marker not in log_text
    ]
    if missing:
        raise RuntimeError(
            f"Pixkin smoke log is missing {missing}; runtime data: {runtime_root}"
        )
    return runtime_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    arguments = parser.parse_args()

    runtime_root = smoke_executable(
        arguments.executable,
        timeout_seconds=arguments.timeout,
    )
    print(f"Packaged smoke passed: {runtime_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
