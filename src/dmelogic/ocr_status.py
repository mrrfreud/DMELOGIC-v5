"""
OCR System Status and Configuration Module

Provides centralized checking for OCR system dependencies:
- Tesseract OCR availability
- FTS5 full-text search support
- Watchdog file monitoring support

Use this module to detect feature availability and show appropriate UI messages.
"""
import sqlite3
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class OCRSystemStatus:
    """Status of OCR system dependencies and features."""
    tesseract_available: bool
    tesseract_path: Optional[str]
    fts5_available: bool
    watchdog_available: bool
    warnings: List[str]
    
    @property
    def fully_operational(self) -> bool:
        """Check if all OCR features are available."""
        return (
            self.tesseract_available 
            and self.fts5_available 
            and self.watchdog_available
        )
    
    @property
    def ocr_available(self) -> bool:
        """Check if OCR extraction is available (requires Tesseract)."""
        return self.tesseract_available
    
    @property
    def search_available(self) -> bool:
        """Check if full-text search is available."""
        # Basic search works without FTS5, just slower
        return True
    
    @property
    def fast_search_available(self) -> bool:
        """Check if fast FTS5 search is available."""
        return self.fts5_available
    
    @property
    def auto_watch_available(self) -> bool:
        """Check if automatic folder watching is available."""
        return self.watchdog_available
    
    def get_user_message(self) -> str:
        """
        Get user-friendly status message.
        
        Returns:
            str: Message describing system status and any limitations
        """
        if self.fully_operational:
            return "✅ All OCR features available"
        
        if not self.tesseract_available:
            return (
                "⚠️ OCR Not Available\n"
                "Tesseract OCR is not installed.\n"
                "Install from: https://github.com/tesseract-ocr/tesseract"
            )
        
        messages = []
        if not self.fts5_available:
            messages.append(
                "⚠️ Slow Search Mode\n"
                "FTS5 not available. Search will be slower.\n"
                "Install pysqlite3-binary for fast search."
            )
        
        if not self.watchdog_available:
            messages.append(
                "⚠️ Auto-Watch Disabled\n"
                "Folder monitoring unavailable.\n"
                "Install watchdog for automatic indexing."
            )
        
        return "\n\n".join(messages) if messages else "✅ OCR Available (limited features)"
    
    def get_feature_list(self) -> List[str]:
        """
        Get list of available features.
        
        Returns:
            List of feature description strings
        """
        features = []
        
        if self.tesseract_available:
            features.append(f"[OK] OCR Extraction ({self.tesseract_path})")
        else:
            features.append("[X] OCR Extraction (Tesseract not found)")
        
        if self.fts5_available:
            features.append("[OK] Fast Full-Text Search (FTS5)")
        else:
            features.append("[!] Basic Search Only (FTS5 unavailable)")
        
        if self.watchdog_available:
            features.append("[OK] Automatic Folder Watching")
        else:
            features.append("[X] Automatic Folder Watching (install watchdog)")
        
        return features


def check_tesseract_availability() -> tuple[bool, Optional[str]]:
    """
    Check if Tesseract OCR is available.
    
    Checks in order:
    1. Bundled portable Tesseract next to the app (for USB installs)
    2. System-installed Tesseract (default pytesseract behavior)
    
    Returns:
        tuple: (is_available, path_or_none)
    """
    import os
    import sys
    import shutil
    import pytesseract

    # Known install locations from config (includes "tesseract" for PATH lookup).
    try:
        from dmelogic.config import TESSERACT_PATHS
    except Exception:
        TESSERACT_PATHS = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            r"tesseract",
        ]

    # --- Check for bundled portable Tesseract ---
    # When frozen (PyInstaller), look relative to the executable
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    portable_exe = os.path.join(app_dir, "tesseract_portable", "tesseract.exe")
    if os.path.isfile(portable_exe):
        pytesseract.pytesseract.tesseract_cmd = portable_exe
        # Also set TESSDATA_PREFIX so it finds language data
        tessdata_dir = os.path.join(app_dir, "tesseract_portable", "tessdata")
        if os.path.isdir(tessdata_dir):
            os.environ["TESSDATA_PREFIX"] = tessdata_dir
        return True, portable_exe

    # IMPORTANT: Do not execute tesseract during startup health checks.
    # Some broken installs throw a Windows popup (0xc0000142) when launched.
    for candidate in TESSERACT_PATHS:
        try:
            if candidate.lower() == "tesseract":
                resolved = shutil.which("tesseract")
                if resolved:
                    pytesseract.pytesseract.tesseract_cmd = resolved
                    return True, resolved
            elif os.path.isfile(candidate):
                pytesseract.pytesseract.tesseract_cmd = candidate
                return True, candidate
        except Exception:
            continue

    return False, None


def check_fts5_availability() -> bool:
    """
    Check if SQLite FTS5 extension is available.
    
    Returns:
        bool: True if FTS5 is available
    """
    import sqlite3 as std_sqlite3
    
    try:
        # Try importing pysqlite3 (has FTS5)
        from pysqlite3 import dbapi2 as sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE _fts5_test USING fts5(content)")
        conn.execute("DROP TABLE _fts5_test")
        conn.close()
        return True
    except (ImportError, Exception):
        pass
    
    # Fall back to standard sqlite3
    try:
        conn = std_sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE _fts5_test USING fts5(content)")
        conn.execute("DROP TABLE _fts5_test")
        conn.close()
        return True
    except Exception:
        return False


def check_watchdog_availability() -> bool:
    """
    Check if watchdog file monitoring is available.
    
    Returns:
        bool: True if watchdog is installed
    """
    try:
        from watchdog.observers import Observer
        return True
    except ImportError:
        return False


def get_ocr_system_status() -> OCRSystemStatus:
    """
    Check status of all OCR system dependencies.
    
    Returns:
        OCRSystemStatus: Complete system status with availability flags
    
    Usage:
        status = get_ocr_system_status()
        if not status.fully_operational:
            print(status.get_user_message())
        
        for feature in status.get_feature_list():
            print(feature)
    """
    tesseract_available, tesseract_path = check_tesseract_availability()
    fts5_available = check_fts5_availability()
    watchdog_available = check_watchdog_availability()
    
    warnings = []
    
    if not tesseract_available:
        warnings.append(
            "Tesseract OCR not found. OCR extraction will fail.\n"
            "Install from: https://github.com/tesseract-ocr/tesseract"
        )
    
    if not fts5_available:
        warnings.append(
            "FTS5 not available. Search will be slower.\n"
            "Install: pip install pysqlite3-binary"
        )
    
    if not watchdog_available:
        warnings.append(
            "Watchdog not available. Folder watching disabled.\n"
            "Install: pip install watchdog"
        )
    
    return OCRSystemStatus(
        tesseract_available=tesseract_available,
        tesseract_path=tesseract_path,
        fts5_available=fts5_available,
        watchdog_available=watchdog_available,
        warnings=warnings
    )


def print_ocr_status():
    """Print OCR system status to console."""
    import sys
    status = get_ocr_system_status()
    
    # Safe print function that handles encoding issues
    def safe_print(text):
        try:
            print(text)
        except UnicodeEncodeError:
            # Replace Unicode characters with ASCII equivalents
            safe_text = text.replace('✅', '[OK]').replace('⚠️', '[!]').replace('❌', '[X]')
            print(safe_text.encode('ascii', 'replace').decode('ascii'))
    
    safe_print("\n" + "=" * 60)
    safe_print("OCR SYSTEM STATUS")
    safe_print("=" * 60)
    
    for feature in status.get_feature_list():
        safe_print(feature)
    
    if status.warnings:
        safe_print("\nWARNINGS:")
        for warning in status.warnings:
            safe_print(f"  [!] {warning}")
    
    if status.fully_operational:
        safe_print("\n[OK] All OCR features are fully operational!")
    else:
        safe_print("\n[!] Some OCR features are unavailable (see warnings above)")
    
    safe_print("=" * 60 + "\n")


# Convenience function for application startup
def ensure_ocr_configured() -> OCRSystemStatus:
    """
    Configure OCR system and return status.
    
    Call this once at application startup before using OCR features.
    
    Returns:
        OCRSystemStatus: Complete system status
    
    Usage:
        from dmelogic.ocr_status import ensure_ocr_configured
        
        status = ensure_ocr_configured()
        if not status.ocr_available:
            QMessageBox.warning(self, "OCR Unavailable", status.get_user_message())
    """
    # Configure Tesseract
    from dmelogic.config import configure_tesseract
    configure_tesseract()
    
    # Get and print status
    status = get_ocr_system_status()
    print_ocr_status()
    
    return status
