"""Read-only package inspection and explicit character catalog trust."""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

from core.services.character_service import CharacterService
from core.services.character_trust_service import OfficialCharacterTrustStore
from core.version import VERSION


class CharacterCatalogError(ValueError):
    pass


SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?$"
)


def version_key(value: str) -> tuple[int, int, int, int]:
    match = SEMVER.fullmatch(str(value or ""))
    if not match:
        raise CharacterCatalogError(f"版本号不是 SemVer：{value}")
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        0 if match.group(4) else 1,
    )


@dataclass(frozen=True)
class CharacterInspection:
    package_id: str
    name: str
    version: str
    author: str
    description: str
    fingerprint: str
    file_count: int
    official: bool
    author_trusted: bool
    compatible: bool
    compatibility_message: str
    preview_bytes: bytes | None


@dataclass(frozen=True)
class CharacterCatalogEntry:
    package_id: str
    name: str
    version: str
    archive_url: str
    sha256: str
    source: str
    min_app_version: str
    max_app_version: str

    @property
    def official(self) -> bool:
        return self.source == "official"


class CharacterPackageInspector:
    """Inspect, fingerprint and preview without installing or trusting authors."""

    def __init__(
        self,
        character_service: CharacterService,
        trust_store: OfficialCharacterTrustStore | None = None,
        *,
        app_version: str = VERSION,
    ):
        self.character_service = character_service
        self.trust_store = trust_store
        self.app_version = app_version

    def inspect(self, archive: str | Path) -> CharacterInspection:
        plan = self.character_service.prepare_install(archive)
        package = plan.package
        archive_name = Path(archive).name
        official = bool(
            self.trust_store
            and self.trust_store.verify(archive_name, archive)
        )
        compatible, message = (
            (True, "随当前 Pixkin 发布并通过官方哈希验证")
            if official
            else self._compatibility(package.compatibility)
        )
        return CharacterInspection(
            package_id=package.package_id,
            name=package.name,
            version=package.version,
            author=package.author,
            description=package.description,
            fingerprint=plan.archive_sha256,
            file_count=plan.archive_file_count,
            official=official,
            author_trusted=official,
            compatible=compatible,
            compatibility_message=message,
            preview_bytes=self.character_service.read_archive_preview(
                archive
            ),
        )

    def _compatibility(self, value: dict) -> tuple[bool, str]:
        if not isinstance(value, dict):
            return True, "未声明版本限制"
        minimum = str(value.get("min_app_version") or "")
        maximum = str(value.get("max_app_version") or "")
        try:
            current = version_key(self.app_version)
            if minimum and current < version_key(minimum):
                return False, f"需要 Pixkin {minimum} 或更高版本"
            if maximum and current > version_key(maximum):
                return False, f"最高支持 Pixkin {maximum}"
        except CharacterCatalogError:
            return False, "角色包兼容性版本声明无效"
        return True, "与当前 Pixkin 版本兼容"


class CharacterCatalogService:
    """Validate official/third-party indexes without inheriting author trust."""

    SCHEMA_VERSION = 1
    MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

    def __init__(self, catalog_path: str | Path):
        self.catalog_path = Path(catalog_path)
        self.entries = self._load()

    def compatible_entries(
        self,
        *,
        app_version: str = VERSION,
    ) -> list[CharacterCatalogEntry]:
        current = version_key(app_version)
        return [
            item
            for item in self.entries
            if (
                (not item.min_app_version or current >= version_key(item.min_app_version))
                and (
                    not item.max_app_version
                    or current <= version_key(item.max_app_version)
                )
            )
        ]

    def available_updates(
        self,
        installed: list,
        *,
        app_version: str = VERSION,
    ) -> list[CharacterCatalogEntry]:
        versions = {
            str(item.package_id): version_key(str(item.version))
            for item in installed
        }
        return [
            item
            for item in self.compatible_entries(app_version=app_version)
            if (
                item.package_id in versions
                and version_key(item.version) > versions[item.package_id]
            )
        ]

    def download_verified(
        self,
        entry: CharacterCatalogEntry,
        destination: str | Path,
        *,
        get=requests.get,
    ) -> Path:
        """Download one immutable catalog artifact without credentials."""

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        response = get(
            entry.archive_url,
            stream=True,
            timeout=(5, 45),
            allow_redirects=False,
            headers={"Accept": "application/zip"},
        )
        if int(response.status_code) != 200:
            close = getattr(response, "close", None)
            if close:
                close()
            raise CharacterCatalogError("角色包下载失败或发生重定向")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary_path = Path(temporary)
        digest = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.MAX_ARCHIVE_BYTES:
                        raise CharacterCatalogError(
                            "角色包超过 100 MB 上限"
                        )
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if total <= 0 or digest.hexdigest() != entry.sha256:
                raise CharacterCatalogError("角色包 SHA-256 校验失败")
            os.replace(temporary_path, target)
            return target
        finally:
            temporary_path.unlink(missing_ok=True)
            close = getattr(response, "close", None)
            if close:
                close()

    def install_verified_update(
        self,
        entry: CharacterCatalogEntry,
        archive: str | Path,
        character_service: CharacterService,
        *,
        user_confirmed: bool,
    ):
        if not entry.official:
            raise CharacterCatalogError(
                "第三方目录条目不能走官方自动更新路径"
            )
        if not user_confirmed:
            raise CharacterCatalogError("角色更新需要用户确认")
        path = Path(archive)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.sha256:
            raise CharacterCatalogError("角色更新包哈希不匹配")
        plan = character_service.prepare_install(
            path,
            allow_builtin_replace=True,
        )
        if (
            plan.package.package_id != entry.package_id
            or plan.package.version != entry.version
        ):
            raise CharacterCatalogError("角色更新包身份或版本不匹配")
        active = character_service.get_active()
        return character_service.install(
            plan,
            replace_confirmed=True,
            activate=bool(
                active
                and active.package_id == entry.package_id
            ),
        )

    def _load(self) -> list[CharacterCatalogEntry]:
        try:
            document = json.loads(
                self.catalog_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise CharacterCatalogError("角色目录无法读取") from exc
        if document.get("schema_version") != self.SCHEMA_VERSION:
            raise CharacterCatalogError("角色目录版本不兼容")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            raise CharacterCatalogError("角色目录条目无效")
        entries = []
        seen = set()
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise CharacterCatalogError("角色目录条目无效")
            package_id = str(raw.get("id") or "")
            version = str(raw.get("version") or "")
            sha256 = str(raw.get("sha256") or "").lower()
            source = str(raw.get("source") or "")
            archive_url = str(raw.get("archive_url") or "")
            parsed = urlsplit(archive_url)
            if (
                not package_id
                or package_id in seen
                or source not in {"official", "third_party"}
                or parsed.scheme != "https"
                or not parsed.hostname
                or len(sha256) != 64
                or any(char not in "0123456789abcdef" for char in sha256)
            ):
                raise CharacterCatalogError("角色目录包含不安全条目")
            version_key(version)
            minimum = str(raw.get("min_app_version") or "")
            maximum = str(raw.get("max_app_version") or "")
            if minimum:
                version_key(minimum)
            if maximum:
                version_key(maximum)
            seen.add(package_id)
            entries.append(
                CharacterCatalogEntry(
                    package_id=package_id,
                    name=str(raw.get("name") or package_id),
                    version=version,
                    archive_url=archive_url,
                    sha256=sha256,
                    source=source,
                    min_app_version=minimum,
                    max_app_version=maximum,
                )
            )
        return entries
