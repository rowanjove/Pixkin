# -*- mode: python ; coding: utf-8 -*-

import runpy
from pathlib import Path

root = Path(SPEC).resolve().parent
version_scope = runpy.run_path(str(root / "core" / "version.py"))
portable_name = version_scope["portable_executable_name"]()

a = Analysis(
    ["main.py"],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "character-packs" / "shanshan.zip"), "character-packs"),
        (str(root / "character-packs" / "linlin.zip"), "character-packs"),
        (str(root / "character-packs" / "pip.zip"), "character-packs"),
        (str(root / "character-packs" / "official-sha256.json"), "character-packs"),
        (str(root / "character-packs" / "catalog.json"), "character-packs"),
        (str(root / "assets" / "pixkin"), "assets/pixkin"),
        (str(root / "assets" / "pixkin.ico"), "assets"),
        (str(root / "assets" / "update-public-key.pem"), "assets"),
        (str(root / "CHARACTER_PACKAGE_SPEC.md"), "."),
        (str(root / "README.md"), "."),
    ],
    hiddenimports=["win32cred", "win32timezone", "yaml"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "numpy", "pandas", "matplotlib"],
    noarchive=False,
    optimize=1,
)
# Do not bundle Poppler's ICU 78 DLLs discovered through PATH.  Qt6Core uses
# the Windows ICU shim (System32); bundling the incompatible versioned exports
# breaks QtCore import in the one-file executable.
a.binaries = [
    entry for entry in a.binaries
    if not Path(str(entry[0])).name.lower().startswith("icu")
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=portable_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(root / "assets" / "pixkin.ico")],
    version=str(root / "version_info.txt"),
)
