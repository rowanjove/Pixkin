import base64
import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from core.services.update_service import (
    UpdateError,
    UpdateManifestVerifier,
    UpdateSecurityError,
    UpdateService,
    canonical_payload,
    detect_install_mode,
    installed_manifest_urls,
)


def signed_manifest(private_key, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "signed": payload,
        "signature": base64.b64encode(
            private_key.sign(canonical_payload(payload))
        ).decode("ascii"),
    }


@pytest.fixture
def update_fixture():
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        Encoding.PEM,
        PublicFormat.SubjectPublicKeyInfo,
    )
    installer = b"verified-installer"
    payload = {
        "version": "1.4.0",
        "channel": "stable",
        "published_at": "2026-07-26T00:00:00Z",
        "minimum_compatible_version": "1.3.0",
        "release_notes_url": "https://updates.example/releases/1.4.0",
        "artifacts": [
            {
                "kind": "installer",
                "filename": "Pixkin-Setup-1.4.0.exe",
                "size": len(installer),
                "sha256": hashlib.sha256(installer).hexdigest(),
                "url": "https://updates.example/Pixkin-Setup-1.4.0.exe",
            }
        ],
    }
    return (
        private_key,
        UpdateManifestVerifier(public_pem),
        payload,
        installer,
    )


def test_valid_signed_manifest_is_accepted(update_fixture):
    key, verifier, payload, _ = update_fixture
    release = verifier.verify(
        signed_manifest(key, payload),
        expected_channel="stable",
        current_version="1.3.0",
    )
    assert release.version == "1.4.0"
    assert release.artifact.kind == "installer"


def test_oversized_installer_is_rejected(update_fixture):
    key, verifier, payload, _ = update_fixture
    oversized = json.loads(json.dumps(payload))
    oversized["artifacts"][0]["size"] = (
        UpdateManifestVerifier.MAX_ARTIFACT_BYTES + 1
    )
    with pytest.raises(UpdateSecurityError, match="512 MiB"):
        verifier.verify(
            signed_manifest(key, oversized),
            expected_channel="stable",
            current_version="1.3.0",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest["signed"].update(version="9.9.9"),
        lambda manifest: manifest.update(signature="AAAA"),
        lambda manifest: manifest["signed"]["artifacts"][0].update(
            sha256="0" * 64
        ),
    ],
)
def test_tampered_manifest_is_rejected(update_fixture, mutation):
    key, verifier, payload, _ = update_fixture
    manifest = signed_manifest(key, payload)
    mutation(manifest)
    with pytest.raises(UpdateSecurityError):
        verifier.verify(
            manifest,
            expected_channel="stable",
            current_version="1.3.0",
        )


def test_channel_downgrade_and_minimum_version_are_rejected(update_fixture):
    key, verifier, payload, _ = update_fixture
    with pytest.raises(UpdateSecurityError):
        verifier.verify(
            signed_manifest(key, payload),
            expected_channel="beta",
            current_version="1.3.0",
        )
    old_payload = dict(payload, version="1.2.0")
    with pytest.raises(UpdateError):
        verifier.verify(
            signed_manifest(key, old_payload),
            expected_channel="stable",
            current_version="1.3.0",
        )
    incompatible = dict(payload, minimum_compatible_version="1.3.1")
    with pytest.raises(UpdateError):
        verifier.verify(
            signed_manifest(key, incompatible),
            expected_channel="stable",
            current_version="1.3.0",
        )


def test_unknown_fields_and_non_https_urls_are_rejected(update_fixture):
    key, verifier, payload, _ = update_fixture
    unknown = dict(payload, command="powershell.exe")
    with pytest.raises(UpdateSecurityError):
        verifier.verify(
            signed_manifest(key, unknown),
            expected_channel="stable",
            current_version="1.3.0",
        )
    insecure = json.loads(json.dumps(payload))
    insecure["artifacts"][0]["url"] = "http://updates.example/setup.exe"
    with pytest.raises(UpdateSecurityError):
        verifier.verify(
            signed_manifest(key, insecure),
            expected_channel="stable",
            current_version="1.3.0",
        )


class FakeDownloadResponse:
    def __init__(self, content: bytes, url: str):
        self.content = content
        self.url = url
        self.headers = {"content-length": str(len(content))}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


def test_manifest_fetch_is_streamed_and_size_bounded(update_fixture):
    key, verifier, payload, _ = update_fixture
    manifest = json.dumps(
        signed_manifest(key, payload)
    ).encode("utf-8")
    response = FakeDownloadResponse(
        manifest,
        "https://updates.example/update-stable.json",
    )
    session = FakeSession([response])
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(
            verifier,
            directory,
            session=session,
        )
        release = service.fetch_release(
            "https://updates.example/update-stable.json",
            channel="stable",
            current_version="1.3.0",
        )

    assert release.version == "1.4.0"
    assert session.calls[0][1]["stream"] is True

    oversized = FakeDownloadResponse(
        b"x" * (UpdateService.MAX_MANIFEST_BYTES + 1),
        "https://updates.example/update-stable.json",
    )
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(
            verifier,
            directory,
            session=FakeSession([oversized]),
        )
        with pytest.raises(
            UpdateSecurityError,
            match="256 KiB",
        ):
            service.fetch_release(
                "https://updates.example/update-stable.json",
                channel="stable",
                current_version="1.3.0",
            )


def test_download_is_atomic_hash_verified_and_receipted(update_fixture):
    key, verifier, payload, installer = update_fixture
    release = verifier.verify(
        signed_manifest(key, payload),
        expected_channel="stable",
        current_version="1.3.0",
    )
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(
            verifier,
            directory,
            session=FakeSession(
                [FakeDownloadResponse(installer, release.artifact.url)]
            ),
        )
        result = service.download_installer(release)
        assert result.read_bytes() == installer
        assert (Path(directory) / "1.4.0.receipt.json").is_file()
        assert not list(Path(directory).glob("*.part"))


def test_download_rejects_hash_or_redirect_downgrade(update_fixture):
    key, verifier, payload, installer = update_fixture
    release = verifier.verify(
        signed_manifest(key, payload),
        expected_channel="stable",
        current_version="1.3.0",
    )
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(
            verifier,
            directory,
            session=FakeSession(
                [
                    FakeDownloadResponse(
                        b"x" * len(installer),
                        release.artifact.url,
                    )
                ]
            ),
        )
        with pytest.raises(UpdateSecurityError):
            service.download_installer(release)
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(
            verifier,
            directory,
            session=FakeSession(
                [FakeDownloadResponse(installer, "http://evil.example/a.exe")]
            ),
        )
        with pytest.raises(UpdateSecurityError):
            service.download_installer(release)


def test_install_requires_confirmation_and_verified_cache(update_fixture):
    key, verifier, payload, installer = update_fixture
    release = verifier.verify(
        signed_manifest(key, payload),
        expected_channel="stable",
        current_version="1.3.0",
    )
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(
            verifier,
            directory,
            session=FakeSession(
                [FakeDownloadResponse(installer, release.artifact.url)]
            ),
        )
        target = service.download_installer(release)
        with pytest.raises(UpdateSecurityError):
            service.launch_installer(target, user_confirmed=False)
        with mock.patch("subprocess.Popen") as popen:
            service.launch_installer(target, user_confirmed=True)
        args = popen.call_args.args[0]
        assert args[0] == str(target.resolve())
        assert args[1:] == ["/CURRENTUSER", "/SP-", "/NORESTART"]


def test_install_rechecks_receipt_bound_file_before_launch(update_fixture):
    key, verifier, payload, installer = update_fixture
    release = verifier.verify(
        signed_manifest(key, payload),
        expected_channel="stable",
        current_version="1.3.0",
    )
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(
            verifier,
            directory,
            session=FakeSession(
                [FakeDownloadResponse(installer, release.artifact.url)]
            ),
        )
        target = service.download_installer(release)
        target.write_bytes(b"tampered")
        with pytest.raises(UpdateSecurityError):
            service.launch_installer(target, user_confirmed=True)


def test_install_mode_and_daily_check():
    with tempfile.TemporaryDirectory() as directory:
        executable = Path(directory) / "Pixkin.exe"
        executable.write_bytes(b"")
        assert detect_install_mode(executable) == "portable"
        (Path(directory) / "install-mode.json").write_text(
            json.dumps(
                {
                    "mode": "installed",
                    "app_id": "Pixkin.Desktop",
                    "update_manifest_urls": {
                        "stable": "https://updates.example/stable.json",
                        "beta": "http://evil.example/beta.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        assert detect_install_mode(executable) == "installed"
        assert installed_manifest_urls(executable) == {
            "stable": "https://updates.example/stable.json"
        }
    now = datetime.now(timezone.utc)
    assert not UpdateService.automatic_check_due(
        (now - timedelta(hours=23)).isoformat(),
        now=now,
    )
    assert UpdateService.automatic_check_due(
        (now - timedelta(hours=25)).isoformat(),
        now=now,
    )


def test_rollback_uses_latest_older_hash_verified_installer(update_fixture):
    _, verifier, _, _ = update_fixture
    with tempfile.TemporaryDirectory() as directory:
        service = UpdateService(verifier, Path(directory) / "cache")
        service.rollback_dir.mkdir(parents=True)
        for version in ("1.2.0", "1.3.0", "1.4.0"):
            installer = (
                service.rollback_dir
                / f"Pixkin-Setup-{version}.exe"
            )
            content = f"installer-{version}".encode()
            installer.write_bytes(content)
            installer.with_suffix(".exe.sha256").write_text(
                hashlib.sha256(content).hexdigest(),
                encoding="utf-8",
            )
        rollback = service.available_rollback(
            current_version="1.4.0"
        )
        assert rollback is not None
        assert rollback.name == "Pixkin-Setup-1.3.0.exe"
        with mock.patch("subprocess.Popen") as popen:
            service.launch_rollback(
                rollback,
                user_confirmed=True,
            )
        assert "/ALLOWDOWNGRADE" in popen.call_args.args[0]
        rollback.write_bytes(b"tampered")
        with pytest.raises(UpdateSecurityError):
            service.launch_rollback(
                rollback,
                user_confirmed=True,
            )


def test_private_key_can_be_serialized_only_for_release_tooling():
    private_key = Ed25519PrivateKey.generate()
    encoded = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    )
    assert b"PRIVATE KEY" in encoded
