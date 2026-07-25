import base64
import hashlib
import io
import re
import shutil
import time
import uuid
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml
from openai import OpenAI
from PIL import Image, ImageOps
from PyQt6.QtCore import QThread, pyqtSignal

from core.pet_animation_builder import LOOP_STATES, PetAnimationBuilder
from core.pet_animation_qa import PetAnimationQa
from core.pet_generation_diagnostics import PetGenerationDiagnostics
from core.pet_generation_run import PetGenerationRunStore
from core.pet_generation_qa import PetGenerationQa
from core.version import VERSION


POSES = {
    "idle": "neutral friendly standing pose, calm open eyes",
    "blink": "same neutral pose, both eyes gently closed in a cute blink",
    "stretch": "cheerful full-body stretch with tiny paws raised",
    "wave": "one tiny paw raised in a friendly wave; no motion marks",
    "nod": "small polite nod with head slightly lowered",
    "sleep": "peaceful curled sleeping pose; no floating symbols",
    "dragging": "surprised but cute pose with paws tucked in",
    "talking": "engaged speaking expression with a small open mouth",
    "alerting": "excited celebratory hop pose; no detached effects",
}

STANDARD_POSES = {
    **POSES,
    "look_around": (
        "curious neutral standing pose, eyes looking gently to one side"
    ),
    "wake": "freshly awake standing pose with a small alert stretch",
    "listening": (
        "attentive listening pose leaning forward slightly, mouth closed"
    ),
    "thinking": (
        "thoughtful pose with one tiny paw near the chin; no symbols"
    ),
    "working": (
        "focused ready-to-help pose, attentive eyes; no desk or props"
    ),
    "waiting": (
        "patient relaxed waiting pose with a calm neutral expression"
    ),
    "success": (
        "proud happy success pose with tiny paws raised; no effects"
    ),
    "failed": (
        "gentle apologetic disappointed pose, still cute and reassuring"
    ),
    "touch": (
        "delighted reaction to a friendly head pat, eyes softly closed"
    ),
    "happy": "bright joyful pose with a warm smile; no detached effects",
}

FULL_ONLY_POSES = {
    "annoyed": (
        "mildly annoyed but still friendly pose with a tiny pout"
    ),
    "walk_right": (
        "clear side-view walking pose facing right, one foot stepping forward"
    ),
    "walk_left": (
        "clear side-view walking pose facing left, one foot stepping forward"
    ),
    "run_right": (
        "energetic side-view running pose facing right with compact limbs"
    ),
    "run_left": (
        "energetic side-view running pose facing left with compact limbs"
    ),
    "jump": "upward jumping pose with both feet visibly off the ground",
    "land": "soft landing pose with bent knees and a stable low stance",
    "alerting_important": (
        "urgent attention pose with both paws raised; no symbols or effects"
    ),
    "celebrate_live": (
        "joyful livestream celebration pose with raised paws; no props"
    ),
}

EDGE_SIDES = ("left", "right", "top", "bottom")
EDGE_GENERATION_SIDES = ("right", "left", "top", "bottom")
EDGE_PHASES = ("enter", "idle", "hover", "exit")
_EDGE_SIDE_PROMPTS = {
    "left": "toward the left screen edge while looking inward to the right",
    "right": "toward the right screen edge while looking inward to the left",
    "top": "toward the top screen edge while looking inward and downward",
    "bottom": "toward the bottom screen edge while looking inward and upward",
}
_EDGE_PHASE_PROMPTS = {
    "enter": "moving into a playful screen-edge hiding position",
    "idle": "calmly peeking from a screen edge with the face clearly visible",
    "hover": "leaning farther inward with a curious welcoming expression",
    "exit": "pulling away from the screen edge back toward the desktop",
}
EDGE_POSES = {
    f"edge_{phase}_{side}": (
        f"{_EDGE_PHASE_PROMPTS[phase]}, {_EDGE_SIDE_PROMPTS[side]}; "
        "keep the complete body inside the image"
    )
    for phase in EDGE_PHASES
    for side in EDGE_GENERATION_SIDES
}
FULL_POSES = {
    **STANDARD_POSES,
    **FULL_ONLY_POSES,
    **EDGE_POSES,
}

BASIC_POSE_IDS = ("idle", "talking", "dragging", "alerting")
STANDARD_POSE_IDS = tuple(STANDARD_POSES)
FULL_POSE_IDS = tuple(FULL_POSES)
CORE_REVIEW_POSE_IDS = BASIC_POSE_IDS
GENERATION_MODES = {"basic", "legacy_full", "standard", "full"}
MAX_TRANSIENT_RETRIES = 2
RETRY_DELAYS_SECONDS = (1.0, 2.0)
HORIZONTAL_MIRROR_SOURCES = {
    "walk_left": "walk_right",
    "run_left": "run_right",
    **{
        f"edge_{phase}_left": f"edge_{phase}_right"
        for phase in EDGE_PHASES
    },
}

HARD_CARTOON_RULES = """
NON-NEGOTIABLE OUTPUT RULES — these override every reference and style note:
Create a fictional CARTOON DIGITAL PET, never a real person or realistic animal.
If a reference is a photograph, simplify it into an original mascot and do not
preserve a photoreal face, skin, fur, anatomy, lighting, or exact human likeness.
Use compact chibi proportions, chunky silhouette, thick dark pixel-adjacent
outline, visible stepped edges, limited palette, flat cel shading, simple face,
and tiny limbs. No photography, realism, 3D render, painterly art, anime key art,
soft gradients, detailed hair or fur, skin texture, scenery, text, watermark,
speech bubble, cast shadow, floor shadow, glow, or detached visual effects.
Show exactly one complete full-body pet, centered with generous safe padding.
Background must be perfectly flat solid #00ff00, uniform edge-to-edge with no
variation. Never use #00ff00 in the pet.
"""


class PetGenerationWorker(QThread):
    """从至少一张参考图生成强制卡通化的 Pixkin 角色包。"""

    progress_changed = pyqtSignal(int, str)
    run_created = pyqtSignal(str)
    review_ready = pyqtSignal(str, str)
    core_review_ready = pyqtSignal(str, str, str)
    qa_review_ready = pyqtSignal(str, str, str)
    package_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        quality: str,
        pet_name: str,
        personality: str,
        style_notes: str,
        reference_paths,
        full_hatch: bool = True,
        generation_mode: str = None,
        allow_horizontal_mirror: bool = False,
        max_api_calls: int = None,
        run_id: str = None,
        run_store: PetGenerationRunStore = None,
        retry_task_id: str = None,
    ):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.quality = quality
        self.pet_name = pet_name.strip() or "My Pixkin"
        self.personality = personality.strip()
        self.style_notes = style_notes.strip()[:800]
        self.reference_paths = [Path(path) for path in reference_paths][:4]
        self.generation_mode = (
            str(generation_mode)
            if generation_mode is not None
            else ("legacy_full" if full_hatch else "basic")
        )
        if self.generation_mode not in GENERATION_MODES:
            raise ValueError(
                f"不支持的伙伴工坊生成模式：{self.generation_mode}"
            )
        self.full_hatch = self.generation_mode != "basic"
        self.allow_horizontal_mirror = bool(
            allow_horizontal_mirror
            and self.generation_mode == "full"
        )
        if max_api_calls is None:
            self.max_api_calls = (
                None
                if run_id is not None
                else self.planned_api_calls(
                    self.generation_mode,
                    self.allow_horizontal_mirror,
                ) + 3
            )
        else:
            self.max_api_calls = int(max_api_calls)
            if self.max_api_calls < 1:
                raise ValueError("图像 API 调用预算必须至少为 1。")
        self._client = None
        self._preflight_complete = False
        self.run_id = run_id
        self._run_store = run_store
        self.retry_task_id = retry_task_id
        self._active_task_id = None

    @classmethod
    def resume_from(
        cls,
        *,
        run_id: str,
        api_key: str,
        run_store: PetGenerationRunStore = None,
        retry_task_id: str = None,
    ):
        store = run_store or PetGenerationRunStore()
        record = store.load(run_id)
        request = record["request"]
        stored_mode = str(request.get("mode", "draft"))
        if stored_mode == "full":
            task_ids = set(record.get("tasks") or {})
            generation_mode = (
                "full"
                if set(FULL_POSE_IDS).issubset(task_ids)
                else "legacy_full"
            )
        else:
            generation_mode = {
                "draft": "basic",
            }.get(stored_mode, stored_mode)
        return cls(
            api_key=api_key,
            base_url=str(request.get("image_base_url", "")),
            model=str(request.get("image_model", "gpt-image-2")),
            quality=str(request.get("image_quality", "medium")),
            pet_name=str(request.get("pet_name", "My Pixkin")),
            personality=str(request.get("personality", "")),
            style_notes=str(request.get("style_notes", "")),
            reference_paths=request.get("reference_paths", []),
            full_hatch=generation_mode != "basic",
            generation_mode=generation_mode,
            allow_horizontal_mirror=bool(
                request.get("allow_horizontal_mirror", False)
            ),
            max_api_calls=request.get("max_api_calls"),
            run_id=run_id,
            run_store=store,
            retry_task_id=retry_task_id,
        )

    def cancel(self):
        self.requestInterruption()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

    @staticmethod
    def planned_api_calls(
        generation_mode: str,
        allow_horizontal_mirror: bool = False,
    ) -> int:
        pose_counts = {
            "basic": len(BASIC_POSE_IDS),
            "legacy_full": len(POSES),
            "standard": len(STANDARD_POSE_IDS),
            "full": len(FULL_POSE_IDS),
        }
        count = pose_counts.get(str(generation_mode))
        if count is None:
            raise ValueError(
                f"不支持的伙伴工坊生成模式：{generation_mode}"
            )
        if generation_mode == "full" and allow_horizontal_mirror:
            count -= len(HORIZONTAL_MIRROR_SOURCES)
        return 1 + count

    @staticmethod
    def api_calls_used(record) -> int:
        return sum(
            max(0, int(task.get("attempts", 0)))
            for task in (record.get("tasks") or {}).values()
            if isinstance(task, dict)
        )

    @staticmethod
    def api_timing_summary(record):
        calls = (
            (record.get("metrics") or {}).get("api_calls") or []
        )
        durations = [
            max(0, int(call.get("duration_ms", 0)))
            for call in calls
            if isinstance(call, dict)
        ]
        total_ms = sum(durations)
        return {
            "count": len(durations),
            "total_ms": total_ms,
            "average_ms": (
                round(total_ms / len(durations))
                if durations
                else 0
            ),
            "successful": sum(
                call.get("outcome") == "success"
                for call in calls
                if isinstance(call, dict)
            ),
            "failed": sum(
                call.get("outcome") == "failed"
                for call in calls
                if isinstance(call, dict)
            ),
        }

    @staticmethod
    def classify_generation_error(exc):
        status = getattr(exc, "status_code", None)
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        name = type(exc).__name__.lower()
        if status == 401 or "authentication" in name:
            category, retriable = "authentication", False
        elif status == 403 or "permission" in name:
            category, retriable = "permission", False
        elif status in {400, 413, 415, 422} or "badrequest" in name:
            category, retriable = "invalid_request", False
        elif status == 404 or "notfound" in name:
            category, retriable = "not_found", False
        elif status == 429 or "ratelimit" in name:
            category, retriable = "rate_limit", True
        elif status in {408, 504} or "timeout" in name:
            category, retriable = "timeout", True
        elif status is not None and status >= 500:
            category, retriable = "server", True
        elif (
            "没有返回可用图片" in str(exc)
            or "图片数据损坏" in str(exc)
        ):
            category, retriable = "invalid_response", True
        elif (
            "connection" in name
            or isinstance(exc, (ConnectionError, TimeoutError))
        ):
            category, retriable = "connection", True
        else:
            category, retriable = "unknown", False
        labels = {
            "authentication": "认证失败",
            "permission": "接口无权限",
            "invalid_request": "请求参数不受支持",
            "not_found": "接口或模型不存在",
            "rate_limit": "接口限流",
            "timeout": "接口超时",
            "server": "接口服务异常",
            "invalid_response": "接口图片响应无效",
            "connection": "网络连接失败",
            "unknown": "未知错误",
        }
        return {
            "category": category,
            "retriable": retriable,
            "label": labels[category],
            "status_code": status,
        }

    @staticmethod
    def mirror_dependents(source_state: str):
        return tuple(
            target
            for target, source in HORIZONTAL_MIRROR_SOURCES.items()
            if source == source_state
        )

    def run(self):
        try:
            self._run_store = self._run_store or PetGenerationRunStore()
            pose_items = self._pose_items()
            slug = self._slug()
            if self.run_id is None:
                self._validate_generation_inputs()
                self.run_id = f"{slug}-{uuid.uuid4().hex[:8]}"
                self._run_store.create(
                    run_id=self.run_id,
                    request={
                        "pet_name": self.pet_name,
                        "personality": self.personality,
                        "style_notes": self.style_notes,
                        "reference_paths": [
                            str(path) for path in self.reference_paths
                        ],
                        "mode": self.generation_mode,
                        "allow_horizontal_mirror":
                            self.allow_horizontal_mirror,
                        "planned_api_calls": self.planned_api_calls(
                            self.generation_mode,
                            self.allow_horizontal_mirror,
                        ),
                        "max_api_calls": self.max_api_calls,
                        "image_base_url": self.base_url,
                        "image_model": self.model,
                        "image_quality": self.quality,
                    },
                    task_ids=[
                        "canonical", *(state for state, _ in pose_items)
                    ],
                )
                self.run_created.emit(self.run_id)
                self._snapshot_references()
            record = self._run_store.load(self.run_id)
            work = self._run_store.workspace(self.run_id)
            images_dir = work / "images"
            images_dir.mkdir(parents=True, exist_ok=True)

            canonical = images_dir / "canonical.png"
            canonical_complete = (
                record["tasks"]["canonical"]["status"] == "complete"
                and canonical.is_file()
            )
            if self.retry_task_id == "canonical":
                self._ensure_legacy_candidate("canonical", canonical)
                self._run_store.reset_task(self.run_id, "canonical")
                canonical_complete = False
            if not canonical_complete:
                self._validate_generation_inputs()
                client = self._generation_client()
                self.progress_changed.emit(2, "正在读取风格参考…")
                self._run_store.update_stage(
                    self.run_id, "canonical_generation", status="running"
                )
                self._active_task_id = "canonical"
                prompt = self._base_prompt()
                base_bytes = self._generate_with_retry(
                    task_id="canonical",
                    client=client,
                    paths=self.reference_paths,
                    prompt=prompt,
                )
                self._store_candidate(
                    task_id="canonical",
                    raw=base_bytes,
                    active_target=canonical,
                    prompt=prompt,
                )
                self._active_task_id = None
                if self.isInterruptionRequested():
                    self._mark_canceled()
                    return
                self._run_store.update_stage(
                    self.run_id,
                    "canonical_review",
                    status="needs_review",
                    artifacts={"canonical": str(canonical)},
                )
                self.progress_changed.emit(
                    10, "身份稿已生成，请确认角色身份后继续。"
                )
                self.review_ready.emit(self.run_id, str(canonical))
                return

            record = self._run_store.load(self.run_id)
            canonical_review = record["reviews"].get("canonical", {})
            if canonical_review.get("decision") != "accepted":
                self._run_store.update_stage(
                    self.run_id,
                    "canonical_review",
                    status="needs_review",
                    artifacts={"canonical": str(canonical)},
                )
                self.progress_changed.emit(
                    10, "身份稿等待确认；尚未生成动作。"
                )
                self.review_ready.emit(self.run_id, str(canonical))
                return

            if record.get("stage") == "final_review":
                package = Path(str(record["artifacts"].get("package", "")))
                qa_report = Path(str(
                    record["artifacts"].get("qa_report", "")
                ))
                animation_qa_report = Path(str(
                    record["artifacts"].get(
                        "animation_qa_report", ""
                    )
                ))
                if (
                    package.is_file()
                    and qa_report.is_file()
                    and animation_qa_report.is_file()
                ):
                    self.progress_changed.emit(
                        100, "角色包等待最终预览与安装确认。"
                    )
                    self.package_ready.emit(str(package))
                    return
            if record.get("stage") == "core_review":
                core_review = record["reviews"].get("core_actions", {})
                sheet = Path(str(
                    record["artifacts"].get("core_contact_sheet", "")
                ))
                report = Path(str(
                    record["artifacts"].get("core_qa_report", "")
                ))
                if (
                    core_review.get("decision") != "accepted"
                    and sheet.is_file()
                    and report.is_file()
                ):
                    self.progress_changed.emit(
                        55, "核心动作等待一致性审核。"
                    )
                    self.core_review_ready.emit(
                        self.run_id, str(sheet), str(report)
                    )
                    return
            if record.get("stage") == "qa_review":
                sheet = Path(str(
                    record["artifacts"].get("qa_contact_sheet", "")
                ))
                report = Path(str(
                    record["artifacts"].get("qa_report", "")
                ))
                if sheet.is_file() and report.is_file():
                    self.progress_changed.emit(
                        94, "自动 QA 发现问题，请选择动作返工。"
                    )
                    self.qa_review_ready.emit(
                        self.run_id, str(sheet), str(report)
                    )
                    return

            self._run_store.update_stage(
                self.run_id, "action_generation", status="running"
            )
            generated = self._completed_images(images_dir, pose_items)
            review_pose_ids = set(CORE_REVIEW_POSE_IDS)
            if self.generation_mode == "full":
                review_pose_ids.add("run_right")
            core_items = [
                item for item in pose_items
                if item[0] in review_pose_ids
            ]
            remaining_items = [
                item for item in pose_items
                if item[0] not in CORE_REVIEW_POSE_IDS
            ]
            self._generate_pose_group(
                canonical=canonical,
                images_dir=images_dir,
                pose_items=core_items,
                generated=generated,
                all_pose_items=pose_items,
            )
            if self.isInterruptionRequested():
                self._mark_canceled()
                return
            incomplete_core = self._incomplete_states(
                images_dir, core_items
            )
            if incomplete_core:
                self._pause_after_targeted_retry(incomplete_core)
                return

            record = self._run_store.load(self.run_id)
            core_review = record["reviews"].get("core_actions", {})
            if core_review.get("decision") != "accepted":
                core_qa = PetGenerationQa().run(
                    images=generated,
                    expected_states=[
                        state for state, _ in core_items
                    ],
                    output_dir=work / "qa" / "core",
                    canonical=canonical,
                )
                self._run_store.update_stage(
                    self.run_id,
                    "core_review",
                    status="needs_review",
                    artifacts={
                        "core_contact_sheet":
                            core_qa["artifacts"]["contact_sheet"],
                        "core_qa_report":
                            core_qa["artifacts"]["report"],
                    },
                )
                self.progress_changed.emit(
                    55, "核心动作已生成，请检查身份与动作一致性。"
                )
                self.core_review_ready.emit(
                    self.run_id,
                    core_qa["artifacts"]["contact_sheet"],
                    core_qa["artifacts"]["report"],
                )
                return

            self._generate_pose_group(
                canonical=canonical,
                images_dir=images_dir,
                pose_items=remaining_items,
                generated=generated,
                all_pose_items=pose_items,
            )
            if self.isInterruptionRequested():
                self._mark_canceled()
                return
            incomplete = self._incomplete_states(images_dir, pose_items)
            if incomplete:
                self._pause_after_targeted_retry(incomplete)
                return

            self.progress_changed.emit(92, "正在执行自动 QA…")
            qa_report = PetGenerationQa().run(
                images=generated,
                expected_states=[state for state, _ in pose_items],
                output_dir=work / "qa" / "final",
                canonical=canonical,
            )
            if not qa_report["passed"]:
                self._run_store.update_stage(
                    self.run_id,
                    "qa_review",
                    status="needs_review",
                    artifacts={
                        "qa_contact_sheet":
                            qa_report["artifacts"]["contact_sheet"],
                        "qa_report": qa_report["artifacts"]["report"],
                    },
                )
                diagnostic_report = self._persist_diagnostic_report()
                self._run_store.update_stage(
                    self.run_id,
                    "qa_review",
                    status="needs_review",
                    artifacts={
                        "generation_diagnostic_report":
                            diagnostic_report,
                    },
                )
                self.progress_changed.emit(
                    94, "自动 QA 发现问题，请选择动作返工。"
                )
                self.qa_review_ready.emit(
                    self.run_id,
                    qa_report["artifacts"]["contact_sheet"],
                    qa_report["artifacts"]["report"],
                )
                return
            self._run_store.update_stage(
                self.run_id,
                "qa_complete",
                status="running",
                artifacts={
                    "qa_contact_sheet":
                        qa_report["artifacts"]["contact_sheet"],
                    "qa_report": qa_report["artifacts"]["report"],
                },
            )

            self.progress_changed.emit(93, "正在生成多帧微动作与循环预览…")
            animation_build = PetAnimationBuilder().build(
                images=generated,
                output_dir=work / "images" / "frames",
                preview_dir=work / "qa" / "previews",
            )
            animation_qa = PetAnimationQa().run(
                animations=animation_build["animations"],
                previews=animation_build["previews"],
                output_dir=work / "qa" / "animation",
            )
            if not animation_qa["passed"]:
                self._run_store.update_stage(
                    self.run_id,
                    "qa_review",
                    status="needs_review",
                    artifacts={
                        "qa_contact_sheet":
                            qa_report["artifacts"]["contact_sheet"],
                        "qa_report":
                            animation_qa["artifacts"]["report"],
                        "static_qa_report":
                            qa_report["artifacts"]["report"],
                        "animation_qa_report":
                            animation_qa["artifacts"]["report"],
                        "animation_previews": {
                            state: str(path)
                            for state, path
                            in animation_build["previews"].items()
                        },
                    },
                )
                diagnostic_report = self._persist_diagnostic_report()
                self._run_store.update_stage(
                    self.run_id,
                    "qa_review",
                    status="needs_review",
                    artifacts={
                        "generation_diagnostic_report":
                            diagnostic_report,
                    },
                )
                self.progress_changed.emit(
                    94, "动画 QA 发现问题，请选择动作返工。"
                )
                self.qa_review_ready.emit(
                    self.run_id,
                    qa_report["artifacts"]["contact_sheet"],
                    animation_qa["artifacts"]["report"],
                )
                return
            self._run_store.update_stage(
                self.run_id,
                "animation_qa_complete",
                status="running",
                artifacts={
                    "animation_qa_report":
                        animation_qa["artifacts"]["report"],
                    "animation_previews": {
                        state: str(path)
                        for state, path
                        in animation_build["previews"].items()
                    },
                },
            )

            self.progress_changed.emit(94, "正在组装 Pixkin 角色包…")
            self._run_store.update_stage(
                self.run_id, "packaging", status="running"
            )
            zip_path = self._package(
                work,
                slug,
                generated,
                animation_sequences=animation_build["animations"],
            )
            self._run_store.update_stage(
                self.run_id,
                "final_review",
                status="needs_review",
                artifacts={"package": str(zip_path)},
            )
            diagnostic_report = self._persist_diagnostic_report()
            self._run_store.update_stage(
                self.run_id,
                "final_review",
                status="needs_review",
                artifacts={
                    "generation_diagnostic_report":
                        diagnostic_report,
                },
            )
            self.progress_changed.emit(
                100, "角色包已完成，请最终预览并确认安装。"
            )
            self.package_ready.emit(str(zip_path))
        except Exception as exc:
            if self._run_store is not None and self.run_id:
                try:
                    if self.isInterruptionRequested():
                        self._mark_canceled()
                    else:
                        if self._active_task_id:
                            self._run_store.update_task(
                                self.run_id,
                                self._active_task_id,
                                "failed",
                                error=str(exc),
                            )
                        self._run_store.update_stage(
                            self.run_id,
                            "failed",
                            status="failed",
                            error=str(exc),
                        )
                        diagnostic_report = (
                            self._persist_diagnostic_report()
                        )
                        self._run_store.update_stage(
                            self.run_id,
                            "failed",
                            status="failed",
                            error=str(exc),
                            artifacts={
                                "generation_diagnostic_report":
                                    diagnostic_report,
                            },
                        )
                except Exception:
                    pass
            if not self.isInterruptionRequested():
                self.error_occurred.emit(str(exc))
        finally:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    def _pose_items(self):
        if self.generation_mode == "full":
            return list(FULL_POSES.items())
        if self.generation_mode == "standard":
            return list(STANDARD_POSES.items())
        if self.generation_mode == "legacy_full":
            return list(POSES.items())
        return [(state, POSES[state]) for state in BASIC_POSE_IDS]

    def _persist_diagnostic_report(self):
        destination = (
            self._run_store.workspace(self.run_id)
            / "qa"
            / "diagnostics"
            / "generation-diagnostic.json"
        )
        PetGenerationDiagnostics.write(
            self._run_store.load(self.run_id),
            destination,
        )
        return str(destination)

    def _completed_images(self, images_dir, pose_items):
        record = self._run_store.load(self.run_id)
        completed = {}
        for state, _ in pose_items:
            path = images_dir / f"{state}.png"
            if (
                record["tasks"][state]["status"] == "complete"
                and path.is_file()
            ):
                completed[state] = path
        return completed

    def _generate_pose_group(
        self,
        *,
        canonical,
        images_dir,
        pose_items,
        generated,
        all_pose_items,
    ):
        positions = {
            state: index
            for index, (state, _) in enumerate(all_pose_items, start=1)
        }
        total = len(all_pose_items)
        for state, pose in pose_items:
            record = self._run_store.load(self.run_id)
            task = record["tasks"][state]
            target = images_dir / f"{state}.png"
            if self.retry_task_id not in {None, state}:
                continue
            if (
                task["status"] == "complete"
                and target.is_file()
                and self.retry_task_id != state
            ):
                generated[state] = target
                continue
            if self.retry_task_id == state:
                self._ensure_legacy_candidate(state, target)
                self._invalidate_mirror_dependents(state, generated)
                self._run_store.reset_task(self.run_id, state)
            if self.isInterruptionRequested():
                return
            mirror_source = HORIZONTAL_MIRROR_SOURCES.get(state)
            if (
                self.allow_horizontal_mirror
                and self.retry_task_id != state
                and mirror_source in generated
            ):
                index = positions[state]
                percent = 10 + int(index / total * 80)
                self.progress_changed.emit(
                    percent,
                    f"正在从 {mirror_source} 安全镜像 {state}"
                    f"（{index}/{total}）…",
                )
                self._store_mirrored_candidate(
                    task_id=state,
                    source_state=mirror_source,
                    source_path=generated[mirror_source],
                    active_target=target,
                )
                generated[state] = target
                continue
            self._validate_generation_inputs()
            client = self._generation_client()
            index = positions[state]
            percent = 10 + int(index / total * 80)
            self.progress_changed.emit(
                percent, f"正在绘制 {state} 姿态（{index}/{total}）…"
            )
            self._active_task_id = state
            prompt = self._pose_prompt(pose)
            pose_bytes = self._generate_with_retry(
                task_id=state,
                client=client,
                paths=[canonical, *self.reference_paths],
                prompt=prompt,
            )
            self._store_candidate(
                task_id=state,
                raw=pose_bytes,
                active_target=target,
                prompt=prompt,
            )
            generated[state] = target
            self._active_task_id = None

    def _incomplete_states(self, images_dir, pose_items):
        record = self._run_store.load(self.run_id)
        return [
            state
            for state, _ in pose_items
            if (
                record["tasks"][state]["status"] != "complete"
                or not (images_dir / f"{state}.png").is_file()
            )
        ]

    def _pause_after_targeted_retry(self, incomplete):
        if not self.retry_task_id:
            raise RuntimeError(
                "仍有动作尚未完成：" + "、".join(incomplete)
            )
        self._run_store.update_stage(
            self.run_id,
            "action_generation",
            status="pending",
        )
        self.progress_changed.emit(
            85, "选中动作已重试；其余未完成动作可继续生成。"
        )

    def _validate_generation_inputs(self):
        if not self.api_key:
            raise ValueError("请先在伙伴工坊中填写图像 API Key。")
        if not self.reference_paths:
            raise ValueError("至少需要一张风格示意图。")
        for path in self.reference_paths:
            if not path.is_file():
                raise ValueError(f"参考图不存在：{path}")

    def _assert_api_budget_available(self):
        record = self._run_store.load(self.run_id)
        budget = record.get("request", {}).get("max_api_calls")
        used = self.api_calls_used(record)
        if budget is not None and used >= int(budget):
            raise RuntimeError(
                f"图像 API 调用预算已用尽（{used}/{int(budget)}）。"
                "请在继续任务时提高预算，或保留当前产物稍后处理。"
            )
        return record

    def _reserve_api_call(self, task_id: str):
        self._assert_api_budget_available()
        self._run_store.update_task(
            self.run_id,
            task_id,
            "running",
            increment_attempt=True,
        )

    def _generate_with_retry(
        self,
        *,
        task_id: str,
        client,
        paths,
        prompt: str,
    ):
        self._assert_api_budget_available()
        self._preflight_generation_client(client)
        for retry_number in range(MAX_TRANSIENT_RETRIES + 1):
            self._reserve_api_call(task_id)
            started = time.monotonic()
            try:
                result = self._generate(client, paths, prompt)
            except Exception as exc:
                duration_ms = round(
                    (time.monotonic() - started) * 1000
                )
                details = self.classify_generation_error(exc)
                self._run_store.record_api_call(
                    self.run_id,
                    task_id,
                    duration_ms=duration_ms,
                    outcome="failed",
                    error_category=details["category"],
                    retry_number=retry_number,
                )
                if (
                    not details["retriable"]
                    or retry_number >= MAX_TRANSIENT_RETRIES
                ):
                    raise RuntimeError(
                        f"{details['label']}：{exc}"
                    ) from exc
                delay = RETRY_DELAYS_SECONDS[
                    min(retry_number, len(RETRY_DELAYS_SECONDS) - 1)
                ]
                self.progress_changed.emit(
                    0,
                    f"{details['label']}，{delay:g} 秒后自动重试"
                    f"（{retry_number + 1}/{MAX_TRANSIENT_RETRIES}）…",
                )
                if self.isInterruptionRequested():
                    raise RuntimeError("孵化任务已取消。") from exc
                time.sleep(delay)
                continue
            duration_ms = round(
                (time.monotonic() - started) * 1000
            )
            self._run_store.record_api_call(
                self.run_id,
                task_id,
                duration_ms=duration_ms,
                outcome="success",
                retry_number=retry_number,
            )
            return result
        raise RuntimeError("图像生成重试流程异常结束。")

    def _generation_client(self):
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or "https://api.openai.com/v1",
                timeout=180.0,
                max_retries=0,
            )
        return self._client

    def _preflight_generation_client(self, client):
        if self._preflight_complete:
            return
        endpoint = self.base_url or "https://api.openai.com/v1"
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("图像接口地址必须是完整的 HTTP(S) URL。")
        model = self.model or "gpt-image-2"
        quality = self.quality or "medium"
        if not model.strip():
            raise ValueError("图像模型名不能为空。")
        if quality not in {"low", "medium", "high"}:
            raise ValueError("图像质量必须是 low、medium 或 high。")
        edit = getattr(getattr(client, "images", None), "edit", None)
        if not callable(edit):
            raise ValueError("当前接口客户端不支持图像编辑能力。")

        cache_key = hashlib.sha256(
            f"{endpoint.rstrip('/')}|{model}".encode("utf-8")
        ).hexdigest()[:16]
        record = self._run_store.load(self.run_id)
        cached = record.get("request", {}).get(
            "capability_preflight", {}
        )
        if (
            isinstance(cached, dict)
            and cached.get("key") == cache_key
            and cached.get("status") in {"verified", "unverified"}
        ):
            self._preflight_complete = True
            return

        status = "verified"
        note = "模型查询成功，图像编辑方法可用。"
        retrieve = getattr(
            getattr(client, "models", None),
            "retrieve",
            None,
        )
        if not callable(retrieve):
            status = "unverified"
            note = "兼容接口未提供模型查询方法，已保留图像编辑能力检查。"
        else:
            try:
                retrieve(model, timeout=15.0)
            except Exception as exc:
                details = self.classify_generation_error(exc)
                official = parsed.hostname in {
                    "api.openai.com",
                    "www.api.openai.com",
                }
                if details["category"] in {
                    "authentication",
                    "permission",
                } or (
                    official
                    and details["category"] in {
                        "invalid_request",
                        "not_found",
                    }
                ):
                    raise RuntimeError(
                        f"图像接口预检失败（{details['label']}）：{exc}"
                    ) from exc
                status = "unverified"
                note = (
                    f"模型查询无法验证（{details['label']}），"
                    "将由首次图像请求确认能力。"
                )
        self._run_store.update_request(
            self.run_id,
            {
                "capability_preflight": {
                    "key": cache_key,
                    "status": status,
                    "endpoint": endpoint,
                    "model": model,
                    "checked_at": datetime.now(
                        timezone.utc
                    ).isoformat(timespec="seconds"),
                    "note": note,
                }
            },
        )
        self._preflight_complete = True

    def _snapshot_references(self):
        references_dir = (
            self._run_store.workspace(self.run_id) / "references"
        )
        references_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for index, source in enumerate(self.reference_paths, start=1):
            suffix = source.suffix.lower() or ".png"
            destination = references_dir / f"reference-{index}{suffix}"
            shutil.copy2(source, destination)
            copied.append(destination)
        self.reference_paths = copied
        self._run_store.update_request(
            self.run_id,
            {"reference_paths": [str(path) for path in copied]},
        )

    def _store_candidate(
        self,
        *,
        task_id: str,
        raw: bytes,
        active_target: Path,
        prompt: str,
    ):
        record = self._run_store.load(self.run_id)
        attempt = max(1, int(record["tasks"][task_id]["attempts"]))
        candidate_id = f"attempt-{attempt:03d}"
        candidate_dir = (
            self._run_store.workspace(self.run_id)
            / "candidates"
            / task_id
            / candidate_id
        )
        candidate_dir.mkdir(parents=True, exist_ok=True)
        source_suffix = self._source_suffix(raw)
        source_path = candidate_dir / f"source{source_suffix}"
        sprite_path = candidate_dir / "sprite.png"
        source_path.write_bytes(raw)
        self._save_sprite(raw, sprite_path)
        workspace = self._run_store.workspace(self.run_id)
        source_artifact = source_path.relative_to(workspace).as_posix()
        sprite_artifact = sprite_path.relative_to(workspace).as_posix()
        active_artifact = active_target.relative_to(workspace).as_posix()
        self._run_store.record_candidate(
            self.run_id,
            task_id,
            candidate_id=candidate_id,
            source_artifact=source_artifact,
            sprite_artifact=sprite_artifact,
            metadata={
                "model": self.model or "gpt-image-2",
                "quality": self.quality or "medium",
                "prompt": prompt,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        active_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sprite_path, active_target)
        self._run_store.select_candidate(
            self.run_id,
            task_id,
            candidate_id,
            active_artifact=active_artifact,
        )
        return candidate_id

    def _store_mirrored_candidate(
        self,
        *,
        task_id: str,
        source_state: str,
        source_path: Path,
        active_target: Path,
    ):
        record = self._run_store.load(self.run_id)
        task = record["tasks"][task_id]
        mirror_index = 1 + sum(
            str(candidate.get("id", "")).startswith("mirror-")
            for candidate in task.get("candidates", [])
        )
        candidate_id = f"mirror-{mirror_index:03d}"
        workspace = self._run_store.workspace(self.run_id)
        candidate_dir = (
            workspace / "candidates" / task_id / candidate_id
        )
        candidate_dir.mkdir(parents=True, exist_ok=True)
        source_copy = candidate_dir / "source.png"
        sprite_path = candidate_dir / "sprite.png"
        shutil.copy2(source_path, source_copy)
        with Image.open(source_path) as source:
            mirrored = ImageOps.mirror(source.convert("RGBA"))
        mirrored.save(sprite_path, "PNG", optimize=True)
        source_artifact = source_copy.relative_to(workspace).as_posix()
        sprite_artifact = sprite_path.relative_to(workspace).as_posix()
        active_artifact = active_target.relative_to(workspace).as_posix()
        self._run_store.record_candidate(
            self.run_id,
            task_id,
            candidate_id=candidate_id,
            source_artifact=source_artifact,
            sprite_artifact=sprite_artifact,
            metadata={
                "derived": True,
                "operation": "horizontal_mirror",
                "mirror_of": source_state,
                "source_sha256": hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest(),
            },
        )
        active_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sprite_path, active_target)
        self._run_store.select_candidate(
            self.run_id,
            task_id,
            candidate_id,
            active_artifact=active_artifact,
        )
        return candidate_id

    def _invalidate_mirror_dependents(self, source_state, generated):
        if not self.allow_horizontal_mirror:
            return
        record = self._run_store.load(self.run_id)
        for task_id in self.mirror_dependents(source_state):
            if (
                task_id in record["tasks"]
                and record["tasks"][task_id]["status"] == "complete"
            ):
                self._run_store.reset_task(self.run_id, task_id)
                generated.pop(task_id, None)

    def _ensure_legacy_candidate(
        self,
        task_id: str,
        active_target: Path,
    ):
        record = self._run_store.load(self.run_id)
        task = record["tasks"][task_id]
        if task["candidates"] or not active_target.is_file():
            return
        workspace = self._run_store.workspace(self.run_id)
        candidate_id = "legacy-001"
        candidate_dir = (
            workspace / "candidates" / task_id / candidate_id
        )
        candidate_dir.mkdir(parents=True, exist_ok=True)
        source_path = candidate_dir / "source.png"
        sprite_path = candidate_dir / "sprite.png"
        shutil.copy2(active_target, source_path)
        shutil.copy2(active_target, sprite_path)
        self._run_store.record_candidate(
            self.run_id,
            task_id,
            candidate_id=candidate_id,
            source_artifact=source_path.relative_to(workspace).as_posix(),
            sprite_artifact=sprite_path.relative_to(workspace).as_posix(),
            metadata={"migrated_from_active_artifact": True},
        )
        active_artifact = active_target.relative_to(workspace).as_posix()
        self._run_store.select_candidate(
            self.run_id,
            task_id,
            candidate_id,
            active_artifact=active_artifact,
        )

    @staticmethod
    def _source_suffix(raw: bytes) -> str:
        with Image.open(io.BytesIO(raw)) as image:
            image_format = str(image.format or "").upper()
        return {
            "JPEG": ".jpg",
            "PNG": ".png",
            "WEBP": ".webp",
        }.get(image_format, ".bin")

    def _mark_canceled(self):
        if self._run_store is not None and self.run_id:
            if self._active_task_id:
                self._run_store.update_task(
                    self.run_id,
                    self._active_task_id,
                    "canceled",
                )
                self._active_task_id = None
            self._run_store.update_stage(
                self.run_id,
                "canceled",
                status="canceled",
            )

    def _generate(self, client: OpenAI, paths, prompt: str) -> bytes:
        with ExitStack() as stack:
            files = [stack.enter_context(Path(path).open("rb")) for path in paths]
            result = client.images.edit(
                model=self.model or "gpt-image-2",
                image=files,
                prompt=prompt,
                size="1024x1024",
                quality=self.quality or "medium",
                response_format="b64_json",
            )
        encoded = (
            result.data[0].b64_json
            if getattr(result, "data", None)
            else None
        )
        if not encoded:
            raise RuntimeError("图像服务没有返回可用图片。")
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("图像服务返回的图片数据损坏。") from exc

    def _base_prompt(self):
        notes = self.style_notes or "Preserve the reference's broad color mood."
        return (
            f"Design the canonical full-body digital pet named {self.pet_name}. "
            f"Use the supplied images only as visual identity and style references. "
            f"User style notes: {notes}\n{HARD_CARTOON_RULES}"
        )

    def _pose_prompt(self, pose: str):
        return (
            f"Using the first image as the canonical pet identity and the remaining "
            f"images only as supporting references, redraw the exact same pet in this "
            f"pose: {pose}. Preserve head shape, face, markings, palette, outline "
            f"weight, proportions, accessories, and silhouette.\n"
            f"{HARD_CARTOON_RULES}"
        )

    @staticmethod
    def _save_sprite(raw: bytes, target: Path):
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        flat_data = getattr(image, "get_flattened_data", image.getdata)
        pixels = list(flat_data())
        corners = [
            pixels[0],
            pixels[image.width - 1],
            pixels[-image.width],
            pixels[-1],
        ]
        key = tuple(sum(pixel[i] for pixel in corners) // 4 for i in range(3))
        output = []
        for red, green, blue in pixels:
            distance = (
                (red - key[0]) ** 2
                + (green - key[1]) ** 2
                + (blue - key[2]) ** 2
            ) ** 0.5
            if distance <= 22:
                alpha = 0
            elif distance < 115:
                alpha = int((distance - 22) / 93 * 255)
            else:
                alpha = 255
            if alpha < 245 and green > red and green > blue:
                green = min(green, max(red, blue) + 14)
            output.append((red, green, blue, alpha))
        rgba = Image.new("RGBA", image.size)
        rgba.putdata(output)
        bbox = rgba.getchannel("A").getbbox()
        if not bbox:
            raise RuntimeError("生成图片没有检测到有效角色轮廓。")
        subject = rgba.crop(bbox)
        subject.thumbnail((168, 184), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
        canvas.alpha_composite(
            subject,
            (
                (canvas.width - subject.width) // 2,
                194 - subject.height,
            ),
        )
        canvas.save(target, "PNG", optimize=True)

    def _package(
        self,
        work: Path,
        slug: str,
        generated,
        *,
        animation_sequences=None,
    ):
        animation_sequences = animation_sequences or {}
        animations = {}
        for state, path in generated.items():
            sequence = animation_sequences.get(state) or {}
            frame_paths = sequence.get("frames") or [path]
            animations[state] = {
                "source": {
                    "type": "frames",
                    "files": [
                        Path(frame).relative_to(work).as_posix()
                        for frame in frame_paths
                    ],
                    "cell_size": [192, 208],
                },
                "fps": int(sequence.get("fps", 8)),
                "playback": str(
                    sequence.get(
                        "playback",
                        (
                            "loop" if state in LOOP_STATES else "once"
                        ),
                    )
                ),
                "anchor": [96, 194],
                "interruptible": state != "dragging",
            }
        personality = (
            self.personality
            or "聪明、温暖、友好，回答简洁而有帮助"
        )
        generated_states = set(generated)
        if (
            self.generation_mode == "full"
            and set(FULL_POSE_IDS).issubset(generated_states)
        ):
            quality_tier = "full"
        elif (
            self.generation_mode in {"standard", "full"}
            and set(STANDARD_POSE_IDS).issubset(generated_states)
        ):
            quality_tier = "standard"
        else:
            quality_tier = "basic"
        api_calls_used = 0
        timing_summary = self.api_timing_summary({})
        capability_preflight = {}
        if self.run_id and self._run_store is not None:
            run_record = self._run_store.load(self.run_id)
            api_calls_used = self.api_calls_used(run_record)
            timing_summary = self.api_timing_summary(run_record)
            capability_preflight = dict(
                run_record.get("request", {}).get(
                    "capability_preflight", {}
                )
            )
        metadata = {
            "schema_version": "2.0",
            "id": slug,
            "name": self.pet_name,
            "version": "2.0.0",
            "author": "Pixkin 伙伴工坊",
            "description": "由至少一张风格参考图孵化的卡通桌面伙伴。",
            "quality_tier": quality_tier,
            "preview": "images/idle.png",
            "persona": {
                "identity": (
                    f"你是 {self.pet_name}，Pixkin 桌面上的卡通 AI 伙伴。"
                ),
                "core_traits": [personality],
                "relationship": "陪伴用户学习、工作与日常生活的数字伙伴。",
                "voice": {
                    "tone": "自然、友好",
                    "pacing": "清楚、不急促",
                    "reply_length": "short",
                    "vocabulary": "使用自然中文，避免机械套话",
                },
                "initiative": {
                    "animate_without_prompt": True,
                    "speak_without_prompt": False,
                    "open_windows_without_prompt": False,
                    "send_notifications_without_prompt": False,
                },
                "tool_behavior": {
                    "before_call": "先说明为什么需要使用工具",
                    "after_success": "只报告真实执行结果",
                    "after_failure": "明确说明失败，不假装已经完成",
                },
                "boundaries": [
                    "不主动发言、打开窗口或发送通知",
                    "不替用户执行未经授权的敏感操作",
                ],
            },
            "behavior": {
                "motion_temperament": "balanced",
                "speed_multiplier": 1.0,
                "amplitude": "medium",
                "idle_interval_seconds": [8, 16],
                "ambient_weights": {
                    state: weight
                    for state, weight in {
                        "blink": 5,
                        "look_around": 2,
                        "nod": 1,
                        "stretch": 0.8,
                        "wave": 0.6,
                        "sleep": 0.35,
                        "happy": 0.35,
                    }.items()
                    if state in generated
                },
                "cooldown_seconds": {
                    "wave": 45,
                    "stretch": 60,
                },
            },
            "edge": (
                {
                    "supported_sides": list(EDGE_SIDES),
                    "default_enabled_sides": ["left", "right", "top"],
                    "left": {
                        "peek_anchor": [164, 98],
                        "hitbox": [132, 56, 60, 84],
                    },
                    "right": {
                        "peek_anchor": [28, 98],
                        "hitbox": [0, 56, 60, 84],
                    },
                    "top": {
                        "peek_anchor": [96, 172],
                        "hitbox": [54, 144, 84, 64],
                    },
                    "bottom": {
                        "peek_anchor": [96, 34],
                        "hitbox": [54, 0, 84, 64],
                    },
                }
                if quality_tier == "full"
                else {}
            ),
            "animations": animations,
            "compatibility": {
                "min_app_version": VERSION,
                "generated_by": f"Pixkin {VERSION}",
            },
            "rights": {
                "license": "user-provided-references",
                "author_confirmed_rights": False,
                "ai_generated": True,
                "reference_sources": [
                    {
                        "type": "user_provided",
                        "description": "由用户提供给伙伴工坊的参考图片",
                    }
                ],
                "modification_allowed": True,
                "commercial_use_allowed": False,
            },
            "extensions": {
                "pixkin_pet_lab": {
                    "animation_source": "procedural_micro_motion",
                    "generation_mode": self.generation_mode,
                    "allow_horizontal_mirror":
                        self.allow_horizontal_mirror,
                    "planned_api_calls": self.planned_api_calls(
                        self.generation_mode,
                        self.allow_horizontal_mirror,
                    ),
                    "max_api_calls": self.max_api_calls,
                    "api_calls_used": api_calls_used,
                    "api_timing": timing_summary,
                    "capability_preflight": capability_preflight,
                    "key_pose_canvas": [192, 208],
                    "anchor": [96, 194],
                },
            },
        }
        frontmatter = yaml.safe_dump(
            metadata, allow_unicode=True, sort_keys=False
        ).rstrip()
        character_md = (
            f"---\n{frontmatter}\n---\n\n"
            f"# {self.pet_name}\n\n由 Pixkin 伙伴工坊生成。\n"
        )
        (work / "character.md").write_text(character_md, encoding="utf-8")
        zip_path = work.parent / f"{work.name}.zip"
        with zipfile.ZipFile(
            zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            archive.write(work / "character.md", "character.md")
            for path in sorted((work / "images").rglob("*.png")):
                archive.write(
                    path,
                    path.relative_to(work).as_posix(),
                )
        return zip_path

    def _slug(self):
        value = re.sub(r"[^a-z0-9_-]+", "-", self.pet_name.lower()).strip("-")
        if len(value) < 2:
            value = f"pixkin-{uuid.uuid4().hex[:8]}"
        return value[:40]
