"""
Detect the OS-level color scheme so we can default to matching it.

Returns "dark" or "light". On unsupported platforms or errors, falls
back to "light" — a known-safe default.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("theme")


def detect_os_theme() -> str:
    try:
        # PyQt6 6.5+ exposes Qt.ColorScheme via QStyleHints.
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtCore import Qt

        app = QGuiApplication.instance()
        if app is not None:
            hints = app.styleHints()
            if hasattr(hints, "colorScheme"):
                scheme = hints.colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return "dark"
                if scheme == Qt.ColorScheme.Light:
                    return "light"
    except Exception as e:
        logger.debug(f"Qt colorScheme detection failed: {e}")

    # Platform-specific fallbacks for older Qt builds.
    if sys.platform == "win32":
        return _detect_windows_theme()
    if sys.platform == "darwin":
        return _detect_macos_theme()
    return "light"


def _detect_windows_theme() -> str:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if value == 1 else "dark"
    except Exception:
        return "light"


def _detect_macos_theme() -> str:
    try:
        import subprocess
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=2,
        )
        return "dark" if "Dark" in result.stdout else "light"
    except Exception:
        return "light"
