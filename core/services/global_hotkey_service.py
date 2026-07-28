"""Windows global hotkeys with explicit registration and cleanup."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QAbstractNativeEventFilter


MODIFIERS = {
    "ALT": 0x0001,
    "CTRL": 0x0002,
    "SHIFT": 0x0004,
    "WIN": 0x0008,
}
VIRTUAL_KEYS = {
    **{chr(value): value for value in range(ord("A"), ord("Z") + 1)},
    **{str(value): ord(str(value)) for value in range(10)},
    **{f"F{value}": 0x6F + value for value in range(1, 13)},
}


class HotkeyError(ValueError):
    pass


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    virtual_key: int


def parse_hotkey(value: str) -> Hotkey:
    parts = [
        item.strip().upper()
        for item in str(value or "").split("+")
        if item.strip()
    ]
    if len(parts) < 2:
        raise HotkeyError("快捷键必须包含修饰键和主键")
    key_name = parts[-1]
    modifier_names = parts[:-1]
    if (
        key_name not in VIRTUAL_KEYS
        or any(item not in MODIFIERS for item in modifier_names)
        or len(set(modifier_names)) != len(modifier_names)
    ):
        raise HotkeyError(f"不支持的快捷键：{value}")
    modifiers = 0
    for name in modifier_names:
        modifiers |= MODIFIERS[name]
    return Hotkey(modifiers, VIRTUAL_KEYS[key_name])


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """Register process-wide Windows hotkeys and dispatch WM_HOTKEY."""

    WM_HOTKEY = 0x0312

    def __init__(self, application, *, register=None, unregister=None):
        super().__init__()
        self.application = application
        user32 = ctypes.windll.user32 if sys.platform == "win32" else None
        self._register = register or (
            user32.RegisterHotKey if user32 is not None else None
        )
        self._unregister = unregister or (
            user32.UnregisterHotKey if user32 is not None else None
        )
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._installed = False

    def register(self, mappings: dict[str, tuple[str, Callable]]) -> list[str]:
        self.close()
        if self._register is None:
            return list(mappings)
        conflicts = []
        for identifier, (name, (shortcut, callback)) in enumerate(
            mappings.items(), start=0x5100
        ):
            hotkey = parse_hotkey(shortcut)
            if not self._register(
                None,
                identifier,
                hotkey.modifiers | 0x4000,
                hotkey.virtual_key,
            ):
                conflicts.append(name)
                continue
            self._callbacks[identifier] = callback
        if self._callbacks:
            self.application.installNativeEventFilter(self)
            self._installed = True
        return conflicts

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32":
            return False, 0
        try:
            from ctypes import wintypes

            event = wintypes.MSG.from_address(int(message))
            if event.message == self.WM_HOTKEY:
                callback = self._callbacks.get(int(event.wParam))
                if callback is not None:
                    callback()
                    return True, 0
        except (TypeError, ValueError, OSError):
            pass
        return False, 0

    def close(self):
        if self._installed:
            self.application.removeNativeEventFilter(self)
            self._installed = False
        if self._unregister is not None:
            for identifier in tuple(self._callbacks):
                self._unregister(None, identifier)
        self._callbacks.clear()
