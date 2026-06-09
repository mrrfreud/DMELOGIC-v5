"""settings.py — Load and save persistent settings."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional, Iterable

from .config import SETTINGS_FILE, _default_db_folder, debug_log


# Global in-memory cache to avoid repeated disk reads
_SETTINGS_CACHE: Optional[dict] = None


def _normalize_documents_fax_path(path_str: str) -> str:
    """Rewrite legacy FaxManagerData paths to the Documents-based location.

    If the user has moved the live fax tree to Documents, we should stop
    resolving against legacy C:\FaxManagerData paths even if they still exist.
    """
    if not isinstance(path_str, str) or not path_str.strip():
        return path_str

    normalized = path_str.replace('/', '\\').strip()
    lower = normalized.lower()
    marker = "faxmanagerdata"
    if marker not in lower:
        return path_str

    docs_root = Path.home() / "Documents" / "FaxManagerData"
    if not docs_root.exists():
        return path_str

    parts = Path(normalized).parts
    lower_parts = [part.lower() for part in parts]
    try:
        idx = lower_parts.index(marker)
    except ValueError:
        return path_str

    tail_parts = list(parts[idx + 1:])
    while tail_parts and tail_parts[0].lower() == marker:
        tail_parts.pop(0)

    candidate = docs_root.joinpath(*tail_parts) if tail_parts else docs_root
    return str(candidate)


def _iter_candidate_orders_dbs() -> Iterable[Path]:
    """Yield likely orders.db candidates without doing an expensive full-disk scan."""
    # Common/legacy roots (these mirror what users typically have)
    common_relative = [
        Path("Dme_Solutions") / "Data",
        Path(os.path.expanduser("~")) / "Documents" / "DmeSolutionsV1" / "Data",
        Path(os.getenv("PROGRAMDATA", "C:\\ProgramData")) / "DMELogic" / "Data",
        Path("FaxManagerData") / "Data",
        Path(os.path.expanduser("~")) / "FaxManagerData" / "Data",
    ]

    # Probe all existing drive letters for the same relative layouts.
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        for rel in common_relative:
            base = drive / rel
            db = base / "orders.db"
            if db.exists():
                yield db
            # Also consider orders*.db* next to the primary DB (premerge/dev backups)
            if base.is_dir():
                try:
                    for child in base.iterdir():
                        name = child.name.lower()
                        if child.is_file() and name.startswith("orders") and ".db" in name:
                            yield child
                except Exception:
                    pass

    # Small, low-cost extra: look for obvious backups under Desktop.
    try:
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        if desktop.is_dir():
            for root_name in ("backup to dme app", "Dme_Solutions", "FaxManagerData"):
                root = desktop / root_name
                if root.exists() and root.is_dir():
                    for p in root.rglob("orders.db"):
                        yield p
    except Exception:
        pass


def _safe_orders_count(db_path: Path) -> int:
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        if "orders" not in tables:
            conn.close()
            return -1
        cur.execute("SELECT COUNT(*) FROM orders")
        count = int(cur.fetchone()[0])
        conn.close()
        return count
    except Exception:
        return -1


def _discover_best_db_folder() -> str | None:
    """Pick the most complete DB by orders row-count (ties by size/mtime)."""
    best: tuple[int, int, float, Path] | None = None
    seen: set[Path] = set()

    for db in _iter_candidate_orders_dbs():
        try:
            db = db.resolve()
        except Exception:
            pass
        if db in seen:
            continue
        seen.add(db)

        count = _safe_orders_count(db)
        if count < 0:
            continue

        try:
            st = db.stat()
            size = int(st.st_size)
            mtime = float(st.st_mtime)
        except Exception:
            size, mtime = 0, 0.0

        score = (count, size, mtime, db)
        if best is None or score > best:
            best = score

    if best is None:
        return None
    return str(best[3].parent)


def _apply_default_settings(data: dict) -> tuple[dict, bool]:
    """Merge in required defaults when settings file was clobbered/partial."""
    changed = False
    if not isinstance(data, dict):
        data = {}
        changed = True

    # Ensure db_folder exists (common failure mode: overwritten settings).
    if not data.get("db_folder"):
        discovered = _discover_best_db_folder()
        data["db_folder"] = discovered or _default_db_folder()
        changed = True

    # Safe defaults (only if missing)
    if "last_open_folder" not in data:
        data["last_open_folder"] = ""
        changed = True
    if "fee_schedule_path" not in data:
        data["fee_schedule_path"] = ""
        changed = True
    if "theme" not in data:
        data["theme"] = "light"
        changed = True

    for key in ("fax_folder", "ocr_folder", "last_folder"):
        value = data.get(key)
        normalized = _normalize_documents_fax_path(value)
        if value != normalized:
            data[key] = normalized
            changed = True

    return data, changed


def load_settings(create_if_missing: bool = False) -> dict:
    """
    Load settings.json.
    
    Args:
        create_if_missing: If True, create default settings if file doesn't exist.
                          If False (default), return empty dict if file doesn't exist.
                          The First-Run Wizard should handle initial setup.
    
    Cached in memory after first read for performance.
    """
    global _SETTINGS_CACHE
    
    # Return cached settings if available
    if _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE
    
    # Load from disk
    if not os.path.exists(SETTINGS_FILE):
        if create_if_missing:
            settings = {
                "db_folder": _default_db_folder(),
                "last_open_folder": "",
                "fee_schedule_path": "",
                "theme": "light"
            }
            save_settings(settings)
            return settings
        # Don't auto-create - let First-Run Wizard handle it
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
    """
    Safely write settings.json and update cache.
    """
    global _SETTINGS_CACHE
    
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # Update cache after successful write
        _SETTINGS_CACHE = data
    except Exception as e:
        debug_log(f"Failed to save settings.json: {e}")


def invalidate_settings_cache():
    """
    Clear the settings cache to force reload from disk.
    Useful for testing or when settings are modified externally.
    """
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = None
