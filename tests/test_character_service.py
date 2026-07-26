import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.character_package import CharacterPackageError, CharacterPackageManager
from core.config import ConfigManager
from core.services.character_service import (
    CharacterInstallConflict,
    CharacterService,
)


ROOT = Path(__file__).resolve().parents[1]


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
            service = self._service(Path(directory))
            archive = ROOT / "character-packs" / "yeye.zip"
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
            archive = base / "yeye.zip"
            shutil.copy2(
                ROOT / "character-packs" / "yeye.zip",
                archive,
            )
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
            service = self._service(Path(directory))
            service.install_archive(
                ROOT / "character-packs" / "shanshan.zip",
                activate=False,
            )
            service.install_archive(
                ROOT / "character-packs" / "yeye.zip",
            )

            result = service.delete("yeye")

            self.assertEqual(result.deleted_package_id, "yeye")
            self.assertTrue(result.switched_to_fallback)
            self.assertEqual(result.active_package.package_id, "shanshan")
            self.assertFalse((service.root / "yeye").exists())
