"""Persistent task manifests for resumable Pixkin character generation."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from core.generation.state_machine import (
    GenerationStage,
    GenerationStateMachine,
    GenerationTransitionError,
    STAGE_DEFINITIONS,
)
from core.paths import user_data_dir


RUN_SCHEMA_VERSION = 1
WORKFLOW_SCHEMA_VERSION = 2
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
RUN_STATUSES = {
    "pending",
    "running",
    "needs_review",
    "complete",
    "failed",
    "canceled",
}
TASK_STATUSES = {"pending", "running", "complete", "failed", "canceled"}
TASK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CANDIDATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PetGenerationRunError(RuntimeError):
    pass


class PetGenerationRunStore:
    """Owns durable, atomic manifests for Pet Lab generation runs."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else user_data_dir() / "pet-lab" / "runs"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        request: Dict[str, Any],
        task_ids: Iterable[str],
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        run_id = self._validated_run_id(run_id or uuid.uuid4().hex)
        now = _utc_now()
        tasks = {}
        for task_id in task_ids:
            task_id = self._validated_task_id(task_id)
            if task_id in tasks:
                raise PetGenerationRunError("孵化任务 ID 不能为空或重复。")
            tasks[task_id] = {
                "status": "pending",
                "attempts": 0,
                "artifact": None,
                "candidates": [],
                "selected_candidate": None,
                "error": None,
                "updated_at": now,
            }
        workspace = self.workspace(run_id)
        if workspace.exists():
            raise PetGenerationRunError(f"孵化任务已存在：{run_id}")
        workspace.mkdir(parents=True)

        record = {
            "schema_version": RUN_SCHEMA_VERSION,
            "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
            "id": run_id,
            "created_at": now,
            "updated_at": now,
            "status": "pending",
            "stage": "created",
            "request": copy.deepcopy(request),
            "tasks": tasks,
            "artifacts": {},
            "reviews": {},
            "metrics": {"api_calls": []},
            "state_history": [{
                "from": None,
                "to": GenerationStage.CREATED.value,
                "status": "pending",
                "at": now,
                "reason": "created",
            }],
            "error": None,
        }
        self._write(record)
        return copy.deepcopy(record)

    def workspace(self, run_id: str) -> Path:
        return self.root / self._validated_run_id(run_id)

    def artifact_path(self, run_id: str, artifact: str) -> Path:
        relative = self._validated_artifact_path(artifact)
        return self.workspace(run_id).joinpath(*relative.parts)

    def load(self, run_id: str) -> Dict[str, Any]:
        path = self._manifest_path(run_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PetGenerationRunError(f"找不到孵化任务：{run_id}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise PetGenerationRunError(
                f"孵化任务记录无法读取：{run_id}"
            ) from exc
        record = self._migrate_record(record)
        # Early manifests did not contain review decisions. Keep them
        # resumable instead of forcing users to discard generated artwork.
        record.setdefault("reviews", {})
        record.setdefault("metrics", {"api_calls": []})
        record["metrics"].setdefault("api_calls", [])
        record.setdefault("state_history", [])
        for task in record.get("tasks", {}).values():
            if isinstance(task, dict):
                task.setdefault("candidates", [])
                task.setdefault("selected_candidate", None)
        self._validate_record(record, run_id)
        return record

    def list_runs(self) -> List[Dict[str, Any]]:
        records = []
        for path in self.root.glob("*/run.json"):
            try:
                records.append(self.load(path.parent.name))
            except PetGenerationRunError:
                continue
        return sorted(
            records,
            key=lambda item: str(item.get("updated_at", "")),
            reverse=True,
        )

    def update_stage(
        self,
        run_id: str,
        stage: str,
        *,
        status: Optional[str] = None,
        error: Optional[str] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = self.load(run_id)
        if status is not None and status not in RUN_STATUSES:
            raise PetGenerationRunError(f"不支持的孵化状态：{status}")
        now = _utc_now()
        if artifacts:
            record["artifacts"].update(copy.deepcopy(artifacts))
        try:
            GenerationStateMachine.transition(
                record,
                stage,
                status=status,
                at=now,
                reason=reason or "update_stage",
            )
        except GenerationTransitionError as exc:
            raise PetGenerationRunError(str(exc)) from exc
        record["error"] = str(error) if error else None
        record["updated_at"] = now
        self._write(record)
        return copy.deepcopy(record)

    def complete_install(
        self,
        run_id: str,
        installed_package_id: str,
    ) -> Dict[str, Any]:
        """Atomically record final acceptance and the installed package."""
        package_id = str(installed_package_id).strip()
        if not package_id:
            raise PetGenerationRunError("已安装角色 ID 不能为空。")
        record = self.load(run_id)
        now = _utc_now()
        record["reviews"]["final_package"] = {
            "decision": "accepted",
            "note": None,
            "updated_at": now,
        }
        record["artifacts"]["installed_package_id"] = package_id
        try:
            GenerationStateMachine.transition(
                record,
                GenerationStage.INSTALLED,
                status="complete",
                at=now,
                reason="package_installed",
            )
        except GenerationTransitionError as exc:
            raise PetGenerationRunError(str(exc)) from exc
        record["error"] = None
        record["updated_at"] = now
        self._write(record)
        return copy.deepcopy(record)

    def update_request(
        self,
        run_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        record = self.load(run_id)
        record["request"].update(copy.deepcopy(updates))
        record["updated_at"] = _utc_now()
        self._write(record)
        return copy.deepcopy(record)

    def record_review(
        self,
        run_id: str,
        review_id: str,
        decision: str,
        *,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        review_id = str(review_id).strip()
        if not review_id:
            raise PetGenerationRunError("审核 ID 不能为空。")
        if decision not in {"accepted", "rejected", "deferred"}:
            raise PetGenerationRunError(f"不支持的审核决定：{decision}")
        record = self.load(run_id)
        now = _utc_now()
        record["reviews"][review_id] = {
            "decision": decision,
            "note": str(note).strip() if note else None,
            "updated_at": now,
        }
        record["updated_at"] = now
        self._write(record)
        return copy.deepcopy(record)

    def record_api_call(
        self,
        run_id: str,
        task_id: str,
        *,
        duration_ms: int,
        outcome: str,
        error_category: Optional[str] = None,
        retry_number: int = 0,
    ) -> Dict[str, Any]:
        if outcome not in {"success", "failed"}:
            raise PetGenerationRunError("API 调用结果必须是 success 或 failed。")
        record = self.load(run_id)
        if task_id not in record["tasks"]:
            raise PetGenerationRunError(f"找不到动作任务：{task_id}")
        record["metrics"]["api_calls"].append({
            "task_id": str(task_id),
            "recorded_at": _utc_now(),
            "duration_ms": max(0, int(duration_ms)),
            "outcome": outcome,
            "error_category": (
                str(error_category) if error_category else None
            ),
            "retry_number": max(0, int(retry_number)),
        })
        record["updated_at"] = _utc_now()
        self._write(record)
        return copy.deepcopy(record)

    def reset_task(self, run_id: str, task_id: str) -> Dict[str, Any]:
        """Make one generation task retryable while retaining its history."""
        record = self.load(run_id)
        if task_id not in record["tasks"]:
            raise PetGenerationRunError(f"找不到动作任务：{task_id}")
        task = record["tasks"][task_id]
        task["status"] = "pending"
        task["artifact"] = None
        task["error"] = None
        now = _utc_now()
        task["updated_at"] = now
        record["updated_at"] = now
        record["error"] = None
        self._write(record)
        return copy.deepcopy(record)

    def record_candidate(
        self,
        run_id: str,
        task_id: str,
        *,
        candidate_id: str,
        source_artifact: str,
        sprite_artifact: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        record = self.load(run_id)
        if task_id not in record["tasks"]:
            raise PetGenerationRunError(f"找不到动作任务：{task_id}")
        candidate_id = self._validated_candidate_id(candidate_id)
        task = record["tasks"][task_id]
        if any(
            item.get("id") == candidate_id
            for item in task["candidates"]
        ):
            raise PetGenerationRunError(
                f"候选版本已经存在：{candidate_id}"
            )
        now = _utc_now()
        source_artifact = self._validated_artifact_path(
            source_artifact
        ).as_posix()
        sprite_artifact = self._validated_artifact_path(
            sprite_artifact
        ).as_posix()
        task["candidates"].append({
            "id": candidate_id,
            "created_at": now,
            "source_artifact": str(source_artifact),
            "sprite_artifact": str(sprite_artifact),
            "metadata": copy.deepcopy(metadata or {}),
        })
        task["selected_candidate"] = candidate_id
        task["updated_at"] = now
        record["updated_at"] = now
        self._write(record)
        return copy.deepcopy(record)

    def select_candidate(
        self,
        run_id: str,
        task_id: str,
        candidate_id: str,
        *,
        active_artifact: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = self.load(run_id)
        if task_id not in record["tasks"]:
            raise PetGenerationRunError(f"找不到动作任务：{task_id}")
        candidate_id = self._validated_candidate_id(candidate_id)
        task = record["tasks"][task_id]
        candidate = next(
            (
                item for item in task["candidates"]
                if item.get("id") == candidate_id
            ),
            None,
        )
        if candidate is None:
            raise PetGenerationRunError(
                f"找不到候选版本：{candidate_id}"
            )
        now = _utc_now()
        task["selected_candidate"] = candidate_id
        task["status"] = "complete"
        task["error"] = None
        if active_artifact is not None:
            task["artifact"] = self._validated_artifact_path(
                active_artifact
            ).as_posix()
        task["updated_at"] = now
        record["updated_at"] = now
        record["error"] = None
        self._write(record)
        return copy.deepcopy(record)

    def invalidate_after_candidate_selection(
        self,
        run_id: str,
        task_id: str,
        *,
        core_task_ids: Iterable[str] = (),
    ) -> Dict[str, Any]:
        record = self.load(run_id)
        if task_id not in record["tasks"]:
            raise PetGenerationRunError(f"找不到动作任务：{task_id}")
        now = _utc_now()
        record["reviews"].pop("automatic_qa", None)
        record["reviews"].pop("final_package", None)
        for key in (
            "core_contact_sheet",
            "core_qa_report",
            "qa_contact_sheet",
            "qa_report",
            "static_qa_report",
            "animation_qa_report",
            "animation_previews",
            "generation_diagnostic_report",
            "package",
            "installed_package_id",
        ):
            record["artifacts"].pop(key, None)
        if task_id == "canonical":
            record["reviews"].pop("canonical", None)
            record["reviews"].pop("core_actions", None)
            canonical_artifact = record["tasks"]["canonical"].get(
                "artifact"
            )
            if canonical_artifact:
                record["artifacts"]["canonical"] = canonical_artifact
            for other_id, task in record["tasks"].items():
                if other_id == "canonical":
                    continue
                task["status"] = "pending"
                task["artifact"] = None
                task["error"] = None
                task["updated_at"] = now
            target_stage = GenerationStage.CANONICAL_REVIEW
        else:
            if task_id in set(core_task_ids):
                record["reviews"].pop("core_actions", None)
            target_stage = GenerationStage.ACTION_GENERATION
        try:
            GenerationStateMachine.transition(
                record,
                target_stage,
                status=STAGE_DEFINITIONS[target_stage].default_status,
                at=now,
                reason=f"candidate_selected:{task_id}",
            )
        except GenerationTransitionError as exc:
            raise PetGenerationRunError(str(exc)) from exc
        record["error"] = None
        record["updated_at"] = now
        self._write(record)
        return copy.deepcopy(record)

    def update_task(
        self,
        run_id: str,
        task_id: str,
        status: str,
        *,
        artifact: Optional[str] = None,
        error: Optional[str] = None,
        increment_attempt: bool = False,
    ) -> Dict[str, Any]:
        if status not in TASK_STATUSES:
            raise PetGenerationRunError(f"不支持的动作任务状态：{status}")
        record = self.load(run_id)
        if task_id not in record["tasks"]:
            raise PetGenerationRunError(f"找不到动作任务：{task_id}")
        task = record["tasks"][task_id]
        task["status"] = status
        task["artifact"] = (
            self._validated_artifact_path(artifact).as_posix()
            if artifact
            else None
        )
        task["error"] = str(error) if error else None
        if increment_attempt:
            task["attempts"] = int(task.get("attempts", 0)) + 1
        now = _utc_now()
        task["updated_at"] = now
        record["updated_at"] = now
        self._write(record)
        return copy.deepcopy(record)

    def _manifest_path(self, run_id: str) -> Path:
        return self.workspace(run_id) / "run.json"

    @staticmethod
    def _migrate_record(record: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(record, dict):
            raise PetGenerationRunError("孵化任务记录必须是对象。")
        if record.get("schema_version") != RUN_SCHEMA_VERSION:
            raise PetGenerationRunError("不支持的孵化任务记录版本。")
        workflow_version = record.get("workflow_schema_version")
        if workflow_version == WORKFLOW_SCHEMA_VERSION:
            return record
        if workflow_version is not None:
            raise PetGenerationRunError("不支持的孵化工作流版本。")

        migrated = copy.deepcopy(record)
        stage_aliases = {
            "running": GenerationStage.ACTION_GENERATION,
            "ready": GenerationStage.FINAL_REVIEW,
        }
        raw_stage = str(migrated.get("stage") or "")
        try:
            stage = GenerationStateMachine.stage(raw_stage)
        except GenerationTransitionError:
            if migrated.get("status") == "failed":
                stage = GenerationStage.FAILED
            elif migrated.get("status") == "canceled":
                stage = GenerationStage.CANCELED
            else:
                stage = stage_aliases.get(
                    raw_stage,
                    GenerationStage.CREATED,
                )
        if raw_stage in stage_aliases:
            stage = stage_aliases[raw_stage]
        definition = STAGE_DEFINITIONS[stage]
        status = migrated.get("status")
        if status not in definition.statuses:
            status = definition.default_status
        migrated["workflow_schema_version"] = WORKFLOW_SCHEMA_VERSION
        migrated["stage"] = stage.value
        migrated["status"] = status
        migrated["state_history"] = [{
            "from": None,
            "to": stage.value,
            "status": status,
            "at": str(
                migrated.get("updated_at")
                or migrated.get("created_at")
                or _utc_now()
            ),
            "reason": "migrated_from_v1",
        }]
        return migrated

    def _write(self, record: Dict[str, Any]) -> None:
        run_id = self._validated_run_id(str(record.get("id", "")))
        workspace = self.workspace(run_id)
        workspace.mkdir(parents=True, exist_ok=True)
        destination = workspace / "run.json"
        descriptor, temporary = tempfile.mkstemp(
            prefix=".run-",
            suffix=".tmp",
            dir=workspace,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _validated_run_id(run_id: str) -> str:
        value = str(run_id)
        if not RUN_ID_PATTERN.fullmatch(value):
            raise PetGenerationRunError(f"孵化任务 ID 不安全：{value}")
        return value

    @staticmethod
    def _validated_candidate_id(candidate_id: str) -> str:
        value = str(candidate_id)
        if not CANDIDATE_ID_PATTERN.fullmatch(value):
            raise PetGenerationRunError(f"候选版本 ID 不安全：{value}")
        return value

    @staticmethod
    def _validated_task_id(task_id: str) -> str:
        value = str(task_id).strip()
        if not TASK_ID_PATTERN.fullmatch(value):
            raise PetGenerationRunError(f"孵化任务 ID 不安全：{value}")
        return value

    @staticmethod
    def _validated_artifact_path(artifact: str) -> PurePosixPath:
        value = str(artifact).replace("\\", "/")
        path = PurePosixPath(value)
        unsafe_windows_part = any(
            (
                part.split(".", 1)[0].upper()
                in WINDOWS_RESERVED_NAMES
            )
            or part.endswith((" ", "."))
            or any(
                character in WINDOWS_FORBIDDEN_CHARS
                or ord(character) < 32
                for character in part
            )
            for part in path.parts
        )
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
            or unsafe_windows_part
        ):
            raise PetGenerationRunError(
                f"候选产物路径不安全：{artifact}"
            )
        return path

    @staticmethod
    def _validate_record(record: Dict[str, Any], expected_id: str) -> None:
        if not isinstance(record, dict):
            raise PetGenerationRunError("孵化任务记录必须是对象。")
        if record.get("schema_version") != RUN_SCHEMA_VERSION:
            raise PetGenerationRunError("不支持的孵化任务记录版本。")
        if (
            record.get("workflow_schema_version")
            != WORKFLOW_SCHEMA_VERSION
        ):
            raise PetGenerationRunError("不支持的孵化工作流版本。")
        if record.get("id") != expected_id:
            raise PetGenerationRunError("孵化任务 ID 与目录不一致。")
        try:
            GenerationStateMachine.validate_record_state(record)
            GenerationStateMachine.validate_history(record)
        except GenerationTransitionError as exc:
            raise PetGenerationRunError(str(exc)) from exc
        if not isinstance(record.get("tasks"), dict):
            raise PetGenerationRunError("孵化任务列表无效。")
        if not isinstance(record.get("artifacts"), dict):
            raise PetGenerationRunError("孵化产物列表无效。")
        if not isinstance(record.get("reviews"), dict):
            raise PetGenerationRunError("孵化审核记录无效。")
        metrics = record.get("metrics")
        if (
            not isinstance(metrics, dict)
            or not isinstance(metrics.get("api_calls"), list)
        ):
            raise PetGenerationRunError("孵化调用统计无效。")
        for call in metrics["api_calls"]:
            if not isinstance(call, dict):
                raise PetGenerationRunError("孵化调用记录无效。")
            if call.get("outcome") not in {"success", "failed"}:
                raise PetGenerationRunError("孵化调用结果无效。")
        for task_id, task in record["tasks"].items():
            PetGenerationRunStore._validated_task_id(task_id)
            if not isinstance(task, dict):
                raise PetGenerationRunError("孵化任务详情无效。")
            if task.get("status") not in TASK_STATUSES:
                raise PetGenerationRunError("孵化任务详情状态无效。")
            attempts = task.get("attempts")
            if (
                isinstance(attempts, bool)
                or not isinstance(attempts, int)
                or attempts < 0
            ):
                raise PetGenerationRunError("孵化任务尝试次数无效。")
            artifact = task.get("artifact")
            if artifact is not None:
                PetGenerationRunStore._validated_artifact_path(
                    artifact
                )
            if not isinstance(task.get("candidates"), list):
                raise PetGenerationRunError("候选版本列表无效。")
            candidate_ids = set()
            for candidate in task["candidates"]:
                if not isinstance(candidate, dict):
                    raise PetGenerationRunError("候选版本详情无效。")
                candidate_id = (
                    PetGenerationRunStore._validated_candidate_id(
                        candidate.get("id", "")
                    )
                )
                if candidate_id in candidate_ids:
                    raise PetGenerationRunError("候选版本 ID 重复。")
                candidate_ids.add(candidate_id)
                PetGenerationRunStore._validated_artifact_path(
                    candidate.get("source_artifact", "")
                )
                PetGenerationRunStore._validated_artifact_path(
                    candidate.get("sprite_artifact", "")
                )
            selected = task.get("selected_candidate")
            if selected is not None and selected not in candidate_ids:
                raise PetGenerationRunError("当前候选版本不存在。")
