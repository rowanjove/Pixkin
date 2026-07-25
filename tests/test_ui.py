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
from PyQt6.QtCore import Qt, QPointF, QEvent, QObject, QRect
from PyQt6.QtGui import QMouseEvent, QRegion
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QDialog, QListWidget, QMessageBox, QPushButton
)

from core.character_package import (
    AnimationSpec, CharacterPackage, CharacterPackageManager, FrameSpec
)
from core.config import ConfigManager
from core.pet_animator import PetState
from core.pet_generation_run import PetGenerationRunStore
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
                rendered = pet.grab()
                self.assertFalse(rendered.isNull())

                reveal = pet._edge_reveal(side)
                design_reveal = pet._dedicated_edge_design_reveal(side)
                self.assertLess(
                    design_reveal,
                    pet.EDGE_ART_SIZE,
                )
                self.assertEqual(
                    reveal,
                    max(
                        pet.peek_size,
                        round(
                            design_reveal
                            * pet.width()
                            / pet.DESIGN_SIZE
                        ),
                    ),
                )
                if side == "left":
                    visible = QRect(
                        pet.width() - reveal, 0, reveal, pet.height()
                    )
                elif side == "right":
                    visible = QRect(0, 0, reveal, pet.height())
                elif side == "top":
                    visible = QRect(
                        0, pet.height() - reveal, pet.width(), reveal
                    )
                else:
                    visible = QRect(0, 0, pet.width(), reveal)
                subject = QRegion(rendered.mask()).boundingRect()
                self.assertFalse(subject.isEmpty())
                self.assertTrue(
                    visible.contains(subject),
                    f"{side} edge art would be cropped: "
                    f"subject={subject}, visible={visible}",
                )
                if side == "left":
                    self.assertEqual(subject.left(), visible.left())
                elif side == "right":
                    self.assertEqual(subject.right(), visible.right())
                elif side == "top":
                    self.assertEqual(subject.top(), visible.top())
                else:
                    self.assertEqual(subject.bottom(), visible.bottom())

            pet.dock_side = "left"
            pet.is_docked = True
            target = pet._dock_target("left")
            pet.move(*target)
            with patch.object(pet, "_animate_move") as animate:
                pet._show_edge_hover()
                self.assertEqual(
                    pet.animator.current_state,
                    PetState.EDGE_HOVER_LEFT,
                )
                animate.assert_not_called()
                pet._show_edge_idle()
                animate.assert_not_called()

            pet.dock_side = "right"
            pet.is_docked = False
            pet.animator.set_state(PetState.EDGE_EXIT_RIGHT)
            pet.tick = pet._state_started_tick
            self.assertTrue(pet._should_draw_edge_art())
            exit_render = pet.grab()
            exit_subject = QRegion(exit_render.mask()).boundingRect()
            exit_frame = pet._current_package_frame("edge_exit_right")
            _scaled, expected_subject = pet._scaled_edge_art(exit_frame)
            self.assertEqual(
                exit_subject.width(),
                expected_subject.width(),
            )
            self.assertEqual(
                exit_subject.height(),
                expected_subject.height(),
            )

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

    def test_settings_are_non_modal_single_instance_and_chat_stays_usable(self):
        controller = DesktopPetApp.__new__(DesktopPetApp)
        controller.config_mgr = self.config
        controller.package_manager = self.package_manager
        controller._settings_window = None
        controller.pet_window = MagicMock()
        controller._apply_tray_theme = MagicMock()
        controller._restart_live_monitor = MagicMock()
        controller.tray = MagicMock()
        chat = ChatBubbleWindow(self.config)
        chat.show()

        settings = controller._open_settings()
        self.app.processEvents()

        self.assertTrue(settings.isVisible())
        self.assertFalse(settings.isModal())
        self.assertEqual(
            settings.windowModality(),
            Qt.WindowModality.NonModal,
        )
        self.assertIsNone(QApplication.activeModalWidget())
        self.assertIs(controller._open_settings(), settings)

        close_button = next(
            button
            for button in chat.findChildren(QPushButton)
            if button.text() == "×"
        )
        QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(chat.isVisible())

        settings.reject()
        self.app.processEvents()
        self.assertIsNone(controller._settings_window)

        applied = controller._open_settings()
        with patch("main.set_start_with_windows") as set_startup:
            applied.accept()
            self.app.processEvents()
        controller.pet_window.apply_config.assert_called_once()
        controller._apply_tray_theme.assert_called_once()
        controller._restart_live_monitor.assert_called_once()
        set_startup.assert_called_once()
        self.assertIsNone(controller._settings_window)
        chat.close()

    def test_pet_lab_mode_hint_discloses_generation_count(self):
        lab = PetLabWindow(self.config, self.package_manager)
        self.assertEqual(lab.mode_input.currentData(), "basic")
        self.assertIn("5 次图像生成", lab.status.text())
        self.assertFalse(lab.symmetry_input.isEnabled())
        self.assertEqual(lab.call_budget_input.value(), 8)

        lab.mode_input.setCurrentIndex(
            lab.mode_input.findData("standard")
        )

        self.assertIn("20 次图像生成", lab.status.text())
        self.assertIn("实际费用", lab.status.text())
        self.assertEqual(lab.call_budget_input.value(), 23)

        lab.mode_input.setCurrentIndex(
            lab.mode_input.findData("full")
        )

        self.assertIn("45 次图像生成", lab.status.text())
        self.assertIn("四向贴边", lab.status.text())
        self.assertIn("显著高于", lab.status.text())
        self.assertTrue(lab.symmetry_input.isEnabled())
        self.assertEqual(lab.call_budget_input.value(), 48)

        lab.symmetry_input.setChecked(True)

        self.assertIn("39 次图像生成", lab.status.text())
        self.assertEqual(lab.call_budget_input.minimum(), 39)
        self.assertEqual(lab.call_budget_input.value(), 42)
        lab.close()

    def test_pet_lab_run_list_displays_persisted_api_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            run = store.create(
                run_id="usage-run",
                request={
                    "pet_name": "Nova",
                    "mode": "basic",
                    "max_api_calls": 8,
                },
                task_ids=["canonical"],
            )
            store.update_task(
                run["id"],
                "canonical",
                "failed",
                increment_attempt=True,
            )
            store.record_api_call(
                run["id"],
                "canonical",
                duration_ms=1500,
                outcome="failed",
                error_category="timeout",
            )

            lab = PetLabWindow(
                self.config,
                self.package_manager,
                run_store=store,
            )

            self.assertIn("API 1/8", lab.run_input.currentText())
            self.assertIn("1.5s", lab.run_input.currentText())
            self.assertIn("生成失败", lab.run_health.text())
            self.assertTrue(lab.diagnostic_btn.isEnabled())
            self.assertTrue(lab.copy_diagnostic_btn.isEnabled())
            self.assertTrue(lab.copy_issue_btn.isEnabled())
            self.assertTrue(lab.export_diagnostic_btn.isEnabled())
            self.assertTrue(lab.inspect_issue_btn.isEnabled())

            lab._copy_generation_summary()

            copied = QApplication.clipboard().text()
            self.assertIn("Pixkin 伙伴工坊技术摘要", copied)
            self.assertNotIn("usage-run", copied)

            lab._copy_github_issue_markdown()

            copied_issue = QApplication.clipboard().text()
            self.assertIn("## 问题概述", copied_issue)
            self.assertIn("## 自动诊断", copied_issue)
            self.assertNotIn("usage-run", copied_issue)

            destination = Path(directory) / "issue.zip"
            with (
                patch(
                    "ui.pet_lab_window.QFileDialog.getSaveFileName",
                    return_value=(str(destination), "ZIP"),
                ),
                patch.object(QMessageBox, "information"),
            ):
                lab._export_generation_issue_bundle()

            self.assertTrue(destination.is_file())
            with (
                patch(
                    "ui.pet_lab_window.QFileDialog.getOpenFileName",
                    return_value=(str(destination), "ZIP"),
                ),
                patch.object(QMessageBox, "information") as information,
            ):
                lab._inspect_generation_issue_bundle()

            information.assert_called_once()
            self.assertIn("检查通过", information.call_args.args[1])
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

    def test_pet_lab_ignores_finish_from_superseded_worker(self):
        lab = PetLabWindow(self.config, self.package_manager)
        old_worker = MagicMock()
        current_worker = MagicMock()
        lab.worker = current_worker
        lab.hatch_btn.setDisabled(True)

        lab._on_worker_finished(old_worker)

        self.assertIs(lab.worker, current_worker)
        self.assertFalse(lab.hatch_btn.isEnabled())
        old_worker.deleteLater.assert_called_once_with()
        lab.close()

    def test_pet_lab_prioritizes_qa_error_states_for_retry(self):
        report = {
            "errors": [
                {"code": "duplicate_actions", "states": ["talking", "idle"]},
                {"code": "unsafe_margin", "state": "alerting"},
            ],
            "expected_states": [
                "idle", "talking", "dragging", "alerting"
            ],
        }
        self.assertEqual(
            PetLabWindow._qa_retry_candidates(report),
            ["talking", "idle", "alerting", "dragging"],
        )

    def test_pet_lab_final_confirmation_prefers_qa_contact_sheet(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            store = PetGenerationRunStore(Path(directory) / "runs")
            run = store.create(
                run_id="preview-run",
                request={"pet_name": "Nova"},
                task_ids=["canonical", "idle"],
            )
            sheet = root / "assets" / "pixkin" / "pip-avatar.png"
            store.update_stage(
                run["id"],
                "final_review",
                status="needs_review",
                artifacts={"qa_contact_sheet": str(sheet)},
            )
            manager = MagicMock()
            lab = PetLabWindow(
                self.config, manager, run_store=store
            )
            lab.active_run_id = run["id"]
            dialog = MagicMock()

            lab._set_package_preview(dialog, "generated.zip")

            dialog.setIconPixmap.assert_called_once()
            manager.read_zip_preview.assert_not_called()
            lab.close()

    def test_pet_lab_discovers_and_opens_animation_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store = PetGenerationRunStore(base / "runs")
            run = store.create(
                run_id="animation-preview",
                request={"pet_name": "Nova"},
                task_ids=["idle"],
            )
            preview = store.workspace(run["id"]) / "qa/idle.gif"
            preview.parent.mkdir(parents=True)
            Image.new("RGBA", (192, 208), (120, 70, 190, 255)).save(
                preview, "GIF"
            )
            store.update_stage(
                run["id"],
                "final_review",
                status="needs_review",
                artifacts={
                    "animation_previews": {"idle": str(preview)}
                },
            )
            lab = PetLabWindow(
                self.config, self.package_manager, run_store=store
            )
            lab.active_run_id = run["id"]

            previews = lab._available_animation_previews()

            self.assertEqual(previews, {"idle": preview})
            with patch.object(
                QDialog,
                "exec",
                return_value=QDialog.DialogCode.Rejected,
            ):
                lab._show_animation_previews(previews)
            lab.close()

    def test_pet_lab_can_switch_to_an_archived_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store = PetGenerationRunStore(base / "runs")
            run = store.create(
                run_id="candidate-ui",
                request={"pet_name": "Nova"},
                task_ids=["idle"],
            )
            workspace = store.workspace(run["id"])
            colors = ((180, 70, 120, 255), (70, 100, 190, 255))
            for index, color in enumerate(colors, start=1):
                candidate_id = f"attempt-{index:03d}"
                candidate_dir = (
                    workspace / "candidates" / "idle" / candidate_id
                )
                candidate_dir.mkdir(parents=True)
                source = candidate_dir / "source.png"
                sprite = candidate_dir / "sprite.png"
                Image.new("RGBA", (192, 208), color).save(source)
                Image.new("RGBA", (192, 208), color).save(sprite)
                store.record_candidate(
                    run["id"],
                    "idle",
                    candidate_id=candidate_id,
                    source_artifact=source.relative_to(
                        workspace
                    ).as_posix(),
                    sprite_artifact=sprite.relative_to(
                        workspace
                    ).as_posix(),
                )
            active = workspace / "images" / "idle.png"
            active.parent.mkdir()
            shutil.copy2(
                workspace
                / "candidates/idle/attempt-002/sprite.png",
                active,
            )
            store.select_candidate(
                run["id"],
                "idle",
                "attempt-002",
                active_artifact="images/idle.png",
            )
            lab = PetLabWindow(
                self.config, self.package_manager, run_store=store
            )

            def choose_first(dialog):
                candidates = dialog.findChild(QListWidget)
                candidates.setCurrentRow(0)
                return QDialog.DialogCode.Accepted

            with patch.object(QDialog, "exec", choose_first):
                switched = lab._choose_candidate_version(
                    run["id"], "idle", continue_flow=False
                )

            self.assertTrue(switched)
            task = store.load(run["id"])["tasks"]["idle"]
            self.assertEqual(
                task["selected_candidate"], "attempt-001"
            )
            first = (
                workspace
                / "candidates/idle/attempt-001/sprite.png"
            )
            self.assertEqual(active.read_bytes(), first.read_bytes())
            lab.close()

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

    def test_existing_secure_keys_are_not_duplicated_in_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = ConfigManager(str(base / "config.json"))
            manager = CharacterPackageManager(
                config, base / "characters"
            )
            settings = SettingsWindow(config, manager)
            settings.api_key_input.setText("existing-chat-key")
            with (
                patch.object(
                    SecretStore,
                    "get_api_key",
                    return_value="existing-chat-key",
                ),
                patch.object(
                    SecretStore,
                    "set_api_key",
                    return_value=False,
                ),
                patch.object(QMessageBox, "warning") as settings_warning,
                patch.object(settings, "accept") as accept,
            ):
                settings._save()

            self.assertEqual(config.get("api", "api_key"), "")
            settings_warning.assert_not_called()
            accept.assert_called_once()
            settings.close()

            lab = PetLabWindow(config, manager)
            lab.reference_paths = [base / "reference.png"]
            lab.name_input.setText("安全伙伴")
            lab.api_key.setText("existing-image-key")
            with (
                patch.object(
                    SecretStore,
                    "get_image_api_key",
                    return_value="existing-image-key",
                ),
                patch.object(
                    SecretStore,
                    "set_image_api_key",
                    return_value=False,
                ),
                patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch.object(QMessageBox, "warning") as lab_warning,
                patch.object(lab, "_start_worker") as start_worker,
            ):
                lab._start_hatch()

            self.assertEqual(
                config.get("image_generation", "api_key"),
                "",
            )
            lab_warning.assert_not_called()
            start_worker.assert_called_once()
            lab.close()

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
