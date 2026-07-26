import tempfile
import unittest
from pathlib import Path

from core.pet_generation_run import (
    PetGenerationRunError,
    PetGenerationRunStore,
)
from core.services.pet_lab_service import (
    PetLabCommandKind,
    PetLabContinuationKind,
    PetLabService,
)


class PetLabServiceTests(unittest.TestCase):
    def _store_and_service(self, base: Path):
        store = PetGenerationRunStore(base / "runs")
        return store, PetLabService(store)

    @staticmethod
    def _write_artifact(
        store: PetGenerationRunStore,
        run_id: str,
        artifact: str,
        content: bytes = b"fixture",
    ) -> Path:
        path = store.artifact_path(run_id, artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _canonical_review_run(
        self,
        store: PetGenerationRunStore,
        run_id: str,
    ):
        run = store.create(
            run_id=run_id,
            request={"mode": "basic"},
            task_ids=["canonical", "idle"],
        )
        canonical = self._write_artifact(
            store,
            run_id,
            "images/canonical.png",
        )
        store.update_stage(
            run_id,
            "canonical_generation",
            status="running",
        )
        store.update_stage(
            run_id,
            "canonical_review",
            status="needs_review",
            artifacts={"canonical": str(canonical)},
        )
        return run

    def test_canonical_review_decisions_return_explicit_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, service = self._store_and_service(base)
            self._canonical_review_run(store, "canonical-accept")

            accepted = service.apply_canonical_review(
                "canonical-accept",
                "accepted",
            )

            record = store.load("canonical-accept")
            self.assertEqual(accepted.kind, PetLabCommandKind.RESUME)
            self.assertEqual(record["stage"], "action_generation")
            self.assertEqual(
                record["reviews"]["canonical"]["decision"],
                "accepted",
            )

            self._canonical_review_run(store, "canonical-retry")
            retried = service.apply_canonical_review(
                "canonical-retry",
                "rejected",
            )

            record = store.load("canonical-retry")
            self.assertEqual(retried.kind, PetLabCommandKind.RETRY)
            self.assertEqual(retried.retry_task_id, "canonical")
            self.assertEqual(record["stage"], "canonical_generation")
            self.assertEqual(
                record["tasks"]["canonical"]["status"],
                "pending",
            )

            self._canonical_review_run(store, "canonical-defer")
            deferred = service.apply_canonical_review(
                "canonical-defer",
                "deferred",
            )
            self.assertEqual(deferred.kind, PetLabCommandKind.DEFER)
            self.assertEqual(
                store.load("canonical-defer")["stage"],
                "canonical_review",
            )

    def test_action_review_retry_resets_only_selected_task(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, service = self._store_and_service(base)
            run = store.create(
                run_id="core-retry",
                request={"mode": "basic"},
                task_ids=["idle", "talking"],
            )
            store.update_stage(
                run["id"],
                "action_generation",
                status="running",
            )
            store.update_task(
                run["id"],
                "idle",
                "failed",
                error="bad pose",
            )
            sheet = self._write_artifact(
                store,
                run["id"],
                "qa/core-contact.png",
            )
            report = self._write_artifact(
                store,
                run["id"],
                "qa/core.json",
            )
            store.update_stage(
                run["id"],
                "core_review",
                status="needs_review",
                artifacts={
                    "core_contact_sheet": str(sheet),
                    "core_qa_report": str(report),
                },
            )

            command = service.apply_core_review(
                run["id"],
                "retry",
                "idle",
            )

            record = store.load(run["id"])
            self.assertEqual(command.kind, PetLabCommandKind.RETRY)
            self.assertEqual(command.retry_task_id, "idle")
            self.assertEqual(record["stage"], "action_generation")
            self.assertEqual(record["tasks"]["idle"]["status"], "pending")
            self.assertEqual(
                record["reviews"]["core_actions"]["note"],
                "retry:idle",
            )
            with self.assertRaisesRegex(
                PetGenerationRunError,
                "缺少动作任务",
            ):
                service.apply_qa_review(
                    run["id"],
                    "retry",
                )

    def test_continuation_routes_review_and_missing_artifacts_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, service = self._store_and_service(base)
            run = store.create(
                run_id="final-review",
                request={},
                task_ids=["idle"],
            )
            package = self._write_artifact(
                store,
                run["id"],
                "output/pet.zip",
            )
            qa_report = self._write_artifact(
                store,
                run["id"],
                "qa/static.json",
            )
            animation_report = self._write_artifact(
                store,
                run["id"],
                "qa/animation.json",
            )
            store.update_stage(
                run["id"],
                "action_generation",
                status="running",
            )
            store.update_stage(
                run["id"],
                "qa_complete",
                status="running",
                artifacts={
                    "qa_contact_sheet": str(qa_report),
                    "qa_report": str(qa_report),
                },
            )
            store.update_stage(
                run["id"],
                "animation_qa_complete",
                status="running",
                artifacts={
                    "animation_qa_report": str(animation_report),
                    "animation_previews": {
                        "idle": str(animation_report)
                    },
                },
            )
            store.update_stage(
                run["id"],
                "packaging",
                status="running",
            )
            store.update_stage(
                run["id"],
                "final_review",
                status="needs_review",
                artifacts={"package": str(package)},
            )

            action = service.continuation(run["id"])

            self.assertEqual(
                action.kind,
                PetLabContinuationKind.FINAL_REVIEW,
            )
            self.assertEqual(action.artifacts["package"], package)
            animation_report.unlink()
            fallback = service.continuation(run["id"])
            self.assertEqual(
                fallback.kind,
                PetLabContinuationKind.RESUME,
            )

    def test_candidate_selection_invalidates_mirrored_dependent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, service = self._store_and_service(base)
            run = store.create(
                run_id="candidate-switch",
                request={
                    "mode": "full",
                    "allow_horizontal_mirror": True,
                },
                task_ids=["run_right", "run_left"],
            )
            for index, content in enumerate((b"first", b"second"), start=1):
                candidate_id = f"attempt-{index:03d}"
                artifact = (
                    f"candidates/run_right/{candidate_id}/sprite.png"
                )
                self._write_artifact(
                    store,
                    run["id"],
                    artifact,
                    content,
                )
                store.record_candidate(
                    run["id"],
                    "run_right",
                    candidate_id=candidate_id,
                    source_artifact=artifact,
                    sprite_artifact=artifact,
                )
            store.select_candidate(
                run["id"],
                "run_right",
                "attempt-002",
                active_artifact="images/run_right.png",
            )
            store.update_task(
                run["id"],
                "run_left",
                "complete",
                artifact="images/run_left.png",
            )

            tools = service.task_tools(run["id"])
            options = service.candidate_options(
                run["id"],
                "run_right",
            )
            result = service.select_candidate(
                run["id"],
                "run_right",
                "attempt-001",
            )

            self.assertEqual(len(tools), 1)
            self.assertTrue(tools[0].can_choose_candidate)
            self.assertEqual(
                [option.selected for option in options],
                [False, True],
            )
            self.assertEqual(
                result.active_path.read_bytes(),
                b"first",
            )
            record = store.load(run["id"])
            self.assertEqual(
                record["tasks"]["run_right"]["selected_candidate"],
                "attempt-001",
            )
            self.assertEqual(
                record["tasks"]["run_left"]["status"],
                "pending",
            )

    def test_qa_retry_candidates_prioritize_reported_errors(self):
        report = {
            "errors": [
                {"states": ["talking", "idle"]},
                {"state": "alerting"},
                {"state": "canonical"},
            ],
            "expected_states": [
                "idle",
                "talking",
                "dragging",
                "alerting",
            ],
        }
        self.assertEqual(
            PetLabService.qa_retry_candidates(report),
            ["talking", "idle", "alerting", "dragging"],
        )
