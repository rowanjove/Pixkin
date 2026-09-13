"""Unified backup and restart-safe restore for Pixkin local user data."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from core.chat_history_store import ChatHistoryStore
from core.version import VERSION


class LocalDataBackupError(ValueError):
    pass


class LocalDataBackupService:
    """Export user data and stage verified restore for the next startup."""

    SCHEMA_VERSION = 1
    MAX_FILE_COUNT = 5000
    MAX_FILE_BYTES = 100 * 1024 * 1024
    MAX_TOTAL_BYTES = 500 * 1024 * 1024
    FILE_TARGETS = (
        "config.json",
        "chat-history.sqlite3",
        "memories.json",
        "memory.sqlite3",
        "character-state.json",
    )
    DIRECTORY_TARGETS = (
        "characters",
        "pet-lab/runs",
        "profile",
        "plugins",
        "audit",
        "crashes",
    )
    _RESTORE_ID_RE = re.compile(r"^[0-9a-f]{32}$")

    def __init__(
        self,
        data_root: str | Path,
        *,
        chat_store: ChatHistoryStore | None = None,
    ):
        self.data_root = Path(data_root).resolve()
        self.chat_store = chat_store
        self.staging_root = self.data_root / ".restore-staging"
        self.marker_path = self.data_root / "restore-pending.json"

    def export(self, destination: str | Path) -> Path:
        target = Path(destination)
        documents: dict[str, bytes] = {}
        config = self.data_root / "config.json"
        if config.is_file():
            documents["config.json"] = config.read_bytes()
        memories = self.data_root / "memories.json"
        if memories.is_file():
            documents["memories.json"] = memories.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            chat_copy = Path(directory) / "chat-history.sqlite3"
            if self.chat_store is not None:
                self.chat_store.backup(chat_copy)
            else:
                source = self.data_root / "chat-history.sqlite3"
                if source.is_file():
                    shutil.copy2(source, chat_copy)
            if chat_copy.is_file():
                documents["chat-history.sqlite3"] = (
                    chat_copy.read_bytes()
                )
            memory_source = self.data_root / "memory.sqlite3"
            if memory_source.is_file():
                memory_copy = Path(directory) / "memory.sqlite3"
                # Use SQLite's online backup API so a live MemoryV2
                # connection is captured consistently (including pages not
                # yet flushed by the application process).
                source_connection = sqlite3.connect(memory_source)
                target_connection = sqlite3.connect(memory_copy)
                try:
                    source_connection.backup(target_connection)
                finally:
                    target_connection.close()
                    source_connection.close()
                documents["memory.sqlite3"] = memory_copy.read_bytes()
        for relative_root in self.DIRECTORY_TARGETS:
            root = self.data_root / Path(relative_root)
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(self.data_root).as_posix()
                documents[relative] = path.read_bytes()
        self._validate_document_limits(documents)
        manifest = self._manifest(documents)
        archive_documents = {
            **documents,
            "manifest.json": self._encode(manifest),
        }
        self._atomic_zip(target, archive_documents)
        return target

    def inspect(self, source: str | Path) -> dict:
        path = Path(source)
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if len(infos) > self.MAX_FILE_COUNT + 1:
                    raise LocalDataBackupError(
                        "备份文件数量超过上限"
                    )
                documents = {}
                total = 0
                for info in infos:
                    if info.is_dir():
                        continue
                    self._validate_member(info)
                    if info.filename in documents:
                        raise LocalDataBackupError(
                            "备份包含重复文件条目"
                        )
                    total += info.file_size
                    if total > self.MAX_TOTAL_BYTES:
                        raise LocalDataBackupError(
                            "备份总大小超过上限"
                        )
                    documents[info.filename] = archive.read(info)
        except (OSError, zipfile.BadZipFile) as exc:
            raise LocalDataBackupError("备份无法读取") from exc
        manifest_bytes = documents.pop("manifest.json", None)
        if manifest_bytes is None:
            raise LocalDataBackupError("备份缺少清单")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalDataBackupError("备份清单无效") from exc
        if not isinstance(manifest, dict):
            raise LocalDataBackupError("备份清单根节点必须是对象")
        if manifest.get("schema_version") != self.SCHEMA_VERSION:
            raise LocalDataBackupError("备份版本不兼容")
        expected = manifest.get("files")
        if not isinstance(expected, dict) or set(expected) != set(documents):
            raise LocalDataBackupError("备份文件与清单不一致")
        for name, content in documents.items():
            metadata = expected[name]
            if (
                not isinstance(metadata, dict)
                or metadata.get("size") != len(content)
                or metadata.get("sha256") != self._digest(content)
            ):
                raise LocalDataBackupError(
                    f"备份文件校验失败：{name}"
                )
        return manifest

    def stage_restore(self, source: str | Path) -> Path:
        manifest = self.inspect(source)
        restore_id = uuid.uuid4().hex
        stage = (self.staging_root / restore_id).resolve()
        if self.staging_root.resolve() not in stage.parents:
            raise LocalDataBackupError("恢复暂存路径无效")
        stage_data = stage / "data"
        stage_data.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(source) as archive:
            for name in manifest["files"]:
                destination = stage_data / Path(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with (
                    archive.open(name) as source_stream,
                    destination.open("wb") as output,
                ):
                    shutil.copyfileobj(source_stream, output)
        (stage / "manifest.json").write_bytes(
            self._encode(manifest)
        )
        marker = {
            "schema_version": self.SCHEMA_VERSION,
            "restore_id": restore_id,
            "stage": str(stage),
        }
        self._atomic_json(self.marker_path, marker)
        return stage

    def apply_pending_restore(self) -> Path | None:
        if not self.marker_path.is_file():
            return None
        try:
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
            if not isinstance(marker, dict):
                raise ValueError("restore marker must be an object")
            if marker.get("schema_version") != self.SCHEMA_VERSION:
                raise ValueError("restore marker version is incompatible")
            stage = Path(marker["stage"]).resolve()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LocalDataBackupError(
                "待恢复标记无法读取"
            ) from exc
        staging_root = self.staging_root.resolve()
        if staging_root not in stage.parents or not stage.is_dir():
            raise LocalDataBackupError("待恢复暂存目录无效")
        try:
            manifest = json.loads(
                (stage / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalDataBackupError("暂存恢复清单无法读取") from exc
        if not isinstance(manifest, dict):
            raise LocalDataBackupError("暂存恢复清单无效")
        if manifest.get("schema_version") != self.SCHEMA_VERSION:
            raise LocalDataBackupError("暂存恢复清单版本不兼容")
        self._verify_staged(stage / "data", manifest)
        restore_id = str(marker.get("restore_id") or uuid.uuid4().hex)
        if not self._RESTORE_ID_RE.fullmatch(restore_id):
            raise LocalDataBackupError("待恢复标识无效")
        backup_root = (
            self.data_root / "backups" / f"restore-{restore_id}"
        )
        backup_root.mkdir(parents=True, exist_ok=False)
        moved: list[tuple[Path, Path, bool]] = []
        try:
            for relative in (
                *self.FILE_TARGETS,
                *self.DIRECTORY_TARGETS,
            ):
                source = stage / "data" / Path(relative)
                target = self.data_root / Path(relative)
                backup = backup_root / Path(relative)
                existed = target.exists()
                if existed:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(backup)
                if source.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        source.replace(target)
                    except Exception:
                        if existed and backup.exists():
                            backup.replace(target)
                        raise
                moved.append((target, backup, existed))
        except Exception:
            for target, backup, existed in reversed(moved):
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
                if existed and backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.replace(target)
            raise
        self.marker_path.unlink(missing_ok=True)
        shutil.rmtree(stage)
        return backup_root

    def _verify_staged(self, root: Path, manifest: dict) -> None:
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise LocalDataBackupError("暂存恢复清单无效")
        for name, metadata in files.items():
            if not isinstance(name, str) or not isinstance(metadata, dict):
                raise LocalDataBackupError("暂存恢复清单无效")
            member = PurePosixPath(name)
            if (
                member.is_absolute()
                or ".." in member.parts
                or not self._allowed_name(name)
            ):
                raise LocalDataBackupError("暂存恢复路径无效")
            path = root / Path(*member.parts)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != metadata.get("size")
                or self._digest(path.read_bytes())
                != metadata.get("sha256")
            ):
                raise LocalDataBackupError(
                    f"暂存恢复文件校验失败：{name}"
                )

    def _validate_member(self, info: zipfile.ZipInfo) -> None:
        member = PurePosixPath(info.filename)
        if (
            member.is_absolute()
            or ".." in member.parts
            or "\\" in info.filename
            or not self._allowed_name(info.filename)
        ):
            raise LocalDataBackupError("备份包含不安全路径")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise LocalDataBackupError("备份包含符号链接")
        if info.file_size > self.MAX_FILE_BYTES:
            raise LocalDataBackupError("备份单文件超过大小上限")
        if (
            info.file_size > 1024 * 1024
            and info.compress_size > 0
            and info.file_size / info.compress_size > 200
        ):
            raise LocalDataBackupError("备份压缩比异常")

    @classmethod
    def _allowed_name(cls, name: str) -> bool:
        if name == "manifest.json" or name in cls.FILE_TARGETS:
            return True
        return any(
            name.startswith(root.rstrip("/") + "/")
            for root in cls.DIRECTORY_TARGETS
        )

    @classmethod
    def _manifest(cls, documents: dict[str, bytes]) -> dict:
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "app_version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "contains_private_user_data": True,
            "files": {
                name: {
                    "size": len(content),
                    "sha256": cls._digest(content),
                }
                for name, content in documents.items()
            },
        }

    @classmethod
    def _validate_document_limits(
        cls,
        documents: dict[str, bytes],
    ) -> None:
        if len(documents) > cls.MAX_FILE_COUNT:
            raise LocalDataBackupError("备份文件数量超过上限")
        if any(
            len(content) > cls.MAX_FILE_BYTES
            for content in documents.values()
        ):
            raise LocalDataBackupError("备份单文件超过大小上限")
        if sum(map(len, documents.values())) > cls.MAX_TOTAL_BYTES:
            raise LocalDataBackupError("备份总大小超过上限")

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

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

    @staticmethod
    def _atomic_json(destination: Path, value: dict) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
