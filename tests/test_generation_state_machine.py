import json
import tempfile
import unittest
from pathlib import Path

from core.generation.state_machine import (
    GenerationStage,
    GenerationStateMachine,
)
from core.pet_generation_run import (
    RUN_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION,
    PetGenerationRunError,
    PetGenerationRunStore,
)


class GenerationStateMachineTests(unittest.TestCase):
    def test_complete_workflow_records_explicit_transition_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="workflow-run",
                request={},
                task_ids=["canonical", "idle"],
            )
            transitions = (
                ("canonical_generation", "running", None),
                (
                    "canonical_review",
                    "needs_review",
                    {"canonical": "images/canonical.png"},
                ),
                ("action_generation", "running", None),
                (
                    "core_review",
                    "needs_review",
                    {
                        "core_contact_sheet": "core.png",
                        "core_qa_report": "core.json",
                    },
                ),
                ("action_generation", "pending", None),
                (
                    "qa_complete",
                    "running",
                    {
                        "qa_contact_sheet": "qa.png",
                        "qa_report": "qa.json",
                    },
                ),
                (
                    "animation_qa_complete",
                    "running",
                    {
                        "animation_qa_report": "animation.json",
                        "animation_previews": {"idle": "idle.gif"},
                    },
                ),
                ("packaging", "running", None),
                (
                    "final_review",
                    "needs_review",
                    {"package": "pet.zip"},
                ),
                (
                    "installed",
                    "complete",
                    {"installed_package_id": "pet"},
                ),
            )

            for stage, status, artifacts in transitions:
                run = store.update_stage(
                    run["id"],
                    stage,
                    status=status,
                    artifacts=artifacts,
                    reason=f"test:{stage}",
                )

            self.assertEqual(run["schema_version"], RUN_SCHEMA_VERSION)
            self.assertEqual(
                run["workflow_schema_version"],
                WORKFLOW_SCHEMA_VERSION,
            )
            self.assertEqual(run["stage"], "installed")
            self.assertEqual(run["status"], "complete")
            self.assertEqual(
                [item["to"] for item in run["state_history"]],
                [
                    "created",
                    *(stage for stage, _status, _artifacts in transitions),
                ],
            )
            self.assertEqual(
                run["state_history"][-1]["reason"],
                "test:installed",
            )

    def test_illegal_skip_and_mismatched_status_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="illegal-transition",
                request={},
                task_ids=["canonical"],
            )

            with self.assertRaisesRegex(
                PetGenerationRunError,
                "不允许从 created 跳转到 installed",
            ):
                store.update_stage(
                    run["id"], "installed", status="complete"
                )
            with self.assertRaisesRegex(
                PetGenerationRunError,
                "不允许状态 complete",
            ):
                store.update_stage(
                    run["id"],
                    "canonical_generation",
                    status="complete",
                )

    def test_failure_and_cancel_recover_to_declared_path(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="recovery-run",
                request={},
                task_ids=["canonical", "idle"],
            )
            store.update_task(
                run["id"],
                "canonical",
                "complete",
                artifact="images/canonical.png",
            )
            store.record_review(
                run["id"], "canonical", "accepted"
            )
            store.update_stage(
                run["id"], "action_generation", status="running"
            )
            failed = store.update_stage(
                run["id"], "failed", status="failed"
            )

            target = GenerationStateMachine.recovery_transition(failed)

            self.assertEqual(
                target,
                (GenerationStage.ACTION_GENERATION, "pending"),
            )
            resumed = store.update_stage(
                run["id"],
                target[0].value,
                status=target[1],
                reason="resume_after_interruption",
            )
            canceled = store.update_stage(
                run["id"], "canceled", status="canceled"
            )
            self.assertEqual(
                GenerationStateMachine.recovery_transition(canceled),
                (GenerationStage.ACTION_GENERATION, "pending"),
            )
            self.assertEqual(resumed["stage"], "action_generation")

    def test_stage_contract_declares_artifacts_and_retry_path(self):
        final_review = GenerationStateMachine.contract("final_review")
        core_review = GenerationStateMachine.contract("core_review")

        self.assertEqual(
            final_review.required_artifacts,
            frozenset({
                "package",
                "qa_report",
                "animation_qa_report",
            }),
        )
        self.assertEqual(
            final_review.retry_stage,
            GenerationStage.ACTION_GENERATION,
        )
        self.assertEqual(
            core_review.produced_artifacts,
            frozenset({
                "core_contact_sheet",
                "core_qa_report",
            }),
        )
        self.assertEqual(
            GenerationStateMachine.missing_artifacts(
                {"artifacts": {"package": "pet.zip"}},
                "final_review",
            ),
            frozenset({"qa_report", "animation_qa_report"}),
        )

    def test_legacy_ready_workflow_migrates_without_losing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="legacy-ready",
                request={},
                task_ids=["canonical"],
            )
            manifest = store.workspace(run["id"]) / "run.json"
            legacy = json.loads(manifest.read_text(encoding="utf-8"))
            legacy.pop("workflow_schema_version")
            legacy["stage"] = "ready"
            legacy["status"] = "complete"
            legacy["artifacts"]["package"] = "legacy.zip"
            legacy.pop("state_history")
            manifest.write_text(
                json.dumps(legacy, ensure_ascii=False),
                encoding="utf-8",
            )

            migrated = store.load(run["id"])

            self.assertEqual(
                migrated["schema_version"], RUN_SCHEMA_VERSION
            )
            self.assertEqual(
                migrated["workflow_schema_version"],
                WORKFLOW_SCHEMA_VERSION,
            )
            self.assertEqual(migrated["stage"], "final_review")
            self.assertEqual(migrated["status"], "needs_review")
            self.assertEqual(
                migrated["artifacts"]["package"], "legacy.zip"
            )
            self.assertEqual(
                migrated["state_history"][0]["reason"],
                "migrated_from_v1",
            )
            installed = store.update_stage(
                run["id"],
                "installed",
                status="complete",
                artifacts={"installed_package_id": "legacy"},
            )
            self.assertEqual(
                installed["workflow_schema_version"], 2
            )

    def test_transition_rejects_missing_required_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="missing-artifacts",
                request={},
                task_ids=["canonical"],
            )
            store.update_stage(
                run["id"],
                "canonical_generation",
                status="running",
            )

            with self.assertRaisesRegex(
                PetGenerationRunError,
                "缺少必需产物.*canonical",
            ):
                store.update_stage(
                    run["id"],
                    "canonical_review",
                    status="needs_review",
                )

    def test_installed_stage_is_terminal(self):
        record = {
            "stage": "installed",
            "status": "complete",
            "state_history": [],
        }
        with self.assertRaisesRegex(ValueError, "不允许从 installed"):
            GenerationStateMachine.transition(
                record,
                "action_generation",
                status="pending",
                at="2026-07-26T00:00:00+00:00",
            )

    def test_tampered_or_discontinuous_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory))
            run = store.create(
                run_id="history-tamper",
                request={},
                task_ids=["canonical"],
            )
            run = store.update_stage(
                run["id"],
                "canonical_generation",
                status="running",
            )
            manifest = store.workspace(run["id"]) / "run.json"
            record = json.loads(manifest.read_text(encoding="utf-8"))
            record["state_history"][-1]["from"] = "qa_review"
            manifest.write_text(
                json.dumps(record, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                PetGenerationRunError,
                "状态历史链不连续",
            ):
                store.load(run["id"])


if __name__ == "__main__":
    unittest.main()
