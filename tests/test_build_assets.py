import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from scripts.build_v2_character_packs import clean_green_spill


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAssetTests(unittest.TestCase):
    def test_official_hash_manifest_is_bundled_by_both_specs(self):
        for name in ("desktop_pet.spec", "desktop_pet_portable.spec"):
            spec = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("official-sha256.json", spec)
            self.assertIn("update-public-key.pem", spec)

    def test_bundled_characters_use_v2_transparent_atlases(self):
        for package_id in ("shanshan", "linlin", "pip"):
            archive_path = ROOT / "character-packs" / f"{package_id}.zip"
            with zipfile.ZipFile(archive_path) as archive:
                manifest = archive.read("character.md").decode("utf-8")
                self.assertIn('schema_version: "2.0"', manifest)
                self.assertIn("spritesheet.webp", archive.namelist())
                preview = Image.open(
                    io.BytesIO(archive.read("images/preview.png"))
                ).convert("RGBA")
                alpha = preview.getchannel("A")
                self.assertEqual(alpha.getextrema(), (0, 255))
                flat_data = getattr(
                    preview, "get_flattened_data", preview.getdata
                )
                vivid_green = sum(
                    1
                    for red, green, blue, value in flat_data()
                    if (
                        value > 16
                        and green > red + 35
                        and green > blue + 35
                    )
                )
                self.assertEqual(vivid_green, 0)

    def test_green_spill_cleanup_removes_key_and_neutralizes_fringe(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            target = Path(directory) / "target.png"
            image = Image.new("RGBA", (4, 1))
            image.putdata([
                (0, 255, 0, 255),
                (0, 120, 0, 255),
                (40, 70, 45, 255),
                (80, 35, 110, 255),
            ])
            image.save(source)

            clean_green_spill(source, target)

            cleaned = Image.open(target).convert("RGBA")
            flat_data = getattr(
                cleaned, "get_flattened_data", cleaned.getdata
            )
            pixels = list(flat_data())
            self.assertEqual(pixels[0][3], 0)
            self.assertLess(pixels[1][3], 255)
            self.assertLessEqual(pixels[1][1], max(pixels[1][0], pixels[1][2]) + 6)
            self.assertEqual(pixels[3], (80, 35, 110, 255))

    def test_windows_powershell_build_script_has_utf8_bom(self):
        content = (ROOT / "scripts" / "build_release.ps1").read_bytes()
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        text = content.decode("utf-8-sig")
        self.assertIn("Assert-LastExitCode", text)
        self.assertIn("验收重建后的发布资产", text)
        self.assertLess(
            text.index("-m scripts.create_sample_pack"),
            text.index("scripts\\run_quality.ps1"),
        )
        self.assertIn("-m pip_audit", text)
        self.assertIn("SBOM.cdx.json", text)
        self.assertIn('"shanshan.zip", "linlin.zip", "pip.zip"', text)

    def test_official_archives_use_reproducible_zip_metadata(self):
        expected_timestamp = (1980, 1, 1, 0, 0, 0)
        for package_id in ("shanshan", "linlin", "pip"):
            with self.subTest(package_id=package_id):
                with zipfile.ZipFile(
                    ROOT / "character-packs" / f"{package_id}.zip"
                ) as archive:
                    self.assertTrue(archive.infolist())
                    self.assertTrue(
                        all(
                            info.date_time == expected_timestamp
                            for info in archive.infolist()
                        )
                    )
                    self.assertTrue(
                        all(
                            info.compress_type == zipfile.ZIP_STORED
                            for info in archive.infolist()
                        )
                    )

    def test_ci_has_explicit_windows_and_dpi_compatibility_matrix(self):
        text = (
            ROOT / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("windows-2022", text)
        self.assertIn("windows-2025", text)
        self.assertIn('scale: "1.0"', text)
        self.assertIn('scale: "2.0"', text)
        self.assertIn("QT_SCALE_FACTOR", text)
        self.assertIn("tests/test_display_layout.py", text)
        self.assertIn("tests/test_ui.py", text)


if __name__ == "__main__":
    unittest.main()
