"""settings.py — Load and save persistent settings.

Settings live in ``settings.json`` inside the data root (see
:mod:`dmelogic.config`). On a fresh install the First-Run Wizard writes the
initial file; defaults fall back to the canonical data-root subfolders.
"""

import json
import os
from typing import Optional

from .config import SETTINGS_FILE, _default_db_folder, debug_log


# Global in-memory cache to avoid repeated disk reads.
_SETTINGS_CACHE: Optional[dict] = None


def _apply_default_settings(data: dict) -> tuple[dict, bool]:
    """Fill in required defaults when the settings file is partial/clobbered."""
    changed = False
    if not isinstance(data, dict):
        data = {}
        changed = True

    if not data.get("db_folder"):
        data["db_folder"] = _default_db_folder()
        changed = True

    for key, default in (
        ("last_open_folder", ""),
        ("fee_schedule_path", ""),
        ("theme", "light"),
    ):
        if key not in data:
            data[key] = default
            changed = True

    return data, changed


def load_settings(create_if_missing: bool = False) -> dict:
    """Load settings.json (cached after first read).

    Args:
        create_if_missing: If True, write default settings when the file is
            absent. If False (default), return an empty dict and let the
            First-Run Wizard handle initial setup.
    """
    global _SETTINGS_CACHE

    if _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE

    if not os.path.exists(SETTINGS_FILE):
        if create_if_missing:
            settings = {
                "db_folder": _default_db_folder(),
                "last_open_folder": "",
                "fee_schedule_path": "",
                "theme": "light",
            }
            save_settings(settings)
            return settings
        return {}

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        data, changed = _apply_default_settings(data)
        if changed:
            save_settings(data)
        else:
            _SETTINGS_CACHE = data
        return data
    except Exception as e:
        debug_log(f"Failed to load settings.json: {e}")
        return {}


def save_settings(data: dict):
    """Safely write settings.json and update the cache."""
    global _SETTINGS_CACHE
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _SETTINGS_CACHE = data
    except Exception as e:
        debug_log(f"Failed to save settings.json: {e}")


def invalidate_settings_cache():
    """Clear the settings cache to force a reload from disk."""
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = None
