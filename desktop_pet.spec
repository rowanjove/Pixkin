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
# Qt6Core resolves the Windows ICU shim from System32.  The build environment
# also exposes Poppler's ICU 78 DLLs on PATH; PyInstaller would otherwise copy
# those incompatible versioned exports into the app and make QtCore fail to
# load (Qt imports undecorated ICU symbols).  Keep the package aligned with the
# previously shipped build and let the supported Windows runtime provide ICU.
a.binaries = [
    entry for entry in a.binaries
    if not Path(str(entry[0])).name.lower().startswith("icu")
]
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
