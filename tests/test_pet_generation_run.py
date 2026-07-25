import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.pet_generation_run import (
    PetGenerationRunError,
    PetGenerationRunStore,
)


class PetGenerationRunStoreTests(unittest.TestCase):
    def test_run_manifest_tracks_stage_tasks_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            created = store.create(
                run_id="test-pet-1234",
                request={"pet_name": "测试伙伴", "mode": "full"},
                task_ids=["canonical", "idle"],
            )
            self.assertEqual(created["status"], "pending")
            self.assertEqual(created["tasks"]["idle"]["attempts"], 0)

            store.update_stage(
                created["id"], "action_generation", status="running"
            )
            store.update_task(
                created["id"],
                "idle",
                "running",
                increment_attempt=True,
            )
            store.update_task(
                created["id"],
                "idle",
                "complete",
                artifact="images/idle.png",
            )
            ready = store.update_stage(
                created["id"],
                "ready",
                status="complete",
                artifacts={"package": "test-pet.zip"},
            )

            self.assertEqual(ready["status"], "complete")
            self.assertEqual(ready["stage"], "ready")
            self.assertEqual(ready["tasks"]["idle"]["attempts"], 1)
            self.assertEqual(
                ready["tasks"]["idle"]["artifact"], "images/idle.png"
            )
            self.assertEqual(
                ready["artifacts"]["package"], "test-pet.zip"
            )

    def test_manifest_write_is_atomic_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            record = store.create(
                run_id="atomic-run",
                request={},
                task_ids=["canonical"],
            )
            store.update_stage(record["id"], "running", status="running")

            workspace = store.workspace(record["id"])
            parsed = json.loads(
                (workspace / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(parsed["stage"], "running")
            self.assertEqual(list(workspace.glob(".run-*.tmp")), [])

    def test_unsafe_run_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            with self.assertRaises(PetGenerationRunError):
                store.create(
                    run_id="../outside",
                    request={},
                    task_ids=["canonical"],
                )

    def test_list_runs_orders_newest_first_and_skips_corrupt_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            with patch(
                "core.pet_generation_run._utc_now",
                side_effect=[
                    "2026-07-25T01:00:00+00:00",
                    "2026-07-25T02:00:00+00:00",
                ],
            ):
                first = store.create(
                    run_id="first-run",
                    request={},
                    task_ids=["canonical"],
                )
                second = store.create(
                    run_id="second-run",
                    request={},
                    task_ids=["canonical"],
                )
            corrupt = Path(directory) / "corrupt-run"
            corrupt.mkdir()
            (corrupt / "run.json").write_text("{", encoding="utf-8")

            self.assertEqual(
                [item["id"] for item in store.list_runs()],
                [second["id"], first["id"]],
            )

    def test_unknown_task_and_status_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="state-run",
                request={},
                task_ids=["canonical"],
            )
            with self.assertRaises(PetGenerationRunError):
                store.update_task(run["id"], "missing", "running")
            with self.assertRaises(PetGenerationRunError):
                store.update_stage(
                    run["id"], "bad", status="unknown"
                )

    def test_review_decisions_and_single_task_reset_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="review-run",
                request={},
                task_ids=["canonical", "idle"],
            )
            store.update_task(
                run["id"],
                "idle",
                "failed",
                error="temporary failure",
                increment_attempt=True,
            )
            store.record_review(
                run["id"], "canonical", "accepted", note="identity matches"
            )
            reset = store.reset_task(run["id"], "idle")

            self.assertEqual(
                reset["reviews"]["canonical"]["decision"], "accepted"
            )
            self.assertEqual(reset["tasks"]["idle"]["status"], "pending")
            self.assertEqual(reset["tasks"]["idle"]["attempts"], 1)
            self.assertIsNone(reset["tasks"]["idle"]["artifact"])
            self.assertIsNone(reset["tasks"]["idle"]["error"])

    def test_candidates_are_append_only_and_can_be_reselected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="candidate-run",
                request={},
                task_ids=["idle"],
            )
            store.record_candidate(
                run["id"],
                "idle",
                candidate_id="attempt-001",
                source_artifact=(
                    "candidates/idle/attempt-001/source.png"
                ),
                sprite_artifact=(
                    "candidates/idle/attempt-001/sprite.png"
                ),
                metadata={"model": "test-image"},
            )
            store.record_candidate(
                run["id"],
                "idle",
                candidate_id="attempt-002",
                source_artifact=(
                    "candidates/idle/attempt-002/source.png"
                ),
                sprite_artifact=(
                    "candidates/idle/attempt-002/sprite.png"
                ),
            )
            selected = store.select_candidate(
                run["id"],
                "idle",
                "attempt-001",
                active_artifact="images/idle.png",
            )

            task = selected["tasks"]["idle"]
            self.assertEqual(len(task["candidates"]), 2)
            self.assertEqual(task["selected_candidate"], "attempt-001")
            self.assertEqual(task["status"], "complete")
            self.assertEqual(task["artifact"], "images/idle.png")
            self.assertEqual(
                task["candidates"][0]["metadata"]["model"], "test-image"
            )

            with self.assertRaises(PetGenerationRunError):
                store.record_candidate(
                    run["id"],
                    "idle",
                    candidate_id="../unsafe",
                    source_artifact="source.png",
                    sprite_artifact="sprite.png",
                )

    def test_selecting_canonical_invalidates_downstream_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="identity-switch",
                request={},
                task_ids=["canonical", "idle", "talking"],
            )
            store.record_candidate(
                run["id"],
                "canonical",
                candidate_id="attempt-001",
                source_artifact=(
                    "candidates/canonical/attempt-001/source.png"
                ),
                sprite_artifact=(
                    "candidates/canonical/attempt-001/sprite.png"
                ),
            )
            for task_id in ("canonical", "idle", "talking"):
                store.update_task(
                    run["id"],
                    task_id,
                    "complete",
                    artifact=f"images/{task_id}.png",
                )
            store.record_review(
                run["id"], "canonical", "accepted"
            )
            store.record_review(
                run["id"], "core_actions", "accepted"
            )
            store.update_stage(
                run["id"],
                "final_review",
                status="needs_review",
                artifacts={
                    "package": "identity-switch.zip",
                    "qa_report": "qa/final/report.json",
                },
            )

            invalidated = store.invalidate_after_candidate_selection(
                run["id"],
                "canonical",
                core_task_ids=("idle", "talking"),
            )

            self.assertEqual(invalidated["stage"], "canonical_review")
            self.assertEqual(invalidated["status"], "needs_review")
            self.assertNotIn("canonical", invalidated["reviews"])
            self.assertNotIn("core_actions", invalidated["reviews"])
            self.assertNotIn("package", invalidated["artifacts"])
            self.assertEqual(
                invalidated["tasks"]["canonical"]["status"], "complete"
            )
            self.assertEqual(
                invalidated["tasks"]["idle"]["status"], "pending"
            )
            self.assertIsNone(
                invalidated["tasks"]["talking"]["artifact"]
            )


if __name__ == "__main__":
    unittest.main()
