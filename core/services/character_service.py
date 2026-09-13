"""Application service for character package lifecycle operations."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from core.character_package import (
    BUILTIN_PACKAGE_IDS,
    CharacterPackage,
    CharacterPackageError,
    CharacterPackageManager,
)
from core.config import ConfigManager
from core.version import CAPABILITY_MILESTONE, VERSION


_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?$"
)


class CharacterInstallConflict(str, Enum):
    """Conflict detected while preparing a character archive install."""

    NONE = "none"
    EXISTING = "existing"
    PROTECTED_BUILTIN = "protected_builtin"


@dataclass(frozen=True)
class CharacterInstallPlan:
    """Immutable result of validating an archive before user confirmation."""

    archive: Path
    archive_sha256: str
    package: CharacterPackage
    conflict: CharacterInstallConflict
    archive_file_count: int
    allow_builtin_replace: bool = False

    @property
    def requires_confirmation(self) -> bool:
        return self.conflict is CharacterInstallConflict.EXISTING

    @property
    def can_install(self) -> bool:
        return self.conflict is not CharacterInstallConflict.PROTECTED_BUILTIN


@dataclass(frozen=True)
class CharacterInstallResult:
    """Outcome returned after a prepared install commits successfully."""

    package: CharacterPackage
    replaced: bool
    activated: bool


@dataclass(frozen=True)
class CharacterDeleteResult:
    """Outcome of deleting a custom character package."""

    deleted_package_id: str
    active_package: Optional[CharacterPackage]
    switched_to_fallback: bool


class CharacterService:
    """Coordinate character package validation, confirmation, and activation."""

    def __init__(
        self,
        package_manager: CharacterPackageManager,
        *,
        app_version: str = VERSION,
        capability_version: str = CAPABILITY_MILESTONE,
    ):
        self._manager = package_manager
        self.app_version = app_version
        self.capability_version = capability_version

    @property
    def config(self) -> ConfigManager:
        return self._manager.config

    @property
    def root(self) -> Path:
        return self._manager.root

    def list_packages(self) -> list[CharacterPackage]:
        return self._manager.list_packages()

    def get_active(self) -> Optional[CharacterPackage]:
        return self._manager.get_active()

    def prepare_install(
        self,
        archive: str | Path,
        *,
        allow_builtin_replace: bool = False,
    ) -> CharacterInstallPlan:
        """Validate an archive and describe any install conflict without writes."""
        archive_path = Path(archive).expanduser().resolve()
        package = self._manager.inspect_zip(str(archive_path))
        compatible, message = self.compatibility_status(package)
        if not compatible:
            raise CharacterPackageError(message)
        target_exists = (self.root / package.package_id).exists()
        if not target_exists:
            conflict = CharacterInstallConflict.NONE
        elif (
            package.package_id in BUILTIN_PACKAGE_IDS
            and not allow_builtin_replace
        ):
            conflict = CharacterInstallConflict.PROTECTED_BUILTIN
        else:
            conflict = CharacterInstallConflict.EXISTING
        return CharacterInstallPlan(
            archive=archive_path,
            archive_sha256=self._archive_digest(archive_path),
            package=package,
            conflict=conflict,
            archive_file_count=self._archive_file_count(archive_path),
            allow_builtin_replace=allow_builtin_replace,
        )

    def inspect_archive(self, archive: str | Path) -> CharacterPackage:
        """Read and validate package contents without checking install policy."""

        archive_path = Path(archive).expanduser().resolve()
        return self._manager.inspect_zip(str(archive_path))

    def compatibility_status(
        self,
        package: CharacterPackage,
        *,
        app_version: str | None = None,
        capability_version: str | None = None,
    ) -> tuple[bool, str]:
        value = package.compatibility
        if not isinstance(value, dict):
            return False, "角色包兼容性声明无效"
        value = self._normalize_legacy_compatibility(value)
        app = app_version or self.app_version
        capability = capability_version or self.capability_version
        try:
            current_app = self._version_key(app)
            current_capability = self._version_key(capability)
            minimum_app = self._version_limit(value, "min_app_version")
            maximum_app = self._version_limit(value, "max_app_version")
            minimum_capability = self._version_limit(
                value,
                "min_capability_version",
            )
            maximum_capability = self._version_limit(
                value,
                "max_capability_version",
            )
        except ValueError:
            return False, "角色包兼容性版本声明无效"
        if minimum_app and current_app < minimum_app:
            return False, (
                f"需要 Pixkin {value['min_app_version']} 或更高版本"
            )
        if maximum_app and current_app > maximum_app:
            return False, f"最高支持 Pixkin {value['max_app_version']}"
        if minimum_capability and current_capability < minimum_capability:
            return False, (
                "需要 Pixkin 能力里程碑 "
                f"{value['min_capability_version']}"
            )
        if maximum_capability and current_capability > maximum_capability:
            return False, (
                "角色包最高支持能力里程碑 "
                f"{value['max_capability_version']}"
            )
        return True, "与当前 Pixkin 版本和能力里程碑兼容"

    @staticmethod
    def _normalize_legacy_compatibility(value: dict) -> dict:
        """迁移早期把能力版本误写到 min_app_version 的 v2 包。"""

        if (
            "min_capability_version" not in value
            and value.get("atlas_layout") == "pixkin-8x9"
            and value.get("min_app_version") == CAPABILITY_MILESTONE
        ):
            normalized = dict(value)
            normalized.pop("min_app_version", None)
            normalized["min_capability_version"] = CAPABILITY_MILESTONE
            return normalized
        return value

    @staticmethod
    def _version_key(value: str) -> tuple[int, int, int, int]:
        match = _SEMVER.fullmatch(str(value or ""))
        if not match:
            raise ValueError(value)
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            0 if match.group(4) else 1,
        )

    @classmethod
    def _version_limit(
        cls,
        compatibility: dict,
        field: str,
    ) -> tuple[int, int, int, int] | None:
        value = compatibility.get(field)
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError(value)
        return cls._version_key(value)

    @staticmethod
    def archive_fingerprint(archive: str | Path) -> str:
        return CharacterService._archive_digest(
            Path(archive).expanduser().resolve()
        )

    @staticmethod
    def archive_file_count(archive: str | Path) -> int:
        return CharacterService._archive_file_count(
            Path(archive).expanduser().resolve()
        )

    def install(
        self,
        plan: CharacterInstallPlan,
        *,
        replace_confirmed: bool = False,
        activate: bool = True,
    ) -> CharacterInstallResult:
        """Commit a prepared install after revalidating archive and conflict."""
        current = self.prepare_install(
            plan.archive,
            allow_builtin_replace=plan.allow_builtin_replace,
        )
        if (
            current.archive_sha256 != plan.archive_sha256
            or current.package.package_id != plan.package.package_id
        ):
            raise CharacterPackageError(
                "角色包在确认后发生变化，请重新选择并确认。"
            )
        if not current.can_install:
            raise CharacterPackageError(
                "内置角色不能被第三方角色包覆盖。"
            )
        if current.requires_confirmation and not replace_confirmed:
            raise CharacterPackageError(
                f"角色包“{current.package.package_id}”已经安装，"
                "需要确认覆盖后才能继续。"
            )
        replaced = current.conflict is CharacterInstallConflict.EXISTING
        package = self._manager.import_zip(
            str(current.archive),
            replace=replaced,
            activate=activate,
            allow_builtin_replace=current.allow_builtin_replace,
        )
        return CharacterInstallResult(
            package=package,
            replaced=replaced,
            activated=activate,
        )

    def install_archive(
        self,
        archive: str | Path,
        *,
        replace_confirmed: bool = False,
        activate: bool = True,
        allow_builtin_replace: bool = False,
    ) -> CharacterInstallResult:
        """Prepare and immediately install a trusted or pre-confirmed archive."""
        plan = self.prepare_install(
            archive,
            allow_builtin_replace=allow_builtin_replace,
        )
        return self.install(
            plan,
            replace_confirmed=replace_confirmed,
            activate=activate,
        )

    def activate(self, package_id: str) -> CharacterPackage:
        return self._manager.activate(package_id)

    def rename(self, package_id: str, new_name: str) -> CharacterPackage:
        return self._manager.rename_package(package_id, new_name)

    def delete(self, package_id: str) -> CharacterDeleteResult:
        active_before = self.get_active()
        fallback = self._manager.delete_package(package_id)
        active_after = fallback or self.get_active()
        return CharacterDeleteResult(
            deleted_package_id=package_id,
            active_package=active_after,
            switched_to_fallback=bool(
                active_before
                and active_before.package_id == package_id
                and fallback
            ),
        )

    def read_archive_preview(self, archive: str | Path) -> Optional[bytes]:
        return self._manager.read_zip_preview(str(archive))

    def package_matches_archive(
        self,
        package_id: str,
        archive: str | Path,
    ) -> bool:
        return self._manager.package_matches_zip(package_id, str(archive))

    @staticmethod
    def _archive_digest(archive: Path) -> str:
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _archive_file_count(archive: Path) -> int:
        with zipfile.ZipFile(archive) as package:
            return sum(
                1
                for item in package.infolist()
                if not item.is_dir()
            )
