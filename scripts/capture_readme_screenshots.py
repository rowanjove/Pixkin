"""Render privacy-safe Pixkin UI screenshots for the README.

The capture uses the same Qt widgets as the application with an isolated,
temporary configuration and the offscreen Qt backend. No user data, API keys,
or live provider calls are involved.
"""

# ruff: noqa: E402

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QWidget

from core.config import ConfigManager
from core.pet_generation_run import PetGenerationRunStore
from core.character_package import CharacterPackageManager
from ui.chat_window import ChatBubbleWindow
from ui.pet_lab_window import PetLabWindow
from ui.settings_window import SettingsWindow


OUTPUT = ROOT / "docs" / "screenshots"


def save_widget(widget, filename: str) -> None:
    widget.show()
    QApplication.processEvents()
    image = widget.grab()
    if image.isNull():
        raise RuntimeError(f"无法捕获 Qt 界面：{filename}")
    target = OUTPUT / filename
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"无法写入截图：{target}")
    print(f"{target} ({image.width()}x{image.height()})")


def apply_screenshot_font(widget, font: QFont) -> None:
    widget.setFont(font)
    for child in widget.findChildren(QWidget):
        child.setFont(font)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Pixkin")
    font_paths = (
        Path(r"C:\Windows\Fonts\NotoSansHK-VF.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    )
    families = []
    for font_path in font_paths:
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    if not families:
        raise RuntimeError("无法加载截图字体")
    screenshot_font = QFont()
    screenshot_font.setFamilies(families)
    screenshot_font.setPointSize(10)
    app.setFont(screenshot_font)

    with tempfile.TemporaryDirectory(prefix="pixkin-readme-") as directory:
        base = Path(directory)
        config = ConfigManager(str(base / "config.json"))
        config.update_section(
            "app",
            {"theme": "dark", "first_run_complete": True},
        )
        package_manager = CharacterPackageManager(
            config,
            base / "characters",
        )

        settings = SettingsWindow(config, package_manager)
        apply_screenshot_font(settings, screenshot_font)
        settings.resize(1040, 760)
        save_widget(settings, "settings-center.png")

        chat = ChatBubbleWindow(config)
        apply_screenshot_font(chat, screenshot_font)
        chat.resize(560, 680)
        chat.set_character("山山")
        chat.set_tool_schemas(
            [{"function": {"name": "read_clipboard"}}]
        )
        chat.append_message("user", "帮我把今天的工作拆成三个小步骤")
        chat.append_message(
            "assistant",
            "当然。先收集目标，再拆成可执行的小步，最后留出一次复盘。\n\n"
            "我会把每一步控制在今天能完成的范围内。",
        )
        save_widget(chat, "chat-window.png")

        lab = PetLabWindow(
            config,
            package_manager,
            run_store=PetGenerationRunStore(base / "pet-lab" / "runs"),
        )
        apply_screenshot_font(lab, screenshot_font)
        lab.resize(980, 760)
        save_widget(lab, "pet-lab.png")

        settings.close()
        chat.close()
        lab.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
