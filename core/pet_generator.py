import base64
import io
import re
import shutil
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path

import yaml
from openai import OpenAI
from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from core.pet_generation_run import PetGenerationRunStore
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

BASIC_POSE_IDS = ("idle", "talking", "dragging", "alerting")

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
        self.full_hatch = full_hatch
        self._client = None
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
        return cls(
            api_key=api_key,
            base_url=str(request.get("image_base_url", "")),
            model=str(request.get("image_model", "gpt-image-2")),
            quality=str(request.get("image_quality", "medium")),
            pet_name=str(request.get("pet_name", "My Pixkin")),
            personality=str(request.get("personality", "")),
            style_notes=str(request.get("style_notes", "")),
            reference_paths=request.get("reference_paths", []),
            full_hatch=request.get("mode") == "full",
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
                        "mode": "full" if self.full_hatch else "draft",
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
                self._run_store.update_task(
                    self.run_id,
                    "canonical",
                    "running",
                    increment_attempt=True,
                )
                base_bytes = self._generate(
                    client, self.reference_paths, self._base_prompt()
                )
                self._save_sprite(base_bytes, canonical)
                self._run_store.update_task(
                    self.run_id,
                    "canonical",
                    "complete",
                    artifact="images/canonical.png",
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
                if package.is_file():
                    self.progress_changed.emit(
                        100, "角色包等待最终预览与安装确认。"
                    )
                    self.package_ready.emit(str(package))
                    return

            self._validate_generation_inputs()
            client = self._generation_client()
            self._run_store.update_stage(
                self.run_id, "action_generation", status="running"
            )
            generated = {}
            total = len(pose_items)
            for index, (state, pose) in enumerate(pose_items, start=1):
                record = self._run_store.load(self.run_id)
                task = record["tasks"][state]
                target = images_dir / f"{state}.png"
                if (
                    self.retry_task_id not in {None, state}
                    or (
                        task["status"] == "complete"
                        and target.is_file()
                        and self.retry_task_id != state
                    )
                ):
                    if task["status"] == "complete" and target.is_file():
                        generated[state] = target
                    continue
                if self.retry_task_id == state:
                    self._run_store.reset_task(self.run_id, state)
                if self.isInterruptionRequested():
                    self._mark_canceled()
                    return
                percent = 10 + int(index / total * 80)
                self.progress_changed.emit(
                    percent, f"正在绘制 {state} 姿态（{index}/{total}）…"
                )
                self._run_store.update_task(
                    self.run_id,
                    state,
                    "running",
                    increment_attempt=True,
                )
                self._active_task_id = state
                inputs = [canonical, *self.reference_paths]
                pose_bytes = self._generate(
                    client, inputs, self._pose_prompt(pose)
                )
                self._save_sprite(pose_bytes, target)
                generated[state] = target
                self._run_store.update_task(
                    self.run_id,
                    state,
                    "complete",
                    artifact=f"images/{state}.png",
                )
                self._active_task_id = None

            record = self._run_store.load(self.run_id)
            incomplete = [
                state
                for state, _ in pose_items
                if (
                    record["tasks"][state]["status"] != "complete"
                    or not (images_dir / f"{state}.png").is_file()
                )
            ]
            if incomplete:
                if self.retry_task_id:
                    self._run_store.update_stage(
                        self.run_id,
                        "action_generation",
                        status="pending",
                    )
                    self.progress_changed.emit(
                        85,
                        "选中动作已重试；其余未完成动作可继续生成。",
                    )
                    return
                raise RuntimeError(
                    "仍有动作尚未完成：" + "、".join(incomplete)
                )

            self.progress_changed.emit(94, "正在组装 Pixkin 角色包…")
            self._run_store.update_stage(
                self.run_id, "packaging", status="running"
            )
            zip_path = self._package(work, slug, generated)
            self._run_store.update_stage(
                self.run_id,
                "final_review",
                status="needs_review",
                artifacts={"package": str(zip_path)},
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
        if self.full_hatch:
            return list(POSES.items())
        return [(state, POSES[state]) for state in BASIC_POSE_IDS]

    def _validate_generation_inputs(self):
        if not self.api_key:
            raise ValueError("请先在伙伴工坊中填写图像 API Key。")
        if not self.reference_paths:
            raise ValueError("至少需要一张风格示意图。")
        for path in self.reference_paths:
            if not path.is_file():
                raise ValueError(f"参考图不存在：{path}")

    def _generation_client(self):
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or "https://api.openai.com/v1",
                timeout=180.0,
                max_retries=0,
            )
        return self._client

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
        encoded = result.data[0].b64_json
        if not encoded:
            raise RuntimeError("图像服务没有返回可用图片。")
        return base64.b64decode(encoded)

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

    def _package(self, work: Path, slug: str, generated):
        animations = {
            state: {
                "source": {
                    "type": "frames",
                    "files": [f"images/{path.name}"],
                    "cell_size": [192, 208],
                },
                "fps": 8,
                "playback": (
                    "loop"
                    if state in {"idle", "sleep", "talking"}
                    else "once"
                ),
                "anchor": [96, 194],
                "interruptible": state != "dragging",
            }
            for state, path in generated.items()
        }
        personality = (
            self.personality
            or "聪明、温暖、友好，回答简洁而有帮助"
        )
        metadata = {
            "schema_version": "2.0",
            "id": slug,
            "name": self.pet_name,
            "version": "2.0.0",
            "author": "Pixkin 伙伴工坊",
            "description": "由至少一张风格参考图孵化的卡通桌面伙伴。",
            "quality_tier": "basic",
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
                        "stretch": 1,
                        "wave": 0.6,
                    }.items()
                    if state in generated
                },
                "cooldown_seconds": {
                    "wave": 45,
                    "stretch": 60,
                },
            },
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
            for path in sorted((work / "images").glob("*.png")):
                archive.write(path, f"images/{path.name}")
        return zip_path

    def _slug(self):
        value = re.sub(r"[^a-z0-9_-]+", "-", self.pet_name.lower()).strip("-")
        if len(value) < 2:
            value = f"pixkin-{uuid.uuid4().hex[:8]}"
        return value[:40]
