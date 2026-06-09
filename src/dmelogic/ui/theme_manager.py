"""
theme_manager.py — Centralized theme management for DMELogic
"""

from __future__ import annotations

import logging
import re
from PyQt6.QtWidgets import QApplication

from dmelogic.paths import get_assets_dir, get_theme_dir

logger = logging.getLogger("theme")


class ThemeManager:
    """Manages application theme switching between light and dark modes."""
    
    @staticmethod
    def apply_theme(app: QApplication, theme_name: str) -> None:
        """
        Apply a theme to the application.
        
        Args:
            app: QApplication instance
            theme_name: "light" for light mode or "dark" for dark mode
        """
        if theme_name == "dark":
            qss_path = get_theme_dir() / "dark.qss"
        else:
            # Use proper light theme
            qss_path = get_theme_dir() / "light.qss"

        logger.info(f"[ThemeManager] Applying theme '{theme_name}' from {qss_path}")

        if not qss_path.exists():
            logger.warning(f"[ThemeManager] Theme file not found: {qss_path}")
            return

        try:
            with qss_path.open("r", encoding="utf-8") as f:
                qss = f.read()
            ThemeManager._apply_stylesheet(app, qss)
            logger.info(f"[ThemeManager] Theme '{theme_name}' applied successfully ({len(qss)} chars).")
        except Exception:
            logger.exception(f"[ThemeManager] Failed to apply theme '{theme_name}'")

    @staticmethod
    def apply_scale(app: QApplication, scale_percent: int) -> None:
        """Reapply the active stylesheet with a new font scale percentage."""
        if not app:
            return

        base_qss = app.property("dme_theme_raw_qss")
        if base_qss is None:
            # Capture current stylesheet as baseline if theme manager hasn't applied yet
            base_qss = app.styleSheet() or ""
            app.setProperty("dme_theme_raw_qss", base_qss)

        safe_percent = ThemeManager._clamp_scale(scale_percent)
        app.setProperty("dme_ui_scale_percent", safe_percent)
        scaled_qss = ThemeManager._scale_qss(base_qss, safe_percent)
        app.setStyleSheet(scaled_qss)

    @staticmethod
    def _apply_stylesheet(app: QApplication, base_qss: str) -> None:
        """Store and apply the base stylesheet with the active scale factor."""
        if app is None:
            return

        app.setProperty("dme_theme_raw_qss", base_qss or "")
        safe_percent = ThemeManager._clamp_scale(app.property("dme_ui_scale_percent") or 100)
        scaled_qss = ThemeManager._scale_qss(base_qss, safe_percent)
        app.setStyleSheet(scaled_qss)

    @staticmethod
    def _scale_qss(base_qss: str, scale_percent: int) -> str:
        """Return stylesheet text with every font-size multiplied by the scale."""
        if not base_qss:
            return ""

        safe_percent = ThemeManager._clamp_scale(scale_percent)
        if safe_percent == 100:
            return base_qss

        scale = safe_percent / 100.0
        pattern = re.compile(r"(font-size:\s*)(\d+(?:\.\d+)?)(\s*(?:pt|px))", re.IGNORECASE)

        def _format_number(value: float) -> str:
            txt = f"{value:.2f}"
            if "." in txt:
                txt = txt.rstrip("0").rstrip(".")
            return txt

        def repl(match: re.Match) -> str:
            prefix = match.group(1)
            base_value = float(match.group(2))
            unit_with_space = match.group(3)
            new_value = _format_number(base_value * scale)
            return f"{prefix}{new_value}{unit_with_space}"

        return pattern.sub(repl, base_qss)

    @staticmethod
    def _clamp_scale(scale_percent: int | None) -> int:
        try:
            value = int(scale_percent)
        except Exception:
            value = 100
        return max(80, min(150, value))
