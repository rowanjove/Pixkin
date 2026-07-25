# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

root = Path(SPEC).resolve().parent

a = Analysis(
    ["main.py"],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "character-packs" / "shanshan.zip"), "character-packs"),
        (str(root / "character-packs" / "linlin.zip"), "character-packs"),
        (str(root / "character-packs" / "pip.zip"), "character-packs"),
        (str(root / "character-packs" / "yeye.zip"), "character-packs"),
        (str(root / "assets" / "pixkin"), "assets/pixkin"),
        (str(root / "assets" / "pixkin.ico"), "assets"),
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
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Pixkin",
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Pixkin",
)
