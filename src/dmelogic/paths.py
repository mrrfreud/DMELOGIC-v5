"""
paths.py — Centralized path management for DMELogic.

Two distinct roots, never the same place:

* **Install root** — where the application code/assets live (Program Files when
  installed, the repo when running from source). Read-only at runtime.
* **Data root** — where everything the app reads/writes lives (databases,
  scans, backups, exports, logs). Defaults to ``C:\\ProgramData\\DMELogic`` and
  is resolved by :func:`dmelogic.config.data_root`.

All runtime-data helpers below hang off the data root. Each accepts an optional
per-folder override from settings.json (e.g. ``db_folder``, ``ocr_folder``) so
an administrator can relocate individual trees — for example pointing
``ocr_folder`` at an external drive or a shared network share.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .config import (
    _default_db_folder,
    _default_backup_folder,
    _default_scans_folder,
    data_root,
    data_subdir,
    DEBUG_LOG_FILE,
)

logger = logging.getLogger("paths")


# ---- install / project root ----
# src/dmelogic/paths.py → parents[2] is the repository (install) root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _settings():
    """Best-effort settings.json loader (never raises)."""
    try:
        from .settings import load_settings
        return load_settings() or {}
    except Exception:
        return {}


def _override_dir(key: str) -> Path | None:
    """Return a configured override directory for ``key`` if set and usable."""
    value = (_settings().get(key) or "").strip()
    if not value:
        return None
    try:
        p = Path(value)
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        logger.warning("Configured %s is not usable: %s", key, value)
        return None


# ---- install-root resources (frozen-aware) ----
def get_project_root() -> Path:
    """Return the install/project root in both dev and frozen (PyInstaller) modes."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def _get_internal_dir() -> Path:
    """Directory holding bundled, read-only data files."""
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).resolve().parent / "_internal"
    return PROJECT_ROOT


def get_assets_dir() -> Path:
    """Bundled assets directory (icons, images, fee schedules)."""
    if getattr(sys, "frozen", False):
        return _get_internal_dir() / "assets"
    return PROJECT_ROOT / "assets"


def get_theme_dir() -> Path:
    """Bundled theme/QSS directory."""
    if getattr(sys, "frozen", False):
        return _get_internal_dir() / "theme"
    # Source layout: QSS ships inside the package.
    pkg_theme = Path(__file__).resolve().parent / "theme_assets"
    if pkg_theme.exists():
        return pkg_theme
    return PROJECT_ROOT / "assets"


# ---- data root subfolders ----
def db_dir() -> Path:
    """Database folder. Override: settings ``db_folder``; else <data_root>/Databases."""
    override = _override_dir("db_folder")
    if override is not None:
        return override
    return Path(_default_db_folder())


def backup_dir() -> Path:
    """Backups folder. Override: settings ``backup_folder``; else <data_root>/Backups."""
    override = _override_dir("backup_folder")
    if override is not None:
        return override
    return Path(_default_backup_folder())


def fax_root() -> Path:
    """Root for fax/document trees. Override: settings ``fax_folder``; else data_root."""
    override = _override_dir("fax_folder")
    if override is not None:
        return override
    return data_root()


def ocr_folder() -> Path:
    """Folder holding scanned/OCR'd documents.

    Override: settings ``ocr_folder`` (use this to move scans to an external or
    network drive); else <data_root>/Scans.
    """
    override = _override_dir("ocr_folder")
    if override is not None:
        return override
    return Path(_default_scans_folder())


def ocr_cache_db() -> Path:
    """OCR index/cache database, kept alongside the scans folder."""
    return ocr_folder().parent / "ocr_cache.db"


def delivery_tickets_folder() -> Path:
    """Scanned delivery confirmations. Override: ``delivery_tickets_folder``."""
    override = _override_dir("delivery_tickets_folder")
    if override is not None:
        return override
    return data_subdir("DeliveryTickets")


def fax_packets_dir() -> Path:
    return data_subdir("FaxPackets")


def patient_documents_dir() -> Path:
    return data_subdir("PatientDocuments")


def tickets_dir() -> Path:
    return data_subdir("Tickets")


def pod_dir() -> Path:
    """Proof of Delivery documents."""
    return data_subdir("POD")


def cmn_dir() -> Path:
    """Certificate of Medical Necessity forms."""
    return data_subdir("CMN")


def hcfa_1500_exports_dir() -> Path:
    """HCFA-1500 form exports."""
    p = data_subdir("Exports") / "HCFA-1500"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ub04_exports_dir() -> Path:
    """UB-04 form exports."""
    p = data_subdir("Exports") / "UB-04"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---- logs ----
def get_logs_dir() -> Path:
    """Logs directory under the data root (writable in dev and frozen modes)."""
    return data_subdir("Logs")


def logs_dir() -> Path:
    """Alias kept for backwards compatibility."""
    return get_logs_dir()


def debug_log_path() -> Path:
    """Path to the debug log file in the centralized Logs directory."""
    return get_logs_dir() / DEBUG_LOG_FILE


# ---- document resolution ----
def resolve_document_path(filename_or_path: str) -> Path:
    """Resolve a document reference (filename or full path) to an absolute Path.

    Storage convention: the database stores **filenames only**; legacy records
    may contain full absolute paths. Resolution order:

    1. An absolute path that exists → use it as-is.
    2. The scans (``ocr_folder``) root, then the delivery-tickets root.
    3. A recursive search by basename under those roots (handles nested
       year/month/letter subfolders).
    """
    raw = (filename_or_path or "").strip().strip('"').strip("'")
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return p

    root = ocr_folder()
    delivery_root = delivery_tickets_folder()
    base = p.name

    candidates: list[Path] = []
    if raw and not p.is_absolute():
        candidates.append(root / p)
    if base:
        candidates.append(root / base)
        candidates.append(delivery_root / base)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    if base:
        for search_root in (root, delivery_root):
            try:
                for match in search_root.rglob(base):
                    if match.is_file():
                        return match
            except OSError:
                pass

    return candidates[0] if candidates else root / base
