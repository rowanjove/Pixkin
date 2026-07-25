"""Health summaries and durable diagnostics for Pet Lab generation runs."""

from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


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
ISSUE_REPORT_KEYS = (
    "qa_report",
    "static_qa_report",
    "animation_qa_report",
)
SENSITIVE_KEYS = {
    "api_key",
    "pet_name",
    "personality",
    "style_notes",
    "reference_paths",
}
ISSUE_BUNDLE_SCHEMA_VERSION = 2
ISSUE_BUNDLE_REQUIRED_FILES = {
    "manifest.json",
    "diagnostic.json",
    "run-summary.json",
}
ISSUE_BUNDLE_ALLOWED_FILES = ISSUE_BUNDLE_REQUIRED_FILES | {
    f"reports/{key}.json" for key in ISSUE_REPORT_KEYS
}
ISSUE_BUNDLE_MAX_FILE_BYTES = 5 * 1024 * 1024
ISSUE_BUNDLE_MAX_TOTAL_BYTES = 20 * 1024 * 1024
ANONYMOUS_RUN_ID_PATTERN = re.compile(r"^anonymous-[0-9a-f]{12}$")
SECRET_TEXT_PATTERN = re.compile(
    r"(?i)(?:\b(?:sk|key)-[a-z0-9_-]{8,}\b|"
    r"\bBearer\s+(?!<redacted>)[a-z0-9._-]{8,})"
)


class IssueBundleError(ValueError):
    """Raised when an issue bundle fails the read-only safety inspection."""


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

    @classmethod
    def technical_summary(cls, report):
        api = report["api"]
        tasks = report["tasks"]
        preflight = report.get("preflight") or {}
        errors = report.get("errors") or {}
        categories = errors.get("categories") or {}
        budget = api.get("budget")
        usage = (
            f"{api['used']}/{budget}"
            if budget is not None
            else str(api["used"])
        )
        lines = [
            "Pixkin 伙伴工坊技术摘要",
            f"健康状态：{report['health']}",
            f"流程阶段：{report['stage']}",
            f"任务完成：{tasks['complete']}/{tasks['total']}",
            f"API 调用：{usage}",
            f"剩余真实调用：{api['remaining_calls']}",
            (
                "预计剩余："
                + (
                    cls.format_duration(
                        api.get("estimated_remaining_ms")
                    )
                    or "尚无数据"
                )
            ),
            (
                "能力预检："
                + str(preflight.get("status", "尚未执行"))
            ),
            (
                "成功/失败调用："
                f"{api['successful_calls']}/{api['failed_calls']}"
            ),
        ]
        if categories:
            lines.append(
                "错误分类："
                + "、".join(
                    f"{name} × {count}"
                    for name, count in sorted(categories.items())
                )
            )
        return "\n".join(lines)

    @classmethod
    def build_issue_bundle(
        cls,
        record,
        *,
        workspace: Path,
        destination: Path,
    ):
        workspace = Path(workspace).resolve()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        anonymous_id = sha256(
            str(record.get("id", "")).encode("utf-8")
        ).hexdigest()[:12]
        request = record.get("request") or {}
        redactions = []
        for key in SENSITIVE_KEYS:
            value = request.get(key)
            if isinstance(value, (list, tuple)):
                redactions.extend(str(item) for item in value if item)
            elif value:
                redactions.append(str(value))
        diagnostic = cls._sanitize_value(
            cls.summarize(record),
            workspace=workspace,
            redactions=redactions,
        )
        diagnostic["run_id"] = f"anonymous-{anonymous_id}"
        diagnostic["errors"]["latest"] = (
            "<error-recorded>"
            if diagnostic["errors"].get("latest")
            else None
        )
        diagnostic["errors"]["failed_tasks"] = {
            task_id: "<error-recorded>"
            for task_id, error in diagnostic["errors"][
                "failed_tasks"
            ].items()
            if error
        }
        run_summary = {
            "schema_version": 1,
            "anonymous_run_id": f"anonymous-{anonymous_id}",
            "status": str(record.get("status", "pending")),
            "stage": str(record.get("stage", "created")),
            "request": {
                key: request.get(key)
                for key in (
                    "mode",
                    "image_model",
                    "image_quality",
                    "allow_horizontal_mirror",
                    "planned_api_calls",
                    "max_api_calls",
                    "capability_preflight",
                )
                if key in request
            },
            "tasks": {
                task_id: {
                    "status": task.get("status"),
                    "attempts": task.get("attempts", 0),
                    "candidate_count": len(
                        task.get("candidates", [])
                    ),
                    "has_error": bool(task.get("error")),
                }
                for task_id, task in (record.get("tasks") or {}).items()
                if isinstance(task, dict)
            },
            "metrics": record.get("metrics") or {"api_calls": []},
        }
        run_summary = cls._sanitize_value(
            run_summary,
            workspace=workspace,
            redactions=redactions,
        )
        documents = {
            "diagnostic.json": diagnostic,
            "run-summary.json": run_summary,
        }
        artifacts = record.get("artifacts") or {}
        included_reports = []
        for key in ISSUE_REPORT_KEYS:
            source = cls._safe_json_artifact(
                artifacts.get(key),
                workspace=workspace,
            )
            if source is None:
                continue
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = f"reports/{key}.json"
            documents[name] = cls._sanitize_value(
                cls._qa_report_excerpt(payload),
                workspace=workspace,
                redactions=redactions,
            )
            included_reports.append(name)
        encoded_documents = {
            name: json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            for name, payload in documents.items()
        }
        manifest = {
            "schema_version": ISSUE_BUNDLE_SCHEMA_VERSION,
            "created_at": _utc_now(),
            "anonymous_run_id": f"anonymous-{anonymous_id}",
            "privacy": {
                "images_included": False,
                "reference_images_included": False,
                "api_keys_included": False,
                "freeform_character_fields_included": False,
            },
            "contents": sorted([
                "manifest.json",
                *documents,
            ]),
            "included_reports": included_reports,
            "files": {
                name: {
                    "sha256": sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }
                for name, payload in sorted(encoded_documents.items())
            },
        }
        encoded_documents["manifest.json"] = json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".pixkin-issue-",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary_path = Path(temporary)
        try:
            with zipfile.ZipFile(
                temporary_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                for name, payload in sorted(encoded_documents.items()):
                    archive.writestr(name, payload)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return {
            "path": str(destination),
            "anonymous_run_id": f"anonymous-{anonymous_id}",
            "contents": manifest["contents"],
        }

    @classmethod
    def inspect_issue_bundle(cls, source: Path):
        """Validate an issue bundle without extracting or executing its files."""
        source = Path(source)
        if not source.is_file():
            raise IssueBundleError("问题包不存在或不是文件。")
        try:
            archive = zipfile.ZipFile(source, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise IssueBundleError("文件不是有效的 Pixkin 问题包。") from exc
        with archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise IssueBundleError("问题包包含重复文件名。")
            actual_names = set(names)
            missing = ISSUE_BUNDLE_REQUIRED_FILES - actual_names
            unsupported = actual_names - ISSUE_BUNDLE_ALLOWED_FILES
            if missing:
                raise IssueBundleError(
                    "问题包缺少必需文件："
                    + "、".join(sorted(missing))
                )
            if unsupported:
                raise IssueBundleError(
                    "问题包包含不受支持的文件："
                    + "、".join(sorted(unsupported))
                )
            total_bytes = 0
            encoded = {}
            for info in infos:
                cls._validate_zip_member(info)
                total_bytes += info.file_size
                if total_bytes > ISSUE_BUNDLE_MAX_TOTAL_BYTES:
                    raise IssueBundleError("问题包解压后体积超过安全上限。")
                try:
                    encoded[info.filename] = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise IssueBundleError(
                        f"无法安全读取问题包文件：{info.filename}"
                    ) from exc

        documents = {}
        for name, payload in encoded.items():
            try:
                documents[name] = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IssueBundleError(
                    f"问题包文件不是有效的 UTF-8 JSON：{name}"
                ) from exc
        manifest = documents["manifest.json"]
        if not isinstance(manifest, dict):
            raise IssueBundleError("问题包清单格式无效。")
        if manifest.get("schema_version") != ISSUE_BUNDLE_SCHEMA_VERSION:
            raise IssueBundleError("问题包版本不受支持。")
        anonymous_id = str(manifest.get("anonymous_run_id", ""))
        if not ANONYMOUS_RUN_ID_PATTERN.fullmatch(anonymous_id):
            raise IssueBundleError("问题包匿名编号格式无效。")
        contents = manifest.get("contents")
        if (
            not isinstance(contents, list)
            or not all(isinstance(name, str) for name in contents)
            or len(contents) != len(set(contents))
            or set(contents) != set(encoded)
        ):
            raise IssueBundleError("问题包清单与实际文件不一致。")
        expected_reports = {
            name for name in encoded if name.startswith("reports/")
        }
        included_reports = manifest.get("included_reports")
        if (
            not isinstance(included_reports, list)
            or not all(
                isinstance(name, str) for name in included_reports
            )
            or len(included_reports) != len(set(included_reports))
            or set(included_reports) != expected_reports
        ):
            raise IssueBundleError("问题包 QA 报告清单不一致。")
        expected_privacy = {
            "images_included": False,
            "reference_images_included": False,
            "api_keys_included": False,
            "freeform_character_fields_included": False,
        }
        if manifest.get("privacy") != expected_privacy:
            raise IssueBundleError("问题包隐私声明缺失或无效。")
        file_records = manifest.get("files")
        expected_hashed_names = set(encoded) - {"manifest.json"}
        if (
            not isinstance(file_records, dict)
            or set(file_records) != expected_hashed_names
        ):
            raise IssueBundleError("问题包哈希清单不完整。")
        for name in sorted(expected_hashed_names):
            record = file_records.get(name)
            if not isinstance(record, dict):
                raise IssueBundleError(f"问题包文件记录无效：{name}")
            if record.get("size_bytes") != len(encoded[name]):
                raise IssueBundleError(f"问题包文件大小校验失败：{name}")
            if record.get("sha256") != sha256(encoded[name]).hexdigest():
                raise IssueBundleError(f"问题包文件哈希校验失败：{name}")
        diagnostic = documents["diagnostic.json"]
        run_summary = documents["run-summary.json"]
        if not isinstance(diagnostic, dict) or not isinstance(run_summary, dict):
            raise IssueBundleError("问题包诊断内容格式无效。")
        if (
            diagnostic.get("run_id") != anonymous_id
            or run_summary.get("anonymous_run_id") != anonymous_id
        ):
            raise IssueBundleError("问题包内的匿名编号不一致。")
        cls._validate_private_content(documents)
        return {
            "valid": True,
            "anonymous_run_id": anonymous_id,
            "contents": sorted(encoded),
            "manifest": manifest,
            "diagnostic": diagnostic,
            "run_summary": run_summary,
            "reports": {
                name: payload
                for name, payload in documents.items()
                if name.startswith("reports/")
            },
        }

    @classmethod
    def github_issue_markdown(cls, report, *, bundle_filename=None):
        """Build a privacy-safe, copy-ready GitHub issue description."""
        attachment = (
            f"`{Path(bundle_filename).name}`"
            if bundle_filename
            else "由“导出问题包”生成的匿名 ZIP"
        )
        return "\n".join([
            "## 问题概述",
            "",
            "<!-- 请用一句话描述遇到的问题 -->",
            "",
            "## 复现步骤",
            "",
            "1. ",
            "2. ",
            "3. ",
            "",
            "## 期望结果",
            "",
            "<!-- 你原本希望发生什么？ -->",
            "",
            "## 实际结果",
            "",
            "<!-- 实际发生了什么？不要粘贴 API 密钥或私人图片。 -->",
            "",
            "## 自动诊断",
            "",
            "```text",
            cls.technical_summary(report),
            "```",
            "",
            "## 附件",
            "",
            f"- {attachment}",
            "- [ ] 我已确认附件不包含原始图片、参考图、API 密钥和角色自由文本。",
        ])

    @staticmethod
    def _validate_zip_member(info):
        name = info.filename
        path = Path(name.replace("/", os.sep))
        if (
            not name
            or "\\" in name
            or name.startswith("/")
            or path.is_absolute()
            or ".." in path.parts
            or info.is_dir()
        ):
            raise IssueBundleError(f"问题包包含不安全路径：{name}")
        if info.flag_bits & 0x1:
            raise IssueBundleError(f"问题包包含加密文件：{name}")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == 0o120000:
            raise IssueBundleError(f"问题包包含符号链接：{name}")
        if info.file_size > ISSUE_BUNDLE_MAX_FILE_BYTES:
            raise IssueBundleError(f"问题包文件超过安全上限：{name}")
        if info.compress_size and info.file_size / info.compress_size > 200:
            raise IssueBundleError(f"问题包文件压缩比异常：{name}")

    @classmethod
    def _validate_private_content(cls, value, *, path="root"):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in SENSITIVE_KEYS:
                    raise IssueBundleError(
                        f"问题包包含受保护字段：{path}.{key}"
                    )
                cls._validate_private_content(
                    item,
                    path=f"{path}.{key}",
                )
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                cls._validate_private_content(
                    item,
                    path=f"{path}[{index}]",
                )
            return
        if isinstance(value, str) and SECRET_TEXT_PATTERN.search(value):
            raise IssueBundleError(
                f"问题包可能包含未脱敏凭据：{path}"
            )

    @staticmethod
    def _qa_report_excerpt(payload):
        if not isinstance(payload, dict):
            return {}
        excerpt = {
            key: payload[key]
            for key in (
                "schema_version",
                "generated_at",
                "passed",
                "expected_states",
                "summary",
                "checks",
            )
            if key in payload
        }
        for issue_type in ("errors", "warnings"):
            issues = payload.get(issue_type)
            if not isinstance(issues, list):
                continue
            excerpt[issue_type] = [
                {
                    key: issue[key]
                    for key in ("code", "state", "states")
                    if key in issue
                }
                for issue in issues
                if isinstance(issue, dict)
            ]
        return excerpt

    @classmethod
    def _sanitize_value(
        cls,
        value,
        *,
        workspace: Path,
        redactions=(),
    ):
        if isinstance(value, dict):
            return {
                str(key): cls._sanitize_value(
                    item,
                    workspace=workspace,
                    redactions=redactions,
                )
                for key, item in value.items()
                if str(key).lower() not in SENSITIVE_KEYS
            }
        if isinstance(value, list):
            return [
                cls._sanitize_value(
                    item,
                    workspace=workspace,
                    redactions=redactions,
                )
                for item in value
            ]
        if isinstance(value, tuple):
            return [
                cls._sanitize_value(
                    item,
                    workspace=workspace,
                    redactions=redactions,
                )
                for item in value
            ]
        if isinstance(value, str):
            return cls._sanitize_text(
                value,
                workspace=workspace,
                redactions=redactions,
            )
        return value

    @staticmethod
    def _sanitize_text(
        value: str,
        *,
        workspace: Path,
        redactions=(),
    ):
        text = str(value)
        for private_value in sorted(
            {str(item) for item in redactions if item},
            key=len,
            reverse=True,
        ):
            text = text.replace(private_value, "<redacted>")
        workspace_text = str(workspace)
        for variant in {
            workspace_text,
            workspace_text.replace("\\", "/"),
            workspace_text.replace("/", "\\"),
        }:
            if variant:
                text = text.replace(variant, "<run-workspace>")
        text = re.sub(
            r"(?i)\b(?:sk|key)-[a-z0-9_-]{8,}\b",
            "<redacted-api-key>",
            text,
        )
        text = re.sub(
            r"(?i)\bBearer\s+[a-z0-9._-]{8,}",
            "Bearer <redacted>",
            text,
        )

        def sanitize_url(match):
            try:
                parsed = urlsplit(match.group(0))
                hostname = parsed.hostname or ""
                parsed_port = parsed.port
            except ValueError:
                return "<redacted-url>"
            port = f":{parsed_port}" if parsed_port else ""
            return urlunsplit((
                parsed.scheme,
                hostname + port,
                "",
                "",
                "",
            ))

        text = re.sub(
            r"https?://[^\s\"'<>]+",
            sanitize_url,
            text,
        )
        text = re.sub(
            r"(?i)\b[A-Z]:\\(?:[^\\\r\n]+\\)+[^\\\r\n]*",
            "<local-path>",
            text,
        )
        text = re.sub(
            r"\\\\[^\\\s]+\\[^\\\s]+(?:\\[^\\\r\n]+)*",
            "<network-path>",
            text,
        )
        text = re.sub(
            r"(?<![\w])/(?:[^/\s]+/)+[^/\s]+",
            "<local-path>",
            text,
        )
        return text

    @staticmethod
    def _safe_json_artifact(value, *, workspace: Path):
        if not value:
            return None
        path = Path(str(value))
        if not path.is_absolute():
            path = workspace / path
        try:
            resolved = path.resolve()
            resolved.relative_to(workspace)
        except (OSError, ValueError):
            return None
        if (
            resolved.suffix.lower() != ".json"
            or not resolved.is_file()
            or resolved.stat().st_size > 5 * 1024 * 1024
        ):
            return None
        return resolved

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
