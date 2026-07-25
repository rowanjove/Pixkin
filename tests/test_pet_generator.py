import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml
from PIL import Image, ImageDraw

from core.character_package import (
    EDGE_STATES,
    FULL_STATES,
    STANDARD_STATES,
    CharacterPackageManager,
)
from core.config import ConfigManager
from core.pet_animation_builder import PetAnimationBuilder
from core.pet_generation_run import PetGenerationRunStore
from core.pet_generator import (
    BASIC_POSE_IDS,
    FULL_POSES,
    FULL_POSE_IDS,
    HARD_CARTOON_RULES,
    POSES,
    STANDARD_POSE_IDS,
    PetGenerationWorker,
)


class PetGeneratorTests(unittest.TestCase):
    @staticmethod
    def _generated_sprite_bytes(index):
        image = Image.new("RGB", (512, 512), (0, 255, 0))
        color = (
            90 + index * 25,
            35 + index * 7,
            170 - index * 12,
        )
        ImageDraw.Draw(image).rounded_rectangle(
            (150, 90, 362, 430),
            radius=70,
            fill=color,
            outline=(30, 35, 55),
            width=14,
        )
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()

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

    @staticmethod
    def _write_pose(path: Path, index: int):
        image = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (36, 20, 156, 194),
            radius=28,
            fill=(
                80 + (index * 7) % 150,
                50 + (index * 11) % 120,
                100 + (index * 13) % 140,
                255,
            ),
        )
        image.save(path)

    def test_every_prompt_keeps_non_negotiable_cartoon_lock(self):
        worker = self._worker("reference.png")
        self.assertIn("never a real person", HARD_CARTOON_RULES)
        self.assertIn(HARD_CARTOON_RULES, worker._base_prompt())
        for pose in FULL_POSES.values():
            self.assertIn(HARD_CARTOON_RULES, worker._pose_prompt(pose))

    def test_standard_generation_states_match_package_contract(self):
        self.assertEqual(set(STANDARD_POSE_IDS), STANDARD_STATES)
        self.assertEqual(len(STANDARD_POSE_IDS), 19)
        self.assertTrue(set(BASIC_POSE_IDS).issubset(STANDARD_POSE_IDS))

    def test_full_generation_states_match_package_contract(self):
        required = FULL_STATES | EDGE_STATES

        self.assertEqual(set(FULL_POSE_IDS), required)
        self.assertEqual(len(FULL_POSE_IDS), 44)
        self.assertTrue(set(STANDARD_POSE_IDS).issubset(FULL_POSE_IDS))

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

    def test_standard_mode_emits_a_valid_standard_tier_package(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            work = base / "standard-work"
            images = work / "images"
            images.mkdir(parents=True)
            generated = {}
            for index, state in enumerate(STANDARD_POSE_IDS):
                target = images / f"{state}.png"
                self._write_pose(target, index)
                generated[state] = target
            worker = PetGenerationWorker(
                api_key="test",
                base_url="",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="",
                style_notes="",
                reference_paths=[
                    root / "assets" / "pixkin" / "pip-avatar.png"
                ],
                generation_mode="standard",
            )
            animation_build = PetAnimationBuilder().build(
                images=generated,
                output_dir=images / "frames",
                preview_dir=work / "previews",
            )
            package = worker._package(
                work,
                "nova",
                generated,
                animation_sequences=animation_build["animations"],
            )
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config, root=base / "characters"
            )

            inspected = manager.inspect_zip(str(package))

            self.assertEqual(inspected.quality_tier, "standard")
            self.assertEqual(
                set(inspected.animations), STANDARD_STATES
            )
            self.assertTrue(
                all(
                    len(animation.frames) >= 4
                    for animation in inspected.animations.values()
                )
            )
            self.assertEqual(
                inspected.animations["thinking"].playback,
                "loop",
            )
            self.assertIn(
                "look_around",
                inspected.behavior["ambient_weights"],
            )
            self.assertIn(
                "happy",
                inspected.behavior["ambient_weights"],
            )

    def test_full_mode_emits_a_valid_full_tier_package(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            work = base / "full-work"
            images = work / "images"
            images.mkdir(parents=True)
            generated = {}
            for index, state in enumerate(FULL_POSE_IDS):
                target = images / f"{state}.png"
                self._write_pose(target, index)
                generated[state] = target
            worker = PetGenerationWorker(
                api_key="test",
                base_url="",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="",
                style_notes="",
                reference_paths=[
                    root / "assets" / "pixkin" / "pip-avatar.png"
                ],
                generation_mode="full",
            )
            animation_build = PetAnimationBuilder().build(
                images=generated,
                output_dir=images / "frames",
                preview_dir=work / "previews",
            )
            package = worker._package(
                work,
                "nova",
                generated,
                animation_sequences=animation_build["animations"],
            )
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config, root=base / "characters"
            )

            inspected = manager.inspect_zip(str(package))

            self.assertEqual(inspected.quality_tier, "full")
            self.assertEqual(
                set(inspected.animations),
                FULL_STATES | EDGE_STATES,
            )
            self.assertEqual(
                inspected.edge["supported_sides"],
                ["left", "right", "top", "bottom"],
            )
            self.assertEqual(
                inspected.animations["walk_left"].playback,
                "loop",
            )
            self.assertEqual(
                inspected.animations["edge_enter_left"].playback,
                "once",
            )
            self.assertEqual(
                inspected.animations["edge_idle_left"].playback,
                "loop",
            )

    def test_standard_run_creates_all_tasks_before_first_review(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            worker = PetGenerationWorker(
                api_key="test",
                base_url="",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="",
                style_notes="",
                reference_paths=[reference],
                generation_mode="standard",
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    worker,
                    "_generate",
                    return_value=self._generated_sprite_bytes(1),
                ),
            ):
                worker.run()

            run = store.list_runs()[0]
            self.assertEqual(run["request"]["mode"], "standard")
            self.assertEqual(
                set(run["tasks"]),
                {"canonical", *STANDARD_POSE_IDS},
            )
            self.assertEqual(run["stage"], "canonical_review")

    def test_full_run_creates_all_tasks_and_resumes_as_full(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            worker = PetGenerationWorker(
                api_key="test",
                base_url="",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="",
                style_notes="",
                reference_paths=[reference],
                generation_mode="full",
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    worker,
                    "_generate",
                    return_value=self._generated_sprite_bytes(1),
                ),
            ):
                worker.run()

            run = store.list_runs()[0]
            store.record_review(
                run["id"], "canonical", "accepted"
            )
            resumed = PetGenerationWorker.resume_from(
                run_id=run["id"],
                api_key="test",
                run_store=store,
            )

            self.assertEqual(run["request"]["mode"], "full")
            self.assertEqual(
                set(run["tasks"]),
                {"canonical", *FULL_POSE_IDS},
            )
            self.assertEqual(resumed.generation_mode, "full")
            self.assertEqual(
                [state for state, _ in resumed._pose_items()],
                list(FULL_POSE_IDS),
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    resumed,
                    "_generate",
                    return_value=self._generated_sprite_bytes(2),
                ) as generate,
            ):
                resumed.run()

            review = store.load(run["id"])
            self.assertEqual(generate.call_count, 5)
            self.assertEqual(review["stage"], "core_review")
            self.assertEqual(
                review["tasks"]["run_right"]["status"],
                "complete",
            )
            self.assertEqual(
                review["tasks"]["run_left"]["status"],
                "pending",
            )

    def test_legacy_full_mode_resumes_with_only_original_nine_tasks(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            run = store.create(
                run_id="legacy-full-run",
                request={
                    "pet_name": "Nova",
                    "reference_paths": [
                        str(root / "assets/pixkin/pip-avatar.png")
                    ],
                    "mode": "full",
                },
                task_ids=["canonical", *POSES],
            )

            resumed = PetGenerationWorker.resume_from(
                run_id=run["id"],
                api_key="test",
                run_store=store,
            )

            self.assertEqual(resumed.generation_mode, "legacy_full")
            self.assertEqual(
                [state for state, _ in resumed._pose_items()],
                list(POSES),
            )

    def test_worker_pauses_for_identity_review_then_builds_draft(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            store = PetGenerationRunStore(data_root / "pet-lab" / "runs")
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
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    worker,
                    "_generate",
                    return_value=reference.read_bytes(),
                ),
            ):
                worker.run()

            runs = store.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "needs_review")
            self.assertEqual(runs[0]["stage"], "canonical_review")
            self.assertEqual(
                runs[0]["tasks"]["canonical"]["status"], "complete"
            )
            stored_references = [
                Path(path)
                for path in runs[0]["request"]["reference_paths"]
            ]
            self.assertEqual(len(stored_references), 1)
            self.assertTrue(stored_references[0].is_file())
            self.assertEqual(
                stored_references[0].parent.name, "references"
            )
            for state in BASIC_POSE_IDS:
                self.assertEqual(
                    runs[0]["tasks"][state]["status"], "pending"
                )

            store.record_review(
                runs[0]["id"], "canonical", "accepted"
            )
            resumed = PetGenerationWorker.resume_from(
                run_id=runs[0]["id"],
                api_key="test",
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    resumed,
                    "_generate",
                    side_effect=[
                        self._generated_sprite_bytes(index)
                        for index in range(len(BASIC_POSE_IDS))
                    ],
                ),
            ):
                resumed.run()

            core_review = store.load(runs[0]["id"])
            self.assertEqual(core_review["status"], "needs_review")
            self.assertEqual(core_review["stage"], "core_review")
            self.assertTrue(
                Path(
                    core_review["artifacts"]["core_contact_sheet"]
                ).is_file()
            )
            self.assertTrue(
                Path(core_review["artifacts"]["core_qa_report"]).is_file()
            )
            store.record_review(
                runs[0]["id"], "core_actions", "accepted"
            )
            finalized = PetGenerationWorker.resume_from(
                run_id=runs[0]["id"],
                api_key="",
                run_store=store,
            )
            with patch("core.pet_generator.OpenAI") as openai:
                finalized.run()
            openai.assert_not_called()

            ready = store.load(runs[0]["id"])
            self.assertEqual(ready["status"], "needs_review")
            self.assertEqual(ready["stage"], "final_review")
            self.assertTrue(
                Path(ready["artifacts"]["qa_report"]).is_file()
            )
            self.assertTrue(
                Path(
                    ready["artifacts"]["animation_qa_report"]
                ).is_file()
            )
            for preview in ready["artifacts"][
                "animation_previews"
            ].values():
                self.assertTrue(Path(preview).is_file())
            for state in BASIC_POSE_IDS:
                self.assertEqual(
                    ready["tasks"][state]["status"], "complete"
                )
            package_path = Path(ready["artifacts"]["package"])
            self.assertTrue(package_path.is_file())
            config = ConfigManager(str(data_root / "config.json"))
            package_manager = CharacterPackageManager(
                config,
                root=data_root / "characters",
            )
            inspected = package_manager.inspect_zip(str(package_path))
            self.assertEqual(inspected.schema_version, "2.0")
            self.assertEqual(inspected.quality_tier, "basic")
            for state in BASIC_POSE_IDS:
                self.assertGreaterEqual(
                    len(inspected.animations[state].frames), 4
                )
                self.assertTrue(all(
                    "images/frames" in frame.file.as_posix()
                    for frame in inspected.animations[state].frames
                ))

            manifest_path = (
                store.workspace(runs[0]["id"]) / "run.json"
            )
            legacy = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            legacy["reviews"].pop("core_actions", None)
            legacy["artifacts"].pop("qa_report", None)
            legacy["artifacts"].pop("qa_contact_sheet", None)
            manifest_path.write_text(
                json.dumps(legacy, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            migrated = PetGenerationWorker.resume_from(
                run_id=runs[0]["id"],
                api_key="test",
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    migrated,
                    "_generate",
                    side_effect=AssertionError(
                        "completed images must be reused"
                    ),
                ),
            ):
                migrated.run()
            self.assertEqual(
                store.load(runs[0]["id"])["stage"], "core_review"
            )

    def test_retrying_one_failed_action_does_not_redraw_completed_work(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            first = PetGenerationWorker(
                api_key="test",
                base_url="https://api.openai.com/v1",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="温暖可靠",
                style_notes="",
                reference_paths=[reference],
                full_hatch=False,
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    first,
                    "_generate",
                    return_value=reference.read_bytes(),
                ),
            ):
                first.run()
            run = store.list_runs()[0]
            store.record_review(run["id"], "canonical", "accepted")

            failed = PetGenerationWorker.resume_from(
                run_id=run["id"],
                api_key="test",
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    failed,
                    "_generate",
                    side_effect=RuntimeError("temporary image failure"),
                ),
            ):
                failed.run()
            self.assertEqual(
                store.load(run["id"])["tasks"]["idle"]["status"], "failed"
            )

            retried = PetGenerationWorker.resume_from(
                run_id=run["id"],
                api_key="test",
                run_store=store,
                retry_task_id="idle",
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    retried,
                    "_generate",
                    return_value=reference.read_bytes(),
                ) as generate,
            ):
                retried.run()

            record = store.load(run["id"])
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(record["status"], "pending")
            self.assertEqual(record["stage"], "action_generation")
            self.assertEqual(record["tasks"]["idle"]["status"], "complete")
            self.assertEqual(record["tasks"]["idle"]["attempts"], 2)
            self.assertEqual(record["tasks"]["talking"]["status"], "pending")

    def test_regeneration_preserves_raw_and_normalized_candidates(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            first_raw = self._generated_sprite_bytes(1)
            second_raw = self._generated_sprite_bytes(2)
            worker = PetGenerationWorker(
                api_key="test",
                base_url="https://api.openai.com/v1",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="",
                style_notes="",
                reference_paths=[reference],
                full_hatch=False,
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    worker, "_generate", return_value=first_raw
                ),
            ):
                worker.run()
            run = store.list_runs()[0]
            first_task = run["tasks"]["canonical"]
            self.assertEqual(
                first_task["selected_candidate"], "attempt-001"
            )
            first_candidate = first_task["candidates"][0]
            self.assertEqual(
                store.artifact_path(
                    run["id"], first_candidate["source_artifact"]
                ).read_bytes(),
                first_raw,
            )

            regenerated = PetGenerationWorker.resume_from(
                run_id=run["id"],
                api_key="test",
                run_store=store,
                retry_task_id="canonical",
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    regenerated, "_generate", return_value=second_raw
                ),
            ):
                regenerated.run()

            task = store.load(run["id"])["tasks"]["canonical"]
            self.assertEqual(
                [item["id"] for item in task["candidates"]],
                ["attempt-001", "attempt-002"],
            )
            self.assertEqual(task["selected_candidate"], "attempt-002")
            for candidate in task["candidates"]:
                self.assertTrue(
                    store.artifact_path(
                        run["id"], candidate["source_artifact"]
                    ).is_file()
                )
                self.assertTrue(
                    store.artifact_path(
                        run["id"], candidate["sprite_artifact"]
                    ).is_file()
                )
            active = store.workspace(run["id"]) / "images/canonical.png"
            selected = store.artifact_path(
                run["id"], task["candidates"][1]["sprite_artifact"]
            )
            self.assertEqual(active.read_bytes(), selected.read_bytes())

    def test_first_retry_of_legacy_run_archives_existing_sprite(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            worker = PetGenerationWorker(
                api_key="test",
                base_url="",
                model="gpt-image-2",
                quality="low",
                pet_name="Nova",
                personality="",
                style_notes="",
                reference_paths=[reference],
                full_hatch=False,
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    worker,
                    "_generate",
                    return_value=self._generated_sprite_bytes(1),
                ),
            ):
                worker.run()
            run = store.list_runs()[0]
            manifest = store.workspace(run["id"]) / "run.json"
            legacy = json.loads(manifest.read_text(encoding="utf-8"))
            legacy_task = legacy["tasks"]["canonical"]
            legacy_task.pop("candidates", None)
            legacy_task.pop("selected_candidate", None)
            manifest.write_text(
                json.dumps(legacy, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            retried = PetGenerationWorker.resume_from(
                run_id=run["id"],
                api_key="test",
                run_store=store,
                retry_task_id="canonical",
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    retried,
                    "_generate",
                    return_value=self._generated_sprite_bytes(2),
                ),
            ):
                retried.run()

            task = store.load(run["id"])["tasks"]["canonical"]
            self.assertEqual(
                [item["id"] for item in task["candidates"]],
                ["legacy-001", "attempt-002"],
            )
            self.assertTrue(
                task["candidates"][0]["metadata"][
                    "migrated_from_active_artifact"
                ]
            )

    def test_full_hatch_pauses_after_core_actions_before_remaining_poses(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / "assets" / "pixkin" / "pip-avatar.png"
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            worker = self._worker(reference)
            worker._run_store = store
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    worker,
                    "_generate",
                    return_value=reference.read_bytes(),
                ),
            ):
                worker.run()
            run = store.list_runs()[0]
            store.record_review(run["id"], "canonical", "accepted")

            resumed = PetGenerationWorker.resume_from(
                run_id=run["id"],
                api_key="test",
                run_store=store,
            )
            with (
                patch("core.pet_generator.OpenAI"),
                patch.object(
                    resumed,
                    "_generate",
                    side_effect=[
                        self._generated_sprite_bytes(index)
                        for index in range(len(BASIC_POSE_IDS))
                    ],
                ) as generate,
            ):
                resumed.run()

            review = store.load(run["id"])
            self.assertEqual(generate.call_count, len(BASIC_POSE_IDS))
            self.assertEqual(review["stage"], "core_review")
            for state in BASIC_POSE_IDS:
                self.assertEqual(
                    review["tasks"][state]["status"], "complete"
                )
            self.assertEqual(review["tasks"]["blink"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
