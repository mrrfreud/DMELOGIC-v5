# Building the DMELogic installer

Two steps: build the app bundle with PyInstaller, then compile the installer
with Inno Setup.

## Prerequisites

- Python 3.11+ with the project installed: `pip install -e .[build,nova]`
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (`iscc` on PATH)
- *(Optional)* A portable Tesseract OCR copied into `vendor/tesseract/` so OCR
  works without a separate system install.

## 1. Build the application bundle

From the repository root:

```powershell
pyinstaller installer\DMELogic.spec
```

This produces `dist\DMELogic\DMELogic.exe` plus an `_internal\` folder with all
dependencies and bundled assets/theme.

## 2. Compile the installer

```powershell
iscc installer\DMELogic.iss
```

The signed-ready setup lands in `installer\Output\DMELogic_Setup_5.0.0.exe`.

## What the installer does

- Installs the app to `C:\Program Files\DMELogic` (read-only at runtime).
- Creates the **shared data root** `C:\ProgramData\DMELogic` with all runtime
  subfolders (`Databases`, `Backups`, `Scans`, `Logs`, `Exports`, …), writable
  by all users. Patient data never lives in the install folder.
- Leaves the data root intact on uninstall so reinstalls/upgrades keep data.

## Nova-less edition

To produce the AI-free build:

1. `pip install -e .[build]` (omit the `nova` extra), and
2. set `[nova] enabled = false` in the bundled `config.toml`, or ship with the
   `DMELOGIC_NOVA=0` environment variable.

See [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for how the feature flag
is resolved.
