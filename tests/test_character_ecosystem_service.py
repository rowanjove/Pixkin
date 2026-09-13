import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from core.character_package import CharacterPackageManager
from core.config import ConfigManager
from core.services.character_ecosystem_service import (
    CharacterCatalogError,
    CharacterCatalogService,
    CharacterPackageInspector,
)
from core.services.character_service import CharacterService
from core.services.character_trust_service import OfficialCharacterTrustStore
from ui.character_ecosystem_components import CharacterBehaviorEditor


ROOT = Path(__file__).resolve().parents[1]


class CharacterEcosystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_inspector_is_read_only_and_trusts_only_matching_official_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ConfigManager(str(root / "config.json"))
            manager = CharacterPackageManager(config, root / "characters")
            inspector = CharacterPackageInspector(
                CharacterService(manager),
                OfficialCharacterTrustStore(
                    ROOT / "character-packs" / "official-sha256.json"
                ),
            )

            official = inspector.inspect(
                ROOT / "character-packs" / "shanshan.zip"
            )
            third_party = inspector.inspect(
                ROOT / "character-packs" / "default-assistant.zip"
            )

            self.assertTrue(official.official)
            self.assertTrue(official.author_trusted)
            self.assertTrue(official.compatible)
            self.assertEqual(len(official.fingerprint), 64)
            self.assertFalse(third_party.official)
            self.assertFalse(third_party.author_trusted)
            self.assertEqual(manager.list_packages(), [])

    def test_catalog_filters_compatibility_and_reports_updates(self):
        catalog = CharacterCatalogService(
            ROOT / "character-packs" / "catalog.json"
        )
        installed = [
            SimpleNamespace(package_id="shanshan", version="1.9.0"),
            SimpleNamespace(package_id="pip", version="2.0.0"),
        ]

        updates = catalog.available_updates(
            installed,
            app_version="1.3.0",
        )

        self.assertEqual(
            [item.package_id for item in updates],
            ["shanshan"],
        )
        self.assertTrue(all(item.official for item in catalog.entries))

    def test_catalog_download_and_confirmed_official_update_are_hash_bound(self):
        class Download:
            status_code = 200

            def __init__(self, content):
                self.content = content
                self.closed = False

            def iter_content(self, _size):
                yield self.content[:100]
                yield self.content[100:]

            def close(self):
                self.closed = True

        catalog = CharacterCatalogService(
            ROOT / "character-packs" / "catalog.json"
        )
        entry = next(
            item
            for item in catalog.entries
            if item.package_id == "shanshan"
        )
        content = (
            ROOT / "character-packs" / "shanshan.zip"
        ).read_bytes()
        response = Download(content)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = catalog.download_verified(
                entry,
                root / "download.zip",
                get=lambda *args, **kwargs: response,
            )
            config = ConfigManager(str(root / "config.json"))
            service = CharacterService(
                CharacterPackageManager(
                    config, root / "characters"
                )
            )
            service.install_archive(archive)
            result = catalog.install_verified_update(
                entry,
                archive,
                service,
                user_confirmed=True,
            )

            self.assertEqual(result.package.package_id, "shanshan")
            self.assertTrue(response.closed)

    def test_catalog_rejects_insecure_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "entries": [
                            {
                                "id": "unsafe",
                                "name": "Unsafe",
                                "version": "1.0.0",
                                "archive_url": "http://example.test/a.zip",
                                "sha256": "a" * 64,
                                "source": "third_party",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CharacterCatalogError):
                CharacterCatalogService(path)

    def test_catalog_rejects_credentialed_or_fragment_urls(self):
        for archive_url in (
            "https://user:secret@example.test/a.zip",
            "https://example.test/a.zip#fragment",
        ):
            with self.subTest(archive_url=archive_url):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "catalog.json"
                    path.write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "entries": [
                                    {
                                        "id": "unsafe",
                                        "name": "Unsafe",
                                        "version": "1.0.0",
                                        "archive_url": archive_url,
                                        "sha256": "a" * 64,
                                        "source": "third_party",
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaises(CharacterCatalogError):
                        CharacterCatalogService(path)

    def test_catalog_rejects_non_object_and_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(CharacterCatalogError, "根节点"):
                CharacterCatalogService(path)

            path.write_bytes(b"\xff")
            with self.assertRaises(CharacterCatalogError):
                CharacterCatalogService(path)

    def test_behavior_editor_keeps_overrides_partitioned_and_previews(self):
        package_a = SimpleNamespace(
            package_id="a",
            animations={"idle": object(), "wave": object()},
        )
        package_b = SimpleNamespace(
            package_id="b",
            animations={"idle": object(), "nod": object()},
        )
        editor = CharacterBehaviorEditor()
        previewed = []
        editor.preview_requested.connect(previewed.append)
        editor.set_package(package_a)
        weight = editor.table.cellWidget(0, 1)
        weight.setValue(2.5)
        editor.table.cellDoubleClicked.emit(0, 0)
        editor.set_package(package_b)

        values = editor.values()

        self.assertEqual(values["a"]["ambient_weights"]["wave"], 2.5)
        self.assertIn("b", values)
        self.assertEqual(previewed, ["wave"])
        editor.deleteLater()


if __name__ == "__main__":
    unittest.main()
