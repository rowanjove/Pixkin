import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml
from PIL import Image

from core.character_package import (
    DEFAULT_PACKAGE_ID, CharacterPackageError, CharacterPackageManager
)
from core.config import ConfigManager


ROOT = Path(__file__).resolve().parents[1]


class CharacterPackageTests(unittest.TestCase):
    @staticmethod
    def _write_png(path: Path, size=(192, 208), color=(90, 120, 210, 255)):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", size, color).save(path)

    def _write_v2_package(self, root: Path, *, extra=None, animations=None):
        images = root / "images"
        colors = {
            "idle": (90, 120, 210, 255),
            "talking": (110, 130, 220, 255),
            "dragging": (130, 140, 230, 255),
            "alerting": (150, 150, 240, 255),
        }
        for state, color in colors.items():
            self._write_png(images / f"{state}.png", color=color)
        metadata = {
            "schema_version": "2.0",
            "id": "nova-v2",
            "name": "Nova",
            "version": "2.0.0",
            "author": "Tests",
            "description": "v2 fixture",
            "quality_tier": "basic",
            "preview": "images/idle.png",
            "persona": {
                "identity": "你是 Nova，一位可靠的桌面伙伴。",
                "core_traits": ["温暖", "可靠"],
                "initiative": {
                    "animate_without_prompt": True,
                    "speak_without_prompt": False,
                },
            },
            "behavior": {
                "motion_temperament": "calm",
                "idle_interval_seconds": [9, 18],
            },
            "animations": animations or {
                state: {
                    "source": {
                        "type": "frames",
                        "files": [f"images/{state}.png"],
                    },
                    "fps": 8,
                    "playback": "loop" if state in {"idle", "talking"} else "once",
                }
                for state in colors
            },
            "compatibility": {},
            "rights": {
                "license": "personal-use",
                "author_confirmed_rights": True,
                "ai_generated": False,
            },
        }
        if extra:
            metadata.update(extra)
        frontmatter = yaml.safe_dump(
            metadata, allow_unicode=True, sort_keys=False
        ).rstrip()
        (root / "character.md").write_text(
            f"---\n{frontmatter}\n---\n", encoding="utf-8"
        )
        return metadata

    def test_import_and_activate_sample_package(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            package = manager.import_zip(
                str(ROOT / "character-packs" / "shanshan.zip")
            )
            preview = manager.read_zip_preview(
                str(ROOT / "character-packs" / "shanshan.zip")
            )
            self.assertEqual(package.package_id, "shanshan")
            self.assertTrue(preview)
            self.assertTrue(package.preview.is_file())
            self.assertIn("idle", package.animations)
            self.assertEqual(
                manager.get_active().package_id, "shanshan"
            )

    def test_directory_metadata_invalid_utf8_is_domain_error(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            package_root = base / "characters" / "broken"
            package_root.mkdir(parents=True)
            (package_root / "character.md").write_bytes(b"\xff")

            with self.assertRaisesRegex(CharacterPackageError, "UTF-8"):
                manager._load_from_directory(package_root)

    def test_failed_replace_activation_restores_previous_package(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            archive = str(ROOT / "character-packs" / "shanshan.zip")
            manager.import_zip(archive, activate=False)
            marker = (
                base / "characters" / "shanshan" / "old-install-marker.txt"
            )
            marker.write_text("keep", encoding="utf-8")
            config.update_sections = lambda _updates: False

            with self.assertRaisesRegex(
                CharacterPackageError, "配置文件无法保存"
            ):
                manager.import_zip(
                    archive,
                    replace=True,
                    activate=True,
                    allow_builtin_replace=True,
                )

            self.assertTrue(marker.is_file())

    def test_builtin_package_is_verified_and_cannot_be_replaced_by_user(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            archive = str(ROOT / "character-packs" / "shanshan.zip")
            manager.import_zip(archive, activate=False)
            self.assertTrue(manager.package_matches_zip("shanshan", archive))

            character_md = (
                base / "characters" / "shanshan" / "character.md"
            )
            character_md.write_text("tampered", encoding="utf-8")
            self.assertFalse(manager.package_matches_zip("shanshan", archive))
            with self.assertRaisesRegex(
                CharacterPackageError, "内置角色不能"
            ):
                manager.import_zip(archive, replace=True, activate=False)
            manager.import_zip(
                archive,
                replace=True,
                activate=False,
                allow_builtin_replace=True,
            )
            self.assertTrue(manager.package_matches_zip("shanshan", archive))
            with self.assertRaisesRegex(
                CharacterPackageError, "内置角色不可重命名"
            ):
                manager.rename_package("shanshan", "伪装角色")

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            zip_path = base / "unsafe.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("../escape.png", b"bad")
                archive.writestr(
                    "character.md",
                    "---\nid: unsafe-pack\nname: Unsafe\n"
                    "animations:\n  idle: images/idle.png\n---\n",
                )
                archive.writestr("images/idle.png", b"not-an-image")
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            with self.assertRaises(CharacterPackageError):
                manager.import_zip(str(zip_path))

    def test_zip_rejects_windows_unsafe_and_case_colliding_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            unsafe_names = (
                "images/file:stream.png",
                "images/CON.png",
                "images/trailing-dot.",
                "images/trailing-space ",
            )
            for index, unsafe_name in enumerate(unsafe_names):
                with self.subTest(name=unsafe_name):
                    archive_path = base / f"unsafe-{index}.zip"
                    with zipfile.ZipFile(archive_path, "w") as archive:
                        archive.writestr(unsafe_name, b"unsafe")
                    with self.assertRaisesRegex(
                        CharacterPackageError,
                        "路径不安全",
                    ):
                        manager.inspect_zip(str(archive_path))

            collision = base / "collision.zip"
            with zipfile.ZipFile(collision, "w") as archive:
                archive.writestr("images/Pet.png", b"first")
                archive.writestr("images/pet.png", b"second")
            with self.assertRaisesRegex(
                CharacterPackageError,
                "大小写冲突",
            ):
                manager.inspect_zip(str(collision))

    def test_zip_rejects_encrypted_member_and_recursive_yaml_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            encrypted = zipfile.ZipInfo("images/idle.png")
            encrypted.flag_bits |= 0x1
            with self.assertRaisesRegex(
                CharacterPackageError,
                "加密文件",
            ):
                manager._validate_zip_member(encrypted)

            with self.assertRaisesRegex(
                CharacterPackageError,
                "循环 YAML 别名",
            ):
                manager._parse_frontmatter(
                    "---\n"
                    "id: alias-pack\n"
                    "name: Alias\n"
                    "persona: &loop\n"
                    "  nested: *loop\n"
                    "animations:\n"
                    "  idle: images/idle.png\n"
                    "---\n"
                )

    def test_invalid_active_package_id_cannot_escape_character_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            config.update_section(
                "character",
                {"active_pack": "../outside"},
            )

            self.assertIsNone(manager.get_active())
            with self.assertRaisesRegex(
                CharacterPackageError,
                "id 只能使用",
            ):
                manager.activate("../outside")

    def test_rename_and_delete_character_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            manager.import_zip(
                str(ROOT / "character-packs" / "shanshan.zip")
            )
            custom = manager.import_zip(
                str(ROOT / "character-packs" / "default-assistant.zip")
            )
            renamed = manager.rename_package(custom.package_id, "自定义伙伴")
            self.assertEqual(renamed.name, "自定义伙伴")
            self.assertIn("自定义伙伴", manager.get_active().system_prompt)

            fallback = manager.delete_package(custom.package_id)
            self.assertEqual(fallback.package_id, DEFAULT_PACKAGE_ID)
            self.assertEqual(manager.get_active().package_id, DEFAULT_PACKAGE_ID)
            self.assertFalse((base / "characters" / custom.package_id).exists())

    def test_default_character_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            manager.import_zip(
                str(ROOT / "character-packs" / "shanshan.zip"),
                activate=False,
            )
            self.assertEqual(manager.get_active().package_id, DEFAULT_PACKAGE_ID)
            with self.assertRaisesRegex(
                CharacterPackageError, "内置角色不可删除"
            ):
                manager.delete_package(DEFAULT_PACKAGE_ID)

    def test_second_builtin_character_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            manager.import_zip(
                str(ROOT / "character-packs" / "linlin.zip"),
                activate=False,
            )
            with self.assertRaisesRegex(
                CharacterPackageError, "内置角色不可删除"
            ):
                manager.delete_package("linlin")

    def test_pip_builtin_character_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            manager.import_zip(
                str(ROOT / "character-packs" / "pip.zip"),
                activate=False,
            )
            with self.assertRaisesRegex(
                CharacterPackageError, "内置角色不可删除"
            ):
                manager.delete_package("pip")

    def test_yeye_is_a_deletable_imported_package(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            manager.import_zip(
                str(ROOT / "character-packs" / "shanshan.zip"),
                activate=False,
            )
            manager.import_zip(
                str(ROOT / "character-packs" / "yeye.zip"),
                activate=False,
            )

            manager.delete_package("yeye")

            self.assertFalse((base / "characters" / "yeye").exists())

    def test_v2_frames_package_loads_structured_persona_and_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package_root = base / "characters" / "nova-v2"
            package_root.mkdir(parents=True)
            self._write_v2_package(package_root)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")

            package = manager._load_from_directory(package_root)

            self.assertEqual(package.schema_version, "2.0")
            self.assertEqual(package.quality_tier, "basic")
            self.assertFalse(package.is_legacy)
            self.assertEqual(package.behavior["motion_temperament"], "calm")
            self.assertIn("Nova", package.system_prompt)
            self.assertIn("不得主动发言", package.system_prompt)
            self.assertEqual(package.animations["idle"].playback, "loop")
            self.assertEqual(
                package.animations["idle"].frames[0].file,
                package_root / "images" / "idle.png",
            )

    def test_v2_atlas_and_strip_sources_are_normalized_to_frame_rects(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package_root = base / "characters" / "nova-v2"
            package_root.mkdir(parents=True)
            images = package_root / "images"
            self._write_png(images / "atlas.webp", size=(1536, 1872))
            self._write_png(images / "talking-strip.png", size=(768, 208))
            for state in ("dragging", "alerting"):
                self._write_png(images / f"{state}.png")
            animations = {
                "idle": {
                    "source": {
                        "type": "atlas",
                        "file": "images/atlas.webp",
                        "row": 1,
                        "column": 2,
                        "frames": 6,
                        "cell_size": [192, 208],
                    },
                    "fps": 8,
                    "playback": "loop",
                },
                "talking": {
                    "source": {
                        "type": "strip",
                        "file": "images/talking-strip.png",
                        "frames": 4,
                        "direction": "horizontal",
                        "cell_size": [192, 208],
                    },
                    "fps": 8,
                    "playback": "loop",
                },
                "dragging": {
                    "source": {
                        "type": "frames",
                        "files": ["images/dragging.png"],
                    },
                    "playback": "once",
                },
                "alerting": {
                    "source": {
                        "type": "frames",
                        "files": ["images/alerting.png"],
                    },
                    "playback": "once",
                },
            }
            self._write_v2_package(package_root, animations=animations)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")

            package = manager._load_from_directory(package_root)

            idle = package.animations["idle"]
            talking = package.animations["talking"]
            self.assertEqual(len(idle.frames), 6)
            self.assertEqual(idle.frames[0].rect, (384, 208, 192, 208))
            self.assertEqual(idle.frames[-1].rect, (1344, 208, 192, 208))
            self.assertEqual(len(talking.frames), 4)
            self.assertEqual(talking.frames[-1].rect, (576, 0, 192, 208))

    def test_v2_basic_tier_requires_all_basic_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package_root = base / "characters" / "nova-v2"
            package_root.mkdir(parents=True)
            animations = {
                "idle": {
                    "source": {
                        "type": "frames",
                        "files": ["images/idle.png"],
                    }
                }
            }
            self._write_v2_package(package_root, animations=animations)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")

            with self.assertRaisesRegex(
                CharacterPackageError, "基础级.*talking"
            ):
                manager._load_from_directory(package_root)

    def test_v2_unknown_top_level_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package_root = base / "characters" / "nova-v2"
            package_root.mkdir(parents=True)
            self._write_v2_package(
                package_root, extra={"personna": {"typo": True}}
            )
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")

            with self.assertRaisesRegex(
                CharacterPackageError, "不支持的顶层字段.*personna"
            ):
                manager._load_from_directory(package_root)

    def test_v2_frames_source_has_per_animation_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package_root = base / "characters" / "nova-v2"
            package_root.mkdir(parents=True)
            animations = {
                state: {
                    "source": {
                        "type": "frames",
                        "files": (
                            [f"images/{state}.png"] * 31
                            if state == "idle"
                            else [f"images/{state}.png"]
                        ),
                    },
                    "playback": "loop",
                }
                for state in ("idle", "talking", "dragging", "alerting")
            }
            self._write_v2_package(package_root, animations=animations)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")

            with self.assertRaisesRegex(
                CharacterPackageError, "帧数不能超过"
            ):
                manager._load_from_directory(package_root)

    def test_package_rejects_excessive_cumulative_image_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package_root = base / "characters" / "nova-v2"
            package_root.mkdir(parents=True)
            self._write_v2_package(package_root)
            for state in ("idle", "talking", "dragging", "alerting"):
                self._write_png(
                    package_root / "images" / f"{state}.png",
                    size=(3000, 3000),
                )
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")

            with self.assertRaisesRegex(
                CharacterPackageError, "累计像素"
            ):
                manager._load_from_directory(package_root)


    def test_v3_identity_and_runtime_sections_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "nova-v3"
            for state in ("idle", "talking", "dragging", "alerting"):
                self._write_png(root / "images" / f"{state}.png")
            (root / "character.md").write_text(
                "---\n"
                "schema_version: '3.0'\n"
                "identity:\n  id: nova-v3\n  name: Nova 3\n"
                "persona:\n  identity: reliable\n"
                "behavior: {}\nrenderer:\n  type: sprite\n"
                "voice:\n  provider: windows_sapi\n"
                "memory_policy: {}\ncapabilities: {}\n"
                "animations:\n"
                "  idle:\n    source:\n      type: frames\n      files: [images/idle.png]\n"
                "  talking:\n    source:\n      type: frames\n      files: [images/talking.png]\n"
                "  dragging:\n    source:\n      type: frames\n      files: [images/dragging.png]\n"
                "  alerting:\n    source:\n      type: frames\n      files: [images/alerting.png]\n"
                "---\n",
                encoding="utf-8",
            )
            manager = CharacterPackageManager.__new__(CharacterPackageManager)
            package = manager._load_from_directory(root)
            self.assertEqual(package.schema_version, "3.0")
            self.assertEqual(package.package_id, "nova-v3")
            self.assertEqual(package.renderer["type"], "sprite")


if __name__ == "__main__":
    unittest.main()
