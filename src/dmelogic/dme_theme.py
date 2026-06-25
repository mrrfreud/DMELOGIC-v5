"""
dme_theme.py
============
DMELogic — Complete Design System & Global Stylesheet
======================================================

Drop this file into your DMELogic project folder and call:

    from dmelogic.dme_theme import apply_theme, COLORS
    apply_theme(app)           # app = QApplication instance
    apply_theme(app, dark=False)

That single call transforms every widget in the entire application.

DESIGN TOKENS:
    COLORS.NAVY       — #1c2e45  (topbar, action bars)
    COLORS.TEAL       — #0d9488  (primary accent, buttons, links)
    COLORS.TEAL_DARK  — #0a7c71  (hover)
    COLORS.SAGE_BG    — #f2f3ee  (main background)
    COLORS.WHITE      — #ffffff  (card/panel backgrounds)
    COLORS.SLATE_100  — #f1f5f9  (table alt row)
    COLORS.SLATE_200  — #e2e8f0  (borders)
    COLORS.SLATE_400  — #94a3b8  (muted text)
    COLORS.SLATE_600  — #475569  (secondary text)
    COLORS.SLATE_800  — #1e293b  (primary text)
    COLORS.GREEN      — #059669  (success/shipped)
    COLORS.AMBER      — #d97706  (warning/pending)
    COLORS.RED        — #e11d48  (danger/rx hold)
    COLORS.PURPLE     — #7c3aed  (billed)
    COLORS.YELLOW     — #a16207  (unbilled)

STATUS COLORS (badge backgrounds + text):
    STATUS_COLORS dict maps status name → (bg, text, border) hex tuples
"""

from PyQt6.QtWidgets import QApplication, QProxyStyle, QStyle, QCalendarWidget, QDateEdit
from PyQt6.QtGui import QFont, QFontDatabase, QColor, QPalette, QTextCharFormat
from PyQt6.QtCore import Qt, QObject, QEvent


# ─────────────────────────────────────────────────────────────────────────────
#  COLOR TOKENS
# ─────────────────────────────────────────────────────────────────────────────

class COLORS:
    # Backgrounds
    NAVY          = "#1c2e45"
    NAVY_DARK     = "#152336"
    NAVY_LIGHT    = "#243a56"
    SAGE_BG       = "#f2f3ee"
    WHITE         = "#ffffff"

    # Teal / Primary
    TEAL          = "#0d9488"
    TEAL_DARK     = "#0a7c71"
    TEAL_LIGHT    = "#ccfbf1"
    TEAL_PALE     = "#f0fdfa"

    # Slate scale
    SLATE_50      = "#f8fafc"
    SLATE_100     = "#f1f5f9"
    SLATE_200     = "#e2e8f0"
    SLATE_300     = "#cbd5e1"
    SLATE_400     = "#94a3b8"
    SLATE_500     = "#64748b"
    SLATE_600     = "#475569"
    SLATE_700     = "#334155"
    SLATE_800     = "#1e293b"
    SLATE_900     = "#0f172a"

    # Semantic
    GREEN         = "#059669"
    GREEN_PALE    = "#f0fdf4"
    GREEN_BORDER  = "#86efac"
    AMBER         = "#d97706"
    AMBER_PALE    = "#fff7ed"
    AMBER_BORDER  = "#fcd34d"
    RED           = "#e11d48"
    RED_PALE      = "#fff1f2"
    RED_BORDER    = "#fda4af"
    PURPLE        = "#7c3aed"
    PURPLE_PALE   = "#ede9fe"
    PURPLE_BORDER = "#c4b5fd"
    YELLOW        = "#a16207"
    YELLOW_PALE   = "#fefce8"
    YELLOW_BORDER = "#fde047"
    BLUE          = "#1d4ed8"
    BLUE_PALE     = "#eff6ff"
    BLUE_BORDER   = "#93c5fd"

    # Text
    TEXT_PRIMARY   = "#1e293b"
    TEXT_SECONDARY = "#475569"
    TEXT_MUTED     = "#94a3b8"
    TEXT_LINK      = "#0d9488"


# Status badge color map: status_key → (bg, text_color, border_color)
STATUS_COLORS = {
    "incomplete": (COLORS.AMBER_PALE,   COLORS.AMBER,   COLORS.AMBER_BORDER),
    "pending":   (COLORS.AMBER_PALE,   COLORS.AMBER,   COLORS.AMBER_BORDER),
    "shipped":   (COLORS.GREEN_PALE,   COLORS.GREEN,   COLORS.GREEN_BORDER),
    "unbilled":  (COLORS.YELLOW_PALE,  COLORS.YELLOW,  COLORS.YELLOW_BORDER),
    "billed":    (COLORS.PURPLE_PALE,  COLORS.PURPLE,  COLORS.PURPLE_BORDER),
    "rx hold":   (COLORS.RED_PALE,     COLORS.RED,     COLORS.RED_BORDER),
    "rx_hold":   (COLORS.RED_PALE,     COLORS.RED,     COLORS.RED_BORDER),
    "complete":  (COLORS.GREEN_PALE,   COLORS.GREEN,   COLORS.GREEN_BORDER),
    "done":      (COLORS.GREEN_PALE,   COLORS.GREEN,   COLORS.GREEN_BORDER),
    "active":    (COLORS.TEAL_PALE,    COLORS.TEAL,    COLORS.TEAL_LIGHT),
    "inactive":  (COLORS.SLATE_100,    COLORS.SLATE_500, COLORS.SLATE_300),
    "on hold":   (COLORS.AMBER_PALE,   COLORS.AMBER,   COLORS.AMBER_BORDER),
    "urgent":    (COLORS.RED_PALE,     COLORS.RED,     COLORS.RED_BORDER),
    "today":     (COLORS.RED_PALE,     COLORS.RED,     COLORS.RED_BORDER),
    "3 days":    (COLORS.AMBER_PALE,   COLORS.AMBER,   COLORS.AMBER_BORDER),
    "flexible":  (COLORS.GREEN_PALE,   COLORS.GREEN,   COLORS.GREEN_BORDER),
    "medicaid":  (COLORS.TEAL_PALE,    COLORS.TEAL,    COLORS.TEAL_LIGHT),
    "medicare":  (COLORS.BLUE_PALE,    COLORS.BLUE,    COLORS.BLUE_BORDER),
}

def get_status_colors(status_text: str):
    """Return (bg, text, border) for a status string. Falls back to slate."""
    key = status_text.lower().strip()
    return STATUS_COLORS.get(key, (COLORS.SLATE_100, COLORS.SLATE_600, COLORS.SLATE_300))


# ─────────────────────────────────────────────────────────────────────────────
#  FONT SETUP
# ─────────────────────────────────────────────────────────────────────────────

def _load_fonts():
    """Attempt to load DM Sans. Falls back gracefully to system sans-serif."""
    # DM Sans is available on many systems; if not, Qt falls back cleanly
    pass

BASE_FONT_FAMILY = "DM Sans, Segoe UI, Inter, -apple-system, sans-serif"
MONO_FONT_FAMILY = "DM Mono, Consolas, Cascadia Code, monospace"


# ─────────────────────────────────────────────────────────────────────────────
#  COMPLETE QSS STYLESHEET
# ─────────────────────────────────────────────────────────────────────────────

def _build_qss():
    C = COLORS
    return f"""

/* ══════════════════════════════════════════════════════════════════
   GLOBAL BASE
══════════════════════════════════════════════════════════════════ */

QWidget {{
    font-family: {BASE_FONT_FAMILY};
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    background-color: {C.SAGE_BG};
}}

QMainWindow, QDialog {{
    background-color: {C.SAGE_BG};
}}

QFrame {{
    border: none;
    background-color: transparent;
}}

/* ══════════════════════════════════════════════════════════════════
   SCROLLBARS
══════════════════════════════════════════════════════════════════ */

QScrollBar:vertical {{
    background: {C.SLATE_100};
    width: 7px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {C.SLATE_300};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C.SLATE_400};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {C.SLATE_100};
    height: 7px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {C.SLATE_300};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C.SLATE_400};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ══════════════════════════════════════════════════════════════════
   LABELS
══════════════════════════════════════════════════════════════════ */

QLabel {{
    background: transparent;
    border: none;
    color: {C.TEXT_PRIMARY};
}}
QLabel[class="page-title"] {{
    font-size: 18px;
    font-weight: 700;
    color: {C.SLATE_800};
    letter-spacing: -0.3px;
}}
QLabel[class="page-subtitle"] {{
    font-size: 12px;
    color: {C.TEXT_MUTED};
    font-weight: 400;
}}
QLabel[class="section-title"] {{
    font-size: 11px;
    font-weight: 700;
    color: {C.SLATE_600};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QLabel[class="form-label"] {{
    font-size: 11px;
    font-weight: 600;
    color: {C.SLATE_600};
    text-transform: uppercase;
    letter-spacing: 0.3px;
}}
QLabel[class="mono"] {{
    font-family: {MONO_FONT_FAMILY};
    font-size: 12px;
}}
QLabel[class="badge-pending"] {{
    background-color: {C.AMBER_PALE};
    color: {C.AMBER};
    border: 1px solid {C.AMBER_BORDER};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[class="badge-shipped"] {{
    background-color: {C.GREEN_PALE};
    color: {C.GREEN};
    border: 1px solid {C.GREEN_BORDER};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[class="badge-unbilled"] {{
    background-color: {C.YELLOW_PALE};
    color: {C.YELLOW};
    border: 1px solid {C.YELLOW_BORDER};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[class="badge-billed"] {{
    background-color: {C.PURPLE_PALE};
    color: {C.PURPLE};
    border: 1px solid {C.PURPLE_BORDER};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[class="badge-rxhold"] {{
    background-color: {C.RED_PALE};
    color: {C.RED};
    border: 1px solid {C.RED_BORDER};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}

/* ══════════════════════════════════════════════════════════════════
   PUSH BUTTONS
══════════════════════════════════════════════════════════════════ */

QPushButton {{
    background-color: {C.WHITE};
    color: {C.TEXT_PRIMARY};
    border: 1px solid {C.SLATE_300};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
    min-height: 28px;
}}
QPushButton:hover {{
    background-color: {C.SLATE_50};
    border-color: {C.SLATE_400};
}}
QPushButton:pressed {{
    background-color: {C.SLATE_100};
}}
QPushButton:disabled {{
    background-color: {C.SLATE_100};
    color: {C.TEXT_MUTED};
    border-color: {C.SLATE_200};
}}

/* Primary / Teal */
QPushButton[class="btn-primary"] {{
    background-color: {C.TEAL};
    color: white;
    border: 1px solid {C.TEAL_DARK};
}}
QPushButton[class="btn-primary"]:hover {{
    background-color: {C.TEAL_DARK};
    border-color: {C.TEAL_DARK};
}}
QPushButton[class="btn-primary"]:pressed {{
    background-color: #087a70;
}}

/* Danger / Red */
QPushButton[class="btn-danger"], QPushButton[class="btn-red"] {{
    background-color: {C.RED};
    color: white;
    border: 1px solid #c51a3d;
}}
QPushButton[class="btn-danger"]:hover, QPushButton[class="btn-red"]:hover {{
    background-color: #c51a3d;
}}

/* Ghost (outline) */
QPushButton[class="btn-ghost"] {{
    background-color: transparent;
    color: {C.TEXT_SECONDARY};
    border: 1px solid {C.SLATE_300};
}}
QPushButton[class="btn-ghost"]:hover {{
    background-color: {C.SLATE_100};
    color: {C.TEXT_PRIMARY};
}}

/* Ghost on dark (for action bars) */
QPushButton[class="btn-ghost-dark"] {{
    background-color: transparent;
    color: {C.SLATE_300};
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 5px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
}}
QPushButton[class="btn-ghost-dark"]:hover {{
    background-color: rgba(255,255,255,0.1);
    color: white;
    border-color: rgba(255,255,255,0.4);
}}

/* Teal outline ghost */
QPushButton[class="btn-teal-ghost"] {{
    background-color: transparent;
    color: {C.TEAL};
    border: 1px solid {C.TEAL};
    border-radius: 5px;
}}
QPushButton[class="btn-teal-ghost"]:hover {{
    background-color: {C.TEAL_PALE};
}}

/* Green */
QPushButton[class="btn-green"] {{
    background-color: {C.GREEN};
    color: white;
    border: 1px solid #047857;
}}
QPushButton[class="btn-green"]:hover {{
    background-color: #047857;
}}

/* Small */
QPushButton[class~="btn-sm"] {{
    padding: 3px 10px;
    font-size: 11px;
    min-height: 22px;
}}
/* Extra small */
QPushButton[class~="btn-xs"] {{
    padding: 1px 6px;
    font-size: 10px;
    min-height: 18px;
    border-radius: 4px;
}}

/* Topbar nav tabs */
QPushButton[class="nav-tab"] {{
    background-color: transparent;
    color: rgba(255,255,255,0.65);
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 0 10px;
    font-size: 12px;
    font-weight: 500;
    min-height: 40px;
}}
QPushButton[class="nav-tab"]:hover {{
    color: rgba(255,255,255,0.9);
    background-color: rgba(255,255,255,0.07);
}}
QPushButton[class="nav-tab-active"] {{
    background-color: transparent;
    color: white;
    border: none;
    border-bottom: 2px solid {C.TEAL};
    border-radius: 0;
    padding: 0 10px;
    font-size: 12px;
    font-weight: 600;
    min-height: 40px;
}}

/* ══════════════════════════════════════════════════════════════════
   LINE EDIT / TEXT INPUT
══════════════════════════════════════════════════════════════════ */

QLineEdit {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_300};
    border-radius: 5px;
    padding: 5px 9px;
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    selection-background-color: {C.TEAL_LIGHT};
}}
QLineEdit:focus {{
    border-color: {C.TEAL};
    outline: none;
}}
QLineEdit:disabled {{
    background-color: {C.SLATE_100};
    color: {C.TEXT_MUTED};
    border-color: {C.SLATE_200};
}}
QLineEdit::placeholder {{
    color: {C.TEXT_MUTED};
}}

/* ══════════════════════════════════════════════════════════════════
   TEXT EDIT / PLAIN TEXT EDIT
══════════════════════════════════════════════════════════════════ */

QTextEdit, QPlainTextEdit {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_300};
    border-radius: 5px;
    padding: 6px 9px;
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    selection-background-color: {C.TEAL_LIGHT};
}}
QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {C.TEAL};
}}

/* ══════════════════════════════════════════════════════════════════
   COMBO BOX / SELECT
══════════════════════════════════════════════════════════════════ */

QComboBox {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_300};
    border-radius: 5px;
    padding: 5px 9px;
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    min-height: 28px;
}}
QComboBox:focus {{
    border-color: {C.TEAL};
}}
QComboBox:hover {{
    border-color: {C.SLATE_400};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid {C.SLATE_300};
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background: {C.SLATE_50};
}}
QComboBox::down-arrow {{
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 5px;
    selection-background-color: {C.TEAL_PALE};
    selection-color: {C.TEAL_DARK};
    padding: 3px;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: 3px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {C.SLATE_100};
}}

/* ══════════════════════════════════════════════════════════════════
   SPIN BOX
══════════════════════════════════════════════════════════════════ */

QSpinBox, QDoubleSpinBox {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_300};
    border-radius: 5px;
    padding: 4px 9px;
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {C.TEAL};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {C.SLATE_50};
    border: none;
    border-left: 1px solid {C.SLATE_200};
    width: 18px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {C.SLATE_100};
}}

/* ══════════════════════════════════════════════════════════════════
   DATE EDIT
══════════════════════════════════════════════════════════════════ */

QDateEdit {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_300};
    border-radius: 5px;
    padding: 5px 9px;
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    min-height: 28px;
}}
QDateEdit:focus {{
    border-color: {C.TEAL};
}}
QDateEdit::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid {C.SLATE_200};
    background: {C.SLATE_50};
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}}
QCalendarWidget {{
    background: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 6px;
    min-width: 300px;
    min-height: 240px;
}}
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background: {C.SLATE_50};
    border-bottom: 1px solid {C.SLATE_200};
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    min-height: 36px;
    padding: 2px 6px;
}}
QCalendarWidget QToolButton {{
    background: transparent;
    color: {C.TEXT_PRIMARY};
    border: none;
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 13px;
    font-weight: 600;
}}
QCalendarWidget QToolButton:hover {{
    background: {C.SLATE_200};
}}
QCalendarWidget QToolButton#qt_calendar_prevmonth,
QCalendarWidget QToolButton#qt_calendar_nextmonth {{
    qproperty-iconSize: 14px;
    min-width: 28px;
}}
QCalendarWidget QAbstractItemView {{
    background: {C.WHITE};
    selection-background-color: {C.TEAL};
    selection-color: white;
    font-size: 12px;
    outline: none;
    gridline-color: {C.SLATE_100};
}}
QCalendarWidget QAbstractItemView::item {{
    padding: 4px;
    min-width: 32px;
    min-height: 28px;
}}
QCalendarWidget QAbstractItemView::item:hover {{
    background-color: {C.TEAL_PALE};
    border-radius: 4px;
}}
QCalendarWidget QAbstractItemView::item:selected {{
    background-color: {C.TEAL};
    color: white;
    border-radius: 4px;
}}
QCalendarWidget #qt_calendar_calendarview {{
    background: {C.WHITE};
}}
QCalendarWidget QMenu {{
    background: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 4px;
    padding: 4px;
}}
QCalendarWidget QMenu::item {{
    padding: 4px 20px;
}}
QCalendarWidget QMenu::item:selected {{
    background: {C.TEAL_PALE};
    color: {C.TEAL_DARK};
}}
QCalendarWidget QSpinBox {{
    border: 1px solid {C.SLATE_300};
    border-radius: 3px;
    padding: 2px 4px;
    background: {C.WHITE};
    color: {C.TEXT_PRIMARY};
    font-size: 13px;
    min-width: 60px;
}}

/* ══════════════════════════════════════════════════════════════════
   CHECK BOX
══════════════════════════════════════════════════════════════════ */

QCheckBox {{
    spacing: 7px;
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    background: transparent;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1.5px solid {C.SLATE_300};
    border-radius: 3px;
    background: {C.WHITE};
}}
QCheckBox::indicator:hover {{
    border-color: {C.TEAL};
}}
QCheckBox::indicator:checked {{
    background-color: {C.TEAL};
    border-color: {C.TEAL};
    image: none;
}}
QCheckBox::indicator:checked:after {{
    color: white;
}}

/* ══════════════════════════════════════════════════════════════════
   RADIO BUTTON
══════════════════════════════════════════════════════════════════ */

QRadioButton {{
    spacing: 7px;
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    background: transparent;
}}
QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1.5px solid {C.SLATE_300};
    border-radius: 8px;
    background: {C.WHITE};
}}
QRadioButton::indicator:hover {{
    border-color: {C.TEAL};
}}
QRadioButton::indicator:checked {{
    background-color: {C.TEAL};
    border-color: {C.TEAL};
}}

/* ══════════════════════════════════════════════════════════════════
   TABLES
══════════════════════════════════════════════════════════════════ */

QTableWidget, QTableView {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 6px;
    gridline-color: {C.SLATE_100};
    alternate-background-color: {C.SLATE_50};
    selection-background-color: {C.TEAL_PALE};
    selection-color: {C.TEXT_PRIMARY};
    font-size: 13px;
    outline: 0;
}}
QTableWidget::item, QTableView::item {{
    padding: 4px 10px;
    border: none;
    color: {C.TEXT_PRIMARY};
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {C.TEAL_PALE};
    color: {C.TEXT_PRIMARY};
    border-left: 2px solid {C.TEAL};
}}
QTableWidget::item:hover, QTableView::item:hover {{
    background-color: {C.SLATE_50};
}}
QHeaderView {{
    background-color: {C.SLATE_50};
    border: none;
}}
QHeaderView::section {{
    background-color: {C.SLATE_50};
    color: {C.SLATE_500};
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    padding: 8px 10px;
    border: none;
    border-bottom: 2px solid {C.SLATE_200};
    border-right: 1px solid {C.SLATE_200};
}}
QHeaderView::section:last {{
    border-right: none;
}}
QHeaderView::section:hover {{
    background-color: {C.SLATE_100};
    color: {C.TEXT_PRIMARY};
}}
QHeaderView::section:checked {{
    background-color: {C.TEAL_PALE};
    color: {C.TEAL_DARK};
}}
QTableCornerButton::section {{
    background-color: {C.SLATE_50};
    border-right: 1px solid {C.SLATE_200};
    border-bottom: 2px solid {C.SLATE_200};
}}

/* ══════════════════════════════════════════════════════════════════
   LIST WIDGET
══════════════════════════════════════════════════════════════════ */

QListWidget {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 6px;
    alternate-background-color: {C.SLATE_50};
    outline: 0;
}}
QListWidget::item {{
    padding: 8px 12px;
    border: none;
    border-radius: 0;
    color: {C.TEXT_PRIMARY};
}}
QListWidget::item:hover {{
    background-color: {C.SLATE_50};
}}
QListWidget::item:selected {{
    background-color: {C.TEAL_PALE};
    color: {C.TEXT_PRIMARY};
    border-left: 2px solid {C.TEAL};
}}

/* ══════════════════════════════════════════════════════════════════
   TAB WIDGET
══════════════════════════════════════════════════════════════════ */

QTabWidget::pane {{
    border: none;
    border-top: 1px solid {C.SLATE_200};
    background: {C.WHITE};
}}
QTabWidget::tab-bar {{
    alignment: left;
}}
QTabBar {{
    background-color: {C.WHITE};
}}
QTabBar::tab {{
    background-color: #161d2c;
    color: #93c5fd;
    border: 1px solid #253550;
    border-radius: 5px;
    padding: 5px 12px;
    margin: 4px 2px 4px 2px;
    font-size: 9pt;
    font-weight: 600;
    min-width: 60px;
    min-height: 26px;
}}
QTabBar::tab:hover {{
    color: #bfdbfe;
    background-color: #1e293b;
    border-color: #3b82f6;
}}
QTabBar::tab:selected {{
    color: #3b82f6;
    background-color: #0f172a;
    border-color: #3b82f6;
}}

/* ══════════════════════════════════════════════════════════════════
   MENU BAR & MENUS
══════════════════════════════════════════════════════════════════ */

QMenuBar {{
    background-color: {C.NAVY};
    color: rgba(255,255,255,0.85);
    border-bottom: 1px solid {C.NAVY_DARK};
    font-size: 13px;
    padding: 2px 4px;
}}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background: rgba(255,255,255,0.1);
    color: white;
}}
QMenu {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 6px;
    padding: 4px;
    font-size: 13px;
}}
QMenu::item {{
    padding: 7px 14px;
    border-radius: 4px;
    color: {C.TEXT_PRIMARY};
}}
QMenu::item:selected {{
    background-color: {C.TEAL_PALE};
    color: {C.TEAL_DARK};
}}
QMenu::item:disabled {{
    color: {C.TEXT_MUTED};
}}
QMenu::separator {{
    height: 1px;
    background: {C.SLATE_200};
    margin: 4px 8px;
}}

/* ══════════════════════════════════════════════════════════════════
   TOOL BAR
══════════════════════════════════════════════════════════════════ */

QToolBar {{
    background-color: {C.WHITE};
    border-bottom: 1px solid {C.SLATE_200};
    spacing: 4px;
    padding: 4px 8px;
}}
QToolBar QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px 10px;
    font-size: 12px;
    color: {C.TEXT_PRIMARY};
}}
QToolBar QToolButton:hover {{
    background-color: {C.SLATE_100};
    border-color: {C.SLATE_200};
}}

/* ══════════════════════════════════════════════════════════════════
   DIALOG BUTTONS (QDialogButtonBox)
══════════════════════════════════════════════════════════════════ */

QDialogButtonBox QPushButton {{
    min-width: 80px;
}}
QDialogButtonBox QPushButton[text="OK"],
QDialogButtonBox QPushButton[text="Save"],
QDialogButtonBox QPushButton[text="Apply"] {{
    background-color: {C.TEAL};
    color: white;
    border: 1px solid {C.TEAL_DARK};
}}
QDialogButtonBox QPushButton[text="OK"]:hover,
QDialogButtonBox QPushButton[text="Save"]:hover {{
    background-color: {C.TEAL_DARK};
}}
QDialogButtonBox QPushButton[text="Cancel"],
QDialogButtonBox QPushButton[text="Close"] {{
    background-color: {C.WHITE};
    color: {C.TEXT_SECONDARY};
    border: 1px solid {C.SLATE_300};
}}

/* ══════════════════════════════════════════════════════════════════
   GROUP BOX
══════════════════════════════════════════════════════════════════ */

QGroupBox {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px 12px 10px;
    font-size: 11px;
    font-weight: 700;
    color: {C.SLATE_600};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background-color: {C.WHITE};
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: {C.SLATE_600};
}}

/* ══════════════════════════════════════════════════════════════════
   PROGRESS BAR
══════════════════════════════════════════════════════════════════ */

QProgressBar {{
    background-color: {C.SLATE_100};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    font-size: 10px;
}}
QProgressBar::chunk {{
    background-color: {C.TEAL};
    border-radius: 4px;
}}

/* ══════════════════════════════════════════════════════════════════
   SLIDER
══════════════════════════════════════════════════════════════════ */

QSlider::groove:horizontal {{
    height: 4px;
    background: {C.SLATE_200};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C.TEAL};
    border: none;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {C.TEAL_DARK};
}}
QSlider::sub-page:horizontal {{
    background: {C.TEAL};
    border-radius: 2px;
}}

/* ══════════════════════════════════════════════════════════════════
   SPLITTER
══════════════════════════════════════════════════════════════════ */

QSplitter::handle {{
    background-color: {C.SLATE_200};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}
QSplitter::handle:hover {{
    background-color: {C.TEAL};
}}

/* ══════════════════════════════════════════════════════════════════
   STATUS BAR
══════════════════════════════════════════════════════════════════ */

QStatusBar {{
    background-color: {C.SLATE_50};
    border-top: 1px solid {C.SLATE_200};
    color: {C.TEXT_SECONDARY};
    font-size: 11px;
    padding: 2px 8px;
}}
QStatusBar::item {{
    border: none;
}}

/* ══════════════════════════════════════════════════════════════════
   TOOL TIP
══════════════════════════════════════════════════════════════════ */

QToolTip {{
    background-color: {C.SLATE_800};
    color: white;
    border: 1px solid {C.SLATE_700};
    border-radius: 5px;
    padding: 5px 9px;
    font-size: 11px;
}}

/* ══════════════════════════════════════════════════════════════════
   MESSAGE BOX
══════════════════════════════════════════════════════════════════ */

QMessageBox {{
    background-color: {C.WHITE};
    border-radius: 8px;
}}
QMessageBox QLabel {{
    font-size: 13px;
    color: {C.TEXT_PRIMARY};
    background: transparent;
}}

/* ══════════════════════════════════════════════════════════════════
   INPUT DIALOG
══════════════════════════════════════════════════════════════════ */

QInputDialog {{
    background-color: {C.WHITE};
}}

/* ══════════════════════════════════════════════════════════════════
   FILE DIALOG
══════════════════════════════════════════════════════════════════ */

QFileDialog {{
    background-color: {C.SAGE_BG};
}}
QFileDialog QListView, QFileDialog QTreeView {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 4px;
}}

/* ══════════════════════════════════════════════════════════════════
   DMELogic-SPECIFIC CUSTOM FRAMES
   (applied via setProperty("role", "..."))
══════════════════════════════════════════════════════════════════ */

/* Top navigation bar */
QFrame[role="topbar"] {{
    background-color: {C.NAVY};
    border: none;
    border-bottom: 1px solid {C.NAVY_DARK};
    min-height: 42px;
    max-height: 42px;
}}

/* Secondary demo/breadcrumb bar */
QFrame[role="demobar"] {{
    background-color: {C.SLATE_800};
    border: none;
    border-bottom: 1px solid {C.SLATE_700};
    min-height: 30px;
    max-height: 30px;
}}

/* Page content area */
QFrame[role="content-area"] {{
    background-color: {C.SAGE_BG};
    border: none;
}}

/* White card panel */
QFrame[role="card"] {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 8px;
}}

/* Section box (within modals/forms) */
QFrame[role="section-box"] {{
    background-color: {C.SLATE_50};
    border: 1px solid {C.SLATE_200};
    border-radius: 6px;
}}

/* Dashboard stat card */
QFrame[role="stat-card"] {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 8px;
    border-left: 4px solid {C.TEAL};
}}
QFrame[role="stat-card-warn"] {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 8px;
    border-left: 4px solid {C.AMBER};
}}
QFrame[role="stat-card-danger"] {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 8px;
    border-left: 4px solid {C.RED};
}}
QFrame[role="stat-card-purple"] {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 8px;
    border-left: 4px solid {C.PURPLE};
}}

/* Action bar (appears at bottom on row select) */
QFrame[role="action-bar"] {{
    background-color: {C.NAVY};
    border: none;
    border-top: 2px solid {C.TEAL};
    min-height: 44px;
    max-height: 44px;
}}

/* Alert boxes */
QFrame[role="alert-info"] {{
    background-color: {C.BLUE_PALE};
    border: 1px solid {C.BLUE_BORDER};
    border-radius: 5px;
    padding: 2px;
}}
QFrame[role="alert-warn"] {{
    background-color: {C.AMBER_PALE};
    border: 1px solid {C.AMBER_BORDER};
    border-radius: 5px;
    padding: 2px;
}}
QFrame[role="alert-danger"] {{
    background-color: {C.RED_PALE};
    border: 1px solid {C.RED_BORDER};
    border-radius: 5px;
    padding: 2px;
}}
QFrame[role="alert-success"] {{
    background-color: {C.GREEN_PALE};
    border: 1px solid {C.GREEN_BORDER};
    border-radius: 5px;
    padding: 2px;
}}

/* Refill summary box (dark navy) */
QFrame[role="refill-summary"] {{
    background-color: {C.NAVY};
    border-radius: 6px;
    padding: 4px;
}}
QFrame[role="refill-summary"] QLabel {{
    color: rgba(255,255,255,0.85);
    background: transparent;
}}
QFrame[role="refill-summary"] QLabel[role="value"] {{
    color: white;
    font-weight: 600;
}}

/* Status radio item */
QFrame[role="status-radio-item"] {{
    background-color: {C.WHITE};
    border: 1.5px solid {C.SLATE_200};
    border-radius: 6px;
    padding: 2px;
}}
QFrame[role="status-radio-item"]:hover {{
    border-color: {C.TEAL};
    background-color: {C.TEAL_PALE};
}}
QFrame[role="status-radio-item-selected"] {{
    background-color: {C.TEAL_PALE};
    border: 2px solid {C.TEAL};
    border-radius: 6px;
    padding: 2px;
}}

/* ePACES field row */
QFrame[role="epaces-field"] {{
    background-color: {C.SLATE_50};
    border: 1px solid {C.SLATE_200};
    border-radius: 4px;
    padding: 2px;
}}

/* Wizard step indicator */
QFrame[role="wizard-step"] {{
    background: transparent;
    border: none;
}}
QFrame[role="wizard-step-active"] {{
    background: transparent;
    border: none;
}}

/* Bottom banner (low stock warning, etc.) */
QFrame[role="banner-warn"] {{
    background-color: {C.AMBER_PALE};
    border-top: 2px solid {C.AMBER};
    border-radius: 0;
    min-height: 32px;
    max-height: 32px;
}}
QFrame[role="banner-danger"] {{
    background-color: {C.RED_PALE};
    border-top: 2px solid {C.RED};
    border-radius: 0;
    min-height: 32px;
    max-height: 32px;
}}

/* Segmented control tab bar */
QFrame[role="seg-tabs"] {{
    background-color: {C.SLATE_100};
    border-radius: 6px;
    border: none;
    padding: 2px;
}}
QPushButton[role="seg-tab"] {{
    background-color: transparent;
    border: none;
    border-radius: 5px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: 500;
    color: {C.TEXT_SECONDARY};
    min-height: 26px;
}}
QPushButton[role="seg-tab"]:hover {{
    background-color: {C.SLATE_200};
    color: {C.TEXT_PRIMARY};
}}
QPushButton[role="seg-tab-active"] {{
    background-color: {C.WHITE};
    border: none;
    border-radius: 5px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: 600;
    color: {C.TEXT_PRIMARY};
    min-height: 26px;
}}

/* Copy button (ePACES helper) */
QPushButton[role="copy-btn"] {{
    background-color: {C.SLATE_100};
    color: {C.TEXT_SECONDARY};
    border: 1px solid {C.SLATE_200};
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    min-height: 20px;
    max-height: 20px;
}}
QPushButton[role="copy-btn"]:hover {{
    background-color: {C.TEAL_PALE};
    color: {C.TEAL};
    border-color: {C.TEAL};
}}

/* Document file row */
QFrame[role="doc-row"] {{
    background-color: {C.WHITE};
    border: 1px solid {C.SLATE_200};
    border-radius: 5px;
    padding: 2px;
}}
QFrame[role="doc-row"]:hover {{
    border-color: {C.TEAL};
    background-color: {C.TEAL_PALE};
}}
QFrame[role="doc-row-selected"] {{
    background-color: {C.TEAL_PALE};
    border: 1px solid {C.TEAL};
    border-radius: 5px;
    padding: 2px;
}}

"""


# ─────────────────────────────────────────────────────────────────────────────
#  CALENDAR POPUP STYLING
# ─────────────────────────────────────────────────────────────────────────────

class _CalendarStyleFilter(QObject):
    """
    App-level event filter that auto-styles every QCalendarWidget
    pop-up when it becomes visible.  Installed once by apply_theme().
    """

    _styled: set  # track already-configured widgets by id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._styled = set()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # QCalendarWidget fires Show when the popup opens
        if event.type() == QEvent.Type.Show and isinstance(obj, QCalendarWidget):
            wid = id(obj)
            if wid not in self._styled:
                self._styled.add(wid)
                _configure_calendar(obj)
        return False


# Singleton filter instance (kept alive as class attribute)
_calendar_filter_instance: _CalendarStyleFilter | None = None


def _configure_calendar(cal: QCalendarWidget) -> None:
    """Configure a QCalendarWidget for a polished, readable look."""
    # Use single-letter day headers (S M T W T F S) for compact display
    cal.setHorizontalHeaderFormat(
        QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames
    )
    # Hide week numbers column
    cal.setVerticalHeaderFormat(
        QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
    )

    # Enforce minimum popup size so cells aren't crushed
    cal.setMinimumSize(280, 220)

    # Style the header row (day-of-week labels)
    header_fmt = QTextCharFormat()
    header_fmt.setForeground(QColor(COLORS.TEXT_SECONDARY))
    header_fmt.setBackground(QColor(COLORS.SLATE_50))
    header_fmt.setFontWeight(QFont.Weight.DemiBold)
    cal.setHeaderTextFormat(header_fmt)

    # Style "today"
    today_fmt = QTextCharFormat()
    today_fmt.setForeground(QColor(COLORS.TEAL))
    today_fmt.setFontWeight(QFont.Weight.Bold)
    from PyQt6.QtCore import QDate
    cal.setDateTextFormat(QDate.currentDate(), today_fmt)

    # Grid
    cal.setGridVisible(False)


def style_date_edit(date_edit: QDateEdit) -> None:
    """
    Convenience: explicitly configure a QDateEdit's calendar popup.
    Also installs the event filter so re-shows are handled.
    """
    global _calendar_filter_instance
    if date_edit.calendarPopup():
        cal = date_edit.calendarWidget()
        _configure_calendar(cal)
        if _calendar_filter_instance is not None:
            cal.installEventFilter(_calendar_filter_instance)


def style_all_calendars(root_widget=None) -> None:
    """
    Walk a widget tree (or all app widgets) and configure every
    QDateEdit calendar popup found.  Call after building the UI.
    """
    global _calendar_filter_instance
    app = QApplication.instance()
    if root_widget is not None:
        widgets = root_widget.findChildren(QDateEdit)
    elif app is not None:
        widgets = [w for w in app.allWidgets() if isinstance(w, QDateEdit)]
    else:
        return

    for de in widgets:
        if de.calendarPopup():
            cal = de.calendarWidget()
            _configure_calendar(cal)
            if _calendar_filter_instance is not None:
                cal.installEventFilter(_calendar_filter_instance)


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def apply_theme(app: QApplication):
    """
    Apply the full DMELogic design system to the application.

    Call once after creating your QApplication, before showing any windows:

        app = QApplication(sys.argv)
        from dmelogic.dme_theme import apply_theme
        apply_theme(app)

    This sets:
      - The global QSS stylesheet
      - A clean font (DM Sans / Segoe UI fallback)
      - The application palette for non-QSS paths
    """
    _load_fonts()

    # Font
    font = QFont("DM Sans")
    font.setPixelSize(13)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    # Stylesheet
    app.setStyleSheet(_build_qss())

    # Palette (covers native widget paths that ignore QSS)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS.SAGE_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS.WHITE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS.SLATE_50))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS.WHITE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS.TEAL))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLORS.TEAL))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(COLORS.SLATE_200))
    palette.setColor(QPalette.ColorRole.Mid, QColor(COLORS.SLATE_300))
    palette.setColor(QPalette.ColorRole.Dark, QColor(COLORS.SLATE_500))
    app.setPalette(palette)

    # Install calendar style filter (auto-styles popups on first show)
    global _calendar_filter_instance
    _calendar_filter_instance = _CalendarStyleFilter(app)

    # Connect to focusChanged to catch newly-opened calendar popups
    def _on_focus_changed(old, new):
        if new is not None:
            # Walk up to check if this widget is inside a QCalendarWidget
            parent = new
            while parent is not None:
                if isinstance(parent, QCalendarWidget):
                    wid = id(parent)
                    if wid not in _calendar_filter_instance._styled:
                        _calendar_filter_instance._styled.add(wid)
                        _configure_calendar(parent)
                        parent.installEventFilter(_calendar_filter_instance)
                    break
                parent = parent.parent() if hasattr(parent, 'parent') else None

    app.focusChanged.connect(_on_focus_changed)

    # Retroactively style any QCalendarWidgets already in existence
    for widget in app.allWidgets():
        if isinstance(widget, QCalendarWidget):
            _configure_calendar(widget)
            widget.installEventFilter(_calendar_filter_instance)
        elif isinstance(widget, QDateEdit) and widget.calendarPopup():
            cal = widget.calendarWidget()
            _configure_calendar(cal)
            cal.installEventFilter(_calendar_filter_instance)

    print("[DMELogic] Theme applied — v2.4 design system loaded.")


def get_status_badge_style(status: str) -> str:
    """
    Return a QSS-compatible inline style string for a status badge label.

    Usage:
        lbl = QLabel("Shipped")
        lbl.setStyleSheet(get_status_badge_style("shipped"))
    """
    bg, text, border = get_status_colors(status)
    return f"""
        QLabel {{
            background-color: {bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
        }}
    """


def style_table(table, alternating=True, row_height=30):
    """
    Apply standard DMELogic table styling to a QTableWidget or QTableView.

    Usage:
        from dmelogic.dme_theme import style_table
        style_table(self.orders_table)
    """
    from PyQt6.QtWidgets import QAbstractItemView, QHeaderView
    table.setAlternatingRowColors(alternating)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setHighlightSections(False)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)


if __name__ == "__main__":
    # Quick preview test
    import sys
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QLineEdit, QComboBox, QTableWidget,
        QTableWidgetItem, QFrame
    )

    app = QApplication(sys.argv)
    apply_theme(app)

    win = QMainWindow()
    win.setWindowTitle("DMELogic Theme Preview")
    win.resize(900, 600)

    central = QWidget()
    layout = QVBoxLayout(central)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    # Buttons
    btn_row = QHBoxLayout()
    for cls, txt in [
        ("btn-primary", "Primary"),
        ("btn-ghost", "Ghost"),
        ("btn-danger", "Danger"),
        ("btn-green", "Green"),
        ("btn-ghost", "Disabled"),
    ]:
        b = QPushButton(txt)
        b.setProperty("class", cls)
        btn_row.addWidget(b)
    btn_row.addStretch()
    layout.addLayout(btn_row)

    # Inputs
    inp_row = QHBoxLayout()
    for ph in ["Search patients...", "HCPCS Code", "Notes"]:
        e = QLineEdit(); e.setPlaceholderText(ph); inp_row.addWidget(e)
    cb = QComboBox(); cb.addItems(["Pending", "Shipped", "Unbilled", "Billed"]); inp_row.addWidget(cb)
    layout.addLayout(inp_row)

    # Table
    tbl = QTableWidget(4, 5)
    style_table(tbl)
    tbl.setHorizontalHeaderLabels(["Order #", "Patient", "HCPCS", "Status", "Amount"])
    data = [
        ("ORD-316", "DANNER, WARREN", "T4522", "Pending", "$436.80"),
        ("ORD-014-R2", "ABREU, ELIANE", "A4554", "Unbilled", "$215.40"),
        ("ORD-090", "RODRIGUEZ, NATIVIDAD", "T4522", "Shipped", "$436.80"),
        ("ORD-025", "CHEN, LISA", "A4495", "Billed", "$180.00"),
    ]
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            tbl.setItem(r, c, QTableWidgetItem(val))
    layout.addWidget(tbl)

    # Status badges
    badge_row = QHBoxLayout()
    for s in ["Pending", "Shipped", "Unbilled", "Billed", "RX Hold"]:
        lbl = QLabel(s)
        lbl.setStyleSheet(get_status_badge_style(s))
        badge_row.addWidget(lbl)
    badge_row.addStretch()
    layout.addLayout(badge_row)

    win.setCentralWidget(central)
    win.show()
    sys.exit(app.exec())
