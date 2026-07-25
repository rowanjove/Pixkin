import subprocess
import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Pixkin"


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    main_py = Path(__file__).resolve().parents[1] / "main.py"
    return f'"{pythonw}" "{main_py}" --minimized'


def set_start_with_windows(enabled: bool) -> bool:
    """写入当前用户 Run 项，不需要管理员权限。"""
    if sys.platform != "win32":
        return False
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, _startup_command()
            )
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
    return True


def is_start_with_windows_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
