"""开发期窗口预览：py -3.11 preview_ui.py [chat|settings|pet]。"""

import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import QApplication

from core.config import ConfigManager
from core.character_package import CharacterPackageManager
from ui.chat_window import ChatBubbleWindow
from ui.onboarding_window import FirstRunWindow
from ui.pet_lab_window import PetLabWindow
from ui.pet_window import PetWindow
from ui.settings_window import SettingsWindow


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    config = ConfigManager("config.json")
    mode = sys.argv[1] if len(sys.argv) > 1 else "chat"

    if mode == "render":
        return render_previews(app, config)
    elif mode == "settings":
        window = SettingsWindow(config)
    elif mode == "pet":
        window = PetWindow(config)
    else:
        window = ChatBubbleWindow()
        # 预览时使用普通窗口，便于截图工具捕获；正式运行仍是无任务栏悬浮卡片。
        window.setWindowFlags(Qt.WindowType.Window)
        window.append_message("user", "今天有什么可以帮我？")
        window.append_message("assistant", "我在呀。可以陪你聊天，也可以帮你打开常用工具。")
        window.show_alert_bubble("开播啦！", "你关注的主播【示例主播】正在 B 站直播。")

    window.show()
    return app.exec()


def render_previews(app, config):
    output = Path("artifacts")
    output.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as directory:
        preview_config = ConfigManager(str(Path(directory) / "config.json"))
        manager = CharacterPackageManager(
            preview_config, Path(directory) / "characters"
        )
        package = manager.import_zip(
            str(Path("character-packs/shanshan.zip").resolve())
        )
        manager.import_zip(
            str(Path("character-packs/linlin.zip").resolve()),
            activate=False,
        )
        manager.import_zip(
            str(Path("character-packs/pip.zip").resolve()),
            activate=False,
        )

        onboarding = FirstRunWindow(manager)
        onboarding.show()
        app.processEvents()
        onboarding.grab().save(str(output / "onboarding-preview.png"))

        preview_config.update_section("app", {"theme": "light"})
        chat = ChatBubbleWindow(preview_config)
        chat.set_character(package.name, package.preview)
        chat.append_message("user", "今天有什么可以帮我？")
        chat.append_message("assistant", "我在呀。可以陪你聊天，也可以帮你打开常用工具。")
        chat.show_alert_bubble("开播啦！", "你关注的主播【示例主播】正在 B 站直播。")
        app.processEvents()
        chat.grab().save(str(output / "chat-preview-light.png"))
        preview_config.update_section("app", {"theme": "dark"})
        chat.apply_theme()
        app.processEvents()
        chat.grab().save(str(output / "chat-preview-dark.png"))
        preview_config.update_section("app", {"theme": "light"})

        settings = SettingsWindow(preview_config, manager)
        settings._add_room_row({
            "enabled": True,
            "platform": "bilibili",
            "room_id": "123456",
            "anchor_name": "示例主播",
        })
        settings._add_room_row({
            "enabled": True,
            "platform": "douyin",
            "room_id": "https://live.douyin.com/example",
            "anchor_name": "抖音主播",
        })
        settings.show()
        app.processEvents()
        settings.grab().save(str(output / "settings-preview.png"))
        settings.navigation.setCurrentRow(1)
        app.processEvents()
        settings.grab().save(str(output / "character-settings-preview.png"))
        settings.navigation.setCurrentRow(2)
        app.processEvents()
        settings.grab().save(str(output / "live-settings-preview.png"))
        settings.navigation.setCurrentRow(3)
        app.processEvents()
        settings.grab().save(str(output / "theme-settings-preview.png"))
        preview_config.update_section("app", {"theme": "dark"})
        settings.apply_theme("dark")
        settings.navigation.setCurrentRow(1)
        app.processEvents()
        settings.grab().save(
            str(output / "character-settings-preview-dark.png")
        )
        settings.navigation.setCurrentRow(2)
        app.processEvents()
        settings.grab().save(
            str(output / "live-settings-preview-dark.png")
        )
        preview_config.update_section("app", {"theme": "light"})

        pet_lab = PetLabWindow(preview_config, manager)
        pet_lab.reference_paths = [
            str(Path("assets/pixkin/pip-chroma.png").resolve())
        ]
        pet_lab.references.addItem("pip-chroma.png")
        pet_lab.show()
        app.processEvents()
        pet_lab.grab().save(str(output / "pet-lab-preview.png"))

        pet = PetWindow(preview_config, package)
        pet.show()
        app.processEvents()
        canvas = QPixmap(300, 260)
        canvas.fill(QColor("#ECEAF4"))
        painter = QPainter(canvas)
        painter.drawPixmap(
            (300 - pet.width()) // 2,
            (260 - pet.height()) // 2,
            pet.grab(),
        )
        painter.end()
        canvas.save(str(output / "pet-preview.png"))

        onboarding.close()
        chat.close()
        settings.close()
        pet_lab.close()
        pet.close()
        pet.chat_window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
