"""Health summaries and durable diagnostics for Pet Lab generation runs."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


MIRROR_TARGETS = {
    "walk_left",
    "run_left",
    "edge_enter_left",
    "edge_idle_left",
    "edge_hover_left",
    "edge_exit_left",
}
REVIEW_STAGES = {
    "canonical_review",
    "core_review",
    "qa_review",
    "final_review",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PetGenerationDiagnostics:
    """Calculate honest progress, ETA, and failure information."""

    @classmethod
    def summarize(cls, record):
        tasks = record.get("tasks") or {}
        request = record.get("request") or {}
        calls = (record.get("metrics") or {}).get("api_calls") or []
        statuses = Counter(
            str(task.get("status", "pending"))
            for task in tasks.values()
            if isinstance(task, dict)
        )
        completed = statuses["complete"]
        total = len(tasks)
        allow_mirror = bool(
            request.get("allow_horizontal_mirror", False)
        )
        remaining_task_ids = [
            task_id
            for task_id, task in tasks.items()
            if (
                isinstance(task, dict)
                and task.get("status") != "complete"
            )
        ]
        remaining_api_calls = sum(
            not (allow_mirror and task_id in MIRROR_TARGETS)
            for task_id in remaining_task_ids
        )
        used_api_calls = sum(
            max(0, int(task.get("attempts", 0)))
            for task in tasks.values()
            if isinstance(task, dict)
        )
        budget = request.get("max_api_calls")
        budget_remaining = (
            max(0, int(budget) - used_api_calls)
            if budget is not None
            else None
        )
        valid_calls = [
            call for call in calls if isinstance(call, dict)
        ]
        successful_calls = sum(
            call.get("outcome") == "success"
            for call in valid_calls
        )
        failed_calls = sum(
            call.get("outcome") == "failed"
            for call in valid_calls
        )
        total_duration_ms = sum(
            max(0, int(call.get("duration_ms", 0)))
            for call in valid_calls
        )
        average_success_cycle_ms = (
            round(total_duration_ms / successful_calls)
            if successful_calls
            else 0
        )
        eta_ms = (
            average_success_cycle_ms * remaining_api_calls
            if successful_calls and remaining_api_calls
            else (0 if not remaining_api_calls else None)
        )
        error_categories = Counter(
            str(call.get("error_category"))
            for call in valid_calls
            if (
                call.get("outcome") == "failed"
                and call.get("error_category")
            )
        )
        mirrored_tasks = sum(
            any(
                (candidate.get("metadata") or {}).get("operation")
                == "horizontal_mirror"
                for candidate in task.get("candidates", [])
                if isinstance(candidate, dict)
            )
            for task in tasks.values()
            if isinstance(task, dict)
        )
        stage = str(record.get("stage", "created"))
        waiting_for_review = stage in REVIEW_STAGES
        artifacts = record.get("artifacts") or {}
        if (
            budget_remaining is not None
            and budget_remaining < remaining_api_calls
        ):
            health = "blocked"
        elif record.get("status") == "failed" or statuses["failed"]:
            health = "critical"
        elif waiting_for_review:
            health = "review"
        elif failed_calls and failed_calls / max(1, len(valid_calls)) >= 0.25:
            health = "warning"
        elif record.get("status") == "running":
            health = "running"
        else:
            health = "healthy"
        return {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "run_id": str(record.get("id", "")),
            "health": health,
            "status": str(record.get("status", "pending")),
            "stage": stage,
            "waiting_for_review": waiting_for_review,
            "tasks": {
                "total": total,
                "complete": completed,
                "pending": statuses["pending"],
                "running": statuses["running"],
                "failed": statuses["failed"],
                "canceled": statuses["canceled"],
                "remaining_ids": remaining_task_ids,
                "mirrored": mirrored_tasks,
            },
            "api": {
                "used": used_api_calls,
                "budget": int(budget) if budget is not None else None,
                "budget_remaining": budget_remaining,
                "remaining_calls": remaining_api_calls,
                "successful_calls": successful_calls,
                "failed_calls": failed_calls,
                "total_duration_ms": total_duration_ms,
                "average_success_cycle_ms": average_success_cycle_ms,
                "estimated_remaining_ms": eta_ms,
            },
            "preflight": dict(
                request.get("capability_preflight") or {}
            ),
            "errors": {
                "categories": dict(sorted(error_categories.items())),
                "latest": record.get("error"),
                "failed_tasks": {
                    task_id: task.get("error")
                    for task_id, task in tasks.items()
                    if (
                        isinstance(task, dict)
                        and task.get("status") == "failed"
                    )
                },
            },
            "related_reports": {
                key: artifacts[key]
                for key in (
                    "qa_report",
                    "static_qa_report",
                    "animation_qa_report",
                    "qa_contact_sheet",
                )
                if key in artifacts
            },
        }

    @classmethod
    def write(cls, record, destination: Path):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        report = cls.summarize(record)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".generation-diagnostic-",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return report

    @classmethod
    def compact_text(cls, report):
        health_labels = {
            "healthy": "健康",
            "running": "生成中",
            "review": "等待审核",
            "warning": "需要关注",
            "critical": "生成失败",
            "blocked": "预算不足",
        }
        tasks = report["tasks"]
        api = report["api"]
        budget = api["budget"]
        usage = (
            f"{api['used']}/{budget}"
            if budget is not None
            else str(api["used"])
        )
        eta = cls.format_duration(api["estimated_remaining_ms"])
        eta_copy = (
            "批准后约 " + eta
            if report["waiting_for_review"] and eta
            else ("预计剩余 " + eta if eta else "尚无 ETA")
        )
        return (
            f"{health_labels.get(report['health'], report['health'])}"
            f" · 任务 {tasks['complete']}/{tasks['total']}"
            f" · API {usage}"
            f" · {eta_copy}"
        )

    @staticmethod
    def format_duration(milliseconds):
        if milliseconds is None:
            return None
        seconds = max(0, round(int(milliseconds) / 1000))
        if seconds < 60:
            return f"{seconds} 秒"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes} 分 {seconds} 秒"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小时 {minutes} 分"
