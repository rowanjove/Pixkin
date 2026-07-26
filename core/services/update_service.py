"""Signed update manifest validation and bounded installer downloads."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import load_pem_public_key


class UpdateError(ValueError):
    pass


class UpdateSecurityError(UpdateError):
    pass


@dataclass(frozen=True)
class UpdateArtifact:
    kind: str
    filename: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    channel: str
    published_at: str
    minimum_compatible_version: str
    release_notes_url: str
    artifact: UpdateArtifact
    manifest: dict[str, Any]


def canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def parse_version(version: str) -> tuple[int, int, int]:
    pieces = version.split(".")
    if len(pieces) != 3 or any(
        not piece.isdigit() or (len(piece) > 1 and piece.startswith("0"))
        for piece in pieces
    ):
        raise UpdateError(f"无效版本号：{version}")
    return tuple(int(piece) for piece in pieces)  # type: ignore[return-value]


def load_install_metadata(
    executable: str | Path | None = None,
) -> dict[str, Any]:
    target = Path(executable or sys.executable).resolve()
    marker = target.parent / "install-mode.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def detect_install_mode(executable: str | Path | None = None) -> str:
    data = load_install_metadata(executable)
    if (
        data.get("mode") == "installed"
        and data.get("app_id") == "Pixkin.Desktop"
    ):
        return "installed"
    return "portable"


def installed_manifest_urls(
    executable: str | Path | None = None,
) -> dict[str, str]:
    data = load_install_metadata(executable)
    urls = data.get("update_manifest_urls")
    if not isinstance(urls, dict):
        return {}
    result = {}
    for channel in ("stable", "beta"):
        value = urls.get(channel)
        if not value:
            continue
        try:
            result[channel] = UpdateManifestVerifier._https_url(str(value))
        except UpdateSecurityError:
            continue
    return result


class UpdateManifestVerifier:
    _SIGNED_KEYS = {
        "version",
        "channel",
        "published_at",
        "minimum_compatible_version",
        "release_notes_url",
        "artifacts",
    }
    _ARTIFACT_KEYS = {"kind", "filename", "size", "sha256", "url"}

    def __init__(self, public_key_pem: bytes):
        key = load_pem_public_key(public_key_pem)
        if not isinstance(key, Ed25519PublicKey):
            raise UpdateSecurityError("更新公钥不是 Ed25519 公钥")
        self.public_key = key

    def verify(
        self,
        manifest_data: bytes | str | dict[str, Any],
        *,
        expected_channel: str,
        current_version: str,
    ) -> UpdateRelease:
        manifest = self._load(manifest_data)
        if set(manifest) != {"schema_version", "signed", "signature"}:
            raise UpdateSecurityError("更新清单顶层字段不符合白名单")
        if manifest["schema_version"] != 1:
            raise UpdateSecurityError("更新清单版本不兼容")
        signed = manifest["signed"]
        if not isinstance(signed, dict) or set(signed) != self._SIGNED_KEYS:
            raise UpdateSecurityError("更新清单签名载荷字段不符合白名单")
        try:
            signature = base64.b64decode(
                manifest["signature"],
                validate=True,
            )
            self.public_key.verify(signature, canonical_payload(signed))
        except (InvalidSignature, TypeError, ValueError) as exc:
            raise UpdateSecurityError("更新清单签名无效") from exc

        channel = signed["channel"]
        if channel not in {"stable", "beta"} or channel != expected_channel:
            raise UpdateSecurityError("更新渠道与当前设置不一致")
        version = self._required_string(signed, "version")
        minimum = self._required_string(
            signed,
            "minimum_compatible_version",
        )
        current_tuple = parse_version(current_version)
        target_tuple = parse_version(version)
        if target_tuple <= current_tuple:
            raise UpdateError("没有比当前版本更新的可用版本")
        if current_tuple < parse_version(minimum):
            raise UpdateError("当前版本过旧，需要手动安装完整安装包")
        published_at = self._required_string(signed, "published_at")
        self._parse_time(published_at)
        notes_url = self._https_url(
            self._required_string(signed, "release_notes_url")
        )
        artifacts = signed["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            raise UpdateSecurityError("更新清单缺少安装器")
        installer = None
        for item in artifacts:
            if not isinstance(item, dict) or set(item) != self._ARTIFACT_KEYS:
                raise UpdateSecurityError("更新产物字段不符合白名单")
            if item.get("kind") == "installer":
                if installer is not None:
                    raise UpdateSecurityError("更新清单包含多个安装器")
                installer = self._artifact(item)
        if installer is None:
            raise UpdateSecurityError("更新清单没有安装器产物")
        return UpdateRelease(
            version=version,
            channel=channel,
            published_at=published_at,
            minimum_compatible_version=minimum,
            release_notes_url=notes_url,
            artifact=installer,
            manifest=manifest,
        )

    @staticmethod
    def _load(data: bytes | str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(data, dict):
            return data
        try:
            parsed = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateSecurityError("更新清单不是有效 JSON") from exc
        if not isinstance(parsed, dict):
            raise UpdateSecurityError("更新清单根节点必须是对象")
        return parsed

    @staticmethod
    def _required_string(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise UpdateSecurityError(f"更新清单字段无效：{key}")
        return value

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise UpdateSecurityError("更新时间格式无效") from exc
        if parsed.tzinfo is None:
            raise UpdateSecurityError("更新时间必须包含时区")
        return parsed

    @staticmethod
    def _https_url(value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise UpdateSecurityError("更新地址必须是无凭据的 HTTPS URL")
        return value

    def _artifact(self, data: dict[str, Any]) -> UpdateArtifact:
        filename = self._required_string(data, "filename")
        if (
            Path(filename).name != filename
            or not filename.lower().endswith(".exe")
        ):
            raise UpdateSecurityError("安装器文件名无效")
        size = data.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise UpdateSecurityError("安装器大小无效")
        digest = self._required_string(data, "sha256").lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise UpdateSecurityError("安装器 SHA-256 无效")
        return UpdateArtifact(
            kind="installer",
            filename=filename,
            size=size,
            sha256=digest,
            url=self._https_url(self._required_string(data, "url")),
        )


class UpdateService:
    MAX_MANIFEST_BYTES = 256 * 1024

    def __init__(
        self,
        verifier: UpdateManifestVerifier,
        cache_dir: str | Path,
        *,
        session: Any = None,
    ):
        self.verifier = verifier
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()
        self.rollback_dir = self.cache_dir.parent / "rollback"

    @staticmethod
    def automatic_check_due(
        last_checked_at: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        if not last_checked_at:
            return True
        try:
            previous = datetime.fromisoformat(
                last_checked_at.replace("Z", "+00:00")
            )
        except ValueError:
            return True
        if previous.tzinfo is None:
            return True
        current = now or datetime.now(timezone.utc)
        return current - previous >= timedelta(hours=24)

    def fetch_release(
        self,
        manifest_url: str,
        *,
        channel: str,
        current_version: str,
    ) -> UpdateRelease:
        UpdateManifestVerifier._https_url(manifest_url)
        content = bytearray()
        with self.session.get(
            manifest_url,
            stream=True,
            timeout=(5, 15),
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            UpdateManifestVerifier._https_url(response.url)
            declared = response.headers.get("content-length")
            if declared:
                try:
                    declared_size = int(declared)
                except (TypeError, ValueError) as exc:
                    raise UpdateSecurityError(
                        "更新清单响应大小无效"
                    ) from exc
                if (
                    declared_size < 0
                    or declared_size > self.MAX_MANIFEST_BYTES
                ):
                    raise UpdateSecurityError(
                        "更新清单超过 256 KiB 上限"
                    )
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if (
                    len(content) + len(chunk)
                    > self.MAX_MANIFEST_BYTES
                ):
                    raise UpdateSecurityError(
                        "更新清单超过 256 KiB 上限"
                    )
                content.extend(chunk)
        return self.verifier.verify(
            bytes(content),
            expected_channel=channel,
            current_version=current_version,
        )

    def download_installer(self, release: UpdateRelease) -> Path:
        artifact = release.artifact
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / artifact.filename
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{artifact.filename}.",
            suffix=".part",
            dir=self.cache_dir,
        )
        temporary_path = Path(temporary)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                with self.session.get(
                    artifact.url,
                    stream=True,
                    timeout=(5, 60),
                    allow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    UpdateManifestVerifier._https_url(response.url)
                    declared = response.headers.get("content-length")
                    if declared and int(declared) != artifact.size:
                        raise UpdateSecurityError("安装器响应大小与清单不一致")
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > artifact.size:
                            raise UpdateSecurityError("安装器超过清单声明大小")
                        digest.update(chunk)
                        handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size != artifact.size:
                raise UpdateSecurityError("安装器下载不完整")
            if digest.hexdigest() != artifact.sha256:
                raise UpdateSecurityError("安装器 SHA-256 校验失败")
            os.replace(temporary_path, target)
            self._write_receipt(release, target)
            return target
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def launch_installer(
        self,
        installer: str | Path,
        *,
        user_confirmed: bool,
        allow_downgrade: bool = False,
    ) -> subprocess.Popen:
        if not user_confirmed:
            raise UpdateSecurityError("安装更新必须由用户确认")
        target = Path(installer).resolve()
        if target.parent != self.cache_dir.resolve() or not target.is_file():
            raise UpdateSecurityError("只能启动已验证的更新缓存安装器")
        arguments = [str(target), "/CURRENTUSER", "/SP-", "/NORESTART"]
        if allow_downgrade:
            arguments.append("/ALLOWDOWNGRADE")
        return subprocess.Popen(arguments, close_fds=True)

    def available_rollback(
        self,
        *,
        current_version: str,
    ) -> Path | None:
        if not self.rollback_dir.is_dir():
            return None
        current = parse_version(current_version)
        candidates = []
        for path in self.rollback_dir.glob("Pixkin-Setup-*.exe"):
            match = re.fullmatch(
                r"Pixkin-Setup-(\d+\.\d+\.\d+)\.exe",
                path.name,
            )
            if match is None:
                continue
            try:
                version = parse_version(match.group(1))
                self._verify_rollback_file(path)
            except (OSError, UpdateError):
                continue
            if version < current:
                candidates.append((version, path))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def launch_rollback(
        self,
        installer: str | Path,
        *,
        user_confirmed: bool,
    ) -> subprocess.Popen:
        if not user_confirmed:
            raise UpdateSecurityError("回滚必须由用户确认")
        target = Path(installer).resolve()
        if target.parent != self.rollback_dir.resolve():
            raise UpdateSecurityError("回滚安装器不在受控目录")
        self._verify_rollback_file(target)
        return subprocess.Popen(
            [
                str(target),
                "/CURRENTUSER",
                "/SP-",
                "/NORESTART",
                "/ALLOWDOWNGRADE",
            ],
            close_fds=True,
        )

    @staticmethod
    def _verify_rollback_file(installer: Path) -> None:
        if not installer.is_file():
            raise UpdateSecurityError("回滚安装器不存在")
        digest_path = installer.with_suffix(installer.suffix + ".sha256")
        try:
            expected = digest_path.read_text(
                encoding="utf-8"
            ).strip().lower()
        except OSError as exc:
            raise UpdateSecurityError("回滚安装器缺少本地校验值") from exc
        if len(expected) != 64 or any(
            character not in "0123456789abcdef"
            for character in expected
        ):
            raise UpdateSecurityError("回滚安装器校验值无效")
        digest = hashlib.sha256()
        with installer.open("rb") as handle:
            for chunk in iter(
                lambda: handle.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise UpdateSecurityError("回滚安装器本地校验失败")

    def _write_receipt(
        self,
        release: UpdateRelease,
        installer: Path,
    ) -> None:
        receipt = {
            "schema_version": 1,
            "version": release.version,
            "channel": release.channel,
            "installer": installer.name,
            "size": release.artifact.size,
            "sha256": release.artifact.sha256,
            "verified_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "manifest": release.manifest,
        }
        target = self.cache_dir / f"{release.version}.receipt.json"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=self.cache_dir,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    receipt,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
