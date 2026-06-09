"""
paths.py — Centralized path management for DMELogic
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .config import _default_db_folder, _default_backup_folder, DEBUG_LOG_FILE


# ---- base project dir ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # dmelogic/ → project root


def _dedupe_adjacent_path_parts(path: Path) -> Path:
    """Collapse accidentally duplicated adjacent folders in a path.

    Example:
        C:\FaxManagerData\FaxManagerData\Faxes OCR'd
        -> C:\FaxManagerData\Faxes OCR'd
    """
    parts = list(path.parts)
    if not parts:
        return path

    cleaned: list[str] = []
    for index, part in enumerate(parts):
        if index == 0:
            cleaned.append(part)
            continue
        if cleaned[-1].lower() == part.lower():
            continue
        cleaned.append(part)
    try:
        return Path(*cleaned)
    except Exception:
        return path


def _configured_path_candidates(path_str: str) -> list[Path]:
    """Generate likely corrected variants for a saved settings path."""
    raw = Path(path_str)
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            candidates.append(path)

    add(raw)

    try:
        normalized = _dedupe_adjacent_path_parts(raw)
        lower_parts = [part.lower() for part in normalized.parts]
        if "faxmanagerdata" in lower_parts:
            idx = lower_parts.index("faxmanagerdata")
            tail = Path(*normalized.parts[idx + 1:]) if idx + 1 < len(normalized.parts) else Path()

            docs_root = Path.home() / "Documents" / "FaxManagerData"
            add(docs_root)
            add(docs_root / tail)

            # Keep legacy C: paths as a fallback only.
            add(_dedupe_adjacent_path_parts(raw))

            legacy_root = Path("C:/FaxManagerData")
            add(legacy_root)
            add(legacy_root / tail)
        else:
            add(normalized)
    except Exception:
        add(_dedupe_adjacent_path_parts(raw))

    return candidates


def _first_existing_path(candidates: list[Path]) -> Path | None:
    """Return the first candidate that exists on disk."""
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            pass
    return None


def get_project_root() -> Path:
    """Return the project root in both dev and frozen (PyInstaller) modes."""
    if getattr(sys, 'frozen', False):
        # When frozen, base is the folder where the EXE resides
        return Path(sys.executable).resolve().parent
    # dev mode: same as existing PROJECT_ROOT behavior
    return Path(__file__).resolve().parents[1]


def _get_internal_dir() -> Path:
    """Get the _internal directory for PyInstaller bundled data."""
    if getattr(sys, 'frozen', False):
        # PyInstaller stores data files in _internal subfolder (or MEIPASS for onefile)
        if hasattr(sys, '_MEIPASS'):
            return Path(sys._MEIPASS)
        return Path(sys.executable).resolve().parent / "_internal"
    # dev mode: use project root
    return Path(__file__).resolve().parents[1]


def get_assets_dir() -> Path:
    """Get assets directory (works in dev and frozen modes)."""
    if getattr(sys, 'frozen', False):
        return _get_internal_dir() / "assets"
    return get_project_root() / "assets"


def get_theme_dir() -> Path:
    """Get theme directory (works in dev and frozen modes)."""
    if getattr(sys, 'frozen', False):
        return _get_internal_dir() / "theme"
    return get_project_root() / "theme"


def get_logs_dir() -> Path:
    """Ensure logs folder exists and return it (in a writable location)."""
    if getattr(sys, 'frozen', False):
        # When frozen (installed), prefer per-user logs.
        # This avoids elevation requirements and prevents cross-user profile confusion.
        local_appdata = os.getenv('LOCALAPPDATA') or os.path.expanduser('~')
        logs = Path(local_appdata) / "DMELogic" / "Logs"
    else:
        # In dev mode, use project root
        logs = get_project_root() / "Logs"
    
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def _get_installed_data_path() -> Path | None:
    """
    Check if running from an installed version and read data_path.txt.
    This ensures installed apps use the correct data directory.
    """
    try:
        # Check if we're running as a frozen executable (PyInstaller)
        if getattr(sys, 'frozen', False):
            # Get the directory where the .exe is located
            exe_dir = Path(sys.executable).parent
            data_path_file = exe_dir / "data_path.txt"
            
            if data_path_file.exists():
                path_str = data_path_file.read_text(encoding="utf-8").strip()
                if path_str:
                    p = Path(path_str)
                    p.mkdir(parents=True, exist_ok=True)
                    return p
    except Exception:
        pass
    return None


# ---- core dirs (under your existing structure) ----
def db_dir() -> Path:
    """
    Central DB folder with priority:
    1. Settings.json db_folder
    2. Installed app data path (from data_path.txt)
    3. Default db folder
    """
    # First check settings (authoritative for both dev + USB/installed builds)
    try:
        from .settings import load_settings
        settings = load_settings()
        db_folder = settings.get("db_folder")
        if db_folder:
            p = Path(db_folder)
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass

    # Then check if this is an installed app (legacy/installer-provided location)
    installed_path = _get_installed_data_path()
    if installed_path:
        return installed_path
    
    # Finally fall back to default
    return Path(_default_db_folder())


def backup_dir() -> Path:
    """
    Backups folder with priority:
    1. Settings.json backup_folder
    2. Default backup folder
    """
    try:
        from .settings import load_settings
        settings = load_settings()
        backup_folder = settings.get("backup_folder")
        if backup_folder:
            p = Path(backup_folder)
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    return Path(_default_backup_folder())


def fax_root() -> Path:
    """
    Root for fax-related data with priority:
    1. Settings.json fax_folder (parent of fax folder)
    2. Default fax root
    """
    try:
        from .settings import load_settings
        settings = load_settings()
        fax_folder = settings.get("fax_folder")
        if fax_folder:
            # fax_folder might be the OCR'd folder or root
            root_candidates: list[Path] = []
            for candidate in _configured_path_candidates(fax_folder):
                if candidate.name.lower() in ("faxes ocr'd", "faxes ocred", "faxes"):
                    root_candidates.append(candidate.parent)
                else:
                    root_candidates.append(candidate)

            existing = _first_existing_path(root_candidates)
            if existing is not None:
                return existing

            # Nothing exists; still return a normalized candidate rather than
            # the raw malformed settings path.
            if root_candidates:
                return _dedupe_adjacent_path_parts(root_candidates[0])
    except Exception:
        pass

    preferred_docs = Path.home() / "Documents" / "FaxManagerData"
    if preferred_docs.exists():
        return preferred_docs

    legacy_root = Path("C:/FaxManagerData")
    if legacy_root.exists():
        return legacy_root

    return preferred_docs


def ocr_folder() -> Path:
    """
    The folder containing scanned/OCR'd documents.

    Priority:
    1. Settings.json ``ocr_folder`` (explicit override — use this when
       the folder moves to an external drive)
    2. ``fax_root() / "Faxes OCR'd"`` (current default)
    """
    try:
        from .settings import load_settings
        settings = load_settings()
        explicit = settings.get("ocr_folder")
        if explicit:
            candidates = _configured_path_candidates(explicit)
            existing = _first_existing_path(candidates)
            if existing is not None:
                return existing
            if candidates:
                return _dedupe_adjacent_path_parts(candidates[0])
    except Exception:
        pass

    candidates = [
        fax_root() / "Faxes OCR'd",
        Path.home() / "Documents" / "FaxManagerData" / "Faxes OCR'd",
        Path("C:/FaxManagerData/Faxes OCR'd"),
    ]
    existing = _first_existing_path(candidates)
    if existing is not None:
        return existing
    return candidates[0]


def ocr_cache_db() -> Path:
    """Path to the OCR index/cache database, kept alongside the OCR folder."""
    return ocr_folder().parent / "ocr_cache.db"


def resolve_document_path(filename_or_path: str) -> Path:
    """Resolve a document reference (filename or full path) to an absolute Path.

    Storage convention:
    • DB stores **filenames only** (e.g. ``SMITH, JOHN RX.pdf``).
    • Legacy records may still contain full absolute paths.

    Resolution order:
    1. If the value is already an absolute path that exists → use it.
    2. Look in ``ocr_folder()`` root.
    3. Search one level of subfolders (e.g. ``ocr_folder()/S/file.pdf``).
    """
    raw = (filename_or_path or "").strip().strip('"').strip("'")
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return p

    root = ocr_folder()
    fax = fax_root()
    delivery_root = delivery_tickets_folder()

    # Some legacy records store a relative path such as:
    #   FaxManagerData\Faxes OCR'd\file.pdf
    #   Faxes OCR'd\2026\05\file.pdf
    # or just the filename.
    # Normalize those forms into candidate paths under the configured roots.
    path_parts = list(p.parts)
    lower_parts = [part.lower() for part in path_parts]

    candidate_paths: list[Path] = []

    # 1. Relative path directly under OCR folder.
    if raw and not p.is_absolute():
        candidate_paths.append(root / p)

    # 2. Relative path that redundantly includes FaxManagerData or Faxes OCR'd.
    try:
        if "faxes ocr'd" in lower_parts:
            idx = lower_parts.index("faxes ocr'd")
            tail = Path(*path_parts[idx + 1:]) if idx + 1 < len(path_parts) else Path()
            candidate_paths.append(root / tail)
        elif fax.name and fax.name.lower() in lower_parts:
            idx = lower_parts.index(fax.name.lower())
            tail = Path(*path_parts[idx + 1:]) if idx + 1 < len(path_parts) else Path()
            candidate_paths.append(fax / tail)
    except Exception:
        pass

    # 3. Filename-only fallback at OCR root.
    base = p.name
    if base:
        candidate_paths.append(root / base)
        candidate_paths.append(delivery_root / base)

    seen: set[str] = set()
    for candidate in candidate_paths:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    # 4. Recursive lookup by basename anywhere under the OCR root.
    # This handles current real-world layouts where files may live in nested
    # year/month/letter folders while the DB stores only a filename.
    if base:
        try:
            for match in root.rglob(base):
                if match.is_file():
                    return match
        except OSError:
            pass
        try:
            for match in delivery_root.rglob(base):
                if match.is_file():
                    return match
        except OSError:
            pass

    # Return the best normalized candidate even if it doesn't exist.
    if candidate_paths:
        return candidate_paths[0]
    return root / base


def fax_packets_dir() -> Path:
    return fax_root() / "FaxPackets"


def patient_documents_dir() -> Path:
    return fax_root() / "PatientDocuments"


def tickets_dir() -> Path:
    return fax_root() / "Tickets"


def delivery_tickets_folder() -> Path:
    """Folder where scanned delivery confirmations are saved."""
    try:
        from .settings import load_settings
        settings = load_settings()
        configured = (settings.get("delivery_tickets_folder") or "").strip()
        if configured:
            p = Path(configured)
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass

    p = Path.home() / "Documents" / "DELIVERY TICKETS"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---- DME-specific directories ----
def pod_dir() -> Path:
    """Proof of Delivery documents."""
    p = fax_root() / "POD"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cmn_dir() -> Path:
    """Certificate of Medical Necessity forms."""
    p = fax_root() / "CMN"
    p.mkdir(parents=True, exist_ok=True)
    return p


def hcfa_1500_exports_dir() -> Path:
    """HCFA-1500 form exports."""
    p = PROJECT_ROOT / "Exports" / "HCFA-1500"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ub04_exports_dir() -> Path:
    """UB-04 form exports."""
    p = PROJECT_ROOT / "Exports" / "UB-04"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    """Centralized logs directory (writable in both dev and frozen modes)."""
    return get_logs_dir()


def debug_log_path() -> Path:
    """Path to debug log file in centralized Logs directory."""
    return get_logs_dir() / DEBUG_LOG_FILE
