import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from core.character_package import CharacterPackageError, CharacterPackageManager
from core.config import ConfigManager
from core.services.character_service import (
    CharacterInstallConflict,
    CharacterService,
)
from core.version import CAPABILITY_MILESTONE


ROOT = Path(__file__).resolve().parents[1]


def _custom_archive(base: Path) -> Path:
    """Create a non-built-in archive from a verified package fixture."""
    source = ROOT / "character-packs" / "shanshan.zip"
    archive_path = base / "custom.zip"
    with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(
        archive_path, "w"
    ) as target_zip:
        for info in source_zip.infolist():
            data = source_zip.read(info.filename)
            if info.filename == "character.md":
                data = data.replace(b"id: shanshan", b"id: custom")
            target_zip.writestr(info, data)
    return archive_path


class CharacterServiceTests(unittest.TestCase):
    def _service(self, base: Path) -> CharacterService:
        config = ConfigManager(str(base / "config.json"))
        manager = CharacterPackageManager(config, base / "characters")
        return CharacterService(manager)

    def test_prepared_install_activates_valid_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            archive = ROOT / "character-packs" / "shanshan.zip"

            plan = service.prepare_install(archive)
            result = service.install(plan)

            self.assertEqual(plan.conflict, CharacterInstallConflict.NONE)
            self.assertTrue(plan.can_install)
            self.assertFalse(plan.requires_confirmation)
            self.assertEqual(result.package.package_id, "shanshan")
            self.assertFalse(result.replaced)
            self.assertTrue(result.activated)
            self.assertEqual(service.get_active().package_id, "shanshan")

    def test_installed_builtin_is_protected_from_untrusted_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            archive = ROOT / "character-packs" / "shanshan.zip"
            service.install_archive(archive, activate=False)

            plan = service.prepare_install(archive)

            self.assertEqual(
                plan.conflict,
                CharacterInstallConflict.PROTECTED_BUILTIN,
            )
            self.assertFalse(plan.can_install)
            with self.assertRaisesRegex(
                CharacterPackageError,
                "内置角色不能",
            ):
                service.install(plan, replace_confirmed=True, activate=False)

    def test_existing_custom_character_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            service = self._service(base)
            archive = _custom_archive(base)
            service.install_archive(archive, activate=False)
            plan = service.prepare_install(archive)

            self.assertEqual(plan.conflict, CharacterInstallConflict.EXISTING)
            self.assertTrue(plan.requires_confirmation)
            with self.assertRaisesRegex(
                CharacterPackageError,
                "需要确认覆盖",
            ):
                service.install(plan, activate=False)

            result = service.install(
                plan,
                replace_confirmed=True,
                activate=False,
            )
            self.assertTrue(result.replaced)
            self.assertFalse(result.activated)

    def test_archive_changed_after_confirmation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            service = self._service(base)
            archive = _custom_archive(base)
            plan = service.prepare_install(archive)
            with zipfile.ZipFile(archive, "a") as package:
                package.comment = b"changed-after-confirmation"

            with self.assertRaisesRegex(
                CharacterPackageError,
                "确认后发生变化",
            ):
                service.install(plan)
            self.assertIsNone(service.get_active())

    def test_deleting_active_custom_character_switches_to_default(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            service = self._service(base)
            service.install_archive(
                ROOT / "character-packs" / "shanshan.zip",
                activate=False,
            )
            archive = _custom_archive(base)
            service.install_archive(archive)

            result = service.delete("custom")

            self.assertEqual(result.deleted_package_id, "custom")
            self.assertTrue(result.switched_to_fallback)
            self.assertEqual(result.active_package.package_id, "shanshan")
            self.assertFalse((service.root / "custom").exists())

    def test_capability_compatibility_is_checked_during_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            package = service.inspect_archive(
                ROOT / "character-packs" / "shanshan.zip"
            )
            compatible, message = service.compatibility_status(
                package,
                app_version="1.5.0",
                capability_version=CAPABILITY_MILESTONE,
            )

            self.assertTrue(compatible)
            self.assertIn("能力里程碑", message)

    def test_public_app_and_capability_versions_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            package = SimpleNamespace(
                compatibility={
                    "min_app_version": "1.5.0",
                    "min_capability_version": "2.0.0",
                }
            )

            self.assertEqual(
                service.compatibility_status(
                    package,
                    app_version="1.5.0",
                    capability_version="2.0.0",
                ),
                (True, "与当前 Pixkin 版本和能力里程碑兼容"),
            )
            compatible, message = service.compatibility_status(
                package,
                app_version="1.5.0",
                capability_version="1.9.0",
            )
            self.assertFalse(compatible)
            self.assertIn("2.0.0", message)

            app_bound = SimpleNamespace(
                compatibility={"min_app_version": "2.0.0"}
            )
            compatible, _ = service.compatibility_status(
                app_bound,
                app_version="1.5.0",
                capability_version="2.0.0",
            )
            self.assertFalse(compatible)
