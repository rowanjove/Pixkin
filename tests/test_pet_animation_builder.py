import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from core.pet_animation_builder import PetAnimationBuilder
from core.pet_animation_qa import PetAnimationQa


class PetAnimationBuilderTests(unittest.TestCase):
    @staticmethod
    def _pose(path: Path, color=(120, 70, 190, 255)):
        image = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle(
            (36, 20, 156, 194),
            radius=34,
            fill=color,
            outline=(25, 31, 52, 255),
            width=5,
        )
        image.save(path)

    def test_builder_emits_distinct_frames_and_gif_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = {}
            for index, state in enumerate(
                ("idle", "talking", "dragging", "alerting")
            ):
                path = root / f"{state}.png"
                self._pose(path, (110 + index * 20, 60, 180, 255))
                images[state] = path

            result = PetAnimationBuilder().build(
                images=images,
                output_dir=root / "frames",
                preview_dir=root / "previews",
            )
            report = PetAnimationQa().run(
                animations=result["animations"],
                previews=result["previews"],
                output_dir=root / "qa",
            )

            self.assertTrue(report["passed"])
            for state, animation in result["animations"].items():
                self.assertGreaterEqual(len(animation["frames"]), 4)
                self.assertGreater(
                    len({
                        path.read_bytes()
                        for path in animation["frames"]
                    }),
                    1,
                )
                with Image.open(result["previews"][state]) as preview:
                    self.assertEqual(
                        preview.n_frames, len(animation["frames"])
                    )
            self.assertTrue(
                Path(report["artifacts"]["report"]).is_file()
            )

    def test_animation_qa_rejects_identical_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / "frame.png"
            copy = root / "copy.png"
            preview = root / "idle.gif"
            self._pose(frame)
            copy.write_bytes(frame.read_bytes())
            with Image.open(frame) as image:
                image.save(preview, "GIF")

            report = PetAnimationQa().run(
                animations={
                    "idle": {
                        "frames": [frame, copy],
                        "fps": 8,
                        "playback": "loop",
                    }
                },
                previews={"idle": preview},
                output_dir=root / "qa",
            )

            self.assertFalse(report["passed"])
            self.assertIn(
                "no_visible_motion",
                {issue["code"] for issue in report["errors"]},
            )

    def test_full_tier_motion_profiles_use_expected_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            states = (
                "walk_left",
                "run_right",
                "jump",
                "land",
                "edge_enter_left",
                "edge_idle_right",
                "edge_hover_top",
                "edge_exit_bottom",
            )
            images = {}
            for index, state in enumerate(states):
                path = root / f"{state}.png"
                self._pose(path, (80 + index * 12, 70, 170, 255))
                images[state] = path

            result = PetAnimationBuilder().build(
                images=images,
                output_dir=root / "frames",
                preview_dir=root / "previews",
            )
            report = PetAnimationQa().run(
                animations=result["animations"],
                previews=result["previews"],
                output_dir=root / "qa",
            )

            self.assertTrue(report["passed"])
            self.assertEqual(
                result["animations"]["walk_left"]["playback"],
                "loop",
            )
            self.assertEqual(
                result["animations"]["run_right"]["fps"],
                11,
            )
            self.assertEqual(
                result["animations"]["jump"]["playback"],
                "once",
            )
            self.assertEqual(
                result["animations"]["edge_enter_left"]["playback"],
                "once",
            )
            self.assertEqual(
                result["animations"]["edge_idle_right"]["playback"],
                "loop",
            )
            self.assertEqual(
                result["animations"]["edge_hover_top"]["playback"],
                "loop",
            )
            self.assertEqual(
                result["animations"]["edge_exit_bottom"]["playback"],
                "once",
            )


if __name__ == "__main__":
    unittest.main()
