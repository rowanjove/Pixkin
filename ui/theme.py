from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication


THEME_SYSTEM = "system"
THEME_DARK = "dark"
THEME_LIGHT = "light"


def configured_theme(config_manager) -> str:
    value = str(config_manager.get("app", "theme", THEME_SYSTEM) or THEME_SYSTEM)
    if value not in {THEME_SYSTEM, THEME_DARK, THEME_LIGHT}:
        return THEME_SYSTEM
    return value


def resolved_theme(config_manager, requested: str = None) -> str:
    value = requested or configured_theme(config_manager)
    if value != THEME_SYSTEM:
        return value
    app = QApplication.instance()
    if app is not None:
        try:
            if app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                return THEME_DARK
        except (AttributeError, RuntimeError):
            pass
    return THEME_LIGHT


def is_dark(config_manager, requested: str = None) -> bool:
    return resolved_theme(config_manager, requested) == THEME_DARK
