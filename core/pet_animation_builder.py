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

LOOP_STATES = {"idle", "sleep", "talking", "dragging"}


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
            profile = dict(ANIMATION_PROFILES.get(
                state, DEFAULT_PROFILE
            ))
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
