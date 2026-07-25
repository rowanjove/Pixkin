"""Frame-level QA for Pet Lab animation sequences."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from PIL import Image

from core.pet_animation_builder import ANCHOR, CANVAS_SIZE


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PetAnimationQa:
    """Validate geometry, motion, and preview artifacts for frame sequences."""

    def run(
        self,
        *,
        animations: Dict,
        previews: Dict[str, Path],
        output_dir: Path,
    ) -> Dict:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        errors = []
        warnings = []
        checks = {}
        for state, animation in animations.items():
            frames = [Path(path) for path in animation.get("frames", [])]
            state_checks = {
                "frame_count": len(frames),
                "fps": int(animation.get("fps", 0)),
                "playback": str(animation.get("playback", "")),
                "frames": [],
            }
            checks[state] = state_checks
            if len(frames) < 2:
                errors.append(self._issue(
                    "insufficient_frames",
                    state,
                    "动画至少需要两个独立帧。",
                ))
                continue
            if not 1 <= state_checks["fps"] <= 30:
                errors.append(self._issue(
                    "invalid_fps", state, "动画 FPS 必须在 1–30。"
                ))
            if state_checks["playback"] not in {
                "once", "loop", "ping_pong"
            }:
                errors.append(self._issue(
                    "invalid_playback", state, "动画播放模式无效。"
                ))

            hashes = []
            boxes = []
            for frame_path in frames:
                if not frame_path.is_file():
                    errors.append(self._issue(
                        "missing_frame", state, f"缺少帧：{frame_path.name}"
                    ))
                    continue
                with Image.open(frame_path) as source:
                    source.load()
                    frame = source.convert("RGBA")
                bbox = frame.getchannel("A").getbbox()
                frame_check = {
                    "file": str(frame_path),
                    "size": list(frame.size),
                    "alpha_bbox": list(bbox) if bbox else None,
                }
                state_checks["frames"].append(frame_check)
                if frame.size != CANVAS_SIZE:
                    errors.append(self._issue(
                        "invalid_frame_size",
                        state,
                        f"帧画布必须为 {CANVAS_SIZE[0]} × {CANVAS_SIZE[1]}。",
                    ))
                if not bbox:
                    errors.append(self._issue(
                        "empty_frame", state, "动画包含空帧。"
                    ))
                    continue
                left, top, right, bottom = bbox
                boxes.append(bbox)
                if (
                    left < 4
                    or top < 4
                    or right > CANVAS_SIZE[0] - 4
                    or bottom > CANVAS_SIZE[1] - 4
                ):
                    errors.append(self._issue(
                        "unsafe_frame_margin",
                        state,
                        "动画帧触碰画布安全区。",
                    ))
                if abs(bottom - ANCHOR[1]) > 20:
                    warnings.append(self._issue(
                        "frame_baseline_drift",
                        state,
                        "动画帧偏离角色基准线超过 20 像素。",
                    ))
                hashes.append(hashlib.sha256(
                    frame_path.read_bytes()
                ).hexdigest())

            if hashes and len(set(hashes)) < 2:
                errors.append(self._issue(
                    "no_visible_motion",
                    state,
                    "所有动画帧完全相同。",
                ))
            if boxes:
                widths = [right - left for left, _, right, _ in boxes]
                heights = [bottom - top for _, top, _, bottom in boxes]
                if (
                    max(widths) > min(widths) * 1.2
                    or max(heights) > min(heights) * 1.2
                ):
                    warnings.append(self._issue(
                        "frame_scale_drift",
                        state,
                        "动画帧之间的角色尺寸变化较大。",
                    ))
                centers = [
                    ((left + right) / 2, (top + bottom) / 2)
                    for left, top, right, bottom in boxes
                ]
                if (
                    max(x for x, _ in centers)
                    - min(x for x, _ in centers) > 16
                    or max(y for _, y in centers)
                    - min(y for _, y in centers) > 16
                ):
                    warnings.append(self._issue(
                        "frame_position_drift",
                        state,
                        "动画帧之间的位置变化超过 16 像素。",
                    ))
            preview = Path(previews.get(state, ""))
            state_checks["preview"] = str(preview)
            if not preview.is_file():
                errors.append(self._issue(
                    "missing_preview", state, "缺少循环 GIF 预览。"
                ))

        report_path = output_dir / "animation-report.json"
        report = {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "passed": not errors,
            "expected_states": list(animations),
            "summary": {
                "errors": len(errors),
                "warnings": len(warnings),
                "checked_states": len(checks),
            },
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
            "artifacts": {"report": str(report_path)},
        }
        self._atomic_json(report_path, report)
        return report

    @staticmethod
    def _issue(code: str, state: str, message: str):
        return {"code": code, "state": state, "message": message}

    @staticmethod
    def _atomic_json(destination: Path, value: Dict) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".animation-report-",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
