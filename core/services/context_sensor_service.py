"""Lightweight, privacy-first desktop context sensor for Windows."""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
)

LOGGER = logging.getLogger("desktop_pet.sensor")


@dataclass(frozen=True)
class DesktopContextSnapshot:
    """In-memory observation of current desktop state."""

    active_window_title: str
    process_name: str
    idle_seconds: float
    is_fullscreen: bool
    timestamp: float
    clipboard_text: str = ""
    selected_text: str = ""
    screen_snapshot: bytes = b""
    system_state: str = ""

    @property
    def has_clipboard(self) -> bool:
        return bool(self.clipboard_text.strip())

    @property
    def has_screen_snapshot(self) -> bool:
        return bool(self.screen_snapshot)


def _default_get_active_window_info() -> tuple[str, str, bool]:
    """Inspect the foreground window via Win32 API."""
    try:
        import win32api
        import win32gui
        import win32process
        import os

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindow(hwnd):
            return "", "", False

        title = str(win32gui.GetWindowText(hwnd) or "").strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = ""
        if pid:
            # Query process executable name if available
            try:
                import win32con
                h_proc = win32api.OpenProcess(
                    win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                if h_proc:
                    try:
                        import win32process
                        full_path = win32process.GetModuleFileNameEx(h_proc, 0)
                        process_name = os.path.basename(full_path)
                    finally:
                        win32api.CloseHandle(h_proc)
            except Exception:
                pass

        # Check if fullscreen
        is_fullscreen = False
        try:
            rect = win32gui.GetWindowRect(hwnd)
            try:
                # 2 = win32con.MONITOR_DEFAULTTONEAREST
                h_mon = win32api.MonitorFromWindow(hwnd, 2)
                mon_info = win32api.GetMonitorInfo(h_mon)
                mon_rect = mon_info.get("Monitor")
            except Exception:
                mon_rect = None

            if mon_rect:
                covers_screen = (
                    rect[0] <= mon_rect[0]
                    and rect[1] <= mon_rect[1]
                    and rect[2] >= mon_rect[2]
                    and rect[3] >= mon_rect[3]
                )
            else:
                screen_w = win32api.GetSystemMetrics(0)
                screen_h = win32api.GetSystemMetrics(1)
                covers_screen = (
                    rect[0] <= 0
                    and rect[1] <= 0
                    and rect[2] >= screen_w
                    and rect[3] >= screen_h
                )

            if covers_screen:
                style = win32gui.GetWindowLong(hwnd, -16)  # GWL_STYLE
                # WS_CAPTION = 0x00C00000; fullscreen borderless windows don't have caption
                if not (style & 0x00C00000):
                    is_fullscreen = True
        except Exception:
            pass

        return title, process_name, is_fullscreen
    except Exception as exc:
        LOGGER.debug("读取前台窗口信息失败: %s", exc)
        return "", "", False


def _default_get_idle_seconds() -> float:
    """Calculate idle seconds since last user keyboard/mouse input."""
    try:
        import win32api

        last_input_tick = win32api.GetLastInputInfo()
        current_tick = win32api.GetTickCount()
        elapsed_ms = max(0, current_tick - last_input_tick)
        return elapsed_ms / 1000.0
    except Exception as exc:
        LOGGER.debug("读取系统空闲时间失败: %s", exc)
        return 0.0


def _default_get_clipboard_text() -> str:
    """Read plain text from the clipboard without retaining it on disk."""
    try:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
                return ""
            return str(win32clipboard.GetClipboardData(13) or "")[:20_000]
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        LOGGER.debug("读取剪贴板失败: %s", exc)
        return ""


def _default_get_screen_snapshot() -> bytes:
    """Capture a bounded PNG in memory; never write a screenshot to disk."""
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(all_screens=False)
        image.thumbnail((1600, 1000))
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        return data if len(data) <= 8 * 1024 * 1024 else b""
    except Exception as exc:
        LOGGER.debug("截图失败: %s", exc)
        return b""


def _default_get_selected_text() -> str:
    """Return selected text only when a platform adapter explicitly supplies it.

    Windows has no safe, universal read-only selected-text API. The default
    is therefore empty; UI Automation integrations can inject a resolver
    without changing the sensor or permission boundary.
    """
    return ""


def _default_get_system_state() -> str:
    """Return a short optional system-state marker without persistence."""
    return ""


class ContextSensorService:
    """Continuously observe desktop context in memory with zero persistence."""

    def __init__(
        self,
        *,
        get_window_info: Optional[Callable[[], tuple[str, str, bool]]] = None,
        get_idle_seconds: Optional[Callable[[], float]] = None,
        get_clipboard_text: Optional[Callable[[], str]] = None,
        get_selected_text: Optional[Callable[[], str]] = None,
        get_screen_snapshot: Optional[Callable[[], bytes]] = None,
        get_system_state: Optional[Callable[[], str]] = None,
        time_fn: Callable[[], float] = time.time,
        permission_service: Optional[ContextPermissionService] = None,
    ):
        self._get_window_info = get_window_info or _default_get_active_window_info
        self._get_idle_seconds = get_idle_seconds or _default_get_idle_seconds
        self._get_clipboard_text = get_clipboard_text or _default_get_clipboard_text
        self._get_selected_text = get_selected_text or _default_get_selected_text
        self._get_screen_snapshot = get_screen_snapshot or _default_get_screen_snapshot
        self._get_system_state = get_system_state or _default_get_system_state
        self._time_fn = time_fn
        # Context sensors are also embeddable outside the application root;
        # default-deny must therefore hold even when no service is injected.
        self._permission_service = permission_service or ContextPermissionService()

    def poll(self, *, resolve_ask: bool = True) -> DesktopContextSnapshot:
        """Poll context without prompting when running in a background loop."""
        now = self._time_fn()
        can_read_window = self._permission_allowed(
            PermissionResource.WINDOW_METADATA,
            resolve_ask=resolve_ask,
        )
        can_read_system = self._permission_allowed(
            PermissionResource.SYSTEM_STATE,
            resolve_ask=resolve_ask,
        )
        can_read_clipboard = self._permission_allowed(
            PermissionResource.CLIPBOARD,
            resolve_ask=resolve_ask,
        )
        if can_read_window:
            try:
                title, proc_name, is_fullscreen = self._get_window_info()
            except Exception:
                LOGGER.debug("读取注入的窗口信息失败", exc_info=True)
                title, proc_name, is_fullscreen = "", "", False
        else:
            title, proc_name, is_fullscreen = "", "", False
        try:
            idle_s = self._get_idle_seconds() if can_read_system else 0.0
        except Exception:
            LOGGER.debug("读取注入的空闲时间失败", exc_info=True)
            idle_s = 0.0
        try:
            clipboard = self._get_clipboard_text() if can_read_clipboard else ""
        except Exception:
            LOGGER.debug("读取注入的剪贴板失败", exc_info=True)
            clipboard = ""
        try:
            selected = self._get_selected_text() if can_read_clipboard else ""
        except Exception:
            LOGGER.debug("读取注入的选中文本失败", exc_info=True)
            selected = ""
        try:
            system_state = self._get_system_state() if can_read_system else ""
        except Exception:
            LOGGER.debug("读取注入的系统状态失败", exc_info=True)
            system_state = ""

        try:
            idle_value = max(0.0, float(idle_s))
        except (TypeError, ValueError):
            idle_value = 0.0

        return DesktopContextSnapshot(
            active_window_title=str(title or "")[:500],
            process_name=str(proc_name or "")[:260],
            idle_seconds=idle_value,
            is_fullscreen=bool(is_fullscreen),
            timestamp=now,
            clipboard_text=str(clipboard or "")[:20_000],
            selected_text=str(selected or "")[:20_000],
            # Periodic polling never captures the screen.  See the explicit
            # ``capture_screen_snapshot`` one-shot API below.
            screen_snapshot=b"",
            system_state=str(system_state or "")[:200],
        )

    def _permission_allowed(
        self,
        resource: PermissionResource,
        *,
        resolve_ask: bool = True,
    ) -> bool:
        return self._permission_service.decide(
            resource,
            PermissionOperation.OBSERVE,
            resolve_ask=resolve_ask,
        ).allowed

    def capture_screen_snapshot(self) -> bytes:
        """Capture one bounded screenshot after an explicit permission check."""
        if not self._permission_allowed(PermissionResource.SCREEN):
            return b""
        try:
            return bytes(self._get_screen_snapshot() or b"")[:8 * 1024 * 1024]
        except Exception:
            LOGGER.debug("读取注入的屏幕快照失败", exc_info=True)
            return b""
