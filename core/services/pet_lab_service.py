"""Application service for Pet Lab review, recovery, and candidate workflows."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

from core.pet_generation_run import (
    PetGenerationRunError,
    PetGenerationRunStore,
)


class PetLabCommandKind(str, Enum):
    RESUME = "resume"
    RETRY = "retry"
    DEFER = "defer"


class PetLabContinuationKind(str, Enum):
    CANONICAL_REVIEW = "canonical_review"
    CORE_REVIEW = "core_review"
    QA_REVIEW = "qa_review"
    FINAL_REVIEW = "final_review"
    RESUME = "resume"


@dataclass(frozen=True)
class PetLabCommand:
    kind: PetLabCommandKind
    run_id: str
    retry_task_id: Optional[str] = None


@dataclass(frozen=True)
class PetLabContinuation:
    kind: PetLabContinuationKind
    run_id: str
    artifacts: dict[str, Path]


@dataclass(frozen=True)
class PetLabTaskTool:
    task_id: str
    status: str
    candidate_count: int

    @property
    def retryable(self) -> bool:
        return self.status in {"failed", "canceled"}

    @property
    def can_choose_candidate(self) -> bool:
        return self.candidate_count > 1

    @property
    def label(self) -> str:
        details = []
        if self.retryable:
            details.append(self.status)
        if self.can_choose_candidate:
            details.append(f"{self.candidate_count} 个候选")
        return f"{self.task_id} · {' · '.join(details)}"


@dataclass(frozen=True)
class PetLabCandidate:
    candidate_id: str
    sprite_path: Path
    selected: bool
    label: str


@dataclass(frozen=True)
class PetLabCandidateSelection:
    run_id: str
    task_id: str
    candidate_id: str
    active_path: Path
    canonical_actions_invalidated: bool


class PetLabService:
    """Own non-visual Pet Lab decisions and durable workflow mutations."""

    def __init__(self, run_store: PetGenerationRunStore):
        self.run_store = run_store

    def apply_canonical_review(
        self,
        run_id: str,
        decision: str,
    ) -> PetLabCommand:
        if decision == "accepted":
            self.run_store.record_review(
                run_id,
                "canonical",
                "accepted",
            )
            self.run_store.update_stage(
                run_id,
                "action_generation",
                status="pending",
            )
            return PetLabCommand(PetLabCommandKind.RESUME, run_id)
        if decision == "rejected":
            self.run_store.record_review(
                run_id,
                "canonical",
                "rejected",
            )
            self.run_store.reset_task(run_id, "canonical")
            self.run_store.update_stage(
                run_id,
                "canonical_generation",
                status="pending",
            )
            return PetLabCommand(
                PetLabCommandKind.RETRY,
                run_id,
                retry_task_id="canonical",
            )
        if decision != "deferred":
            raise PetGenerationRunError(
                f"不支持的身份审核决定：{decision}"
            )
        self.run_store.record_review(
            run_id,
            "canonical",
            "deferred",
        )
        return PetLabCommand(PetLabCommandKind.DEFER, run_id)

    def apply_core_review(
        self,
        run_id: str,
        decision: str,
        task_id: Optional[str] = None,
    ) -> PetLabCommand:
        if decision == "accepted":
            self.run_store.record_review(
                run_id,
                "core_actions",
                "accepted",
            )
            self.run_store.update_stage(
                run_id,
                "action_generation",
                status="pending",
            )
            return PetLabCommand(PetLabCommandKind.RESUME, run_id)
        if decision == "retry":
            retry_task_id = self._required_retry_task(task_id)
            self.run_store.record_review(
                run_id,
                "core_actions",
                "rejected",
                note=f"retry:{retry_task_id}",
            )
            self.run_store.reset_task(run_id, retry_task_id)
            self.run_store.update_stage(
                run_id,
                "action_generation",
                status="pending",
            )
            return PetLabCommand(
                PetLabCommandKind.RETRY,
                run_id,
                retry_task_id=retry_task_id,
            )
        if decision != "deferred":
            raise PetGenerationRunError(
                f"不支持的核心动作审核决定：{decision}"
            )
        self.run_store.record_review(
            run_id,
            "core_actions",
            "deferred",
        )
        return PetLabCommand(PetLabCommandKind.DEFER, run_id)

    def apply_qa_review(
        self,
        run_id: str,
        decision: str,
        task_id: Optional[str] = None,
    ) -> PetLabCommand:
        if decision == "retry":
            retry_task_id = self._required_retry_task(task_id)
            self.run_store.record_review(
                run_id,
                "automatic_qa",
                "rejected",
                note=f"retry:{retry_task_id}",
            )
            self.run_store.reset_task(run_id, retry_task_id)
            self.run_store.update_stage(
                run_id,
                "action_generation",
                status="pending",
            )
            return PetLabCommand(
                PetLabCommandKind.RETRY,
                run_id,
                retry_task_id=retry_task_id,
            )
        if decision != "deferred":
            raise PetGenerationRunError(
                f"不支持的自动 QA 决定：{decision}"
            )
        self.run_store.record_review(
            run_id,
            "automatic_qa",
            "deferred",
        )
        return PetLabCommand(PetLabCommandKind.DEFER, run_id)

    def continuation(self, run_id: str) -> PetLabContinuation:
        record = self.run_store.load(run_id)
        stage = record.get("stage")
        artifacts = record.get("artifacts", {})
        rules = (
            (
                "canonical_review",
                PetLabContinuationKind.CANONICAL_REVIEW,
                {"canonical": "canonical"},
            ),
            (
                "core_review",
                PetLabContinuationKind.CORE_REVIEW,
                {
                    "sheet": "core_contact_sheet",
                    "report": "core_qa_report",
                },
            ),
            (
                "qa_review",
                PetLabContinuationKind.QA_REVIEW,
                {
                    "sheet": "qa_contact_sheet",
                    "report": "qa_report",
                },
            ),
            (
                "final_review",
                PetLabContinuationKind.FINAL_REVIEW,
                {
                    "package": "package",
                    "qa_report": "qa_report",
                    "animation_qa_report": "animation_qa_report",
                },
            ),
        )
        for expected_stage, kind, required in rules:
            if stage != expected_stage:
                continue
            resolved = {
                name: Path(str(artifacts.get(artifact_id, "")))
                for name, artifact_id in required.items()
            }
            if all(path.is_file() for path in resolved.values()):
                return PetLabContinuation(kind, run_id, resolved)
            break
        return PetLabContinuation(
            PetLabContinuationKind.RESUME,
            run_id,
            {},
        )

    def task_tools(self, run_id: str) -> list[PetLabTaskTool]:
        tasks = self.run_store.load(run_id)["tasks"]
        return [
            PetLabTaskTool(
                task_id=task_id,
                status=str(task.get("status", "")),
                candidate_count=len(task.get("candidates", [])),
            )
            for task_id, task in tasks.items()
            if (
                task.get("status") in {"failed", "canceled"}
                or len(task.get("candidates", [])) > 1
            )
        ]

    def animation_previews(
        self,
        run_id: Optional[str],
    ) -> dict[str, Path]:
        if not run_id:
            return {}
        try:
            record = self.run_store.load(run_id)
        except PetGenerationRunError:
            return {}
        values = record["artifacts"].get("animation_previews", {})
        if not isinstance(values, dict):
            return {}
        return {
            str(state): Path(str(path))
            for state, path in values.items()
            if Path(str(path)).is_file()
        }

    def candidate_count(self, run_id: Optional[str], task_id: str) -> int:
        if not run_id:
            return 0
        try:
            task = self.run_store.load(run_id)["tasks"][task_id]
        except (KeyError, PetGenerationRunError):
            return 0
        return len(task.get("candidates", []))

    def tasks_with_candidates(
        self,
        run_id: Optional[str],
        task_ids: Iterable[str],
    ) -> list[str]:
        if not run_id:
            return []
        try:
            tasks = self.run_store.load(run_id)["tasks"]
        except PetGenerationRunError:
            return []
        return [
            task_id
            for task_id in task_ids
            if (
                task_id in tasks
                and len(tasks[task_id].get("candidates", [])) > 1
            )
        ]

    def candidate_options(
        self,
        run_id: str,
        task_id: str,
    ) -> list[PetLabCandidate]:
        record = self.run_store.load(run_id)
        try:
            task = record["tasks"][task_id]
        except KeyError as exc:
            raise PetGenerationRunError(
                f"找不到动作任务：{task_id}"
            ) from exc
        selected_id = task.get("selected_candidate")
        return [
            PetLabCandidate(
                candidate_id=str(candidate["id"]),
                sprite_path=self.run_store.artifact_path(
                    run_id,
                    str(candidate["sprite_artifact"]),
                ),
                selected=candidate["id"] == selected_id,
                label=(
                    f"版本 {index} · {candidate['id']}"
                    + (" · 当前" if candidate["id"] == selected_id else "")
                ),
            )
            for index, candidate in enumerate(
                task.get("candidates", []),
                start=1,
            )
        ]

    def canonical_switch_invalidates_actions(
        self,
        run_id: str,
        task_id: str,
    ) -> bool:
        if task_id != "canonical":
            return False
        record = self.run_store.load(run_id)
        return any(
            other_id != "canonical" and task["status"] == "complete"
            for other_id, task in record["tasks"].items()
        )

    def select_candidate(
        self,
        run_id: str,
        task_id: str,
        candidate_id: str,
    ) -> PetLabCandidateSelection:
        record = self.run_store.load(run_id)
        try:
            task = record["tasks"][task_id]
        except KeyError as exc:
            raise PetGenerationRunError(
                f"找不到动作任务：{task_id}"
            ) from exc
        if candidate_id == task.get("selected_candidate"):
            raise PetGenerationRunError("所选候选已经是当前版本。")
        candidate = next(
            (
                item
                for item in task.get("candidates", [])
                if item.get("id") == candidate_id
            ),
            None,
        )
        if candidate is None:
            raise PetGenerationRunError(
                f"找不到候选版本：{candidate_id}"
            )
        source = self.run_store.artifact_path(
            run_id,
            str(candidate["sprite_artifact"]),
        )
        if not source.is_file():
            raise PetGenerationRunError(
                f"候选版本文件不存在：{source}"
            )
        active_artifact = (
            "images/canonical.png"
            if task_id == "canonical"
            else f"images/{task_id}.png"
        )
        target = self.run_store.artifact_path(run_id, active_artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.run_store.select_candidate(
            run_id,
            task_id,
            candidate_id,
            active_artifact=active_artifact,
        )
        core_task_ids = ["idle", "talking", "dragging", "alerting"]
        if record.get("request", {}).get("mode") == "full":
            core_task_ids.append("run_right")
        self.run_store.invalidate_after_candidate_selection(
            run_id,
            task_id,
            core_task_ids=core_task_ids,
        )
        if (
            task_id != "canonical"
            and record.get("request", {}).get(
                "allow_horizontal_mirror",
                False,
            )
        ):
            for dependent in self._mirror_dependents(task_id):
                if dependent in record["tasks"]:
                    self.run_store.reset_task(run_id, dependent)
        return PetLabCandidateSelection(
            run_id=run_id,
            task_id=task_id,
            candidate_id=candidate_id,
            active_path=target,
            canonical_actions_invalidated=(
                task_id == "canonical"
                and any(
                    other_id != "canonical"
                    and task["status"] == "complete"
                    for other_id, task in record["tasks"].items()
                )
            ),
        )

    @staticmethod
    def qa_retry_candidates(report: dict[str, Any]) -> list[str]:
        candidates = []
        for issue in report.get("errors", []):
            states = issue.get("states") or [issue.get("state")]
            for state in states:
                if (
                    state
                    and state != "canonical"
                    and state not in candidates
                ):
                    candidates.append(str(state))
        for state in report.get("expected_states", []):
            if state not in candidates:
                candidates.append(str(state))
        return candidates

    @staticmethod
    def _required_retry_task(task_id: Optional[str]) -> str:
        if not task_id:
            raise PetGenerationRunError("返工决定缺少动作任务。")
        return task_id

    @staticmethod
    def _mirror_dependents(source_state: str) -> tuple[str, ...]:
        if source_state == "walk_right":
            return ("walk_left",)
        if source_state == "run_right":
            return ("run_left",)
        if (
            source_state.startswith("edge_")
            and source_state.endswith("_right")
        ):
            return (source_state.removesuffix("_right") + "_left",)
        return ()
