"""Explicit stages and transition rules for resumable Pet Lab runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Tuple


class GenerationStage(str, Enum):
    CREATED = "created"
    CANONICAL_GENERATION = "canonical_generation"
    CANONICAL_REVIEW = "canonical_review"
    ACTION_GENERATION = "action_generation"
    CORE_REVIEW = "core_review"
    QA_REVIEW = "qa_review"
    QA_COMPLETE = "qa_complete"
    ANIMATION_QA_COMPLETE = "animation_qa_complete"
    PACKAGING = "packaging"
    FINAL_REVIEW = "final_review"
    INSTALLED = "installed"
    FAILED = "failed"
    CANCELED = "canceled"


class GenerationTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class StageDefinition:
    label: str
    statuses: FrozenSet[str]
    default_status: str
    next_stages: FrozenSet[GenerationStage]
    required_artifacts: FrozenSet[str] = frozenset()
    produced_artifacts: FrozenSet[str] = frozenset()
    retry_stage: Optional[GenerationStage] = None


_FAILURE_STAGES = frozenset({
    GenerationStage.FAILED,
    GenerationStage.CANCELED,
})


STAGE_DEFINITIONS: Dict[GenerationStage, StageDefinition] = {
    GenerationStage.CREATED: StageDefinition(
        "已创建",
        frozenset({"pending"}),
        "pending",
        frozenset({
            GenerationStage.CANONICAL_GENERATION,
            GenerationStage.ACTION_GENERATION,
            *_FAILURE_STAGES,
        }),
    ),
    GenerationStage.CANONICAL_GENERATION: StageDefinition(
        "身份生成",
        frozenset({"pending", "running"}),
        "pending",
        frozenset({
            GenerationStage.CANONICAL_REVIEW,
            *_FAILURE_STAGES,
        }),
        produced_artifacts=frozenset({"canonical"}),
        retry_stage=GenerationStage.CANONICAL_GENERATION,
    ),
    GenerationStage.CANONICAL_REVIEW: StageDefinition(
        "身份待确认",
        frozenset({"needs_review"}),
        "needs_review",
        frozenset({
            GenerationStage.CANONICAL_GENERATION,
            GenerationStage.ACTION_GENERATION,
            *_FAILURE_STAGES,
        }),
        required_artifacts=frozenset({"canonical"}),
        retry_stage=GenerationStage.CANONICAL_GENERATION,
    ),
    GenerationStage.ACTION_GENERATION: StageDefinition(
        "动作生成",
        frozenset({"pending", "running"}),
        "pending",
        frozenset({
            GenerationStage.CORE_REVIEW,
            GenerationStage.QA_REVIEW,
            GenerationStage.QA_COMPLETE,
            *_FAILURE_STAGES,
        }),
        retry_stage=GenerationStage.ACTION_GENERATION,
    ),
    GenerationStage.CORE_REVIEW: StageDefinition(
        "核心动作待确认",
        frozenset({"needs_review"}),
        "needs_review",
        frozenset({
            GenerationStage.CANONICAL_REVIEW,
            GenerationStage.ACTION_GENERATION,
            *_FAILURE_STAGES,
        }),
        required_artifacts=frozenset({
            "core_contact_sheet",
            "core_qa_report",
        }),
        produced_artifacts=frozenset({
            "core_contact_sheet",
            "core_qa_report",
        }),
        retry_stage=GenerationStage.ACTION_GENERATION,
    ),
    GenerationStage.QA_REVIEW: StageDefinition(
        "QA 待返工",
        frozenset({"needs_review"}),
        "needs_review",
        frozenset({
            GenerationStage.CANONICAL_REVIEW,
            GenerationStage.ACTION_GENERATION,
            *_FAILURE_STAGES,
        }),
        required_artifacts=frozenset({
            "qa_contact_sheet",
            "qa_report",
        }),
        retry_stage=GenerationStage.ACTION_GENERATION,
    ),
    GenerationStage.QA_COMPLETE: StageDefinition(
        "静态 QA 已通过",
        frozenset({"running"}),
        "running",
        frozenset({
            GenerationStage.QA_REVIEW,
            GenerationStage.ANIMATION_QA_COMPLETE,
            *_FAILURE_STAGES,
        }),
        required_artifacts=frozenset({
            "qa_contact_sheet",
            "qa_report",
        }),
        produced_artifacts=frozenset({
            "qa_contact_sheet",
            "qa_report",
        }),
        retry_stage=GenerationStage.ACTION_GENERATION,
    ),
    GenerationStage.ANIMATION_QA_COMPLETE: StageDefinition(
        "动画 QA 已通过",
        frozenset({"running"}),
        "running",
        frozenset({
            GenerationStage.PACKAGING,
            *_FAILURE_STAGES,
        }),
        required_artifacts=frozenset({
            "animation_qa_report",
            "animation_previews",
        }),
        produced_artifacts=frozenset({
            "animation_qa_report",
            "animation_previews",
        }),
        retry_stage=GenerationStage.ACTION_GENERATION,
    ),
    GenerationStage.PACKAGING: StageDefinition(
        "正在打包",
        frozenset({"running"}),
        "running",
        frozenset({
            GenerationStage.FINAL_REVIEW,
            *_FAILURE_STAGES,
        }),
        produced_artifacts=frozenset({"package"}),
        retry_stage=GenerationStage.ACTION_GENERATION,
    ),
    GenerationStage.FINAL_REVIEW: StageDefinition(
        "待安装",
        frozenset({"needs_review"}),
        "needs_review",
        frozenset({
            GenerationStage.CANONICAL_REVIEW,
            GenerationStage.ACTION_GENERATION,
            GenerationStage.INSTALLED,
            *_FAILURE_STAGES,
        }),
        required_artifacts=frozenset({
            "package",
            "qa_report",
            "animation_qa_report",
        }),
        retry_stage=GenerationStage.ACTION_GENERATION,
    ),
    GenerationStage.INSTALLED: StageDefinition(
        "已安装",
        frozenset({"complete"}),
        "complete",
        frozenset(),
        required_artifacts=frozenset({
            "package",
            "installed_package_id",
        }),
    ),
    GenerationStage.FAILED: StageDefinition(
        "失败",
        frozenset({"failed"}),
        "failed",
        frozenset({
            GenerationStage.CANONICAL_GENERATION,
            GenerationStage.CANONICAL_REVIEW,
            GenerationStage.ACTION_GENERATION,
            GenerationStage.CORE_REVIEW,
            GenerationStage.QA_REVIEW,
            GenerationStage.FINAL_REVIEW,
        }),
    ),
    GenerationStage.CANCELED: StageDefinition(
        "已取消",
        frozenset({"canceled"}),
        "canceled",
        frozenset({
            GenerationStage.CANONICAL_GENERATION,
            GenerationStage.CANONICAL_REVIEW,
            GenerationStage.ACTION_GENERATION,
            GenerationStage.CORE_REVIEW,
            GenerationStage.QA_REVIEW,
            GenerationStage.FINAL_REVIEW,
        }),
    ),
}


class GenerationStateMachine:
    @staticmethod
    def stage(value: Any) -> GenerationStage:
        if isinstance(value, GenerationStage):
            return value
        try:
            return GenerationStage(str(value))
        except ValueError as exc:
            raise GenerationTransitionError(
                f"不支持的孵化阶段：{value}"
            ) from exc

    @classmethod
    def label(cls, value: Any) -> str:
        try:
            return STAGE_DEFINITIONS[cls.stage(value)].label
        except GenerationTransitionError:
            return str(value or "未完成")

    @classmethod
    def contract(cls, value: Any) -> StageDefinition:
        return STAGE_DEFINITIONS[cls.stage(value)]

    @classmethod
    def missing_artifacts(
        cls,
        record: Dict[str, Any],
        stage: Any,
    ) -> FrozenSet[str]:
        artifacts = record.get("artifacts")
        available = (
            {
                str(key)
                for key, value in artifacts.items()
                if value
            }
            if isinstance(artifacts, dict)
            else set()
        )
        return frozenset(
            cls.contract(stage).required_artifacts - available
        )

    @classmethod
    def transition(
        cls,
        record: Dict[str, Any],
        target: Any,
        *,
        status: Optional[str] = None,
        at: str,
        reason: Optional[str] = None,
    ) -> None:
        current = cls.stage(record.get("stage"))
        next_stage = cls.stage(target)
        definition = STAGE_DEFINITIONS[next_stage]
        next_status = status or definition.default_status
        if next_status not in definition.statuses:
            raise GenerationTransitionError(
                f"阶段 {next_stage.value} 不允许状态 {next_status}"
            )
        if (
            next_stage != current
            and next_stage
            not in STAGE_DEFINITIONS[current].next_stages
        ):
            raise GenerationTransitionError(
                f"不允许从 {current.value} 跳转到 {next_stage.value}"
            )
        missing = cls.missing_artifacts(record, next_stage)
        if missing:
            raise GenerationTransitionError(
                f"阶段 {next_stage.value} 缺少必需产物："
                + "、".join(sorted(missing))
            )

        previous_status = str(record.get("status"))
        record["stage"] = next_stage.value
        record["status"] = next_status
        if next_stage != current or next_status != previous_status:
            record.setdefault("state_history", []).append({
                "from": current.value,
                "to": next_stage.value,
                "status": next_status,
                "at": at,
                "reason": str(reason) if reason else None,
            })

    @classmethod
    def validate_record_state(cls, record: Dict[str, Any]) -> None:
        stage = cls.stage(record.get("stage"))
        status = record.get("status")
        if status not in STAGE_DEFINITIONS[stage].statuses:
            raise GenerationTransitionError(
                f"阶段 {stage.value} 的状态无效：{status}"
            )
        # Non-terminal records may be partially persisted after a crash and
        # are deliberately accepted so recovery_transition() can rewind them.
        # Terminal records have no recovery path and must remain complete.
        missing = cls.missing_artifacts(record, stage)
        if stage == GenerationStage.INSTALLED and missing:
            raise GenerationTransitionError(
                f"阶段 {stage.value} 缺少必需产物："
                + "、".join(sorted(missing))
            )

    @classmethod
    def validate_history(cls, record: Dict[str, Any]) -> None:
        history = record.get("state_history")
        if not isinstance(history, list) or not history:
            raise GenerationTransitionError("孵化状态历史无效")
        previous_target: Optional[GenerationStage] = None
        previous_status: Optional[str] = None
        for index, entry in enumerate(history):
            if not isinstance(entry, dict):
                raise GenerationTransitionError("孵化状态历史无效")
            source_value = entry.get("from")
            source = (
                cls.stage(source_value)
                if source_value is not None
                else None
            )
            target = cls.stage(entry.get("to"))
            status = entry.get("status")
            if status not in STAGE_DEFINITIONS[target].statuses:
                raise GenerationTransitionError(
                    f"状态历史中 {target.value} 的状态无效：{status}"
                )
            if index == 0:
                if source is not None:
                    raise GenerationTransitionError(
                        "孵化状态历史必须从空来源开始"
                    )
            else:
                if source != previous_target:
                    raise GenerationTransitionError(
                        "孵化状态历史链不连续"
                    )
                if source is None:
                    raise GenerationTransitionError(
                        "孵化状态历史缺少来源阶段"
                    )
                if (
                    source != target
                    and target
                    not in STAGE_DEFINITIONS[source].next_stages
                ):
                    raise GenerationTransitionError(
                        f"状态历史包含非法跳转："
                        f"{source.value} -> {target.value}"
                    )
            previous_target = target
            previous_status = str(status)

        current = cls.stage(record.get("stage"))
        if (
            previous_target != current
            or previous_status != str(record.get("status"))
        ):
            raise GenerationTransitionError(
                "孵化状态历史与当前阶段不一致"
            )

    @classmethod
    def recovery_transition(
        cls,
        record: Dict[str, Any],
    ) -> Optional[Tuple[GenerationStage, str]]:
        current = cls.stage(record.get("stage"))
        if current not in _FAILURE_STAGES:
            return None

        previous = cls._previous_stage(record)
        target: GenerationStage
        if (
            previous is not None
            and previous in {
            GenerationStage.CANONICAL_REVIEW,
            GenerationStage.CORE_REVIEW,
            GenerationStage.QA_REVIEW,
            GenerationStage.FINAL_REVIEW,
            }
            and not cls.missing_artifacts(record, previous)
        ):
            target = previous
        elif previous == GenerationStage.CANONICAL_GENERATION:
            target = GenerationStage.CANONICAL_GENERATION
        elif previous == GenerationStage.CREATED:
            target = GenerationStage.CANONICAL_GENERATION
        else:
            target = cls._infer_recovery_stage(record)
        return target, STAGE_DEFINITIONS[target].default_status

    @classmethod
    def _previous_stage(
        cls,
        record: Dict[str, Any],
    ) -> Optional[GenerationStage]:
        history = record.get("state_history")
        if not isinstance(history, list):
            return None
        for entry in reversed(history):
            if not isinstance(entry, dict):
                continue
            if entry.get("to") not in {
                GenerationStage.FAILED.value,
                GenerationStage.CANCELED.value,
            }:
                continue
            try:
                return cls.stage(entry.get("from"))
            except GenerationTransitionError:
                return None
        return None

    @classmethod
    def _infer_recovery_stage(
        cls,
        record: Dict[str, Any],
    ) -> GenerationStage:
        tasks = record.get("tasks", {})
        canonical = tasks.get("canonical", {}) if isinstance(tasks, dict) else {}
        if canonical.get("status") != "complete":
            return GenerationStage.CANONICAL_GENERATION
        reviews = record.get("reviews", {})
        artifacts = record.get("artifacts", {})
        if (
            isinstance(artifacts, dict)
            and artifacts.get("package")
            and artifacts.get("qa_report")
            and artifacts.get("animation_qa_report")
        ):
            return GenerationStage.FINAL_REVIEW
        if (
            isinstance(reviews, dict)
            and reviews.get("canonical", {}).get("decision")
            != "accepted"
            and isinstance(artifacts, dict)
            and artifacts.get("canonical")
        ):
            return GenerationStage.CANONICAL_REVIEW
        return GenerationStage.ACTION_GENERATION
