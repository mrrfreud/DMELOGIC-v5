"""
theme_manager.py — Unified theme management for DME Logic application.

This module provides centralized theme loading and application-wide styling
to ensure consistent, professional appearance across all screens.

Usage:
    from dmelogic.theme_manager import apply_theme
    
    app = QApplication(sys.argv)
    apply_theme(app, "dark")  # or "light"
"""

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor, QFont


def get_theme_path(theme_name: str = "dark") -> Path:
    """
    Get the path to a theme file.
    
    Args:
        theme_name: Name of the theme ("dark" or "light")
        
    Returns:
        Path to the .qss theme file
    """
    theme_dir = Path(__file__).parent / "theme"
    theme_file = theme_dir / f"{theme_name}.qss"
    
    if not theme_file.exists():
        # Fall back to dark theme
        theme_file = theme_dir / "dark.qss"
    
    return theme_file


def load_stylesheet(theme_name: str = "dark") -> str:
    """
    Load a stylesheet from file.
    
    Args:
        theme_name: Name of the theme to load
        
    Returns:
        String containing the QSS stylesheet
    """
    theme_path = get_theme_path(theme_name)
    
    try:
        with open(theme_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not load theme '{theme_name}': {e}")
        return ""


def apply_theme(app: QApplication, theme_name: str = "dark") -> None:
    """Apply a unified theme to the entire application.

    This sets the global font, loads the QSS stylesheet, and
    applies a dark palette when requested.
    """
    # Apply typography baseline first
    apply_typography(app)

    # Load and apply stylesheet
    stylesheet = load_stylesheet(theme_name)
    if stylesheet:
        app.setStyleSheet(stylesheet)
        print(f" Applied '{theme_name}' theme to application")
    else:
        print(f" Failed to apply '{theme_name}' theme")

    # Set application palette for better integration
    if theme_name == "dark":
        palette = create_dark_palette()
        app.setPalette(palette)


def create_dark_palette() -> QPalette:
    """
    Create a dark color palette for the application.
    
    This complements the QSS stylesheet with proper color roles
    for native Qt widgets.
    
    Returns:
        QPalette configured for dark theme
    """
    palette = QPalette()
    
    # Base colors
    dark_bg = QColor(30, 30, 30)        # #1e1e1e
    dark_surface = QColor(43, 43, 43)   # #2b2b2b
    dark_border = QColor(58, 58, 58)    # #3a3a3a
    text_primary = QColor(229, 229, 229)  # #e5e5e5
    text_secondary = QColor(207, 207, 207)  # #cfcfcf
    primary_blue = QColor(0, 122, 204)  # #007acc
    
    # Window and base
    palette.setColor(QPalette.ColorRole.Window, dark_bg)
    palette.setColor(QPalette.ColorRole.WindowText, text_primary)
    palette.setColor(QPalette.ColorRole.Base, dark_surface)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(36, 36, 36))
    palette.setColor(QPalette.ColorRole.Text, text_primary)
    
    # Buttons
    palette.setColor(QPalette.ColorRole.Button, dark_surface)
    palette.setColor(QPalette.ColorRole.ButtonText, text_primary)
    
    # Selection
    palette.setColor(QPalette.ColorRole.Highlight, primary_blue)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    
    # Links
    palette.setColor(QPalette.ColorRole.Link, primary_blue)
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(39, 155, 255))
    
    # Tooltips
    palette.setColor(QPalette.ColorRole.ToolTipBase, dark_surface)
    palette.setColor(QPalette.ColorRole.ToolTipText, text_primary)
    
    # Disabled state
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(136, 136, 136))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(136, 136, 136))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(136, 136, 136))
    
    return palette


def get_icon_color(theme_name: str = "dark") -> str:
    """
    Get the appropriate icon color for the theme.
    
    Args:
        theme_name: Current theme name
        
    Returns:
        Hex color string for icons (e.g., "#e5e5e5")
    """
    if theme_name == "dark":
        return "#e5e5e5"
    else:
        return "#333333"


def get_status_color(status: str, theme_name: str = "dark") -> str:
    """
    Get the color for a given status.
    
    Args:
        status: Status name (e.g., "active", "pending", "error")
        theme_name: Current theme name
        
    Returns:
        Hex color string for the status
    """
    status_colors = {
        "active": "#28a745",
        "success": "#28a745",
        "pending": "#ffc107",
        "warning": "#ffc107",
        "error": "#dc3545",
        "danger": "#dc3545",
        "cancelled": "#dc3545",
        "completed": "#007acc",
        "inactive": "#888888",
        "disabled": "#888888",
    }
    
    return status_colors.get(status.lower(), "#e5e5e5")


# Convenience constants for consistent styling
class ThemeColors:
    """Centralized color constants matching the dark theme."""
    
    # Primary colors
    PRIMARY = "#007acc"
    SUCCESS = "#28a745"
    WARNING = "#ffc107"
    DANGER = "#dc3545"
    
    # Backgrounds
    BG_DARK = "#1e1e1e"
    BG_SURFACE = "#2b2b2b"
    BG_HOVER = "#2d2d2d"
    
    # Borders
    BORDER_DEFAULT = "#3a3a3a"
    BORDER_FOCUS = "#007acc"
    
    # Text
    TEXT_PRIMARY = "#e5e5e5"
    TEXT_SECONDARY = "#cfcfcf"
    TEXT_MUTED = "#888888"
    TEXT_INVERSE = "#ffffff"


class ThemeSpacing:
    """Centralized spacing constants for consistent layouts."""
    
    UNIT = 4  # Base unit in pixels
    
    # Standard spacing
    SMALL = 8
    MEDIUM = 12
    LARGE = 16
    XLARGE = 24
    
    # Component-specific
    BUTTON_PADDING_H = 16
    BUTTON_PADDING_V = 10
    INPUT_PADDING_H = 12
    INPUT_PADDING_V = 8
    TABLE_CELL_PADDING = 12
    TABLE_ROW_HEIGHT = 32
    TABLE_HEADER_HEIGHT = 36


class ThemeTypography:
    """Centralized typography settings."""
    
    FONT_FAMILY = "Segoe UI"
    FONT_SIZE_BASE = 9
    FONT_SIZE_HEADER = 11
    FONT_SIZE_SMALL = 8
    
    FONT_WEIGHT_NORMAL = "normal"
    FONT_WEIGHT_MEDIUM = "500"
    FONT_WEIGHT_SEMIBOLD = "600"
    FONT_WEIGHT_BOLD = "bold"


def apply_typography(app: QApplication) -> None:
    """Apply application-wide typography baseline.

    Sets the global QApplication font based on ThemeTypography so
    all widgets start from a consistent Segoe UI 9pt baseline.
    """
    font = QFont(ThemeTypography.FONT_FAMILY)
    font.setPointSize(ThemeTypography.FONT_SIZE_BASE)
    font.setWeight(QFont.Weight.Normal)
    app.setFont(font)


# Example usage and testing
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel
    
    app = QApplication(sys.argv)
    
    # Apply theme
    apply_theme(app, "dark")
    
    # Create test window
    window = QMainWindow()
    window.setWindowTitle("Theme Test")
    
    central = QWidget()
    layout = QVBoxLayout(central)
    
    layout.addWidget(QLabel("Theme Test - Dark Mode"))
    layout.addWidget(QPushButton("Primary Button"))
    
    btn_success = QPushButton("Success Button")
    btn_success.setProperty("class", "success")
    layout.addWidget(btn_success)
    
    btn_danger = QPushButton("Danger Button")
    btn_danger.setProperty("class", "danger")
    layout.addWidget(btn_danger)
    
    window.setCentralWidget(central)
    window.resize(400, 300)
    window.show()
    
    sys.exit(app.exec())
