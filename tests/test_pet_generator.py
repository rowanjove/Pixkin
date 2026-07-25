import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml
from PIL import Image

from core.character_package import CharacterPackageManager
from core.config import ConfigManager
from core.pet_generation_run import PetGenerationRunStore
from core.pet_generator import (
    BASIC_POSE_IDS,
    HARD_CARTOON_RULES,
    POSES,
    PetGenerationWorker,
)


class PetGeneratorTests(unittest.TestCase):
    def _worker(self, reference):
        return PetGenerationWorker(
            api_key="test",
            base_url="https://api.openai.com/v1",
            model="gpt-image-2",
            quality="low",
            pet_name="Nova",
            personality="温暖可靠",
            style_notes="珊瑚色围巾",
            reference_paths=[reference],
            full_hatch=True,
        )

    def test_every_prompt_keeps_non_negotiable_cartoon_lock(self):
        worker = self._worker("reference.png")
        self.assertIn("never a real person", HARD_CARTOON_RULES)
        self.assertIn(HARD_CARTOON_RULES, worker._base_prompt())
        for pose in POSES.values():
            self.assertIn(HARD_CARTOON_RULES, worker._pose_prompt(pose))

    def test_chroma_background_is_removed(self):
        root = Path(__file__).resolve().parents[1]
        raw = (root / "assets" / "pixkin" / "pip-chroma.png").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "sprite.png"
            PetGenerationWorker._save_sprite(raw, target)
            result = Image.open(target).convert("RGBA")
            self.assertEqual(result.size, (192, 208))
            self.assertEqual(result.getpixel((0, 0))[3], 0)
            self.assertIsNotNone(result.getchannel("A").getbbox())

    def test_generated_package_contains_manifest_and_all_states(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "nova-work"
            images = work / "images"
            images.mkdir(parents=True)
            generated = {}
            for state in POSES:
                target = images / f"{state}.png"
                target.write_bytes(
                    (root / "assets" / "pixkin" / "pip-avatar.png").read_bytes()
                )
                generated[state] = target
            worker = self._worker(root / "assets" / "pixkin" / "pip-avatar.png")
            package = worker._package(work, "nova", generated)
            self.assertEqual(package.name, "nova-work.zip")
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                self.assertIn("character.md", names)
                manifest = archive.read("character.md").decode("utf-8")
                metadata = yaml.safe_load(manifest.split("---", 2)[1])
                self.assertEqual(metadata["schema_version"], "2.0")
                self.assertEqual(metadata["quality_tier"], "basic")
                self.assertFalse(
                    metadata["rights"]["author_confirmed_rights"]
                )
                for state in POSES:
                    self.assertIn(f"images/{state}.png", names)

    def test_worker_persists_a_complete_draft_run_manifest(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            worker = PetGenerationWorker(
                api_key="test",
                base_url="https://api.openai.com/v1",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="温暖可靠",
                style_notes="珊瑚色围巾",
                reference_paths=[reference],
                full_hatch=False,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch(
                    "core.pet_generation_run.user_data_dir",
                    return_value=data_root,
                ),
                patch.object(
                    worker,
                    "_generate",
                    return_value=reference.read_bytes(),
                ),
            ):
                worker.run()

            store = PetGenerationRunStore(data_root / "pet-lab" / "runs")
            runs = store.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "complete")
            self.assertEqual(runs[0]["stage"], "ready")
            self.assertEqual(
                runs[0]["tasks"]["canonical"]["status"], "complete"
            )
            for state in BASIC_POSE_IDS:
                self.assertEqual(
                    runs[0]["tasks"][state]["status"], "complete"
                )
            package_path = Path(runs[0]["artifacts"]["package"])
            self.assertTrue(package_path.is_file())
            config = ConfigManager(str(data_root / "config.json"))
            package_manager = CharacterPackageManager(
                config,
                root=data_root / "characters",
            )
            inspected = package_manager.inspect_zip(str(package_path))
            self.assertEqual(inspected.schema_version, "2.0")
            self.assertEqual(inspected.quality_tier, "basic")


if __name__ == "__main__":
    unittest.main()
