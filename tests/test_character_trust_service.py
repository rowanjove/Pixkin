import json
import tempfile
import unittest
from pathlib import Path

from core.services.character_trust_service import (
    OfficialCharacterTrustError,
    OfficialCharacterTrustStore,
)


ROOT = Path(__file__).resolve().parents[1]


class OfficialCharacterTrustTests(unittest.TestCase):
    def test_bundled_official_archives_match_manifest(self):
        trust = OfficialCharacterTrustStore(
            ROOT / "character-packs" / "official-sha256.json"
        )

        for name in ("shanshan.zip", "linlin.zip", "pip.zip"):
            with self.subTest(name=name):
                self.assertTrue(
                    trust.verify(name, ROOT / "character-packs" / name)
                )

    def test_modified_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "official.zip"
            archive.write_bytes(b"trusted")
            digest = OfficialCharacterTrustStore.digest(archive)
            manifest = base / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "archives": {"official.zip": digest},
                    }
                ),
                encoding="utf-8",
            )
            trust = OfficialCharacterTrustStore(manifest)
            archive.write_bytes(b"tampered")

            self.assertFalse(trust.verify("official.zip", archive))

    def test_invalid_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                '{"schema_version":1,"archives":{"role.zip":"bad"}}',
                encoding="utf-8",
            )

            with self.assertRaises(OfficialCharacterTrustError):
                OfficialCharacterTrustStore(manifest)

    def test_non_object_manifest_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text("[]", encoding="utf-8")

            with self.assertRaises(OfficialCharacterTrustError):
                OfficialCharacterTrustStore(manifest)
