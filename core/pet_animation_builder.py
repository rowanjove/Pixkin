"""Build deterministic micro-animation frames from approved key poses."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from PIL import Image, ImageDraw


CANVAS_SIZE = (192, 208)
ANCHOR = (96, 194)


ANIMATION_PROFILES = {
    "idle": {
        "fps": 6,
        "playback": "loop",
        "transforms": (
            (1.00, 1.00, 0, 0),
            (1.01, 0.99, -1, 1),
            (1.00, 0.98, 0, 2),
            (0.99, 0.99, 1, 1),
        ),
    },
    "talking": {
        "fps": 8,
        "playback": "loop",
        "transforms": (
            (1.00, 1.00, 0, 0),
            (1.02, 0.98, -1, 1),
            (0.99, 1.01, 0, 1),
            (1.01, 0.99, 1, 2),
        ),
    },
    "dragging": {
        "fps": 8,
        "playback": "loop",
        "transforms": (
            (1.00, 1.00, -2, 0),
            (1.00, 0.99, 0, 1),
            (1.00, 1.00, 2, 0),
            (1.01, 0.99, 0, 1),
        ),
    },
    "alerting": {
        "fps": 9,
        "playback": "once",
        "transforms": (
            (1.00, 1.00, 0, 0),
            (0.99, 1.01, -1, 3),
            (1.00, 1.00, 0, 4),
            (0.99, 1.01, 1, 3),
            (1.01, 0.99, 0, 1),
        ),
    },
    "alerting_important": {
        "fps": 10,
        "playback": "once",
        "transforms": (
            (1.00, 1.00, 0, 0),
            (0.99, 1.01, -2, 4),
            (1.00, 1.00, 0, 7),
            (0.99, 1.01, 2, 4),
            (1.01, 0.99, 0, 1),
        ),
    },
    "celebrate_live": {
        "fps": 10,
        "playback": "once",
        "transforms": (
            (1.00, 1.00, 0, 0),
            (0.99, 1.01, -2, 5),
            (1.00, 1.00, 0, 9),
            (0.99, 1.01, 2, 5),
            (1.01, 0.99, 0, 1),
        ),
    },
    "walk_left": {
        "fps": 8,
        "playback": "loop",
        "transforms": (
            (1.00, 1.00, 2, 0),
            (1.01, 0.99, 1, 2),
            (1.00, 1.00, 0, 0),
            (0.99, 1.01, -1, 1),
            (1.00, 1.00, -2, 0),
            (1.01, 0.99, 0, 2),
        ),
    },
    "walk_right": {
        "fps": 8,
        "playback": "loop",
        "transforms": (
            (1.00, 1.00, -2, 0),
            (1.01, 0.99, -1, 2),
            (1.00, 1.00, 0, 0),
            (0.99, 1.01, 1, 1),
            (1.00, 1.00, 2, 0),
            (1.01, 0.99, 0, 2),
        ),
    },
    "run_left": {
        "fps": 11,
        "playback": "loop",
        "transforms": (
            (1.00, 1.00, 4, 0),
            (1.02, 0.98, 2, 3),
            (1.00, 1.00, 0, 1),
            (0.98, 1.02, -2, 3),
            (1.00, 1.00, -4, 0),
            (1.02, 0.98, 0, 3),
        ),
    },
    "run_right": {
        "fps": 11,
        "playback": "loop",
        "transforms": (
            (1.00, 1.00, -4, 0),
            (1.02, 0.98, -2, 3),
            (1.00, 1.00, 0, 1),
            (0.98, 1.02, 2, 3),
            (1.00, 1.00, 4, 0),
            (1.02, 0.98, 0, 3),
        ),
    },
    "jump": {
        "fps": 10,
        "playback": "once",
        "transforms": (
            (1.02, 0.98, 0, 0),
            (1.00, 1.00, -1, 6),
            (0.99, 1.01, 0, 12),
            (1.00, 1.00, 1, 6),
            (1.02, 0.98, 0, 1),
        ),
    },
    "land": {
        "fps": 10,
        "playback": "once",
        "transforms": (
            (1.00, 1.00, 0, 5),
            (1.03, 0.97, 0, 0),
            (1.01, 0.99, 0, 1),
            (1.00, 1.00, 0, 0),
        ),
    },
}

DEFAULT_PROFILE = {
    "fps": 8,
    "playback": "once",
    "transforms": (
        (1.00, 1.00, 0, 0),
        (1.01, 0.99, -1, 1),
        (0.99, 1.01, 0, 2),
        (1.00, 0.99, 1, 1),
    ),
}

LOOP_STATES = {
    "idle",
    "sleep",
    "talking",
    "dragging",
    "listening",
    "thinking",
    "working",
    "waiting",
    "walk_left",
    "walk_right",
    "run_left",
    "run_right",
    *{
        f"edge_{phase}_{side}"
        for phase in ("idle", "hover")
        for side in ("left", "right", "top", "bottom")
    },
}


def _edge_profile(state: str):
    parts = state.split("_")
    if len(parts) != 3 or parts[0] != "edge":
        return None
    _, phase, side = parts
    phase_amounts = {
        "enter": (8, 5, 2, 0),
        "idle": (1, 0, 1, 0),
        "hover": (0, -2, -4, -2),
        "exit": (0, 2, 5, 8),
    }
    amounts = phase_amounts.get(phase)
    if amounts is None or side not in {"left", "right", "top", "bottom"}:
        return None
    scales = (
        (1.00, 1.00),
        (1.01, 0.99),
        (1.00, 1.00),
        (0.99, 1.01),
    )
    transforms = []
    for amount, (scale_x, scale_y) in zip(amounts, scales):
        if side == "left":
            offset_x, lift = -amount, 0
        elif side == "right":
            offset_x, lift = amount, 0
        elif side == "top":
            offset_x, lift = 0, amount
        else:
            offset_x, lift = 0, -amount
        transforms.append((scale_x, scale_y, offset_x, lift))
    return {
        "fps": 8,
        "playback": "loop" if phase in {"idle", "hover"} else "once",
        "transforms": tuple(transforms),
    }


class PetAnimationBuilder:
    """Turn approved static poses into conservative frame sequences."""

    def build(
        self,
        *,
        images: Dict[str, Path],
        output_dir: Path,
        preview_dir: Path,
    ) -> Dict:
        output_dir = Path(output_dir)
        preview_dir = Path(preview_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        preview_dir.mkdir(parents=True, exist_ok=True)
        animations = {}
        previews = {}
        for state, source_path in images.items():
            profile = dict(
                _edge_profile(state)
                or ANIMATION_PROFILES.get(state, DEFAULT_PROFILE)
            )
            if state in LOOP_STATES:
                profile["playback"] = "loop"
            state_dir = output_dir / state
            state_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(source_path) as source:
                key_pose = source.convert("RGBA")
            frames = []
            for index, transform in enumerate(
                profile["transforms"], start=1
            ):
                frame = self._transform(key_pose, *transform)
                frame_path = state_dir / f"{state}-{index:02d}.png"
                frame.save(frame_path, "PNG", optimize=True)
                frames.append(frame_path)
            preview_path = preview_dir / f"{state}.gif"
            self._save_preview(
                frames,
                preview_path,
                fps=int(profile["fps"]),
                loop=profile["playback"] == "loop",
            )
            animations[state] = {
                "frames": frames,
                "fps": int(profile["fps"]),
                "playback": str(profile["playback"]),
            }
            previews[state] = preview_path
        return {
            "animations": animations,
            "previews": previews,
        }

    @staticmethod
    def _transform(
        image: Image.Image,
        scale_x: float,
        scale_y: float,
        offset_x: int,
        lift: int,
    ) -> Image.Image:
        bbox = image.getchannel("A").getbbox()
        if not bbox:
            raise RuntimeError("关键姿态中没有可见角色。")
        subject = image.crop(bbox)
        width = max(1, round(subject.width * float(scale_x)))
        height = max(1, round(subject.height * float(scale_y)))
        subject = subject.resize(
            (width, height), Image.Resampling.LANCZOS
        )
        canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        x = ANCHOR[0] - width // 2 + int(offset_x)
        y = ANCHOR[1] - height - int(lift)
        canvas.alpha_composite(subject, (x, y))
        return canvas

    @staticmethod
    def _save_preview(
        frames,
        destination: Path,
        *,
        fps: int,
        loop: bool,
    ) -> None:
        rendered = []
        for frame_path in frames:
            with Image.open(frame_path) as source:
                frame = source.convert("RGBA")
            background = Image.new(
                "RGBA", CANVAS_SIZE, (29, 38, 56, 255)
            )
            draw = ImageDraw.Draw(background)
            for y in range(0, CANVAS_SIZE[1], 16):
                for x in range(0, CANVAS_SIZE[0], 16):
                    if (x // 16 + y // 16) % 2:
                        draw.rectangle(
                            (x, y, x + 15, y + 15),
                            fill=(43, 53, 73, 255),
                        )
            background.alpha_composite(frame)
            rendered.append(background.convert("P", palette=Image.Palette.ADAPTIVE))
        duration = max(34, round(1000 / max(1, fps)))
        options = {
            "save_all": True,
            "append_images": rendered[1:],
            "duration": duration,
            "disposal": 2,
        }
        if loop:
            options["loop"] = 0
        rendered[0].save(destination, "GIF", **options)
