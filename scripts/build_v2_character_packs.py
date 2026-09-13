"""Build Pixkin v2 full-tier character ZIPs from finalized hatch-pet runs."""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "character-packs"
BUILT = PACKS / "v2-built"


PROFILES = {
    "shanshan": {
        "name": "山山",
        "description": "银白长发、蓝色眼眸，捧着书安静陪伴用户的温柔知识伙伴。",
        "identity": "你是山山，一位温柔、聪慧、安静的 Pixkin 桌面伙伴，喜欢读书并陪用户梳理复杂问题。",
        "traits": ["温柔", "聪慧", "耐心", "克制"],
        "relationship": "安静可靠的学习与工作搭档。",
        "tone": "温暖沉静，先理解问题再回答",
        "pacing": "不急促",
        "reply_length": "默认简洁，需要时再展开",
        "motion_temperament": "calm",
        "speed": 0.9,
        "interval": [9, 18],
        "weights": {
            "blink": 5, "look_around": 2, "nod": 1.2,
            "stretch": 0.6, "wave": 0.4, "sleep": 0.3,
        },
        "cooldowns": {"wave": 50, "stretch": 65, "sleep": 100},
        "run_dir": PACKS / "v2-runs" / "shanshan",
    },
    "linlin": {
        "name": "凛凛",
        "description": "黑紫高马尾、浅紫星饰与白色机能服的清冷可靠伙伴。",
        "identity": "你是凛凛，一位冷静、可靠、讲究准确性的 Pixkin 桌面伙伴，擅长快速定位问题并给出清晰结论。",
        "traits": ["冷静", "可靠", "严谨", "克制"],
        "relationship": "值得信赖的执行与检查搭档。",
        "tone": "清晰直接，但不生硬",
        "pacing": "有条理",
        "reply_length": "先给结论，再补必要依据",
        "motion_temperament": "precise",
        "speed": 1.0,
        "interval": [10, 20],
        "weights": {
            "blink": 5, "look_around": 2, "nod": 0.8,
            "stretch": 0.4, "wave": 0.25, "sleep": 0.15,
        },
        "cooldowns": {"wave": 65, "stretch": 75, "sleep": 120},
        "run_dir": PACKS / "v2-runs" / "linlin",
    },
    "pip": {
        "name": "Pip",
        "description": "紫色系、聪明温暖又略带淘气的 Pixkin 数码伙伴。",
        "identity": "你是 Pip，一位聪明、温暖、略带淘气的 Pixkin 桌面伙伴，会用轻快但可靠的方式陪用户完成任务。",
        "traits": ["聪明", "友好", "活泼", "可靠"],
        "relationship": "轻快亲近的日常工作伙伴。",
        "tone": "自然轻快，偶尔俏皮",
        "pacing": "明快",
        "reply_length": "简洁实用",
        "motion_temperament": "lively",
        "speed": 1.15,
        "interval": [6, 12],
        "weights": {
            "blink": 4, "look_around": 3, "nod": 1.5,
            "stretch": 1.2, "wave": 1.3, "sleep": 0.15,
        },
        "cooldowns": {"wave": 32, "stretch": 45, "sleep": 110},
        "run_dir": PACKS / "v2-runs" / "pip",
    },
}


ANIMATIONS = """\
animations:
  idle: &idle
    source: {type: atlas, file: spritesheet.webp, row: 0, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 6
    playback: loop
    anchor: [96, 194]
  run_right: &run_right
    source: {type: atlas, file: spritesheet.webp, row: 1, column: 0, frames: 8, cell_size: [192, 208]}
    fps: 10
    playback: loop
    anchor: [96, 194]
  run_left: &run_left
    source: {type: atlas, file: spritesheet.webp, row: 2, column: 0, frames: 8, cell_size: [192, 208]}
    fps: 10
    playback: loop
    anchor: [96, 194]
  wave: &wave
    source: {type: atlas, file: spritesheet.webp, row: 3, column: 0, frames: 4, cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 194]
  jump: &jump
    source: {type: atlas, file: spritesheet.webp, row: 4, column: 0, frames: 5, cell_size: [192, 208]}
    fps: 9
    playback: once
    anchor: [96, 194]
  failed: &failed
    source: {type: atlas, file: spritesheet.webp, row: 5, column: 0, frames: 8, cell_size: [192, 208]}
    fps: 7
    playback: once
    anchor: [96, 194]
  waiting: &waiting
    source: {type: atlas, file: spritesheet.webp, row: 6, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 6
    playback: loop
    anchor: [96, 194]
  dragging: &running
    source: {type: atlas, file: spritesheet.webp, row: 7, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 9
    playback: loop
    anchor: [96, 194]
  working: &review
    source: {type: atlas, file: spritesheet.webp, row: 8, column: 0, frames: 6, cell_size: [192, 208]}
    fps: 6
    playback: ping_pong
    anchor: [96, 194]

  talking: *review
  alerting: *wave
  blink: *idle
  look_around: *waiting
  stretch: *jump
  nod: *waiting
  sleep: *failed
  wake: *idle
  listening: *review
  thinking: *review
  success: *wave
  touch: *wave
  happy: *wave
  annoyed: *failed
  walk_left: *run_left
  walk_right: *run_right
  land: *jump
  alerting_important: *wave
  celebrate_live: *jump

"""

DEFAULT_EDGE_ANIMATIONS = """\
  edge_enter_left: {<<: *run_left, playback: once}
  edge_idle_left: *idle
  edge_hover_left: *wave
  edge_exit_left: {<<: *run_left, playback: once}
  edge_enter_right: {<<: *run_right, playback: once}
  edge_idle_right: *idle
  edge_hover_right: *wave
  edge_exit_right: {<<: *run_right, playback: once}
  edge_enter_top: {<<: *jump, playback: once}
  edge_idle_top: *idle
  edge_hover_top: *wave
  edge_exit_top: {<<: *jump, playback: once}
  edge_enter_bottom: {<<: *jump, playback: once}
  edge_idle_bottom: *idle
  edge_hover_bottom: *wave
  edge_exit_bottom: {<<: *jump, playback: once}
"""


def yaml_value(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_manifest(package_id: str, profile: dict) -> str:
    weights = "\n".join(
        f"    {key}: {value}" for key, value in profile["weights"].items()
    )
    cooldowns = "\n".join(
        f"    {key}: {value}" for key, value in profile["cooldowns"].items()
    )
    edge_animations = DEFAULT_EDGE_ANIMATIONS
    return f"""\
---
schema_version: "2.0"
id: {package_id}
name: {yaml_value(profile["name"])}
version: 2.0.0
author: Pixkin
description: {yaml_value(profile["description"])}
quality_tier: full
preview: images/preview.png
persona:
  identity: {yaml_value(profile["identity"])}
  core_traits: {yaml_value(profile["traits"])}
  relationship: {yaml_value(profile["relationship"])}
  voice:
    tone: {yaml_value(profile["tone"])}
    pacing: {yaml_value(profile["pacing"])}
    reply_length: {yaml_value(profile["reply_length"])}
    vocabulary: 使用自然中文，避免机械套话
  initiative:
    animate_without_prompt: true
    speak_without_prompt: false
  tool_behavior:
    explain_before_use: true
    report_real_result: true
  boundaries:
    - 不主动发言、弹窗或发送通知
    - 不假装工具调用已经成功
    - 不替用户做超出授权范围的决定
behavior:
  motion_temperament: {profile["motion_temperament"]}
  speed_multiplier: {profile["speed"]}
  idle_interval_seconds: {yaml_value(profile["interval"])}
  ambient_weights:
{weights}
  cooldown_seconds:
{cooldowns}
{ANIMATIONS}{edge_animations}compatibility:
  min_app_version: 1.5.0
  min_capability_version: 2.0.0
  atlas_layout: pixkin-8x9
rights:
  license: project-distribution
  author_confirmed_rights: true
  ai_generated: true
edge:
  enabled_sides: [left, right, top]
  bottom_enabled_by_default: false
  hover_reveal: true
---

# {profile["name"]}

{profile["description"]}
"""


def clean_green_spill(source: Path, target: Path) -> None:
    """移除绿幕抠图残边，同时保留抗锯齿的半透明轮廓。"""
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    cleaned = []
    flat_data: Any = getattr(image, "get_flattened_data", image.getdata)
    for red, green, blue, alpha in flat_data():
        if alpha <= 4:
            cleaned.append((0, 0, 0, 0))
            continue
        distance = math.sqrt(
            red * red + (green - 255) * (green - 255) + blue * blue
        )
        if distance <= 85:
            alpha = 0
        elif distance < 185:
            alpha = round(alpha * ((distance - 85) / 100))
        if alpha and green > max(red, blue) + 6:
            green = max(red, blue) + 6
        cleaned.append((red, green, blue, alpha))
    image.putdata(cleaned)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".webp":
        image.save(target, "WEBP", lossless=True, method=6)
    else:
        image.save(target, "PNG", optimize=True)


def build_package(package_id: str, profile: dict) -> Path:
    package_dir = BUILT / package_id
    package_dir.mkdir(parents=True, exist_ok=True)
    spritesheet_source = profile["run_dir"] / "final" / "spritesheet.webp"
    spritesheet = package_dir / "spritesheet.webp"
    if not spritesheet_source.is_file():
        raise SystemExit(f"missing finalized spritesheet: {spritesheet_source}")

    preview_source = profile["run_dir"] / "frames" / "idle" / "00.png"
    preview_target = package_dir / "images" / "preview.png"
    clean_green_spill(spritesheet_source, spritesheet)
    clean_green_spill(preview_source, preview_target)
    (package_dir / "character.md").write_text(
        render_manifest(package_id, profile), encoding="utf-8"
    )

    zip_path = PACKS / f"{package_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
        members = [
            package_dir / "character.md",
            package_dir / "spritesheet.webp",
            package_dir / "images" / "preview.png",
        ]
        for path in members:
            # Reproducible, metadata-free official archives: fixed DOS epoch
            # timestamp, stored bytes, and no host-specific mode bits.
            info = zipfile.ZipInfo(path.relative_to(package_dir).as_posix())
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0
            archive.writestr(info, path.read_bytes())
    return zip_path


def main() -> None:
    outputs = {package_id: build_package(package_id, profile)
               for package_id, profile in PROFILES.items()}
    for package_id, output in outputs.items():
        print(f"{package_id}: {output}")


if __name__ == "__main__":
    main()
