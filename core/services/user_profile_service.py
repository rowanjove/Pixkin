"""Durable user-profile asset operations kept outside Qt windows."""

import hashlib
import shutil
from pathlib import Path

from PIL import Image, UnidentifiedImageError


SUPPORTED_AVATAR_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class UserProfileError(ValueError):
    """Raised when a profile asset cannot be validated or persisted."""


class UserProfileService:
    """Validate and copy user-owned profile assets into application storage."""

    @staticmethod
    def persist_avatar(source_path, profile_dir) -> str:
        if not source_path:
            return ""
        source = Path(source_path)
        if not source.is_file():
            raise UserProfileError("所选头像已不存在，请重新选择。")
        try:
            with Image.open(source) as image:
                image.verify()
            content = source.read_bytes()
        except (OSError, UnidentifiedImageError) as exc:
            raise UserProfileError("所选头像无法读取，请重新选择。") from exc

        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_AVATAR_SUFFIXES:
            suffix = ".png"
        digest = hashlib.sha256(content).hexdigest()[:12]
        destination = Path(profile_dir) / f"user-avatar-{digest}{suffix}"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except OSError as exc:
            raise UserProfileError(
                f"无法复制头像到本地资料目录：{exc}"
            ) from exc
        return str(destination)
