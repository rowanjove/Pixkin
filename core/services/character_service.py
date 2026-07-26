"""Application service for character package lifecycle operations."""

from __future__ import annotations

import hashlib
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

    def __init__(self, package_manager: CharacterPackageManager):
        self._manager = package_manager

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
