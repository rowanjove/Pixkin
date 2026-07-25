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


if __name__ == "__main__":
    unittest.main()
