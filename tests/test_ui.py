import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import Qt, QPointF, QEvent, QObject
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.character_package import (
    AnimationSpec, CharacterPackage, CharacterPackageManager, FrameSpec
)
from core.config import ConfigManager
from core.pet_animator import PetState
from core.secrets import SecretStore
from ui.chat_window import BubbleShell, ChatBubbleWindow
from ui.onboarding_window import FirstRunWindow
from ui.pet_lab_window import PetLabWindow
from ui.pet_window import PetWindow
from ui.settings_window import SettingsWindow
from main import BUILTIN_CHARACTER_ARCHIVES, DesktopPetApp


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._config_directory = tempfile.TemporaryDirectory()
        base = Path(cls._config_directory.name)
        cls.config = ConfigManager(str(base / "config.json"))
        cls.package_manager = CharacterPackageManager(
            cls.config, base / "characters"
        )

    def test_first_wave_characters_are_bundled_builtins(self):
        self.assertEqual(
            BUILTIN_CHARACTER_ARCHIVES,
            ("shanshan.zip", "linlin.zip", "pip.zip"),
        )
        self.assertNotIn("yeye.zip", BUILTIN_CHARACTER_ARCHIVES)

    def test_builtin_bootstrap_repairs_corrupt_installed_directory(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            archive = root / "character-packs" / "shanshan.zip"
            manager.import_zip(str(archive), activate=False)
            (manager.root / "shanshan" / "character.md").write_text(
                "corrupt", encoding="utf-8"
            )
            controller = DesktopPetApp.__new__(DesktopPetApp)
            controller.config_mgr = config
            controller.package_manager = manager

            with patch(
                "main.BUILTIN_CHARACTER_ARCHIVES", ("shanshan.zip",)
            ), patch(
                "main.resource_path",
                side_effect=lambda relative: root / relative,
            ):
                controller._ensure_builtin_characters()

            self.assertTrue(
                manager.package_matches_zip("shanshan", str(archive))
            )

    def test_existing_official_yeye_is_upgraded_without_forced_install(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            shutil.copytree(
                root / "character-packs" / "yeye",
                manager.root / "yeye",
            )
            manager.activate("yeye")
            controller = DesktopPetApp.__new__(DesktopPetApp)
            controller.config_mgr = config
            controller.package_manager = manager

            with patch(
                "main.resource_path",
                side_effect=lambda relative: root / relative,
            ):
                controller._update_installed_official_characters()

            upgraded = manager.get_active()
            self.assertEqual(upgraded.package_id, "yeye")
            self.assertEqual(upgraded.schema_version, "2.0")
            self.assertIn("walk_left", upgraded.animations)
            self.assertGreater(len(upgraded.animations["walk_left"].frames), 1)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            controller = DesktopPetApp.__new__(DesktopPetApp)
            controller.config_mgr = config
            controller.package_manager = manager
            with patch(
                "main.resource_path",
                side_effect=lambda relative: root / relative,
            ):
                controller._update_installed_official_characters()
            self.assertFalse((manager.root / "yeye").exists())

    def test_yeye_uses_dedicated_art_for_all_directional_edge_states(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            package = manager.import_zip(
                str(root / "character-packs" / "yeye.zip")
            )
            pet = PetWindow(config, package)

            expected = {
                f"edge_{phase}_{side}"
                for side in ("left", "right", "top", "bottom")
                for phase in ("enter", "idle", "hover", "exit")
            }
            self.assertEqual(pet._dedicated_edge_states, expected)
            for side in ("left", "right", "top", "bottom"):
                pet.dock_side = side
                pet.is_docked = True
                pet.animator.set_state(
                    pet._edge_state_for(side, "idle")
                )
                pet.show()
                self.app.processEvents()
                self.assertFalse(pet.grab().isNull())

            pet.close()
            pet.chat_window.close()

    @classmethod
    def tearDownClass(cls):
        cls._config_directory.cleanup()

    def test_windows_can_be_constructed_and_painted(self):
        pet = PetWindow(self.config)
        chat = ChatBubbleWindow()
        settings = SettingsWindow(self.config, self.package_manager)
        lab = PetLabWindow(
            self.config,
            self.package_manager,
        )
        pet.show()
        chat.show()
        settings.show()
        lab.show()
        self.app.processEvents()
        self.assertGreaterEqual(chat.width(), 360)
        self.assertGreaterEqual(settings.height(), 660)
        self.assertGreaterEqual(lab.width(), 860)
        pet.close()
        pet.chat_window.close()
        chat.close()
        settings.close()
        lab.close()

    def test_chat_escapes_user_html(self):
        chat = ChatBubbleWindow()
        chat.append_message("user", "<script>bad()</script>")
        self.assertNotIn("<script>", chat.chat_history.toHtml())
        chat.close()

    def test_chat_character_updates_placeholder(self):
        chat = ChatBubbleWindow()
        chat.set_character("椰子")
        self.assertIn("椰子", chat.input_field.placeholderText())
        self.assertNotIn("山山", chat.input_field.placeholderText())
        chat.close()

    def test_chat_shell_has_no_bottom_triangle_cutout(self):
        shell = BubbleShell()
        shell.resize(300, 200)
        shell.show()
        self.app.processEvents()
        image = shell.grab().toImage()
        self.assertGreater(image.pixelColor(100, 190).alpha(), 0)
        shell.close()

    def test_chat_theme_switch_updates_bubble_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            config.update_section("app", {"theme": "light"})
            chat = ChatBubbleWindow(config)
            self.assertEqual(chat.shell.background_color, "#F7F9FC")
            config.update_section("app", {"theme": "dark"})
            chat.apply_theme()
            self.assertEqual(chat.shell.background_color, "#101624")
            chat.close()

    def test_settings_exposes_three_theme_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            settings = SettingsWindow(config)
            self.assertEqual(
                [
                    settings.theme_input.itemData(index)
                    for index in range(settings.theme_input.count())
                ],
                ["system", "dark", "light"],
            )
            settings.close()

    def test_light_theme_uses_readable_general_label_color(self):
        style = SettingsWindow._style("light")
        self.assertIn("QLabel { color: #263244; }", style)
        self.assertIn("placeholder-text-color: #667085", style)

    def test_settings_exposes_four_edge_toggles_with_bottom_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            settings = SettingsWindow(config)
            self.assertTrue(settings.edge_side_checks["left"].isChecked())
            self.assertTrue(settings.edge_side_checks["right"].isChecked())
            self.assertTrue(settings.edge_side_checks["top"].isChecked())
            self.assertFalse(settings.edge_side_checks["bottom"].isChecked())
            settings.close()

    def test_character_list_protects_default_role(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            manager.import_zip(
                str(root / "character-packs" / "shanshan.zip")
            )
            manager.import_zip(
                str(root / "character-packs" / "linlin.zip"),
                activate=False,
            )
            manager.import_zip(
                str(root / "character-packs" / "yeye.zip"),
                activate=False,
            )
            settings = SettingsWindow(config, manager)
            default_row = next(
                row
                for row in range(settings.character_list.count())
                if settings.character_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                ) == "shanshan"
            )
            settings.character_list.setCurrentRow(default_row)
            self.app.processEvents()
            fixed_width = settings.character_list.width()
            self.assertFalse(settings.delete_character_btn.isEnabled())
            self.assertEqual(settings.character_list.count(), 3)
            linlin_row = next(
                row
                for row in range(settings.character_list.count())
                if settings.character_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                ) == "linlin"
            )
            settings.character_list.setCurrentRow(linlin_row)
            self.app.processEvents()
            self.assertFalse(settings.delete_character_btn.isEnabled())
            yeye_row = next(
                row
                for row in range(settings.character_list.count())
                if settings.character_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                ) == "yeye"
            )
            settings.character_list.setCurrentRow(yeye_row)
            self.app.processEvents()
            self.assertTrue(settings.delete_character_btn.isEnabled())
            self.assertEqual(settings.character_list.width(), fixed_width)
            self.assertTrue(all(
                settings.character_list.item(row).sizeHint().height() == 54
                for row in range(settings.character_list.count())
            ))
            self.assertEqual(settings.character_list.height(), 178)
            settings.close()

    def test_character_list_hides_replaced_legacy_aliases_and_orders_builtins(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            for archive_name in (
                "shanshan.zip",
                "linlin.zip",
                "pip.zip",
                "default-assistant.zip",
            ):
                manager.import_zip(
                    str(root / "character-packs" / archive_name),
                    activate=archive_name == "default-assistant.zip",
                )

            settings = SettingsWindow(config, manager)
            visible_ids = [
                settings.character_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                )
                for row in range(settings.character_list.count())
            ]
            self.assertEqual(visible_ids[:3], ["shanshan", "linlin", "pip"])
            self.assertNotIn("default-assistant", visible_ids)
            settings.close()

    def test_live_platform_column_has_room_for_full_label(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            settings = SettingsWindow(config)
            settings._add_room_row({
                "platform": "bilibili",
                "room_id": "123",
            })
            self.assertGreaterEqual(settings.rooms_table.columnWidth(1), 104)
            platform = settings.rooms_table.cellWidget(0, 1)
            self.assertGreaterEqual(platform.minimumWidth(), 86)
            self.assertEqual(platform.currentText(), "B站")
            settings.close()

    def test_pet_click_opens_chat(self):
        pet = PetWindow(self.config)
        pet.show()
        self.app.processEvents()
        self.assertFalse(pet.chat_window.isVisible())
        QTest.mouseClick(
            pet,
            Qt.MouseButton.LeftButton,
            pos=pet.rect().center(),
        )
        QTest.qWait(230)
        self.app.processEvents()
        self.assertTrue(pet.chat_window.isVisible())
        pet.close()
        pet.chat_window.close()

    def test_pet_drag_moves_and_docks_when_past_edge(self):
        pet = PetWindow(self.config)
        pet.show()
        pet.move(400, 300)
        self.app.processEvents()
        start = pet.rect().center()
        start_global = pet.mapToGlobal(start)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(start),
            QPointF(start_global),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        pet.mousePressEvent(press)
        target_global = QPointF(start_global.x() + 80, start_global.y() + 40)
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(start.x() + 80, start.y() + 40),
            target_global,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        pet.mouseMoveEvent(move)
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(start.x() + 80, start.y() + 40),
            target_global,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        pet.mouseReleaseEvent(release)
        self.assertGreaterEqual(pet.x(), 470)

        geo = pet._screen_geometry()
        pet.move(geo.left() - 60, geo.center().y())
        self.assertTrue(pet._check_edge_docking())
        self.assertEqual(pet.dock_side, "left")
        self.assertTrue(pet.is_docked)
        pet.close()
        pet.chat_window.close()

    def test_walk_moves_to_edge_and_auto_hides(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            pet = PetWindow(config)
            pet.show()
            self.app.processEvents()
            geo = pet._screen_geometry()
            right_limit = geo.right() - pet.width() + 1
            pet.move(right_limit - 20, geo.center().y())
            pet.animator._durations[PetState.WALK_RIGHT] = 350

            pet.animator.request_state(PetState.WALK_RIGHT)
            QTest.qWait(310)
            self.app.processEvents()

            self.assertTrue(pet.is_docked)
            self.assertEqual(pet.dock_side, "right")
            pet.close()
            pet.chat_window.close()

    def test_bottom_edge_is_disabled_by_default_but_top_uses_directional_state(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            pet = PetWindow(config)
            pet.show()
            self.app.processEvents()
            geo = pet._screen_geometry()

            pet.move(geo.center().x(), geo.bottom() - pet.height() + 1)
            self.assertFalse(pet._check_edge_docking())

            pet.move(geo.center().x(), geo.top() - 10)
            self.assertTrue(pet._check_edge_docking())
            self.assertEqual(pet.dock_side, "top")
            self.assertEqual(
                pet.animator.current_state,
                PetState.EDGE_ENTER_TOP,
            )
            pet.close()
            pet.chat_window.close()

    def test_contextual_alert_remembers_dock_side_for_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            pet = PetWindow(config)
            pet.is_docked = True
            pet.dock_side = "left"
            pet.move(10, 80)

            with patch.object(pet, "_undock") as undock:
                pet.play_contextual_alert(
                    PetState.ALERTING_IMPORTANT, duration_ms=1200
                )

            self.assertEqual(pet._pending_redock["side"], "left")
            undock.assert_called_once()
            pet.close()
            pet.chat_window.close()

    def test_tool_status_replaces_empty_thinking_bubble(self):
        chat = ChatBubbleWindow()
        chat.start_assistant_message()
        chat.append_message("system", "正在使用工具")
        self.assertEqual([item["role"] for item in chat._messages], ["system"])
        chat.close()

    def test_ai_worker_cleanup_does_not_require_qobject_sender(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        worker = QObject()
        controller.ai_worker = worker
        with patch.object(worker, "deleteLater") as delete_later:
            controller._cleanup_ai_worker(worker)
        self.assertIsNone(controller.ai_worker)
        delete_later.assert_called_once_with()

    def test_missing_api_key_finishes_worker_without_exception(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.config_mgr = self.config
        controller.tool_registry = MagicMock()
        controller.tool_registry.get_tools_schema.return_value = []
        controller.chat_history_list = []
        controller.ai_worker = None
        controller.pet_window = SimpleNamespace(
            animator=MagicMock(),
            chat_window=MagicMock(),
        )
        with patch("main.SecretStore.get_api_key", return_value=""):
            controller._on_user_send_message("你好")
        deadline = time.monotonic() + 2
        while controller.ai_worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(20)
        self.assertIsNone(controller.ai_worker)
        controller.pet_window.chat_window.append_message.assert_called()

    def test_ai_lifecycle_updates_animation_states(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.chat_history_list = []
        controller.pet_window = SimpleNamespace(
            animator=MagicMock(),
            chat_window=MagicMock(),
        )

        controller._on_ai_chunk("你好")
        controller._on_tool_executing("正在使用工具")
        controller._on_ai_finished("完成")
        controller._on_ai_error("失败")

        requested = [
            call.args[0]
            for call in controller.pet_window.animator.request_state.call_args_list
        ]
        self.assertEqual(
            requested,
            [
                PetState.TALKING,
                PetState.WORKING,
                PetState.SUCCESS,
                PetState.FAILED,
            ],
        )
        controller.pet_window.chat_window.append_chunk.assert_called_once_with(
            "你好"
        )

    def test_chat_history_is_bounded_and_starts_with_user(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.chat_history_list = []
        for index in range(60):
            controller.chat_history_list.extend([
                {"role": "user", "content": f"question-{index}" * 300},
                {"role": "assistant", "content": f"answer-{index}" * 300},
            ])
        controller._trim_chat_history()
        self.assertLessEqual(len(controller.chat_history_list), 40)
        self.assertEqual(controller.chat_history_list[0]["role"], "user")
        self.assertLessEqual(
            sum(len(item["content"]) for item in controller.chat_history_list),
            24000,
        )

    def test_quit_waits_for_workers_without_blocking_ui_thread(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller._quitting = False
        controller._quit_finalized = False
        controller._monitor_restart_pending = False
        controller.pet_window = SimpleNamespace(save_position=MagicMock())
        controller.tray = SimpleNamespace(hide=MagicMock())
        controller.app = SimpleNamespace(quit=MagicMock())
        controller._instance_lock = SimpleNamespace(unlock=MagicMock())
        controller.live_monitor = MagicMock()
        controller.live_monitor.isRunning.return_value = True
        controller.ai_worker = MagicMock()
        controller.ai_worker.isRunning.return_value = True

        controller._quit_app()

        controller.live_monitor.stop.assert_called_once_with()
        controller.ai_worker.cancel.assert_called_once_with()
        controller.app.quit.assert_not_called()

        controller.live_monitor.isRunning.return_value = False
        controller.ai_worker.isRunning.return_value = False
        controller._maybe_finish_quit()
        controller._instance_lock.unlock.assert_called_once_with()
        controller.app.quit.assert_called_once_with()

    def test_pet_lab_cancel_closes_after_worker_finishes(self):
        lab = PetLabWindow(self.config, self.package_manager)
        worker = MagicMock()
        worker.isRunning.return_value = True
        lab.worker = worker

        lab.reject()

        self.assertTrue(lab._close_after_cancel)
        worker.cancel.assert_called_once_with()
        lab._on_worker_finished(worker)
        self.assertIsNone(lab.worker)
        self.assertFalse(lab._close_after_cancel)

    def test_pet_lab_declined_replacement_does_not_import(self):
        manager = MagicMock()
        existing = SimpleNamespace(package_id="same-id", name="旧角色")
        inspected = SimpleNamespace(package_id="same-id", name="新角色")
        manager.inspect_zip.return_value = inspected
        manager.list_packages.return_value = [existing]
        lab = PetLabWindow(self.config, manager)

        with patch.object(lab, "_confirm_replace", return_value=False):
            lab._on_package_ready("generated.zip")

        manager.import_zip.assert_not_called()
        self.assertIsNone(lab.generated_package)
        lab.close()

    def test_settings_rolls_back_credential_when_config_save_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / "config.json"))
            manager = CharacterPackageManager(
                config, Path(directory) / "characters"
            )
            settings = SettingsWindow(config, manager)
            settings.api_key_input.setText("new-key")
            with patch.object(
                SecretStore, "get_api_key", return_value="old-key"
            ), patch.object(
                SecretStore, "set_api_key", return_value=True
            ) as save_key, patch.object(
                config, "update_sections", return_value=False
            ), patch.object(QMessageBox, "critical"):
                settings._save()

            self.assertEqual(
                [call.args[0] for call in save_key.call_args_list],
                ["new-key", "old-key"],
            )
            settings.close()

    def test_first_run_imports_package_and_character_frames_load(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(config, base / "characters")
            onboarding = FirstRunWindow(manager)
            with patch.object(
                QMessageBox, "information",
                return_value=QMessageBox.StandardButton.Ok,
            ):
                onboarding._import(
                    str(root / "character-packs" / "shanshan.zip")
                )
            self.assertEqual(
                onboarding.imported_package.package_id, "shanshan"
            )
            pet = PetWindow(config, manager.get_active())
            pet.show()
            self.app.processEvents()
            self.assertIn("idle", pet._character_frames)
            pet.close()
            pet.chat_window.close()

    def test_v2_atlas_frames_are_cropped_and_ping_pong_played(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atlas_path = root / "atlas.png"
            atlas = Image.new("RGBA", (384, 208), (255, 0, 0, 255))
            atlas.paste((0, 0, 255, 255), (192, 0, 384, 208))
            atlas.save(atlas_path)
            animation = AnimationSpec(
                frames=[
                    FrameSpec(atlas_path, (0, 0, 192, 208)),
                    FrameSpec(atlas_path, (192, 0, 192, 208)),
                ],
                fps=25,
                playback="ping_pong",
                legacy_effects=False,
            )
            package = CharacterPackage(
                package_id="nova-v2",
                name="Nova",
                version="2.0.0",
                author="Tests",
                description="",
                system_prompt="你是 Nova。",
                root=root,
                preview=atlas_path,
                animations={"idle": animation},
                schema_version="2.0",
                quality_tier="basic",
                persona={},
                behavior={},
                edge={},
                compatibility={},
                rights={},
            )
            pet = PetWindow(self.config, package)

            loaded_frames, _loaded_animation = pet._character_frames["idle"]
            self.assertEqual(
                [(frame.width(), frame.height()) for frame in loaded_frames],
                [(192, 208), (192, 208)],
            )
            pet._state_started_tick = 0
            pet.tick = 0
            first = pet._current_package_frame("idle")
            pet.tick = 1
            second = pet._current_package_frame("idle")
            pet.tick = 2
            third = pet._current_package_frame("idle")
            self.assertEqual(first.toImage().pixelColor(0, 0).red(), 255)
            self.assertEqual(second.toImage().pixelColor(0, 0).blue(), 255)
            self.assertEqual(third.toImage().pixelColor(0, 0).red(), 255)
            pet.close()
            pet.chat_window.close()


if __name__ == "__main__":
    unittest.main()
