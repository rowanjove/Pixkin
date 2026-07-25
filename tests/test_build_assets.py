import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image
import yaml

from scripts.build_v2_character_packs import clean_green_spill


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAssetTests(unittest.TestCase):
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

    def test_yeye_visible_ambient_actions_are_declared(self):
        with zipfile.ZipFile(
            ROOT / "character-packs" / "yeye.zip"
        ) as archive:
            text = archive.read("character.md").decode("utf-8")
            names = set(archive.namelist())
        frontmatter = text.split("---", 2)[1]
        metadata = yaml.safe_load(frontmatter)
        behavior = metadata["behavior"]
        animations = metadata["animations"]
        self.assertEqual(behavior["idle_interval_seconds"], [7, 13])
        self.assertGreater(behavior["ambient_weights"]["walk_left"], 0)
        self.assertGreater(behavior["ambient_weights"]["walk_right"], 0)
        self.assertEqual(
            animations["look_around"]["source"]["row"], 6
        )
        for side in ("left", "right", "top", "bottom"):
            for phase in ("enter", "idle", "hover", "exit"):
                source = animations[f"edge_{phase}_{side}"]["source"]
                self.assertEqual(source["type"], "frames")
                self.assertGreaterEqual(len(source["files"]), 4)
                for file in source["files"]:
                    self.assertIn(file, names)

    def test_yeye_horizontal_edge_art_faces_inward_and_joins_idle(self):
        with zipfile.ZipFile(
            ROOT / "character-packs" / "yeye.zip"
        ) as archive:
            text = archive.read("character.md").decode("utf-8")
        metadata = yaml.safe_load(text.split("---", 2)[1])
        animations = metadata["animations"]

        # The file names describe the art's occupied half. Screen-edge states
        # therefore use the opposite file set so the character faces inward.
        for side in ("left", "right"):
            art_side = "right" if side == "left" else "left"
            prefix = f"images/edge/{art_side}-"
            enter = animations[f"edge_enter_{side}"]["source"]["files"]
            hover = animations[f"edge_hover_{side}"]
            exit_ = animations[f"edge_exit_{side}"]["source"]["files"]
            self.assertEqual(hover["playback"], "once")
            self.assertEqual(
                enter,
                [f"{prefix}{index:02d}.png" for index in range(4)],
            )
            self.assertEqual(
                exit_,
                [f"{prefix}{index:02d}.png" for index in range(3, -1, -1)],
            )

    def test_windows_powershell_build_script_has_utf8_bom(self):
        content = (ROOT / "scripts" / "build_release.ps1").read_bytes()
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        text = content.decode("utf-8-sig")
        self.assertIn("Assert-LastExitCode", text)
        self.assertIn("验收重建后的发布资产", text)
        self.assertLess(
            text.index("-m scripts.create_sample_pack"),
            text.index("tests\\test_build_assets.py"),
        )


if __name__ == "__main__":
    unittest.main()
