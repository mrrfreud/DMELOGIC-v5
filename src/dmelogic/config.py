"""
config.py — Global configuration settings for DMELogic
"""

import os
import json
from datetime import datetime

# -----------------------------
# Base paths
# -----------------------------
DEFAULT_FOLDER_PATH = r"C:\Users\pharmacy\Documents\FaxManagerData\Faxes OCR'd"

TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Users\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    r"tesseract",   # If in PATH
]

def _get_settings_file() -> str:
    """Get the path to settings.json in a user-writable location."""
    if os.name == 'nt':  # Windows
        local_appdata = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        settings_dir = os.path.join(local_appdata, "DMELogic")
    else:
        settings_dir = os.path.join(os.path.expanduser('~'), ".dmelogic")
    
    # Create directory if it doesn't exist
    os.makedirs(settings_dir, exist_ok=True)
    return os.path.join(settings_dir, "settings.json")

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
# Folder Helpers
# -----------------------------
def _default_db_folder() -> str:
    preferred = r"C:\Users\pharmacy\Documents\DmeSolutionsV1\Data"
    try:
        os.makedirs(preferred, exist_ok=True)
        return preferred
    except Exception:
        pass

    try:
        docs = os.path.join(os.path.expanduser("~"), "Documents", "DmeSolutionsV1", "Data")
        os.makedirs(docs, exist_ok=True)
        return docs
    except Exception:
        pass

    fallback = os.path.join(os.getcwd(), "Data")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _default_backup_folder() -> str:
    preferred = r"C:\Users\pharmacy\Documents\DmeSolutionsV1\Backups"
    try:
        os.makedirs(preferred, exist_ok=True)
        return preferred
    except Exception:
        pass

    try:
        docs = os.path.join(os.path.expanduser("~"), "Documents", "DmeSolutionsV1", "Backups")
        os.makedirs(docs, exist_ok=True)
        return docs
    except Exception:
        pass

    fallback = os.path.join(os.getcwd(), "Backups")
    os.makedirs(fallback, exist_ok=True)
    return fallback


BACKUP_FOLDER = _default_backup_folder()
