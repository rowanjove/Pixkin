import runpy
import unittest
from pathlib import Path

from core.config import DEFAULT_CONFIG
from core.version import VERSION, folder_archive_name, portable_executable_name
from scripts.generate_version_info import render_version_info


ROOT = Path(__file__).resolve().parents[1]


class VersioningTests(unittest.TestCase):
    def test_runtime_and_default_config_use_release_version(self):
        self.assertEqual(DEFAULT_CONFIG["app"]["version"], VERSION)
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("setApplicationVersion(VERSION)", main_source)

    def test_build_names_come_from_release_version(self):
        self.assertEqual(
            portable_executable_name(),
            f"Pixkin-Portable-{VERSION}",
        )
        self.assertEqual(
            folder_archive_name(),
            f"Pixkin-{VERSION}-win64",
        )
        portable_spec = (
            ROOT / "desktop_pet_portable.spec"
        ).read_text(encoding="utf-8")
        self.assertNotIn('name="Pixkin-Portable-1.3.0"', portable_spec)

    def test_windows_version_resource_is_current(self):
        expected = render_version_info(VERSION)
        actual = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        self.assertEqual(actual.replace("\r\n", "\n"), expected)

    def test_portable_spec_can_resolve_version_helpers(self):
        scope = runpy.run_path(str(ROOT / "core" / "version.py"))
        self.assertEqual(
            scope["portable_executable_name"](),
            portable_executable_name(),
        )


if __name__ == "__main__":
    unittest.main()
