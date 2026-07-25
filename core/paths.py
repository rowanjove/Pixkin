import os
import shutil
import sys
from pathlib import Path


APP_DIR_NAME = "Pixkin"
LEGACY_APP_DIR_NAME = "DesktopPet"


def user_data_dir() -> Path:
    """返回当前用户可写的数据目录，避免 EXE 安装到 Program Files 后无法保存。"""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        path = Path(base) / APP_DIR_NAME
        legacy = Path(base) / LEGACY_APP_DIR_NAME
    else:
        path = Path.home() / "AppData" / "Local" / APP_DIR_NAME
        legacy = Path.home() / "AppData" / "Local" / LEGACY_APP_DIR_NAME
    if not path.exists() and legacy.is_dir():
        try:
            shutil.copytree(legacy, path)
        except OSError:
            pass
    path.mkdir(parents=True, exist_ok=True)
    return path


def characters_dir() -> Path:
    path = user_data_dir() / "characters"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = user_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(relative: str) -> Path:
    """兼容源码运行与 PyInstaller onedir/onefile 的只读资源路径。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / relative


def executable_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.executable)
