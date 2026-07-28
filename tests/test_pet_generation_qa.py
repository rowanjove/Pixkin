import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from core.pet_generation_qa import PetGenerationQa


class PetGenerationQaTests(unittest.TestCase):
    @staticmethod
    def _sprite(path: Path, color, box=(40, 24, 152, 194)):
        image = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle(
            box, radius=24, fill=(*color, 255)
        )
        image.save(path)

    def test_valid_unique_sprites_emit_report_and_contact_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = {}
            for index, state in enumerate(("idle", "talking", "alerting")):
                path = root / f"{state}.png"
                self._sprite(path, (120 + index * 20, 60, 180))
                images[state] = path

            report = PetGenerationQa().run(
                images=images,
                expected_states=images,
                output_dir=root / "qa",
                canonical=images["idle"],
            )

            self.assertTrue(report["passed"])
            self.assertEqual(report["summary"]["errors"], 0)
            self.assertTrue(
                Path(report["artifacts"]["contact_sheet"]).is_file()
            )
            report_path = Path(report["artifacts"]["report"])
            self.assertTrue(report_path.is_file())
            persisted = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(persisted["passed"])

    def test_duplicate_actions_are_a_blocking_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            idle = root / "idle.png"
            talking = root / "talking.png"
            self._sprite(idle, (160, 70, 180))
            talking.write_bytes(idle.read_bytes())

            report = PetGenerationQa().run(
                images={"idle": idle, "talking": talking},
                expected_states=("idle", "talking"),
                output_dir=root / "qa",
            )

            self.assertFalse(report["passed"])
            duplicate = next(
                issue for issue in report["errors"]
                if issue["code"] == "duplicate_actions"
            )
            self.assertEqual(duplicate["states"], ["idle", "talking"])

    def test_empty_missing_and_edge_touching_sprites_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.png"
            edge = root / "edge.png"
            Image.new("RGBA", (192, 208), (0, 0, 0, 0)).save(empty)
            self._sprite(edge, (180, 70, 100), box=(0, 20, 120, 194))

            report = PetGenerationQa().run(
                images={"empty": empty, "edge": edge},
                expected_states=("empty", "edge", "missing"),
                output_dir=root / "qa",
            )

            codes = {issue["code"] for issue in report["errors"]}
            self.assertIn("empty_sprite", codes)
            self.assertIn("unsafe_margin", codes)
            self.assertIn("missing_image", codes)


if __name__ == "__main__":
    unittest.main()
