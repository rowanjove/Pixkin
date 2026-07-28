import subprocess
import sys
import tempfile
from pathlib import Path

from core.services.update_service import UpdateManifestVerifier


ROOT = Path(__file__).resolve().parents[1]


def test_installer_is_current_user_fixed_identity_and_data_safe():
    script = (ROOT / "installer" / "Pixkin.iss").read_text(
        encoding="utf-8"
    )
    assert "PrivilegesRequired=lowest" in script
    assert (
        "AppIdValue "
        '"{{A31F7F5B-CE81-49BA-93B9-5EFCF8695731}"'
    ) in script
    assert r"DefaultDirName={localappdata}\Programs\Pixkin" in script
    assert "Flags: unchecked" in script
    assert "/ALLOWDOWNGRADE" in script
    assert "CompareVersions" in script
    assert "GetSHA256OfFile" in script
    assert "DeleteUserData" in script
    assert r"Software\Microsoft\Windows\CurrentVersion\Run" in script
    assert "uninsdeletevalue" in script


def test_release_workflow_requires_signed_update_manifest():
    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    assert "PIXKIN_UPDATE_PRIVATE_KEY_B64" in workflow
    assert "release\\update-stable.json" in workflow
    assert "releases/latest/download/update-stable.json" in workflow
    build = (ROOT / "scripts" / "build_release.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "build_installer.ps1" in build
    assert "PIXKIN_UPDATE_PRIVATE_KEY_B64" in build
    assert "PIXKIN_UPDATE_MANIFEST_URL" in build


def test_public_key_is_bundled_but_private_key_is_not_in_workspace():
    public_key = ROOT / "assets" / "update-public-key.pem"
    assert b"PUBLIC KEY" in public_key.read_bytes()
    private_header = b"-----BEGIN " + b"PRIVATE KEY-----"
    private_material = []
    for path in ROOT.rglob("*"):
        if (
            path.is_file()
            and not set(path.parts).intersection(
                {
                    ".git",
                    "__pycache__",
                    "build",
                    "dist",
                    "release",
                    "artifacts",
                }
            )
            and path.stat().st_size <= 1024 * 1024
        ):
            try:
                if private_header in path.read_bytes():
                    private_material.append(path)
            except OSError:
                pass
    assert private_material == []


def test_release_signing_tool_round_trip():
    private_key_path = (
        Path.home()
        / ".pixkin-release"
        / "update-private-key.pem"
    )
    if not private_key_path.is_file():
        return
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installer = root / "Pixkin-Setup-1.3.0.exe"
        installer.write_bytes(b"installer")
        manifest = root / "update-stable.json"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "sign_update_manifest.py"),
                "--private-key",
                str(private_key_path),
                "--public-key",
                str(ROOT / "assets" / "update-public-key.pem"),
                "--installer",
                str(installer),
                "--output",
                str(manifest),
                "--channel",
                "stable",
                "--base-url",
                "https://updates.example/releases/v1.3.0/",
                "--release-notes-url",
                "https://updates.example/releases/v1.3.0",
                "--minimum-compatible-version",
                "1.2.0",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        verifier = UpdateManifestVerifier(
            (ROOT / "assets" / "update-public-key.pem").read_bytes()
        )
        release = verifier.verify(
            manifest.read_bytes(),
            expected_channel="stable",
            current_version="1.2.0",
        )
        assert release.version == "1.3.0"
