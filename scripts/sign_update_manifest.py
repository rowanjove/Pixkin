"""Create a strict Ed25519-signed Pixkin update manifest."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from core.services.update_service import (
    UpdateManifestVerifier,
    canonical_payload,
    parse_version,
)
from core.version import MINIMUM_UPDATE_VERSION, VERSION


def file_sha256(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(destination: Path, data: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                data,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", choices=("stable", "beta"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--release-notes-url", required=True)
    parser.add_argument(
        "--minimum-compatible-version",
        default=MINIMUM_UPDATE_VERSION,
    )
    arguments = parser.parse_args()
    parse_version(VERSION)
    parse_version(arguments.minimum_compatible_version)
    base_url = arguments.base_url.rstrip("/") + "/"
    if urlparse(base_url).scheme != "https":
        raise ValueError("产物基础地址必须使用 HTTPS")
    UpdateManifestVerifier._https_url(arguments.release_notes_url)
    installer = arguments.installer.resolve()
    if not installer.is_file() or installer.suffix.lower() != ".exe":
        raise FileNotFoundError(installer)
    loaded_key = load_pem_private_key(
        arguments.private_key.read_bytes(),
        password=None,
    )
    if not isinstance(loaded_key, Ed25519PrivateKey):
        raise ValueError("更新私钥不是 Ed25519 私钥")
    signed = {
        "version": VERSION,
        "channel": arguments.channel,
        "published_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "minimum_compatible_version": (
            arguments.minimum_compatible_version
        ),
        "release_notes_url": arguments.release_notes_url,
        "artifacts": [
            {
                "kind": "installer",
                "filename": installer.name,
                "size": installer.stat().st_size,
                "sha256": file_sha256(installer),
                "url": urljoin(base_url, installer.name),
            }
        ],
    }
    manifest = {
        "schema_version": 1,
        "signed": signed,
        "signature": base64.b64encode(
            loaded_key.sign(canonical_payload(signed))
        ).decode("ascii"),
    }
    verifier = UpdateManifestVerifier(arguments.public_key.read_bytes())
    verifier.public_key.verify(
        base64.b64decode(manifest["signature"]),
        canonical_payload(signed),
    )
    write_json_atomic(arguments.output, manifest)
    print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
