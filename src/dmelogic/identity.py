"""
identity.py — single source of truth for the app's installed identity.

Everything that distinguishes one installation from another (display name,
data-folder name, single-instance lock, Startup launcher, registry key,
installer paths) derives from here, so a build can be re-skinned by flipping
one flag instead of editing a dozen files.

Controlled by the ``DMELOGIC_EDITION`` environment variable:

* ``release`` (default) — the shipping product: **DMELogic** /
  ``C:\\ProgramData\\DMELogic``. This is what a brand-new company installs.
* ``preview`` — a parallel **DMELogic 5** identity in its own folders, so it
  can be installed and run side-by-side with an existing DMELogic build during
  testing without touching its files, registry, shortcuts, or Startup entries.

To ship the real product, do nothing (release is default). To build the
coexistence preview, set ``DMELOGIC_EDITION=preview`` (the preview launchers
and the preview installer do this for you).
"""

from __future__ import annotations

import os

_EDITIONS = {
    "release": {
        "app_id": "DMELogic",          # slug: folders, locks, registry
        "display_name": "DMELogic",    # user-facing name
        "title": "DMELogic with Nova", # window/title bar
        "data_folder": "DMELogic",     # <ProgramData>\<data_folder>
        "publisher": "DMELogic",
    },
    "preview": {
        "app_id": "DMELogic5",
        "display_name": "DMELogic 5",
        "title": "DMELogic 5 with Nova",
        "data_folder": "DMELogic5",
        "publisher": "DMELogic",
    },
}


def _resolve_edition() -> str:
    # 1. Explicit environment override (dev runs, preview launchers).
    env = (os.environ.get("DMELOGIC_EDITION") or "").strip().lower()
    if env in _EDITIONS:
        return env

    # 2. Bundled marker baked into an installed build: an ``edition.txt`` placed
    #    next to the executable by the installer. This keeps each installed
    #    build's identity fixed without relying on a shared machine env var.
    try:
        import sys
        from pathlib import Path
        if getattr(sys, "frozen", False):
            marker = Path(sys.executable).resolve().parent / "edition.txt"
            if marker.exists():
                value = marker.read_text(encoding="utf-8").strip().lower()
                if value in _EDITIONS:
                    return value
    except Exception:
        pass

    # 3. Default: the shipping product.
    return "release"


EDITION = _resolve_edition()
_meta = _EDITIONS[EDITION]

APP_ID: str = _meta["app_id"]
APP_NAME: str = _meta["display_name"]
APP_TITLE: str = _meta["title"]
DATA_FOLDER: str = _meta["data_folder"]
APP_PUBLISHER: str = _meta["publisher"]


def is_preview() -> bool:
    return EDITION == "preview"
