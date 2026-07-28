"""Anonymous, integrity-checked diagnostic bundles exported by the user."""

import hashlib
import json
import os
import platform
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from core.services.crash_report_store import CrashReportStore
from core.version import VERSION


class DiagnosticBundleError(ValueError):
    pass


class DiagnosticBundleService:
    """Build and inspect a minimal whitelist-only support archive."""

    SCHEMA_VERSION = 1
    REQUIRED_FILES = {
        "manifest.json",
        "runtime.json",
        "crashes.json",
    }
    MAX_FILE_BYTES = 2 * 1024 * 1024

    def __init__(self, crash_store: CrashReportStore):
        self.crash_store = crash_store

    def export(self, destination: str | Path) -> Path:
        target = Path(destination)
        documents = {
            "runtime.json": {
                "schema_version": self.SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "app_version": VERSION,
                "os": platform.system(),
                "os_release": platform.release(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
            },
            "crashes.json": {
                "schema_version": self.SCHEMA_VERSION,
                "reports": list(self.crash_store.reports()),
            },
        }
        encoded = {
            name: self._encode(value)
            for name, value in documents.items()
        }
        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "privacy": {
                "api_keys_included": False,
                "chat_content_included": False,
                "local_paths_included": False,
                "user_identity_included": False,
            },
            "files": {
                name: {
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for name, content in encoded.items()
            },
        }
        encoded["manifest.json"] = self._encode(manifest)
        self._atomic_zip(target, encoded)
        return target

    def inspect(self, source: str | Path) -> dict:
        path = Path(source)
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if names != self.REQUIRED_FILES:
                    raise DiagnosticBundleError(
                        "诊断包文件清单不符合预期"
                    )
                for name in names:
                    member = PurePosixPath(name)
                    if member.is_absolute() or ".." in member.parts:
                        raise DiagnosticBundleError(
                            "诊断包包含不安全路径"
                        )
                    if archive.getinfo(name).file_size > self.MAX_FILE_BYTES:
                        raise DiagnosticBundleError(
                            "诊断包文件超过大小上限"
                        )
                contents = {
                    name: archive.read(name)
                    for name in names
                }
        except (OSError, zipfile.BadZipFile) as exc:
            raise DiagnosticBundleError("诊断包无法读取") from exc
        try:
            manifest = json.loads(
                contents["manifest.json"].decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiagnosticBundleError("诊断包清单无效") from exc
        if manifest.get("schema_version") != self.SCHEMA_VERSION:
            raise DiagnosticBundleError("诊断包版本不兼容")
        privacy = manifest.get("privacy")
        if not isinstance(privacy, dict) or any(privacy.values()):
            raise DiagnosticBundleError("诊断包隐私声明无效")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise DiagnosticBundleError("诊断包哈希清单缺失")
        for name in self.REQUIRED_FILES - {"manifest.json"}:
            metadata = files.get(name)
            content = contents[name]
            if (
                not isinstance(metadata, dict)
                or metadata.get("size") != len(content)
                or metadata.get("sha256")
                != hashlib.sha256(content).hexdigest()
            ):
                raise DiagnosticBundleError(
                    f"诊断包文件校验失败：{name}"
                )
        return manifest

    @staticmethod
    def _encode(value: dict) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def _atomic_zip(
        destination: Path,
        documents: dict[str, bytes],
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary_path = Path(temporary)
        try:
            with zipfile.ZipFile(
                temporary_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for name, content in documents.items():
                    archive.writestr(name, content)
            os.replace(temporary_path, destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
