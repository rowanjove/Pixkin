"""Generate an offline Ed25519 release key and repository public key."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.private_key.exists() or arguments.public_key.exists():
        raise FileExistsError("拒绝覆盖已有更新密钥")
    private_key = Ed25519PrivateKey.generate()
    arguments.private_key.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        arguments.private_key,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(
            private_key.private_bytes(
                Encoding.PEM,
                PrivateFormat.PKCS8,
                NoEncryption(),
            )
        )
        handle.flush()
        os.fsync(handle.fileno())
    arguments.public_key.parent.mkdir(parents=True, exist_ok=True)
    arguments.public_key.write_bytes(
        private_key.public_key().public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        )
    )
    print(arguments.public_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
