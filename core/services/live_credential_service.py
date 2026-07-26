"""Credential-manager isolation for livestream provider cookies."""

import hashlib

from core.secrets import SecretStore


class LiveCredentialStore:
    """Store one credential per platform/room; never serialize it in config."""

    @staticmethod
    def target(platform: str, room_id: str) -> str:
        scope = f"{str(platform).lower()}:{str(room_id).strip()}"
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]
        return f"Pixkin/Live/{str(platform).lower()}/{digest}"

    @classmethod
    def get_cookie(cls, platform: str, room_id: str) -> str:
        return SecretStore._read(cls.target(platform, room_id))

    @classmethod
    def set_cookie(
        cls,
        platform: str,
        room_id: str,
        value: str,
    ) -> bool:
        return SecretStore._write(
            cls.target(platform, room_id),
            value,
            f"Pixkin {platform} 直播间独立凭据",
        )
