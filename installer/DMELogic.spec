# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for DMELogic with Nova.

Build (from the repo root):
    pyinstaller installer\\DMELogic.spec

Produces dist\\DMELogic\\DMELogic.exe (+ _internal\\). The Inno Setup script
(installer\\DMELogic.iss) packages that output into an installer.

Data layout when frozen (see dmelogic/paths.py):
    _internal/assets   <- icons, images, fee schedules
    _internal/theme    <- QSS / theme assets
Runtime data is NOT bundled; it lives under C:\\ProgramData\\DMELogic.
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH).resolve().parent          # repo root (installer/ -> ..)
SRC = ROOT / "src"

block_cipher = None

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(SRC / "dmelogic" / "theme_assets"), "theme"),
]
# matplotlib ships data files it needs at runtime.
datas += collect_data_files("matplotlib")

hiddenimports = []
# The quarantined legacy module + Nova modules are imported lazily/dynamically.
hiddenimports += [
    "dmelogic.legacy.legacy_app",
    "dmelogic.nova_agent",
    "dmelogic.nova_ringcentral",
    "dmelogic.nova_remittance_parser",
    "dmelogic.nova_ui_server",
    "dmelogic.dmelogic_api",
]
hiddenimports += collect_submodules("dmelogic")
# Common deps with dynamic imports.
hiddenimports += ["pytesseract", "fitz", "argon2", "qrcode", "openpyxl", "reportlab"]
# Nova optional deps — harmless to include; the Nova-less build can exclude them.
hiddenimports += collect_submodules("fastapi") + collect_submodules("uvicorn")

a = Analysis(
    [str(SRC / "dmelogic" / "app.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide6", "PySide2"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DMELogic",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                       # GUI app — no console window
    icon=str(ROOT / "assets" / "DMELogic Icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DMELogic",
)
