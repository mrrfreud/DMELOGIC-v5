"""
dme_widgets.py
==============
DMELogic — Reusable UI Component Library
=========================================

Drop into your project alongside dme_theme.py.
Import any component you need:

    from dmelogic.dme_widgets import (
        TopBar, ActionBar, StatusBadge, DMETable,
        PageHeader, SectionBox, FormRow, AlertBox,
        StatCard, SegmentedTabs, WizardSteps, EpacesFieldRow
    )

All components automatically respect the theme applied via dme_theme.apply_theme().
"""

import os
import sys
from typing import List, Optional, Callable, Dict, Tuple

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy, QMenu, QLineEdit, QScrollArea, QApplication,
    QToolButton, QButtonGroup, QStackedWidget
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QPropertyAnimation, QEasingCurve,
    QTimer, QSize, QRect
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QPixmap, QPainter, QBrush, QAction,
    QCursor
)

from dmelogic.dme_theme import COLORS, get_status_colors, style_table, get_status_badge_style


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_btn(text: str, cls: str = "", role: str = "", small: bool = False,
              icon: str = "") -> QPushButton:
    """Factory for styled push buttons."""
    btn = QPushButton(f"{icon} {text}".strip() if icon else text)
    if cls:
        btn.setProperty("class", cls)
    if role:
        btn.setProperty("role", role)
    if small:
        current_cls = btn.property("class") or ""
        btn.setProperty("class", f"{current_cls} btn-sm".strip())
    return btn


# ─────────────────────────────────────────────────────────────────────────────
#  STATUS BADGE
# ─────────────────────────────────────────────────────────────────────────────

class StatusBadge(QLabel):
    """
    Colored status pill that updates its color automatically based on text.

    Usage:
        badge = StatusBadge("Shipped")
        badge.set_status("Unbilled")   # updates color live

    In a QTableWidget, use as a cell widget:
        table.setCellWidget(row, col, StatusBadge(status_text))
    """

    def __init__(self, status: str = "", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(22)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed
        )
        self.set_status(status)

    def set_status(self, status: str):
        self.setText(status)
        # Add dot prefix
        display = f"● {status}" if status else ""
        self.setText(display)
        self.setStyleSheet(get_status_badge_style(status))
        # Compute minimum width based on text
        w = max(70, len(status) * 7 + 30)
        self.setMinimumWidth(w)
        self.setMaximumWidth(w + 20)


# ─────────────────────────────────────────────────────────────────────────────
#  DME TABLE
# ─────────────────────────────────────────────────────────────────────────────

class DMETable(QTableWidget):
    """
    Pre-styled QTableWidget with:
    - Correct header appearance
    - Alternating rows
    - Row selection mode
    - No edit triggers
    - No vertical header
    - Consistent row height
    - row_clicked(int) signal emitted on single click
    - row_double_clicked(int) signal emitted on double click

    Usage:
        table = DMETable(
            columns=["Order #", "Patient", "HCPCS", "Status", "Date"],
            col_widths=[100, 180, 140, 90, 100]
        )
        table.row_clicked.connect(self.on_row_select)
        table.row_double_clicked.connect(self.on_row_dbl)
    """

    row_clicked        = pyqtSignal(int)
    row_double_clicked = pyqtSignal(int)

    def __init__(self, columns: List[str] = None, col_widths: List[int] = None,
                 row_height: int = 30, parent=None):
        super().__init__(parent)
        self._columns    = columns or []
        self._row_height = row_height

        if columns:
            self.setColumnCount(len(columns))
            self.setHorizontalHeaderLabels(columns)

        style_table(self, alternating=True, row_height=row_height)

        if col_widths and columns:
            for i, w in enumerate(col_widths[:len(columns)]):
                if w > 0:
                    self.setColumnWidth(i, w)

        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.cellClicked.connect(lambda r, _: self.row_clicked.emit(r))
        self.cellDoubleClicked.connect(lambda r, _: self.row_double_clicked.emit(r))

    def _on_selection_changed(self):
        rows = self.selectionModel().selectedRows()
        if rows:
            self.row_clicked.emit(rows[0].row())

    def add_row(self, values: list, row_data: dict = None):
        """
        Append a row. values is a list of cell contents (str or QWidget).
        row_data is optional metadata stored on the first item (Qt.UserRole).
        """
        r = self.rowCount()
        self.insertRow(r)
        self.setRowHeight(r, self._row_height)
        for c, val in enumerate(values[:self.columnCount()]):
            if isinstance(val, QWidget):
                self.setCellWidget(r, c, val)
            else:
                item = QTableWidgetItem(str(val) if val is not None else "")
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if c == 0 and row_data is not None:
                    item.setData(Qt.ItemDataRole.UserRole, row_data)
                self.setItem(r, c, item)
        return r

    def add_mono_row(self, values: list, mono_cols: List[int] = None, row_data: dict = None):
        """Add a row with specific columns rendered in monospace."""
        r = self.add_row(values, row_data)
        mono_cols = mono_cols or []
        mono_font = QFont("DM Mono, Consolas, monospace")
        mono_font.setPixelSize(12)
        for c in mono_cols:
            item = self.item(r, c)
            if item:
                item.setFont(mono_font)
        return r

    def highlight_row(self, row: int, color: str = COLORS.RED_PALE):
        """Highlight an entire row with a background color (e.g. overdue refills)."""
        bg = QColor(color)
        for c in range(self.columnCount()):
            item = self.item(row, c)
            if item:
                item.setBackground(QBrush(bg))

    def clear_rows(self):
        """Remove all data rows while keeping headers."""
        self.setRowCount(0)

    def get_selected_row_data(self, role=Qt.ItemDataRole.UserRole):
        """Return data stored on the first item of the currently selected row."""
        rows = self.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.item(rows[0].row(), 0)
        return item.data(role) if item else None

    def get_selected_row_index(self) -> int:
        rows = self.selectionModel().selectedRows()
        return rows[0].row() if rows else -1


# ─────────────────────────────────────────────────────────────────────────────
#  ACTION BAR
# ─────────────────────────────────────────────────────────────────────────────

class ActionBar(QFrame):
    """
    The navy tiered action bar that appears at the bottom of table panels.
    Shows a context label (selected item name) and up to 3 tiers of buttons.

    Usage:
        bar = ActionBar(
            tier1=[
                ("⟳ Process Refill",  "btn-primary",  self.process_refill),
                ("✏ Edit Order",       "btn-ghost-dark", self.edit_order),
                ("💳 Bill in ePACES",  "btn-ghost-dark", self.open_epaces),
            ],
            tier2=[
                ("Update Status",     "btn-ghost-dark", self.update_status),
                ("Refill Request",    "btn-ghost-dark", self.refill_request),
                ("Export",            "btn-ghost-dark", self.export),
            ],
            more_menu=[
                ("Generate 1500 JSON", self.gen_1500),
                ("Print HCFA-1500",   self.print_hcfa),
                ("─",                 None),             # separator
                ("Reverse Refill",    self.reverse_refill),
            ],
            danger_btn=("🗑 Delete", self.delete_order),
            entity_name="order"
        )

    Signals:
        none (you pass callbacks directly)

    To update the context label after row selection:
        bar.set_selection("ORD-316 — DANNER, WARREN")
        bar.clear_selection()
    """

    def __init__(self,
                 tier1: List[Tuple] = None,
                 tier2: List[Tuple] = None,
                 more_menu: List[Tuple] = None,
                 danger_btn: Tuple = None,
                 entity_name: str = "item",
                 parent=None):
        super().__init__(parent)
        self.setProperty("role", "action-bar")
        self.setFixedHeight(48)
        self._entity_name = entity_name
        self._tier1_btns  = []
        self._tier2_btns  = []
        self._more_btn    = None
        self._danger_btn  = None
        self._more_actions = more_menu or []
        self._build(tier1 or [], tier2 or [], more_menu or [], danger_btn)

    def _build(self, tier1, tier2, more_menu, danger_btn):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)

        # Context label
        self._context_label = QLabel(f"← Click a row to see actions")
        self._context_label.setStyleSheet(
            f"color: {COLORS.SLATE_400}; font-size: 12px; font-style: italic; background: transparent;"
        )
        self._context_label.setMinimumWidth(220)
        layout.addWidget(self._context_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"background: rgba(255,255,255,0.15); max-width: 1px; border: none;")
        sep.setFixedWidth(1)
        layout.addWidget(sep)

        # Tier 1 buttons
        self._tier1_container = QWidget()
        self._tier1_container.setStyleSheet("background: transparent;")
        t1_layout = QHBoxLayout(self._tier1_container)
        t1_layout.setContentsMargins(0, 0, 0, 0)
        t1_layout.setSpacing(5)
        for label, cls, callback in tier1:
            btn = QPushButton(label)
            btn.setProperty("class", cls or "btn-ghost-dark")
            if callback:
                btn.clicked.connect(callback)
            t1_layout.addWidget(btn)
            self._tier1_btns.append(btn)
        layout.addWidget(self._tier1_container)

        # Tier 2 buttons
        if tier2:
            sep2 = QFrame()
            sep2.setFrameShape(QFrame.Shape.VLine)
            sep2.setStyleSheet(f"background: rgba(255,255,255,0.1); max-width: 1px; border: none;")
            sep2.setFixedWidth(1)
            layout.addWidget(sep2)

            self._tier2_container = QWidget()
            self._tier2_container.setStyleSheet("background: transparent;")
            t2_layout = QHBoxLayout(self._tier2_container)
            t2_layout.setContentsMargins(0, 0, 0, 0)
            t2_layout.setSpacing(5)
            for label, cls, callback in tier2:
                btn = QPushButton(label)
                btn.setProperty("class", cls or "btn-ghost-dark")
                if callback:
                    btn.clicked.connect(callback)
                t2_layout.addWidget(btn)
                self._tier2_btns.append(btn)
            layout.addWidget(self._tier2_container)

        # More dropdown
        if more_menu:
            self._more_btn = QPushButton("⋯ More")
            self._more_btn.setProperty("class", "btn-ghost-dark")
            self._more_btn.clicked.connect(self._show_more_menu)
            layout.addWidget(self._more_btn)

        layout.addStretch()

        # Danger button (right-aligned)
        if danger_btn:
            label, callback = danger_btn
            self._danger_btn = QPushButton(label)
            self._danger_btn.setProperty("class", "btn-danger")
            self._danger_btn.setProperty("class", "btn-red")
            self._danger_btn.setStyleSheet(
                f"background-color: transparent; color: {COLORS.RED_PALE}; "
                f"border: 1px solid rgba(225,29,72,0.4); border-radius: 5px; "
                f"padding: 4px 12px; font-size: 12px;"
            )
            self._danger_btn.setStyleSheet(
                self._danger_btn.styleSheet() +
                f" QPushButton:hover {{ background-color: {COLORS.RED}; color: white; }}"
            )
            if callback:
                self._danger_btn.clicked.connect(callback)
            layout.addWidget(self._danger_btn)

        self._set_buttons_enabled(False)

    def _show_more_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLORS.NAVY_LIGHT};
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px;
                padding: 4px;
                color: rgba(255,255,255,0.9);
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 7px 16px;
                border-radius: 4px;
                color: rgba(255,255,255,0.85);
            }}
            QMenu::item:selected {{
                background-color: rgba(13,148,136,0.3);
                color: white;
            }}
            QMenu::separator {{
                height: 1px;
                background: rgba(255,255,255,0.15);
                margin: 3px 8px;
            }}
        """)
        for label, callback in self._more_actions:
            if label == "─" or label == "---":
                menu.addSeparator()
            else:
                action = menu.addAction(label)
                if callback:
                    action.triggered.connect(callback)
        btn_pos = self._more_btn.mapToGlobal(
            self._more_btn.rect().bottomLeft()
        )
        menu.exec(btn_pos)

    def _set_buttons_enabled(self, enabled: bool):
        for btn in self._tier1_btns + self._tier2_btns:
            btn.setEnabled(enabled)
        if self._more_btn:
            self._more_btn.setEnabled(enabled)
        if self._danger_btn:
            self._danger_btn.setEnabled(enabled)

    def set_selection(self, label: str):
        """
        Call when a table row is selected.
        label — display text like "ORD-316 — DANNER, WARREN"
        """
        self._context_label.setText(f"Selected: {label}")
        self._context_label.setStyleSheet(
            f"color: white; font-size: 12px; font-weight: 600; background: transparent;"
        )
        self._set_buttons_enabled(True)

    def clear_selection(self):
        """Call when selection is cleared."""
        self._context_label.setText(f"← Click a row to see actions")
        self._context_label.setStyleSheet(
            f"color: {COLORS.SLATE_400}; font-size: 12px; font-style: italic; background: transparent;"
        )
        self._set_buttons_enabled(False)

    def update_context(self, label: str):
        """Alias for set_selection."""
        self.set_selection(label)


# ─────────────────────────────────────────────────────────────────────────────
#  TOP NAVIGATION BAR
# ─────────────────────────────────────────────────────────────────────────────

class TopBar(QFrame):
    """
    The dark navy navigation bar with tab buttons, logo, user chip, and bell.

    tab_changed(index, name) — emitted when a tab is clicked

    Usage:
        tabs = [
            ("Dashboard", "📊"),
            ("Patients",  "👥"),
            ("Orders",    "📋"),
            # ... etc
        ]
        bar = TopBar(app_name="DMELogic", version="v2.4.1", tabs=tabs, user="Melvin")
        bar.tab_changed.connect(self.switch_panel)
    """

    tab_changed = pyqtSignal(int, str)

    def __init__(self, app_name: str = "DMELogic", version: str = "",
                 tabs: List[Tuple] = None, user: str = "User",
                 logo_pixmap: QPixmap = None, parent=None):
        super().__init__(parent)
        self.setProperty("role", "topbar")
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._tabs = tabs or []
        self._tab_btns: List[QPushButton] = []
        self._active_idx = 0
        self._build(app_name, version, user, logo_pixmap)

    def _build(self, app_name, version, user, logo_pixmap):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(0)

        # Logo + app name
        logo_row = QHBoxLayout()
        logo_row.setSpacing(8)
        logo_row.setContentsMargins(0, 0, 12, 0)

        if logo_pixmap:
            logo_lbl = QLabel()
            logo_lbl.setPixmap(logo_pixmap.scaled(
                28, 28,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
            logo_row.addWidget(logo_lbl)

        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        name_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel(app_name)
        name_lbl.setStyleSheet(
            "color: white; font-size: 14px; font-weight: 700; "
            "letter-spacing: -0.3px; background: transparent;"
        )
        name_col.addWidget(name_lbl)
        if version:
            ver_lbl = QLabel(version)
            ver_lbl.setStyleSheet(
                f"color: {COLORS.SLATE_400}; font-size: 9px; background: transparent;"
            )
            name_col.addWidget(ver_lbl)
        logo_row.addLayout(name_col)
        layout.addLayout(logo_row)

        # Divider
        div = QFrame()
        div.setStyleSheet(f"background: rgba(255,255,255,0.15); border: none;")
        div.setFixedWidth(1)
        div.setFixedHeight(24)
        layout.addWidget(div)
        layout.addSpacing(4)

        # Tab buttons
        tabs_widget = QWidget()
        tabs_widget.setStyleSheet("background: transparent;")
        tabs_layout = QHBoxLayout(tabs_widget)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(1)

        for i, tab_info in enumerate(self._tabs):
            if isinstance(tab_info, tuple):
                name = tab_info[0]
                icon = tab_info[1] if len(tab_info) > 1 else ""
            else:
                name = str(tab_info)
                icon = ""

            display = f"{icon} {name}".strip() if icon else name
            btn = QPushButton(display)
            btn.setCheckable(False)
            self._style_tab(btn, active=(i == 0))
            idx = i
            btn.clicked.connect(lambda _, n=i, nm=name: self._on_tab_click(n, nm))
            tabs_layout.addWidget(btn)
            self._tab_btns.append(btn)

        layout.addWidget(tabs_widget)
        layout.addStretch()

        # Right side: bell + settings + user chip
        right_row = QHBoxLayout()
        right_row.setSpacing(6)

        bell_btn = QPushButton("🔔")
        bell_btn.setStyleSheet(
            "background: transparent; border: none; "
            "color: rgba(255,255,255,0.7); font-size: 14px; "
            "padding: 4px; border-radius: 4px;"
        )
        bell_btn.setFixedSize(30, 30)
        right_row.addWidget(bell_btn)

        settings_btn = QPushButton("⚙")
        settings_btn.setStyleSheet(bell_btn.styleSheet())
        settings_btn.setFixedSize(30, 30)
        right_row.addWidget(settings_btn)

        # User chip
        initials = "".join(w[0].upper() for w in user.split()[:2])
        user_chip = QLabel(f"  {initials}  {user}  ")
        user_chip.setStyleSheet(f"""
            QLabel {{
                background-color: {COLORS.TEAL};
                color: white;
                border-radius: 12px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
        """)
        user_chip.setFixedHeight(24)
        right_row.addWidget(user_chip)

        layout.addLayout(right_row)

    def _style_tab(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: white;
                    border: none;
                    border-bottom: 2px solid {COLORS.TEAL};
                    border-radius: 0;
                    padding: 0 10px;
                    font-size: 12px;
                    font-weight: 600;
                    min-height: 40px;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
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
                QPushButton:hover {{
                    color: rgba(255,255,255,0.9);
                    background-color: rgba(255,255,255,0.07);
                }}
            """)

    def _on_tab_click(self, idx: int, name: str):
        self._active_idx = idx
        for i, btn in enumerate(self._tab_btns):
            self._style_tab(btn, active=(i == idx))
        self.tab_changed.emit(idx, name)

    def set_active_tab(self, idx: int):
        """Programmatically set the active tab."""
        if 0 <= idx < len(self._tab_btns):
            self._on_tab_click(idx, self._tabs[idx][0] if isinstance(self._tabs[idx], tuple) else self._tabs[idx])


# ─────────────────────────────────────────────────────────────────────────────
#  PAGE HEADER
# ─────────────────────────────────────────────────────────────────────────────

class PageHeader(QWidget):
    """
    Page title + subtitle + optional right-side action button row.

    Usage:
        header = PageHeader(
            title="Patients",
            subtitle="847 active patients",
            actions=[
                ("＋ New Patient", "btn-primary", self.add_patient),
                ("Export",         "btn-ghost",   self.export_patients),
            ]
        )
    """

    def __init__(self, title: str, subtitle: str = "",
                 actions: List[Tuple] = None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Title + subtitle
        left = QVBoxLayout()
        left.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setProperty("class", "page-title")
        title_lbl.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {COLORS.SLATE_800}; "
            f"letter-spacing: -0.3px; background: transparent;"
        )
        left.addWidget(title_lbl)
        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setStyleSheet(
                f"font-size: 12px; color: {COLORS.TEXT_MUTED}; background: transparent;"
            )
            left.addWidget(sub_lbl)
        self._subtitle_label = sub_lbl if subtitle else None
        layout.addLayout(left)
        layout.addStretch()

        # Action buttons
        if actions:
            for label, cls, callback in actions:
                btn = QPushButton(label)
                btn.setProperty("class", cls)
                if callback:
                    btn.clicked.connect(callback)
                layout.addWidget(btn)

    def set_subtitle(self, text: str):
        if self._subtitle_label:
            self._subtitle_label.setText(text)


# ─────────────────────────────────────────────────────────────────────────────
#  SEGMENTED TABS (filter bar)
# ─────────────────────────────────────────────────────────────────────────────

class SegmentedTabs(QFrame):
    """
    Pill-style segmented tab bar for filtering tables.
    Emits tab_changed(index, label) when a tab is clicked.

    Usage:
        tabs = SegmentedTabs(["All 847", "Active 631", "Inactive 89", "On Hold 12"])
        tabs.tab_changed.connect(self.on_filter_change)
    """

    tab_changed = pyqtSignal(int, str)

    def __init__(self, labels: List[str], parent=None):
        super().__init__(parent)
        self.setProperty("role", "seg-tabs")
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._buttons: List[QPushButton] = []
        self._active_idx = 0
        self._build(labels)

    def _build(self, labels):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for i, label in enumerate(labels):
            btn = QPushButton(label)
            self._style_btn(btn, active=(i == 0))
            idx = i
            btn.clicked.connect(lambda _, n=i, lbl=label: self._on_click(n, lbl))
            layout.addWidget(btn)
            self._buttons.append(btn)

    def _style_btn(self, btn: QPushButton, active: bool):
        if active:
            btn.setProperty("role", "seg-tab-active")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLORS.WHITE};
                    color: {COLORS.TEXT_PRIMARY};
                    border: none;
                    border-radius: 4px;
                    padding: 3px 14px;
                    font-size: 12px;
                    font-weight: 600;
                    min-height: 22px;
                }}
            """)
        else:
            btn.setProperty("role", "seg-tab")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLORS.TEXT_SECONDARY};
                    border: none;
                    border-radius: 4px;
                    padding: 3px 14px;
                    font-size: 12px;
                    font-weight: 500;
                    min-height: 22px;
                }}
                QPushButton:hover {{
                    background-color: {COLORS.SLATE_200};
                    color: {COLORS.TEXT_PRIMARY};
                }}
            """)

    def _on_click(self, idx: int, label: str):
        self._active_idx = idx
        for i, btn in enumerate(self._buttons):
            self._style_btn(btn, active=(i == idx))
        self.tab_changed.emit(idx, label)

    def set_active(self, idx: int):
        if 0 <= idx < len(self._buttons):
            label = self._buttons[idx].text()
            self._on_click(idx, label)


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION BOX
# ─────────────────────────────────────────────────────────────────────────────

class SectionBox(QFrame):
    """
    Grouped form section with a title label.

    Usage:
        box = SectionBox("Basic Information")
        box.layout().addRow("Item #:", QLineEdit())
        # or add widgets manually:
        box.add_widget(my_form_layout)
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setProperty("role", "section-box")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        title_lbl = QLabel(title.upper())
        title_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; color: {COLORS.SLATE_500}; "
            f"letter-spacing: 0.5px; background: transparent;"
        )
        outer.addWidget(title_lbl)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        outer.addWidget(self._content)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def content_layout(self):
        return self._content_layout


# ─────────────────────────────────────────────────────────────────────────────
#  FORM ROW
# ─────────────────────────────────────────────────────────────────────────────

class FormRow(QWidget):
    """
    Label + input widget row for forms.

    Usage:
        row = FormRow("Patient *", QLineEdit())
        row = FormRow("Status", QComboBox(), hint="Required field")
    """

    def __init__(self, label: str, widget: QWidget, hint: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        lbl = QLabel(label.upper())
        lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; "
            f"letter-spacing: 0.3px; background: transparent;"
        )
        layout.addWidget(lbl)
        layout.addWidget(widget)

        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setStyleSheet(
                f"font-size: 10px; color: {COLORS.TEXT_MUTED}; background: transparent;"
            )
            layout.addWidget(hint_lbl)

        self.input = widget


# ─────────────────────────────────────────────────────────────────────────────
#  ALERT BOX
# ─────────────────────────────────────────────────────────────────────────────

class AlertBox(QFrame):
    """
    Info / warning / danger / success alert strip.

    Usage:
        alert = AlertBox("Rx on file expires 03/20/2026", kind="warn")
        alert = AlertBox("Claim submitted successfully!", kind="success")
        kinds: "info", "warn", "danger", "success"
    """

    ICONS = {"info": "ℹ️", "warn": "⚠️", "danger": "🚫", "success": "✅"}

    def __init__(self, text: str, kind: str = "info", parent=None):
        super().__init__(parent)
        role_map = {
            "info": "alert-info",
            "warn": "alert-warn",
            "danger": "alert-danger",
            "success": "alert-success"
        }
        self.setProperty("role", role_map.get(kind, "alert-info"))
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)
        icon = QLabel(self.ICONS.get(kind, "ℹ️"))
        icon.setStyleSheet("background: transparent; font-size: 13px;")
        icon.setFixedWidth(18)
        layout.addWidget(icon)
        text_lbl = QLabel(text)
        text_lbl.setStyleSheet(
            f"background: transparent; font-size: 12px; color: {COLORS.TEXT_PRIMARY};"
        )
        text_lbl.setWordWrap(False)
        layout.addWidget(text_lbl)
        layout.addStretch()
        self._label = text_lbl

    def set_text(self, text: str):
        self._label.setText(text)


# ─────────────────────────────────────────────────────────────────────────────
#  STAT CARD (Dashboard)
# ─────────────────────────────────────────────────────────────────────────────

class StatCard(QFrame):
    """
    Dashboard stat card with a number, label, and optional sub-text.

    Usage:
        card = StatCard(
            value="847",
            label="Total Patients",
            sublabel="Active: 631",
            color="teal"    # teal | warn | danger | purple
        )
    """

    COLOR_MAP = {
        "teal":   ("stat-card",        COLORS.TEAL),
        "warn":   ("stat-card-warn",   COLORS.AMBER),
        "danger": ("stat-card-danger", COLORS.RED),
        "purple": ("stat-card-purple", COLORS.PURPLE),
        "green":  ("stat-card",        COLORS.GREEN),
    }

    def __init__(self, value: str, label: str, sublabel: str = "",
                 color: str = "teal", icon: str = "", parent=None):
        super().__init__(parent)
        role, accent = self.COLOR_MAP.get(color, ("stat-card", COLORS.TEAL))
        self.setProperty("role", role)
        self.setMinimumWidth(160)
        self.setMinimumHeight(78)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(3)

        top_row = QHBoxLayout()
        val_lbl = QLabel(value)
        val_lbl.setStyleSheet(
            f"font-size: 26px; font-weight: 800; color: {COLORS.SLATE_800}; "
            f"background: transparent; letter-spacing: -1px;"
        )
        top_row.addWidget(val_lbl)
        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet(f"font-size: 20px; background: transparent; color: {accent};")
            top_row.addWidget(icon_lbl)
        top_row.addStretch()
        layout.addLayout(top_row)

        label_lbl = QLabel(label)
        label_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {COLORS.TEXT_SECONDARY}; "
            f"background: transparent;"
        )
        layout.addWidget(label_lbl)

        if sublabel:
            sub_lbl = QLabel(sublabel)
            sub_lbl.setStyleSheet(
                f"font-size: 11px; color: {COLORS.TEXT_MUTED}; background: transparent;"
            )
            layout.addWidget(sub_lbl)

        self._value_label = val_lbl

    def set_value(self, value: str):
        self._value_label.setText(value)


# ─────────────────────────────────────────────────────────────────────────────
#  WIZARD STEPS INDICATOR
# ─────────────────────────────────────────────────────────────────────────────

class WizardSteps(QWidget):
    """
    Horizontal step progress indicator for multi-step wizards.

    Usage:
        steps = WizardSteps(["Patient", "Items", "Rx/Docs", "Review"])
        steps.set_step(1)   # 0-indexed; marks 0 as done, 1 as active
    """

    def __init__(self, labels: List[str], parent=None):
        super().__init__(parent)
        self._labels = labels
        self._step = 0
        self.setFixedHeight(56)
        self.setStyleSheet("background: transparent;")
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 8)
        layout.setSpacing(0)
        self._step_widgets = []
        for i, label in enumerate(self._labels):
            step_w = QWidget()
            step_w.setStyleSheet("background: transparent;")
            sw_layout = QHBoxLayout(step_w)
            sw_layout.setContentsMargins(0, 0, 0, 0)
            sw_layout.setSpacing(6)

            # Circle
            circle = QLabel(str(i + 1) if i > 0 else "✓")
            circle.setFixedSize(26, 26)
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._style_circle(circle, "pending")
            sw_layout.addWidget(circle)

            # Label
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"font-size: 12px; color: {COLORS.TEXT_MUTED}; background: transparent;"
            )
            sw_layout.addWidget(lbl)

            # Connector line (except last)
            if i < len(self._labels) - 1:
                line = QFrame()
                line.setFixedHeight(1)
                line.setMinimumWidth(30)
                line.setStyleSheet(f"background-color: {COLORS.SLATE_200}; border: none;")
                sw_layout.addWidget(line)
                sw_layout.setStretchFactor(line, 1)
            else:
                sw_layout.addStretch()

            layout.addWidget(step_w)
            layout.setStretchFactor(step_w, 1)
            self._step_widgets.append((circle, lbl))

        self.set_step(0)

    def _style_circle(self, circle: QLabel, state: str):
        if state == "done":
            circle.setText("✓")
            circle.setStyleSheet(f"""
                QLabel {{
                    background-color: {COLORS.TEAL};
                    color: white;
                    border-radius: 13px;
                    font-size: 13px;
                    font-weight: 700;
                }}
            """)
        elif state == "active":
            circle.setStyleSheet(f"""
                QLabel {{
                    background-color: {COLORS.TEAL};
                    color: white;
                    border-radius: 13px;
                    font-size: 12px;
                    font-weight: 700;
                    border: 2px solid {COLORS.TEAL_DARK};
                }}
            """)
        else:
            circle.setStyleSheet(f"""
                QLabel {{
                    background-color: {COLORS.SLATE_200};
                    color: {COLORS.TEXT_MUTED};
                    border-radius: 13px;
                    font-size: 12px;
                    font-weight: 600;
                }}
            """)

    def set_step(self, active_step: int):
        self._step = active_step
        for i, (circle, lbl) in enumerate(self._step_widgets):
            if i < active_step:
                self._style_circle(circle, "done")
                circle.setText("✓")
                lbl.setStyleSheet(
                    f"font-size: 12px; color: {COLORS.TEAL}; font-weight: 600; background: transparent;"
                )
            elif i == active_step:
                self._style_circle(circle, "active")
                circle.setText(str(i + 1))
                lbl.setStyleSheet(
                    f"font-size: 12px; color: {COLORS.TEXT_PRIMARY}; font-weight: 600; background: transparent;"
                )
            else:
                self._style_circle(circle, "pending")
                circle.setText(str(i + 1))
                lbl.setStyleSheet(
                    f"font-size: 12px; color: {COLORS.TEXT_MUTED}; background: transparent;"
                )


# ─────────────────────────────────────────────────────────────────────────────
#  EPACES COPY FIELD ROW
# ─────────────────────────────────────────────────────────────────────────────

class EpacesFieldRow(QFrame):
    """
    A single row in the ePACES helper: Label | Value | [Copy button]

    Usage:
        row = EpacesFieldRow("Patient Name", "DANNER, WARREN", copyable=True)
        row = EpacesFieldRow("Insurance ID",  "DN44821K",       copyable=True)
        row = EpacesFieldRow("DOS",           "02/18/2026",     copyable=True)
    """

    def __init__(self, label: str, value: str, copyable: bool = True, parent=None):
        super().__init__(parent)
        self.setProperty("role", "epaces-field")
        self._value = value
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        lbl = QLabel(label)
        lbl.setFixedWidth(130)
        lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {COLORS.TEXT_SECONDARY}; background: transparent;"
        )
        layout.addWidget(lbl)

        val = QLabel(value)
        val.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {COLORS.TEXT_PRIMARY}; "
            f"font-family: DM Mono, Consolas, monospace; background: transparent;"
        )
        layout.addWidget(val)
        layout.addStretch()
        self._val_label = val

        if copyable:
            self._copy_btn = QPushButton("Copy")
            self._copy_btn.setProperty("role", "copy-btn")
            self._copy_btn.setFixedSize(52, 22)
            self._copy_btn.clicked.connect(self._copy)
            layout.addWidget(self._copy_btn)

    def _copy(self):
        QApplication.clipboard().setText(self._value)
        self._copy_btn.setText("✓ Done!")
        self._copy_btn.setStyleSheet(
            f"background-color: {COLORS.TEAL_PALE}; color: {COLORS.TEAL}; "
            f"border: 1px solid {COLORS.TEAL}; border-radius: 4px; "
            f"font-size: 11px; font-weight: 600;"
        )
        QTimer.singleShot(1500, self._reset_btn)

    def _reset_btn(self):
        self._copy_btn.setText("Copy")
        self._copy_btn.setStyleSheet("")
        self._copy_btn.setProperty("role", "copy-btn")
        # Force re-polish
        self._copy_btn.style().unpolish(self._copy_btn)
        self._copy_btn.style().polish(self._copy_btn)

    def set_value(self, value: str):
        self._value = value
        self._val_label.setText(value)


# ─────────────────────────────────────────────────────────────────────────────
#  REFILL SUMMARY BOX
# ─────────────────────────────────────────────────────────────────────────────

class RefillSummaryBox(QFrame):
    """
    Navy summary card shown at the top of the Process Refill dialog.

    Usage:
        box = RefillSummaryBox(
            patient="DANNER, WARREN",
            order_id="ORD-316",
            new_order_id="ORD-316-R1",
            hcpcs="T4522-FRBRFMED",
            refills_remaining=4,
            total_refills=5
        )
    """

    def __init__(self, patient: str = "", order_id: str = "",
                 new_order_id: str = "", hcpcs: str = "",
                 refills_remaining: int = 0, total_refills: int = 0,
                 parent=None):
        super().__init__(parent)
        self.setProperty("role", "refill-summary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        rows = [
            ("Patient",          patient),
            ("Order # (new)",    f"{order_id} → {new_order_id}" if new_order_id else order_id),
            ("HCPCS",            hcpcs),
            ("Refills Remaining", f"{refills_remaining} of {total_refills}"),
        ]

        for label, value in rows:
            row_w = QHBoxLayout()
            l = QLabel(label)
            l.setFixedWidth(130)
            l.setStyleSheet(
                f"color: rgba(255,255,255,0.6); font-size: 11px; background: transparent;"
            )
            v = QLabel(value)
            v.setStyleSheet(
                f"color: white; font-size: 12px; font-weight: 600; background: transparent;"
            )
            row_w.addWidget(l)
            row_w.addWidget(v)
            row_w.addStretch()
            layout.addLayout(row_w)


# ─────────────────────────────────────────────────────────────────────────────
#  STATUS RADIO GROUP
# ─────────────────────────────────────────────────────────────────────────────

class StatusRadioGroup(QWidget):
    """
    Clickable status selection cards (used in Update Status dialog).
    Emits status_changed(status_key) when selection changes.

    Usage:
        radio = StatusRadioGroup(
            statuses=[
                ("pending",  "🟡 Pending",  "Created, not yet processed"),
                ("unbilled", "🟠 Unbilled", "Ready to bill in ePACES"),
                ("billed",   "🟣 Billed",   "Claim submitted to payer"),
                ("shipped",  "🟢 Shipped",  "Delivery confirmed"),
                ("rx_hold",  "🔴 RX Hold",  "Waiting for prescription"),
            ]
        )
        radio.status_changed.connect(self.on_status_change)
        current = radio.get_selected()
    """

    status_changed = pyqtSignal(str)

    def __init__(self, statuses: List[Tuple], current: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._statuses = statuses
        self._selected = current or (statuses[0][0] if statuses else "")
        self._frames: Dict[str, QFrame] = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for key, label, desc in self._statuses:
            frame = QFrame()
            frame.setProperty("role",
                "status-radio-item-selected" if key == self._selected else "status-radio-item"
            )
            frame.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            frame.setFixedHeight(46)
            fl = QHBoxLayout(frame)
            fl.setContentsMargins(12, 0, 12, 0)
            fl.setSpacing(10)

            # Color dot
            bg, text_c, _ = get_status_colors(key)
            dot = QLabel("●")
            dot.setFixedWidth(14)
            dot.setStyleSheet(f"color: {text_c}; background: transparent; font-size: 14px;")
            fl.addWidget(dot)

            # Label + desc
            col = QVBoxLayout()
            col.setSpacing(0)
            lbl_w = QLabel(label)
            lbl_w.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {COLORS.TEXT_PRIMARY}; background: transparent;"
            )
            col.addWidget(lbl_w)
            desc_w = QLabel(desc)
            desc_w.setStyleSheet(
                f"font-size: 10px; color: {COLORS.TEXT_MUTED}; background: transparent;"
            )
            col.addWidget(desc_w)
            fl.addLayout(col)
            fl.addStretch()

            frame.mousePressEvent = lambda e, k=key: self._select(k)
            layout.addWidget(frame)
            self._frames[key] = frame

    def _select(self, key: str):
        prev = self._selected
        self._selected = key
        if prev in self._frames:
            self._frames[prev].setProperty("role", "status-radio-item")
            self._frames[prev].style().unpolish(self._frames[prev])
            self._frames[prev].style().polish(self._frames[prev])
        if key in self._frames:
            self._frames[key].setProperty("role", "status-radio-item-selected")
            self._frames[key].style().unpolish(self._frames[key])
            self._frames[key].style().polish(self._frames[key])
        self.status_changed.emit(key)

    def get_selected(self) -> str:
        return self._selected

    def set_selected(self, key: str):
        self._select(key)


# ─────────────────────────────────────────────────────────────────────────────
#  LOW STOCK BANNER
# ─────────────────────────────────────────────────────────────────────────────

class BannerBar(QFrame):
    """
    Full-width warning/danger/info banner (e.g. "⚠ 80 items below reorder level").

    Usage:
        banner = BannerBar("⚠ 80 items are below reorder level", kind="warn",
                           action_label="View Low Stock Report",
                           on_action=self.view_low_stock)
    """

    def __init__(self, text: str, kind: str = "warn",
                 action_label: str = "", on_action: Callable = None,
                 parent=None):
        super().__init__(parent)
        role = {"warn": "banner-warn", "danger": "banner-danger"}.get(kind, "banner-warn")
        self.setProperty("role", role)
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        color = COLORS.AMBER if kind == "warn" else COLORS.RED
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 500; color: {color}; background: transparent;"
        )
        layout.addWidget(lbl)
        layout.addStretch()

        if action_label:
            btn = QPushButton(action_label)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {color};
                    border: 1px solid {color};
                    border-radius: 4px;
                    padding: 2px 10px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: rgba(0,0,0,0.05);
                }}
            """)
            if on_action:
                btn.clicked.connect(on_action)
            layout.addWidget(btn)


# ─────────────────────────────────────────────────────────────────────────────
#  SEARCH / TOOLBAR ROW
# ─────────────────────────────────────────────────────────────────────────────

class ToolbarRow(QWidget):
    """
    Standard table toolbar: search box + filter dropdowns + action buttons.

    Usage:
        toolbar = ToolbarRow(
            placeholder="Search by name, NPI, practice...",
            filters=[("All Specialties", ["All Specialties", "General Practice", "Pediatrics"])],
            actions=[
                ("＋ Add Prescriber", "btn-primary", self.add_prescriber),
                ("Edit",             "btn-ghost",   self.edit_prescriber),
            ]
        )
        toolbar.search_changed.connect(self.on_search)
    """

    search_changed  = pyqtSignal(str)
    filter_changed  = pyqtSignal(int, str)   # (filter_index, selected_value)

    def __init__(self, placeholder: str = "Search...",
                 filters: List[Tuple] = None,
                 actions: List[Tuple] = None,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText(placeholder)
        self._search.setMinimumWidth(220)
        self._search.setMaximumWidth(340)
        self._search.textChanged.connect(self.search_changed)
        # Add magnifying glass icon via placeholder styling
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS.WHITE};
                border: 1px solid {COLORS.SLATE_300};
                border-radius: 5px;
                padding: 5px 9px 5px 28px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {COLORS.TEAL};
            }}
        """)
        layout.addWidget(self._search)

        # Filter dropdowns
        if filters:
            from PyQt6.QtWidgets import QComboBox
            for i, (default_text, options) in enumerate(filters):
                cb = QComboBox()
                cb.addItems(options if options else [default_text])
                cb.setMinimumWidth(140)
                idx = i
                cb.currentTextChanged.connect(lambda v, n=idx: self.filter_changed.emit(n, v))
                layout.addWidget(cb)

        layout.addStretch()

        # Action buttons
        if actions:
            for label, cls, callback in actions:
                btn = QPushButton(label)
                btn.setProperty("class", cls or "btn-ghost")
                if callback:
                    btn.clicked.connect(callback)
                layout.addWidget(btn)

    def get_search_text(self) -> str:
        return self._search.text()

    def clear_search(self):
        self._search.clear()
