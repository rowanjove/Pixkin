"""Atomic, bounded local crash summaries safe for later user export."""

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.paths import user_data_dir
from core.privacy import redact_text
from core.version import VERSION


class CrashReportStore:
    """Persist anonymized crash summaries without unbounded growth."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_reports: int = 10,
    ):
        self.root = Path(root or (user_data_dir() / "crashes"))
        self.max_reports = max(1, int(max_reports))
        self._lock = threading.Lock()

    def save_exception(
        self,
        exc_type,
        exc_value,
        traceback_text: str,
        *,
        component: str = "application",
    ) -> Path:
        report_id = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "report_id": report_id,
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "app_version": VERSION,
            "component": str(component),
            "exception_type": getattr(
                exc_type,
                "__name__",
                str(exc_type),
            ),
            "message": self._sanitize(str(exc_value))[:1000],
            "traceback": self._sanitize(traceback_text)[-12000:],
        }
        name = (
            timestamp.strftime("%Y%m%dT%H%M%SZ")
            + f"-{report_id[:12]}.json"
        )
        destination = self.root / name
        with self._lock:
            self._atomic_write(destination, payload)
            self._prune()
        return destination

    def reports(self) -> tuple[dict, ...]:
        with self._lock:
            if not self.root.is_dir():
                return ()
            reports = []
            for path in sorted(self.root.glob("*.json"))[
                -self.max_reports :
            ]:
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(value, dict)
                    and value.get("schema_version")
                    == self.SCHEMA_VERSION
                ):
                    reports.append(value)
            return tuple(reports)

    def clear(self) -> None:
        with self._lock:
            if not self.root.is_dir():
                return
            for path in self.root.glob("*.json"):
                path.unlink(missing_ok=True)

    def _prune(self) -> None:
        reports = sorted(self.root.glob("*.json"))
        for path in reports[: -self.max_reports]:
            path.unlink(missing_ok=True)

    @classmethod
    def _sanitize(cls, value: str) -> str:
        text = redact_text(value)
        text = re.sub(
            r"(?i)\b[A-Z]:\\(?:[^\\\r\n]+\\)*[^\\\r\n]*",
            "<local-path>",
            text,
        )
        text = re.sub(
            r"\\\\[^\\\s]+\\[^\\\s]+(?:\\[^\\\r\n]+)*",
            "<network-path>",
            text,
        )
        return text

    @staticmethod
    def _atomic_write(destination: Path, payload: dict) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
