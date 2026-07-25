"""Automatic visual and geometry checks for Pet Lab generation runs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, Optional

from PIL import Image, ImageDraw


SPRITE_SIZE = (192, 208)
SAFE_MARGIN = 4
BASELINE_Y = 194


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PetGenerationQa:
    """Validate generated sprites and emit durable review artifacts."""

    def run(
        self,
        *,
        images: Dict[str, Path],
        expected_states: Iterable[str],
        output_dir: Path,
        canonical: Optional[Path] = None,
        report_name: str = "report.json",
        sheet_name: str = "contact-sheet.png",
    ) -> Dict:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        expected = list(dict.fromkeys(str(state) for state in expected_states))
        errors = []
        warnings = []
        checks = {}
        hashes = {}
        heights = []

        for state in expected:
            path = Path(images.get(state, ""))
            state_checks = {}
            checks[state] = state_checks
            if not path.is_file():
                errors.append(self._issue(
                    "missing_image", state, "缺少动作图片。"
                ))
                continue
            try:
                with Image.open(path) as source:
                    source.load()
                    image = source.convert("RGBA")
            except Exception as exc:
                errors.append(self._issue(
                    "invalid_image", state, f"图片无法解码：{exc}"
                ))
                continue

            state_checks["size"] = list(image.size)
            if image.size != SPRITE_SIZE:
                errors.append(self._issue(
                    "invalid_size",
                    state,
                    f"画布必须为 {SPRITE_SIZE[0]} × {SPRITE_SIZE[1]}。",
                ))

            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            state_checks["alpha_bbox"] = list(bbox) if bbox else None
            if not bbox:
                errors.append(self._issue(
                    "empty_sprite", state, "图片中没有可见角色。"
                ))
                continue

            left, top, right, bottom = bbox
            heights.append((state, bottom - top))
            touches_edge = (
                left < SAFE_MARGIN
                or top < SAFE_MARGIN
                or right > image.width - SAFE_MARGIN
                or bottom > image.height - SAFE_MARGIN
            )
            state_checks["safe_margin"] = not touches_edge
            if touches_edge:
                errors.append(self._issue(
                    "unsafe_margin", state, "角色触碰了画布安全区边缘。"
                ))

            baseline_delta = abs(bottom - BASELINE_Y)
            state_checks["baseline_delta"] = baseline_delta
            if baseline_delta > 20:
                warnings.append(self._issue(
                    "baseline_drift",
                    state,
                    f"角色底部距离基准线 {baseline_delta} 像素。",
                ))

            opaque = 0
            green_risk = 0
            flat_data = getattr(
                image, "get_flattened_data", image.getdata
            )
            for red, green, blue, opacity in flat_data():
                if opacity <= 32:
                    continue
                opaque += 1
                if green > red + 45 and green > blue + 45:
                    green_risk += 1
            state_checks["opaque_pixels"] = opaque
            state_checks["green_risk_pixels"] = green_risk
            if green_risk > max(12, int(opaque * 0.01)):
                warnings.append(self._issue(
                    "green_spill",
                    state,
                    "检测到较多高饱和绿色像素，请人工检查色键残留。",
                ))

            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            state_checks["sha256"] = digest
            hashes.setdefault(digest, []).append(state)

        for duplicate_states in hashes.values():
            if len(duplicate_states) > 1:
                errors.append({
                    "code": "duplicate_actions",
                    "states": duplicate_states,
                    "message": (
                        "不同语义动作使用了完全相同的图片："
                        + "、".join(duplicate_states)
                    ),
                })

        if heights:
            typical_height = median(height for _, height in heights)
            for state, height in heights:
                if typical_height and (
                    height < typical_height * 0.62
                    or height > typical_height * 1.55
                ):
                    warnings.append(self._issue(
                        "scale_drift",
                        state,
                        "角色可见高度与其他动作差异较大。",
                    ))

        sheet_path = output_dir / sheet_name
        self._contact_sheet(
            images=images,
            states=expected,
            destination=sheet_path,
            canonical=canonical,
        )
        report_path = output_dir / report_name
        report = {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "passed": not errors,
            "expected_states": expected,
            "summary": {
                "errors": len(errors),
                "warnings": len(warnings),
                "checked_states": len(checks),
            },
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
            "artifacts": {
                "contact_sheet": str(sheet_path),
                "report": str(report_path),
            },
        }
        self._atomic_json(report_path, report)
        return report

    @staticmethod
    def _issue(code: str, state: str, message: str) -> Dict[str, str]:
        return {"code": code, "state": state, "message": message}

    @staticmethod
    def _atomic_json(destination: Path, value: Dict) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.stem}-",
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

    @staticmethod
    def _contact_sheet(
        *,
        images: Dict[str, Path],
        states,
        destination: Path,
        canonical: Optional[Path],
    ) -> None:
        entries = []
        if canonical and Path(canonical).is_file():
            entries.append(("canonical", Path(canonical)))
        entries.extend(
            (state, Path(images[state]))
            for state in states
            if state in images and Path(images[state]).is_file()
        )
        columns = min(4, max(1, len(entries)))
        rows = max(1, (len(entries) + columns - 1) // columns)
        cell_width = 216
        cell_height = 244
        sheet = Image.new(
            "RGBA",
            (columns * cell_width, rows * cell_height),
            (23, 31, 48, 255),
        )
        draw = ImageDraw.Draw(sheet)
        for index, (label, path) in enumerate(entries):
            column = index % columns
            row = index // columns
            origin_x = column * cell_width
            origin_y = row * cell_height
            for y in range(0, SPRITE_SIZE[1], 16):
                for x in range(0, SPRITE_SIZE[0], 16):
                    tone = 54 if (x // 16 + y // 16) % 2 else 68
                    draw.rectangle(
                        (
                            origin_x + 12 + x,
                            origin_y + 12 + y,
                            origin_x + 27 + x,
                            origin_y + 27 + y,
                        ),
                        fill=(tone, tone + 5, tone + 14, 255),
                    )
            with Image.open(path) as source:
                frame = source.convert("RGBA")
            sheet.alpha_composite(frame, (origin_x + 12, origin_y + 12))
            draw.text(
                (origin_x + 12, origin_y + 224),
                label,
                fill=(232, 237, 247, 255),
            )
        sheet.save(destination, "PNG", optimize=True)
