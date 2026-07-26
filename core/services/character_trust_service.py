"""Trust metadata and hash verification for bundled character archives."""

import hashlib
import hmac
import json
from pathlib import Path


class OfficialCharacterTrustError(ValueError):
    pass


class OfficialCharacterTrustStore:
    """Verify official archives against an immutable bundled SHA-256 list."""

    SCHEMA_VERSION = 1

    def __init__(self, manifest_path: str | Path):
        self.manifest_path = Path(manifest_path)
        self._hashes = self._load()

    def expected_hash(self, archive_name: str) -> str | None:
        return self._hashes.get(str(archive_name))

    def verify(self, archive_name: str, archive: str | Path) -> bool:
        expected = self.expected_hash(archive_name)
        if expected is None:
            return False
        return hmac.compare_digest(self.digest(archive), expected)

    def _load(self) -> dict[str, str]:
        try:
            payload = json.loads(
                self.manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise OfficialCharacterTrustError(
                "官方角色哈希清单无法读取"
            ) from exc
        if payload.get("schema_version") != self.SCHEMA_VERSION:
            raise OfficialCharacterTrustError(
                "官方角色哈希清单版本不兼容"
            )
        archives = payload.get("archives")
        if not isinstance(archives, dict) or not archives:
            raise OfficialCharacterTrustError(
                "官方角色哈希清单为空"
            )
        hashes = {}
        for name, digest in archives.items():
            normalized = str(digest).lower()
            if (
                not str(name).lower().endswith(".zip")
                or len(normalized) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in normalized
                )
            ):
                raise OfficialCharacterTrustError(
                    "官方角色哈希清单包含无效条目"
                )
            hashes[str(name)] = normalized
        return hashes

    @staticmethod
    def digest(archive: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(archive).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
