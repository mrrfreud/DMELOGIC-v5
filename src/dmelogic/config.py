"""
config.py — Global configuration settings for DMELogic.

Data lives **outside** the install directory. Everything the app reads or
writes at runtime (databases, scans, backups, exports, logs) is rooted at a
single, configurable ``data_root()`` — by default ``C:\\ProgramData\\DMELogic``
on Windows. The install folder (Program Files) stays small and read-only.

Resolution order for the data root (first that applies wins):
    1. ``DMELOGIC_DATA_DIR`` environment variable
    2. ``data_root`` key in settings.json (lets an admin point at a shared
       network folder so multiple workstations share one dataset)
    3. Platform default: ``%PROGRAMDATA%\\DMELogic`` (Windows) or
       ``~/.local/share/DMELogic`` (POSIX)
"""

import os
import json
from datetime import datetime
from pathlib import Path

APP_NAME = "DMELogic"

TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Users\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    r"tesseract",   # If in PATH
]


# -----------------------------
# Canonical data root
# -----------------------------
def _platform_default_data_root() -> Path:
    """Per-machine writable data root, separate from the install directory."""
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def data_root() -> Path:
    """Return the single root under which all runtime data is stored.

    See module docstring for the resolution order. The directory (and the
    standard subfolders) are created on first access.
    """
    # 1. Environment override (useful for tests / portable installs).
    env = os.environ.get("DMELOGIC_DATA_DIR")
    candidate: Path | None = Path(env) if env else None

    # 2. settings.json override (admin-configured shared/network location).
    if candidate is None:
        try:
            cfg_file = _settings_file_path()
            if cfg_file.exists():
                data = json.loads(cfg_file.read_text(encoding="utf-8") or "{}")
                value = (data.get("data_root") or "").strip()
                if value:
                    candidate = Path(value)
        except Exception:
            candidate = None

    # 3. Platform default.
    if candidate is None:
        candidate = _platform_default_data_root()

    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Last-resort fallback so the app can still start.
        candidate = Path.home() / APP_NAME
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def data_subdir(name: str) -> Path:
    """Return (and create) a named subfolder under the data root."""
    p = data_root() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _settings_file_path() -> Path:
    """settings.json location — kept in the data root (single source of truth)."""
    root = os.environ.get("DMELOGIC_DATA_DIR") or os.environ.get("PROGRAMDATA", r"C:\ProgramData") + "\\" + APP_NAME
    p = Path(root)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p / "settings.json"


def _get_settings_file() -> str:
    """Backward-compatible string accessor for settings.json path."""
    return str(_settings_file_path())


SETTINGS_FILE = _get_settings_file()

DEBUG_LOG_FILE = "print_debug.log"

# -----------------------------
# Database Files
# -----------------------------
# Centralized list of all database files to backup/restore
# Add new databases here as a single source of truth
DB_FILES = [
    "patients.db",
    "orders.db",
    "prescribers.db",
    "inventory.db",
    "billing.db",
    "suppliers.db",
    "insurance_names.db",
    "insurance.db",
    "document_data.db",
    "communications.db",
    # Add new databases here:
    # "claims.db",
    # "audit_log.db",
]

# Optional: Databases to exclude from auto-discovery backup
DB_EXCLUDE = [
    "temp.db",
    "cache.db",
]


# -----------------------------
# Logging
# -----------------------------
def debug_log(msg: str):
    """
    Write message to console + debug file in centralized Logs directory.
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}\n"

        print(line, end="")  # console
        
        # Import here to avoid circular dependency
        from .paths import debug_log_path
        log_path = debug_log_path()
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Fallback to current directory if paths module fails
        try:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


# -----------------------------
# Tesseract Configuration
# -----------------------------
import pytesseract

def configure_tesseract() -> bool:
    """
    Locate and configure Tesseract OCR.
    
    Returns:
        bool: True if Tesseract was found and configured, False otherwise.
    
    Usage:
        Call once at application startup before using OCR features.
        
        if not configure_tesseract():
            # Show warning to user that OCR features are unavailable
            pass
    """
    for path in TESSERACT_PATHS:
        if path == "tesseract" or os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            print(f"[OK] Tesseract configured: {path}")
            return True

    print("[WARNING] Tesseract not found. OCR features will be unavailable.")
    print(f"[INFO] Searched paths: {TESSERACT_PATHS}")
    print("[INFO] Install Tesseract from: https://github.com/tesseract-ocr/tesseract")
    return False


# -----------------------------
# Folder Helpers (all under the canonical data root)
# -----------------------------
def _default_db_folder() -> str:
    """Default databases folder: <data_root>/Databases."""
    return str(data_subdir("Databases"))


def _default_backup_folder() -> str:
    """Default backups folder: <data_root>/Backups."""
    return str(data_subdir("Backups"))


# Default location for scanned/OCR'd documents (was the old "Faxes OCR'd").
def _default_scans_folder() -> str:
    """Default scans folder: <data_root>/Scans."""
    return str(data_subdir("Scans"))


BACKUP_FOLDER = _default_backup_folder()
