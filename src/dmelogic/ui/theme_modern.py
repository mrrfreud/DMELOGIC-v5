"""
theme_modern.py — the modern "Calm Clinical" application theme.

A single comprehensive Qt stylesheet applied at the QApplication level, so it
restyles the whole app (including the legacy tabs) in one pass: soft slate
neutrals, white cards, teal accent, rounded controls, modern typography.

Apply once at startup:

    from dmelogic.ui.theme_modern import apply_modern_theme
    apply_modern_theme(app)            # light (default)
    apply_modern_theme(app, dark=True) # dark

Widgets that set their own inline stylesheets will keep them; everything else
picks up this look automatically.
"""

from __future__ import annotations


# ── design tokens ───────────────────────────────────────────────────────
class _Light:
    bg = "#f1f5f9"          # slate-100 — app canvas
    surface = "#ffffff"     # cards / panels
    surface_alt = "#f8fafc" # slate-50 — subtle fills, headers
    border = "#e2e8f0"      # slate-200
    border_strong = "#cbd5e1"  # slate-300
    text = "#0f172a"        # slate-900
    text_muted = "#64748b"  # slate-500
    text_subtle = "#94a3b8" # slate-400
    primary = "#2563eb"     # blue-600 — trusted clinical blue
    primary_hover = "#1d4ed8"   # blue-700
    primary_press = "#1e40af"   # blue-800
    primary_soft = "#e2e8f0"    # neutral slate-200 (de-pastel hovers)
    primary_softer = "#f1f5f9"
    on_primary = "#ffffff"
    danger = "#dc2626"
    danger_hover = "#b91c1c"
    selection = "#2563eb"       # solid blue selection
    selection_text = "#ffffff"  # white text on selection
    disabled_bg = "#f1f5f9"
    disabled_text = "#94a3b8"


class _Dark:
    bg = "#0f172a"          # slate-900
    surface = "#1e293b"     # slate-800
    surface_alt = "#243044"
    border = "#334155"      # slate-700
    border_strong = "#475569"
    text = "#e2e8f0"
    text_muted = "#94a3b8"
    text_subtle = "#64748b"
    primary = "#14b8a6"     # teal-500 (brighter on dark)
    primary_hover = "#2dd4bf"
    primary_press = "#5eead4"
    primary_soft = "#134e4a"
    primary_softer = "#0f3d3a"
    on_primary = "#062925"
    danger = "#ef4444"
    danger_hover = "#f87171"
    selection = "#134e4a"
    selection_text = "#e2e8f0"
    disabled_bg = "#1e293b"
    disabled_text = "#475569"


_FONT = "'Segoe UI Variable Text', 'Segoe UI', system-ui, sans-serif"


def _build_qss(c) -> str:
    return f"""
* {{
    font-family: {_FONT};
    font-size: 10pt;
    color: {c.text};
}}

QWidget {{ background-color: {c.bg}; }}
QMainWindow, QDialog {{ background-color: {c.bg}; }}

/* Cards / framed panels */
QFrame[frameShape="4"], QFrame[frameShape="5"], QFrame[frameShape="6"] {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 12px;
}}
QGroupBox {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 12px;
    margin-top: 14px;
    padding: 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {c.text_muted};
}}

/* Labels */
QLabel {{ background: transparent; }}

/* ── Buttons ── */
QPushButton {{
    background-color: {c.primary};
    color: {c.on_primary};
    border: none;
    border-radius: 8px;
    padding: 7px 16px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{ background-color: {c.primary_hover}; }}
QPushButton:pressed {{ background-color: {c.primary_press}; }}
QPushButton:disabled {{ background-color: {c.disabled_bg}; color: {c.disabled_text}; }}
QPushButton:focus {{ outline: none; }}

/* Secondary / ghost buttons opt in via objectName or 'flat' */
QPushButton[flat="true"], QToolButton {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border_strong};
    border-radius: 8px;
    padding: 6px 12px;
    font-weight: 500;
}}
QPushButton[flat="true"]:hover, QToolButton:hover {{
    background-color: {c.surface_alt};
    border-color: {c.primary};
    color: {c.primary};
}}

/* ── Inputs ── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
    background-color: {c.surface};
    color: {c.text};
    border: 1px solid {c.border_strong};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {c.primary};
    selection-color: {c.on_primary};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 1px solid {c.primary};
}}
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {{
    background-color: {c.disabled_bg}; color: {c.disabled_text};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 8px;
    selection-background-color: {c.primary_soft};
    selection-color: {c.text};
    outline: none;
}}

/* ── Tabs (modern flat) ── */
QTabWidget::pane {{
    border: 1px solid {c.border};
    border-radius: 12px;
    background: {c.surface};
    top: -1px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {c.text_muted};
    padding: 9px 16px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {c.text}; }}
QTabBar::tab:selected {{
    color: {c.primary};
    border-bottom: 2px solid {c.primary};
}}

/* ── Tables ── */
QTableView, QTableWidget, QTreeView {{
    background-color: {c.surface};
    alternate-background-color: {c.surface_alt};
    gridline-color: {c.border};
    border: 1px solid {c.border};
    border-radius: 10px;
    selection-background-color: {c.selection};
    selection-color: {c.selection_text};
    outline: none;
}}
QHeaderView::section {{
    background-color: {c.surface_alt};
    color: {c.text_muted};
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {c.border};
    font-weight: 600;
}}
QTableView::item, QTreeView::item {{ padding: 4px; }}

/* ── Lists ── */
QListWidget, QListView {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 10px;
    outline: none;
    padding: 4px;
}}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; }}
QListWidget::item:hover {{ background-color: {c.surface_alt}; }}
QListWidget::item:selected {{ background-color: {c.selection}; color: {c.selection_text}; }}

/* ── Scrollbars ── */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c.border_strong}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c.text_subtle}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c.border_strong}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── Menus ── */
QMenu {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 24px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {c.primary_soft}; color: {c.text}; }}
QMenuBar {{ background-color: {c.surface}; }}
QMenuBar::item:selected {{ background-color: {c.primary_soft}; border-radius: 6px; }}

/* ── Misc ── */
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {c.border_strong};
    border-radius: 4px; background: {c.surface};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {c.primary}; border-color: {c.primary};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QToolTip {{
    background-color: {c.text}; color: {c.surface};
    border: none; border-radius: 6px; padding: 6px 8px;
}}
QSplitter::handle {{ background: transparent; }}
QProgressBar {{
    background: {c.surface_alt}; border: none; border-radius: 6px;
    text-align: center; height: 8px;
}}
QProgressBar::chunk {{ background: {c.primary}; border-radius: 6px; }}
"""


LIGHT_QSS = _build_qss(_Light)
DARK_QSS = _build_qss(_Dark)


def apply_modern_theme(app, dark: bool = False) -> None:
    """Apply the modern Calm Clinical theme to the whole QApplication."""
    app.setStyleSheet(DARK_QSS if dark else LIGHT_QSS)
    try:
        from PyQt6.QtGui import QFont
        f = QFont("Segoe UI Variable Text", 10)
        if not f.exactMatch():
            f = QFont("Segoe UI", 10)
        app.setFont(f)
    except Exception:
        pass


# Expose the palette so individual screens can match (chips, accents, etc.).
TOKENS_LIGHT = _Light
TOKENS_DARK = _Dark
