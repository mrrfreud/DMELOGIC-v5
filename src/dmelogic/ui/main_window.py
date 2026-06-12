"""
Main application window for DME Logic.

Phase 2: we define a real MainWindow class that currently just
inherits from the legacy PDFViewer, so behavior is identical.
Later we can move logic out of app_legacy.PDFViewer into here,
step by step, without breaking the app.
"""

from typing import Type, Iterable
from decimal import Decimal
import os
import re
import sys
import subprocess
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QComboBox, QLabel, QPushButton, QCheckBox,
    QFrame, QSizePolicy, QDialog, QMessageBox, QTableWidget, QToolBar, QApplication, QFileDialog, QScrollArea, QInputDialog,
    QSlider, QWidgetAction, QToolButton
)
from PyQt6.QtGui import QIcon, QAction, QShortcut, QKeySequence
from PyQt6.QtCore import QSize, Qt, QObject, QEvent, QTimer
from pathlib import Path

# Legacy implementation from the monolithic app
from dmelogic.legacy import PDFViewer

# Order wizard
from dmelogic.ui.order_wizard import OrderWizard, OrderWizardResult

# EPACES helper dialog
from dmelogic.ui.epaces_helper import EpacesHelperDialog

# Theme manager
from dmelogic.ui.theme_manager import ThemeManager

# DB layer for order creation
from dmelogic.db.inventory import fetch_latest_item_by_hcpcs
from dmelogic.db.patients import fetch_patient_insurance
from dmelogic.db.orders import create_order
from dmelogic.db import fetch_order_with_items
from dmelogic.db.models import OrderInput, OrderItemInput, BillingType, OrderStatus
from dmelogic.db.base import resolve_db_path
from dmelogic.security.auth import is_current_user_agent
from dmelogic.db import sticky_notes as notes_db
from dmelogic.ui.notes_board import NotesBoardDialog
from dmelogic.ui.components.sticky_notes_panel import StickyNotesPanel
from dmelogic.ui.draggable_button_bar import DraggableButtonBar
from dmelogic.ui.rx_import_wizard import RxImportWizard


# ── Shared modern button styling (light theme) ──────────────────────────────
# One place defining the primary / ghost / danger button looks used across the
# action bars. Original neutral palette + one blue accent; generic conventions
# only (no third-party assets or color sets reproduced).
_BTN_PRIMARY = (
    "QPushButton { background:#2563eb; color:#ffffff; font-weight:600;"
    " padding:7px 14px; border:none; border-radius:8px; }"
    "QPushButton:hover { background:#1d4ed8; }"
)
_BTN_GHOST = (
    "QPushButton { background:#ffffff; color:#0f172a; font-weight:600;"
    " padding:7px 14px; border:1px solid #e2e8f0; border-radius:8px; }"
    "QPushButton:hover { background:#f1f5f9; border-color:#cbd5e1; }"
)
_BTN_DANGER = (
    "QPushButton { background:#ffffff; color:#dc2626; font-weight:600;"
    " padding:7px 14px; border:1px solid #fecaca; border-radius:8px; }"
    "QPushButton:hover { background:#fef2f2; border-color:#dc2626; }"
)


def _style_action_buttons(primary=(), ghost=(), danger=()):
    """Apply the shared primary/ghost/danger button styles to the given buttons."""
    from PyQt6.QtCore import Qt as _Qt
    for group, css in ((primary, _BTN_PRIMARY), (ghost, _BTN_GHOST), (danger, _BTN_DANGER)):
        for btn in group:
            if btn is None:
                continue
            try:
                btn.setStyleSheet(css)
                btn.setCursor(_Qt.CursorShape.PointingHandCursor)
            except Exception:
                pass


def build_orders_tab(self) -> QWidget:
    """
    Build the Orders tab UI: top filter bar, orders table, bottom action bar.
    The action bar is a single visible row of buttons plus an expandable
    second row toggled by an arrow button – this avoids two-row layout
    collapse issues across theme switches.
    """
    # ── Modern light-theme styling (same original palette as the other tabs) ──
    ACCENT     = "#2563eb"
    PAGE_BG    = "#f3f5f9"
    SURFACE    = "#ffffff"
    BORDER     = "#e2e8f0"
    TEXT       = "#0f172a"
    TEXT_MUTED = "#64748b"

    tab = QWidget()
    tab.setObjectName("ordTab")
    tab.setStyleSheet(
        f"#ordTab {{ background:{PAGE_BG}; }}"
        f"#ordTab QTableWidget {{ background:{SURFACE}; border:1px solid {BORDER};"
        f" border-radius:10px; gridline-color:{BORDER};"
        f" selection-background-color:#e8f0fe; selection-color:{TEXT}; }}"
        f"#ordTab QTableWidget::item {{ padding:6px 8px; }}"
        f"#ordTab QHeaderView::section {{ background:#f8fafc; color:{TEXT_MUTED};"
        f" font-weight:600; padding:8px; border:none;"
        f" border-bottom:1px solid {BORDER}; }}"
        f"#ordTab QLineEdit#ordSearch {{ background:{SURFACE}; color:{TEXT};"
        f" border:1px solid {BORDER}; border-radius:18px; padding:8px 14px; }}"
        f"#ordTab QLineEdit#ordSearch:focus {{ border-color:{ACCENT}; }}"
        f"#ordTab QComboBox {{ background:{SURFACE}; color:{TEXT};"
        f" border:1px solid {BORDER}; border-radius:8px; padding:6px 10px; }}"
        f"#ordTab QComboBox:hover {{ border-color:#cbd5e1; }}"
        f"#ordTab QCheckBox {{ color:{TEXT_MUTED}; }}"
        f"#ordTab QFrame#OrdersSummaryFrame {{ background:{SURFACE};"
        f" border:1px solid {BORDER}; border-radius:10px; }}"
    )
    main_layout = QVBoxLayout(tab)
    main_layout.setContentsMargins(20, 18, 20, 18)
    main_layout.setSpacing(12)

    # --- Header ---
    ord_header = QLabel("Orders & Tracking")
    ord_header.setStyleSheet(f"font-size:22px; font-weight:700; color:{TEXT};")
    ord_subtitle = QLabel("Track, refill, bill, and deliver patient orders.")
    ord_subtitle.setStyleSheet(f"font-size:12px; color:{TEXT_MUTED};")
    main_layout.addWidget(ord_header)
    main_layout.addWidget(ord_subtitle)

    # --- Top filter bar ---
    top_bar = QHBoxLayout()
    top_bar.setSpacing(8)

    self.orders_search_edit = QLineEdit()
    self.orders_search_edit.setObjectName("ordSearch")
    self.orders_search_edit.setPlaceholderText("🔍  Search orders (patient, order #, status)…")
    self.orders_search_edit.setMinimumWidth(260)

    self.orders_status_combo = QComboBox()
    self.orders_status_combo.addItems(["All statuses", "Unbilled", "On Hold", "Open", "Pending", "Pending Approval", "Shipped", "Delivered", "Cancelled", "RX on File"])

    self.orders_date_combo = QComboBox()
    self.orders_date_combo.addItems(["All dates", "This week", "This month", "This year"])

    _olbl = lambda t: (lambda l: (l.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:600;"), l)[1])(QLabel(t))
    top_bar.addWidget(self.orders_search_edit, 2)
    top_bar.addSpacing(8)
    top_bar.addWidget(_olbl("Status:"))
    top_bar.addWidget(self.orders_status_combo, 1)
    top_bar.addSpacing(8)
    top_bar.addWidget(_olbl("Date:"))
    top_bar.addWidget(self.orders_date_combo, 1)

    self.orders_hide_zero_refills_checkbox = QCheckBox("Hide 0-refill orders")
    self.orders_hide_zero_refills_checkbox.setToolTip(
        "Hide orders where all line items have 0 refills remaining"
    )
    self.orders_show_cancelled_checkbox = QCheckBox("Show cancelled orders")
    self.orders_show_cancelled_checkbox.setToolTip(
        "Include orders with status Cancelled in the table"
    )
    top_bar.addSpacing(12)
    top_bar.addWidget(self.orders_hide_zero_refills_checkbox)
    top_bar.addSpacing(8)
    top_bar.addWidget(self.orders_show_cancelled_checkbox)

    top_bar.addStretch(1)
    main_layout.addLayout(top_bar)

    # NOTE: The legacy "Agent Orders — Pending Approval" panel was removed.
    # Nova now creates orders directly as active "Pending" orders via
    # create_order_db(), so the old OCR/file-drop approval queue is unused.

    # --- Orders table ---
    self.orders_table.setMinimumHeight(100)   # allow table to shrink so bottom bar fits
    main_layout.addWidget(self.orders_table, 10)

    # --- Bottom action bar ---
    bottom_frame = QFrame()
    bottom_frame.setObjectName("OrdersSummaryFrame")
    bottom_frame.setMinimumHeight(54)
    bottom_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    bottom_vbox = QVBoxLayout(bottom_frame)
    bottom_vbox.setContentsMargins(10, 6, 10, 6)
    bottom_vbox.setSpacing(4)

    # -- Top section: summary + primary row of buttons --
    top_section = QHBoxLayout()
    top_section.setSpacing(6)

    # Summary labels (left)
    self.orders_summary_label = QLabel("No order selected")
    self.orders_summary_label.setStyleSheet("font-weight:600; color:#0f172a;")
    self.orders_sub_label = QLabel("Click any row to select an order and use the action buttons →")
    self.orders_sub_label.setStyleSheet("color:#64748b; font-size:9pt;")

    summary_layout = QVBoxLayout()
    summary_layout.setContentsMargins(0, 0, 0, 0)
    summary_layout.setSpacing(2)
    summary_layout.addWidget(self.orders_summary_label)
    summary_layout.addWidget(self.orders_sub_label)

    top_section.addLayout(summary_layout, 3)

    # --- Light "ghost" action buttons (neutral surface, accent on hover) ------
    # Replaces the former dark action bar + rainbow text. Each button stays
    # neutral; its per-action accent now only tints the hover border, giving
    # subtle scannability without a saturated rainbow of text colors.
    _ACTION_BTN_STYLE = """
        QPushButton {{
            font-size: {fsize}pt; font-weight: 600;
            padding: 6px 12px; min-height: 26px;
            border-radius: 6px;
            border: 1px solid {border};
            background-color: {bg};
            color: {fg};
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
            border-color: {hover_border};
            color: {hover_fg};
        }}
        QPushButton:pressed {{
            background-color: {press_bg};
        }}
    """

    def _make_action_btn(label: str, tooltip: str,
                         accent: str | None = None,
                         kind: str = "ghost") -> QPushButton:
        btn = QPushButton(label)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if kind == "primary":
            css = _ACTION_BTN_STYLE.format(
                fsize=9, fg="#ffffff", bg=ACCENT, border=ACCENT,
                hover_bg="#1d4ed8", hover_border="#1d4ed8",
                hover_fg="#ffffff", press_bg="#1e40af")
        elif kind == "danger":
            css = _ACTION_BTN_STYLE.format(
                fsize=9, fg="#dc2626", bg=SURFACE, border="#fecaca",
                hover_bg="#fef2f2", hover_border="#dc2626",
                hover_fg="#dc2626", press_bg="#fee2e2")
        else:  # ghost — neutral; accent only colors the hover border
            css = _ACTION_BTN_STYLE.format(
                fsize=9, fg=TEXT, bg=SURFACE, border=BORDER,
                hover_bg="#f1f5f9", hover_border=accent or "#cbd5e1",
                hover_fg=TEXT, press_bg="#e2e8f0")
        btn.setStyleSheet(css)
        return btn

    # Primary row buttons — each with a distinct accent colour for scannability
    row1_defs = [
        ("edit_order",      "Edit Order",       "Edit the selected order",                                        "#3b82f6"),  # blue
        ("update_status",   "Update Status",    "Update order status",                                            "#6366f1"),  # indigo
        ("delivery_report", "Delivery Report",  "View delivery report",                                           "#8b5cf6"),  # violet
        ("clear_delivery",  "Clear Delivery",   "Set delivery date to blank for the selected order",              "#94a3b8"),  # slate
        ("process_refill",  "Process Refill",   "Create a new order as a refill of the selected one",             "#22c55e"),  # green
        ("reverse_refill",  "Reverse Refill",   "Undo a refill: restore refills to parent and delete the refill", "#14b8a6"),  # teal
        ("refill_request",  "Refill Request",   "Generate a fax to request new prescriptions from the provider",  "#0ea5e9"),  # sky
        ("batch_delivered", "Batch Delivered",   "Mark multiple selected orders as delivered",                     "#f59e0b"),  # amber
        ("batch_billed",    "Batch Billed",     "Mark multiple selected orders as billed/paid",                   "#f97316"),  # orange
    ]

    created = {}
    for key, text, tip, accent in row1_defs:
        kind = "primary" if key == "edit_order" else "ghost"
        btn = _make_action_btn(text, tip, accent=accent, kind=kind)
        created[key] = btn
        top_section.addWidget(btn)

    # Expand/collapse arrow button
    self._orders_more_btn = QPushButton("More ▼")
    self._orders_more_btn.setToolTip("Show / hide additional action buttons")
    self._orders_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    self._orders_more_btn.setStyleSheet(_ACTION_BTN_STYLE.format(
        fsize=9, fg=TEXT, bg="#f1f5f9", border=BORDER,
        hover_bg="#e2e8f0", hover_border="#cbd5e1",
        hover_fg=TEXT, press_bg="#e2e8f0",
    ))
    self._orders_more_btn.setFixedWidth(72)
    top_section.addWidget(self._orders_more_btn)

    bottom_vbox.addLayout(top_section)

    # -- Second row: extra buttons (hidden by default) --
    self._orders_row2 = QWidget()
    row2_layout = QHBoxLayout(self._orders_row2)
    row2_layout.setContentsMargins(0, 2, 0, 0)
    row2_layout.setSpacing(6)
    row2_layout.addStretch(1)

    row2_defs = [
        ("export_portal",        "Export to Portal",     "Export order to state portal (CSV/JSON)",                       "#a78bfa"),  # violet
        ("epaces",               "Bill in ePACES",       "Open copy-friendly helper for manual ePACES portal entry",      "#6366f1"),  # indigo
        ("print_delivery_ticket","Print Delivery Ticket","Generate delivery ticket PDF for the selected order",           "#38bdf8"),  # sky
        ("generate_1500",        "Generate 1500 JSON",   "Generate HCFA-1500 claim data (JSON preview)",                  "#22d3ee"),  # cyan
        ("print_1500",           "Print HCFA-1500",      "Generate and print CMS-1500 claim form (PDF)",                  "#2dd4bf"),  # teal
        ("delete_order",         "Delete Order",         "Delete the selected order",                                     "#ef4444"),  # red
        ("link_patient",         "Link to Patient",      "Link order to a patient",                                       "#a3e635"),  # lime
        ("import_rx",            "Import Rx to Order",   "Upload Rx PDF(s) and auto-create an order",                     "#fb923c"),  # orange
        ("batch_print_tickets",  "Batch Print Tickets",  "Print one combined delivery-ticket PDF for all checked orders", "#38bdf8"),  # sky
    ]

    for key, text, tip, accent in row2_defs:
        kind = "danger" if key == "delete_order" else "ghost"
        btn = _make_action_btn(text, tip, accent=accent, kind=kind)
        created[key] = btn
        row2_layout.addWidget(btn)

    row2_layout.addStretch(1)
    self._orders_row2.setVisible(False)
    bottom_vbox.addWidget(self._orders_row2)

    # Toggle handler
    def _toggle_more():
        showing = not self._orders_row2.isVisible()
        self._orders_row2.setVisible(showing)
        self._orders_more_btn.setText("Less ▲" if showing else "More ▼")
    self._orders_more_btn.clicked.connect(_toggle_more)

    main_layout.addWidget(bottom_frame, 0)

    # Expose button refs for wiring
    self.order_button_bar = None  # no longer a standalone widget
    self._orders_bottom_frame = bottom_frame  # keep a reference
    self.btn_edit_order = created.get("edit_order")
    self.btn_update_status = created.get("update_status")
    self.btn_delivery_report = created.get("delivery_report")
    self.btn_clear_delivery = created.get("clear_delivery")
    self.btn_process_refill = created.get("process_refill")
    self.btn_reverse_refill = created.get("reverse_refill")
    self.btn_refill_request = created.get("refill_request")
    self.btn_batch_delivered = created.get("batch_delivered")
    self.btn_batch_billed = created.get("batch_billed")
    self.btn_export_portal = created.get("export_portal")
    self.btn_epaces = created.get("epaces")
    self.btn_print_delivery_ticket = created.get("print_delivery_ticket")
    self.btn_generate_1500 = created.get("generate_1500")
    self.btn_print_1500 = created.get("print_1500")
    self.btn_delete_order = created.get("delete_order")
    self.btn_link_patient = created.get("link_patient")
    self.btn_import_rx = created.get("import_rx")
    self.btn_batch_print_tickets = created.get("batch_print_tickets")

    try:
        self.btn_edit_order.setProperty("primary", True)
        self.btn_edit_order.style().unpolish(self.btn_edit_order)
        self.btn_edit_order.style().polish(self.btn_edit_order)
    except Exception:
        pass

    # Wire filter signals
    self.orders_search_edit.textChanged.connect(self.apply_table_filters)
    self.orders_status_combo.currentIndexChanged.connect(self.apply_table_filters)
    self.orders_date_combo.currentIndexChanged.connect(self.apply_table_filters)
    self.orders_hide_zero_refills_checkbox.toggled.connect(self.apply_table_filters)
    self.orders_show_cancelled_checkbox.toggled.connect(self.apply_table_filters)

    return tab


def build_patients_tab(self) -> QWidget:
    """
    Build the Patients tab UI: top filter bar, patients table, bottom summary/action panel.
    Assumes self.patients_table already exists and is configured.
    """
    # ── Modern light-theme styling (same original palette as Inventory) ──────
    # Generic design conventions only (light page, white card table, pill
    # search, muted labels). No third-party assets or color sets reproduced.
    ACCENT     = "#2563eb"
    PAGE_BG    = "#f3f5f9"
    SURFACE    = "#ffffff"
    BORDER     = "#e2e8f0"
    TEXT       = "#0f172a"
    TEXT_MUTED = "#64748b"

    tab = QWidget()
    tab.setObjectName("patTab")
    tab.setStyleSheet(
        f"#patTab {{ background:{PAGE_BG}; }}"
        f"#patTab QTableWidget {{ background:{SURFACE}; border:1px solid {BORDER};"
        f" border-radius:10px; gridline-color:{BORDER};"
        f" selection-background-color:#e8f0fe; selection-color:{TEXT}; }}"
        f"#patTab QTableWidget::item {{ padding:6px 8px; }}"
        f"#patTab QHeaderView::section {{ background:#f8fafc; color:{TEXT_MUTED};"
        f" font-weight:600; padding:8px; border:none;"
        f" border-bottom:1px solid {BORDER}; }}"
        f"#patTab QLineEdit#patSearch {{ background:{SURFACE}; color:{TEXT};"
        f" border:1px solid {BORDER}; border-radius:18px; padding:8px 14px; }}"
        f"#patTab QLineEdit#patSearch:focus {{ border-color:{ACCENT}; }}"
        f"#patTab QComboBox {{ background:{SURFACE}; color:{TEXT};"
        f" border:1px solid {BORDER}; border-radius:8px; padding:6px 10px; }}"
        f"#patTab QComboBox:hover {{ border-color:#cbd5e1; }}"
        f"#patTab QFrame#PatientsSummaryFrame {{ background:{SURFACE};"
        f" border:1px solid {BORDER}; border-radius:10px; }}"
    )
    main_layout = QVBoxLayout(tab)
    main_layout.setContentsMargins(20, 18, 20, 18)
    main_layout.setSpacing(12)

    # --- Header ---
    pat_header = QLabel("Patients")
    pat_header.setStyleSheet(f"font-size:22px; font-weight:700; color:{TEXT};")
    pat_subtitle = QLabel("Search, review, and manage patient records.")
    pat_subtitle.setStyleSheet(f"font-size:12px; color:{TEXT_MUTED};")
    main_layout.addWidget(pat_header)
    main_layout.addWidget(pat_subtitle)

    # --- Top filter bar ---
    top_bar = QHBoxLayout()
    top_bar.setSpacing(8)

    self.patients_search_edit = QLineEdit()
    self.patients_search_edit.setObjectName("patSearch")
    self.patients_search_edit.setPlaceholderText("🔍  Search patients (name, DOB, MRN)…")
    self.patients_search_edit.setMinimumWidth(260)

    self.patients_status_combo = QComboBox()
    self.patients_status_combo.addItems(["All", "Active", "Inactive"])

    self.patients_insurance_combo = QComboBox()
    self.patients_insurance_combo.addItem("All insurances")

    _lbl = lambda t: (lambda l: (l.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:600;"), l)[1])(QLabel(t))
    top_bar.addWidget(self.patients_search_edit, 2)
    top_bar.addSpacing(8)
    top_bar.addWidget(_lbl("Status:"))
    top_bar.addWidget(self.patients_status_combo, 1)
    top_bar.addSpacing(8)
    top_bar.addWidget(_lbl("Insurance:"))
    top_bar.addWidget(self.patients_insurance_combo, 1)

    top_bar.addStretch(1)
    main_layout.addLayout(top_bar)
    
    # --- Recent patients row ---
    recent_bar = QHBoxLayout()
    recent_bar.setSpacing(8)
    
    recent_label = QLabel("Recent:")
    recent_label.setStyleSheet("font-weight: normal; color: #666;")
    recent_bar.addWidget(recent_label)
    
    # Create 4 recent patient buttons
    self.recent_patient_buttons = []
    for i in range(4):
        btn = QPushButton("")
        btn.setFixedHeight(30)
        btn.setMinimumWidth(200)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                padding: 5px 10px;
                text-align: left;
                font-size: 10pt;
                color: #000000;
            }
            QPushButton:hover {
                background-color: #e9ecef;
                border-color: #adb5bd;
                color: #000000;
            }
            QPushButton:pressed {
                background-color: #dee2e6;
                color: #000000;
            }
            QPushButton:disabled {
                color: transparent;
                background-color: transparent;
                border: 1px dashed #dee2e6;
            }
        """)
        btn.clicked.connect(lambda checked, idx=i: self.select_recent_patient_main(idx))
        btn.setEnabled(False)
        recent_bar.addWidget(btn)
        self.recent_patient_buttons.append(btn)
    
    recent_bar.addStretch()
    main_layout.addLayout(recent_bar)

    # --- Patients table (existing widget) ---
    main_layout.addWidget(self.patients_table, 10)

    # --- Bottom summary / actions ---
    bottom_frame = QFrame()
    bottom_frame.setObjectName("PatientsSummaryFrame")
    bottom_layout = QHBoxLayout(bottom_frame)
    bottom_layout.setContentsMargins(10, 6, 10, 6)
    bottom_layout.setSpacing(12)

    # Summary labels
    self.patients_summary_label = QLabel("No patient selected")
    self.patients_summary_label.setStyleSheet("font-weight: 500; color: #333333;")
    self.patients_sub_label = QLabel("")
    self.patients_sub_label.setStyleSheet("color: #666666; font-size: 9pt;")

    summary_layout = QVBoxLayout()
    summary_layout.setContentsMargins(0, 0, 0, 0)
    summary_layout.setSpacing(2)
    summary_layout.addWidget(self.patients_summary_label)
    summary_layout.addWidget(self.patients_sub_label)

    bottom_layout.addLayout(summary_layout, 4)
    bottom_layout.addStretch(1)

    # Action buttons using draggable button bar
    def _save_patient_button_order(order_list):
        self.settings['patient_button_order'] = order_list
        self.save_settings()
    
    self.patient_button_bar = DraggableButtonBar(save_callback=_save_patient_button_order)
    
    self.btn_new_patient = self.patient_button_bar.add_button(
        "new_patient", "➕ New Patient", "Add a new patient"
    )
    self.btn_edit_patient = self.patient_button_bar.add_button(
        "edit_patient", "✏️ Edit Patient", "Edit selected patient"
    )
    self.btn_patients_view_orders = self.patient_button_bar.add_button(
        "view_orders", "📋 View Orders", "View patient's orders"
    )
    self.btn_patients_view_docs = self.patient_button_bar.add_button(
        "view_docs", "📄 View Documents", "View patient's documents"
    )
    self.btn_patients_new_order = self.patient_button_bar.add_button(
        "new_order", "🛒 New Order", "Create a new order for this patient"
    )
    self.btn_delete_patient = self.patient_button_bar.add_button(
        "delete_patient", "🗑️ Delete Patient", "Delete selected patient"
    )

    # Apply the modern button styling (primary / ghost / danger) so the action
    # bar matches the rest of the tab instead of the old all-blue look.
    _style_action_buttons(
        primary=[self.btn_new_patient],
        danger=[self.btn_delete_patient],
        ghost=[self.btn_patients_view_orders, self.btn_patients_view_docs,
               self.btn_patients_new_order, self.btn_edit_patient],
    )

    # Restore saved button order if available
    saved_patient_order = self.settings.get('patient_button_order', [])
    if saved_patient_order:
        self.patient_button_bar.set_order(saved_patient_order)
    
    bottom_layout.addWidget(self.patient_button_bar)

    main_layout.addWidget(bottom_frame)

    # Hook filters
    self.patients_search_edit.textChanged.connect(self.on_patients_filter_changed)
    self.patients_status_combo.currentIndexChanged.connect(self.on_patients_filter_changed)
    self.patients_insurance_combo.currentIndexChanged.connect(self.on_patients_filter_changed)

    return tab


# ---------------------------------------------------------------------------
# Windows Taskbar: separate buttons per window
# ---------------------------------------------------------------------------

def _set_taskbar_appid(window, appid: str) -> bool:
    """Set per-window AppUserModelID so Windows shows separate taskbar buttons.

    Uses the Windows Shell COM API (SHGetPropertyStoreForWindow) to assign
    a unique ID to each window, preventing Windows from grouping them.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import (
            c_void_p, c_ulong, c_ushort, c_ubyte, c_wchar_p,
            c_long, byref, POINTER, Structure, WINFUNCTYPE,
        )

        class GUID(Structure):
            _fields_ = [
                ("Data1", c_ulong), ("Data2", c_ushort),
                ("Data3", c_ushort), ("Data4", c_ubyte * 8),
            ]

        class PROPERTYKEY(Structure):
            _fields_ = [("fmtid", GUID), ("pid", c_ulong)]

        class PROPVARIANT(Structure):
            _fields_ = [
                ("vt", c_ushort),
                ("wReserved1", c_ushort),
                ("wReserved2", c_ushort),
                ("wReserved3", c_ushort),
                ("pwszVal", c_wchar_p),
            ]

        # IID_IPropertyStore: {886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}
        IID_IPropertyStore = GUID(
            0x886D8EEB, 0x8CF2, 0x4446,
            (c_ubyte * 8)(0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99),
        )

        # PKEY_AppUserModel_ID: {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, 5
        PKEY_AppUserModel_ID = PROPERTYKEY(
            GUID(0x9F4C2855, 0x9F79, 0x4B39,
                 (c_ubyte * 8)(0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3)),
            5,
        )

        hwnd = int(window.winId())

        # Get IPropertyStore for this window
        pps = c_void_p()
        hr = ctypes.windll.shell32.SHGetPropertyStoreForWindow(
            hwnd, byref(IID_IPropertyStore), byref(pps),
        )
        if hr != 0 or not pps:
            return False

        # Walk the COM vtable to call SetValue (index 6)
        vtbl_ptr = ctypes.cast(pps, POINTER(c_void_p))[0]
        vtbl = ctypes.cast(vtbl_ptr, POINTER(c_void_p * 8))[0]

        # IPropertyStore::SetValue(this, REFPROPERTYKEY, REFPROPVARIANT)
        SetValueProto = WINFUNCTYPE(c_long, c_void_p,
                                    POINTER(PROPERTYKEY), POINTER(PROPVARIANT))
        SetValue = SetValueProto(vtbl[6])

        pv = PROPVARIANT()
        pv.vt = 31  # VT_LPWSTR
        pv.pwszVal = appid

        SetValue(pps, byref(PKEY_AppUserModel_ID), byref(pv))

        # IPropertyStore::Release (index 2)
        ReleaseProto = WINFUNCTYPE(c_ulong, c_void_p)
        Release = ReleaseProto(vtbl[2])
        Release(pps)

        return True
    except Exception as e:
        print(f"Taskbar appid error: {e}")
        return False


class _MenuBarClickFixer(QObject):
    """Make every top-level menu open reliably on click.

    On some Windows theme/style combinations the native QMenuBar fails to keep
    certain menus open on click (the popup opens via aboutToShow then is
    dismissed immediately). This filter lets Qt try natively first, then — on
    the next event-loop tick — force-opens the clicked menu if nothing actually
    opened. It never consumes the event, so working menus are unaffected.
    """

    def __init__(self, menubar):
        super().__init__(menubar)
        self._mb = menubar

    def eventFilter(self, obj, event):
        try:
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                pos = event.position().toPoint()
                act = self._mb.actionAt(pos)
                if act is not None and act.menu() is not None:
                    menu = act.menu()
                    mb = self._mb

                    def _ensure_open():
                        try:
                            any_open = any(
                                a.menu() and a.menu().isVisible()
                                for a in mb.actions()
                            )
                            if not any_open:
                                mb.setActiveAction(act)
                                gpos = mb.mapToGlobal(
                                    mb.actionGeometry(act).bottomLeft())
                                menu.popup(gpos)
                        except Exception:
                            pass

                    QTimer.singleShot(0, _ensure_open)
        except Exception:
            pass
        return False  # never consume — supplement native handling only


class MainWindow(PDFViewer):
    """Primary window for the application.

    For now, this simply subclasses PDFViewer so that all existing
    behavior is preserved. Over time we will move logic from
    app_legacy.PDFViewer into this class and slim down app_legacy.
    """

    # Class-level list to keep secondary windows alive (prevent GC)
    _secondary_windows: list = []
    _window_counter: int = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # TODO: future: inject services, db repos, etc. here
        # e.g. self.orders_repo = OrdersRepository(...)
        # Right now we keep it minimal to avoid behavior changes.
        self._ui_scale_slider = None
        self._ui_scale_value_label = None

        try:
            # Keep menu behavior under Qt control on Windows where native menu
            # interactions can be inconsistent on some theme/style combinations.
            self.menuBar().setNativeMenuBar(False)
        except Exception:
            pass

        # IMPORTANT: build/extend all menus BEFORE the window is shown. On
        # Windows, menu-bar items that are added or modified AFTER the window is
        # realized can fail to open on click. Showing the window last keeps every
        # top-line menu (incl. Nova/Tools/View) reliably clickable.
        self._setup_theme_menu()
        self._setup_tools_menu()
        self._setup_help_menu()
        self._ensure_nova_menu()
        self._dedupe_top_menus({"view", "tools", "communications", "nova"})

        try:
            mb = self.menuBar()
            mb.adjustSize()
            mb.update()
            # Guarantee every top-level menu opens on click (native handling is
            # unreliable for some menus on certain Windows themes).
            self._menubar_click_fixer = _MenuBarClickFixer(mb)
            mb.installEventFilter(self._menubar_click_fixer)
        except Exception:
            pass

        try:
            self.showMaximized()  # Start maximized to avoid layout squeeze
        except Exception:
            pass

        # --- Multi-window: Ctrl+2 opens a second full window ---
        try:
            shortcut = QShortcut(QKeySequence("Ctrl+2"), self)
            shortcut.activated.connect(self._open_new_window)
        except Exception:
            pass

    def _open_new_window(self):
        """Open a second fully independent application instance (Ctrl+2).

        Launches a completely separate process so each window has its own
        event loop. This means modal dialogs in one window will NOT block
        the other — you can have an order editor open in Window 1 and
        still freely use Window 2 to answer a phone call.
        """
        import logging
        import subprocess
        logger = logging.getLogger("multi_window")
        try:
            # Determine the correct executable / script to launch
            if getattr(sys, "frozen", False):
                # Running as compiled .exe — just launch the same exe
                exe_path = sys.executable
                subprocess.Popen(
                    [exe_path, "--window-instance"],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS,
                )
            else:
                # Running from source — launch python app.py
                import os
                script = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__)
                    ))),
                    "app.py",
                )
                subprocess.Popen(
                    [sys.executable, script, "--window-instance"],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS,
                )

            logger.info("Launched independent second window (separate process)")

            if hasattr(self, "toast") and self.toast:
                self.toast.info("Opening second window\u2026")
        except Exception as e:
            logger.exception(f"Failed to open new window: {e}")
            try:
                QMessageBox.warning(
                    self, "New Window",
                    f"Could not open a new window:\n{e}",
                )
            except Exception:
                pass

    def _get_or_create_menu(self, title_candidates: Iterable[str], fallback_title: str):
        """Return an existing menu matching one of the given titles, else create it."""
        menubar = self.menuBar()

        for act in menubar.actions():
            menu = act.menu()
            if not menu:
                continue
            normalized = menu.title().replace("&&", "&")
            if normalized in title_candidates:
                return menu

        return menubar.addMenu(fallback_title)

    @staticmethod
    def _normalize_menu_title(title: str) -> str:
        return (title or "").replace("&&", "&").replace("&", "").strip().lower()

    def _find_menu(self, candidates: Iterable[str]):
        wanted = {self._normalize_menu_title(t) for t in candidates}
        for act in self.menuBar().actions():
            menu = act.menu()
            if not menu:
                continue
            if self._normalize_menu_title(menu.title()) in wanted:
                return menu
        return None

    def _dedupe_top_menus(self, titles: set[str]) -> None:
        """Keep only the first top-level menu for each requested title."""
        try:
            menubar = self.menuBar()
            seen: set[str] = set()
            for act in list(menubar.actions()):
                menu = act.menu()
                if not menu:
                    continue
                key = self._normalize_menu_title(menu.title())
                if key not in titles:
                    continue
                if key in seen:
                    menubar.removeAction(act)
                    continue
                seen.add(key)
        except Exception:
            pass

    def _ensure_nova_menu(self) -> None:
        """Guarantee there is a Nova menu even if legacy creation path failed."""
        if self._find_menu(("Nova",)) is not None:
            return
        try:
            from dmelogic.features import nova_enabled as _nova_enabled
            nova_menu = self.menuBar().addMenu("Nova")

            launch_action = QAction("Launch Nova...", self)
            if hasattr(self, "launch_nova"):
                launch_action.triggered.connect(self.launch_nova)
            else:
                launch_action.setEnabled(False)
            nova_menu.addAction(launch_action)

            enable_action = QAction("Enable Nova on startup", self)
            enable_action.setCheckable(True)
            try:
                enable_action.setChecked(bool(_nova_enabled()))
            except Exception:
                enable_action.setChecked(False)
            if hasattr(self, "set_nova_enabled"):
                enable_action.toggled.connect(self.set_nova_enabled)
            else:
                enable_action.setEnabled(False)
            nova_menu.addAction(enable_action)
        except Exception:
            pass

    @staticmethod
    def _menu_has_action(menu, text: str) -> bool:
        for act in menu.actions():
            if act.text().replace("&&", "&") == text.replace("&&", "&"):
                return True
        return False

    def _setup_help_menu(self) -> None:
        """Add Help menu actions shared by installed + USB builds."""
        help_menu = self._get_or_create_menu(("&Help", "Help"), "&Help")

        # Add a separator if the help menu already has items.
        if help_menu.actions() and not help_menu.actions()[-1].isSeparator():
            help_menu.addSeparator()

        if not self._menu_has_action(help_menu, "Update from File..."):
            self.action_update_from_file = QAction("Update from File...", self)
            self.action_update_from_file.setToolTip("Run an update installer from a file")
            self.action_update_from_file.triggered.connect(self._update_from_file)
            help_menu.addAction(self.action_update_from_file)

        if not self._menu_has_action(help_menu, "Uninstall Application..."):
            self.action_uninstall_app = QAction("Uninstall Application...", self)
            self.action_uninstall_app.setToolTip("Open the Windows uninstaller for DMELogic")
            self.action_uninstall_app.triggered.connect(self._uninstall_application)
            help_menu.addAction(self.action_uninstall_app)

    def _update_from_file(self) -> None:
        if sys.platform != "win32":
            QMessageBox.information(self, "Update", "Update from file is only supported on Windows.")
            return

        start_dir = str(Path.home())
        try:
            if getattr(sys, "frozen", False):
                start_dir = str(Path(sys.executable).resolve().parent)
        except Exception:
            pass

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Update Installer",
            start_dir,
            "Installer (*.exe)"
        )
        if not file_path:
            return

        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            QMessageBox.warning(self, "Update", "Selected installer file was not found.")
            return

        resp = QMessageBox.question(
            self,
            "Update",
            "This will launch the selected installer and close DMELogic. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        try:
            self._launch_windows_exe(file_path)
        except Exception as e:
            QMessageBox.critical(self, "Update", f"Could not launch installer:\n\n{e}")
            return

        # Exit so files can be replaced during update.
        QApplication.quit()

    def _uninstall_application(self) -> None:
        if sys.platform != "win32":
            QMessageBox.information(self, "Uninstall", "Uninstall is only supported on Windows.")
            return

        resp = QMessageBox.question(
            self,
            "Uninstall Application",
            "This will launch the DMELogic uninstaller.\n\n"
            "It is recommended to run a backup first (File → Backup).\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        uninstaller = self._find_uninstaller_path()
        if uninstaller is None:
            QMessageBox.information(
                self,
                "Uninstall",
                "Could not find the automatic uninstaller.\n\n"
                "To uninstall manually:\n"
                "1) Open Windows Settings\n"
                "2) Apps → Installed apps\n"
                "3) Search for 'DMELogic'\n"
                "4) Click Uninstall",
            )
            return

        try:
            # Inno Setup uninstallers are normal EXEs. Launch detached.
            self._launch_windows_exe(str(uninstaller))
        except Exception as e:
            QMessageBox.critical(
                self,
                "Uninstall",
                f"Could not launch uninstaller:\n\n{e}\n\n"
                "To uninstall manually, use Windows Settings → Apps → Installed apps.",
            )
            return

        QMessageBox.information(
            self,
            "Uninstall",
            "The uninstaller has been launched.\n\n"
            "DMELogic will now close.",
        )
        QApplication.quit()

    def _find_uninstaller_path(self) -> Path | None:
        """Try to locate the Inno Setup uninstaller for this app."""
        # 1) If frozen, the uninstaller is usually next to the EXE.
        try:
            if getattr(sys, "frozen", False):
                install_dir = Path(sys.executable).resolve().parent
                matches = sorted(install_dir.glob("unins*.exe"))
                if matches:
                    return matches[0]
        except Exception:
            pass

        # 2) Look in registry uninstall entries.
        try:
            import winreg

            app_id = "{8F3D5E2A-9B4C-4F1A-8D2E-5C6F7A8B9D0E}"
            display_names = {"DMELogic", "DME Logic"}

            roots = [
                winreg.HKEY_LOCAL_MACHINE,
                winreg.HKEY_CURRENT_USER,
            ]
            key_paths = [
                r"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
                r"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
            ]

            for root in roots:
                for base in key_paths:
                    try:
                        with winreg.OpenKey(root, base) as uninstall_key:
                            i = 0
                            while True:
                                try:
                                    subkey_name = winreg.EnumKey(uninstall_key, i)
                                except OSError:
                                    break
                                i += 1

                                try:
                                    with winreg.OpenKey(uninstall_key, subkey_name) as subkey:
                                        try:
                                            display = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                        except OSError:
                                            display = ""

                                        if subkey_name.lower() in {f"{app_id}_is1".lower(), app_id.lower()}:
                                            pass_match = True
                                        else:
                                            pass_match = any(n.lower() in (display or "").lower() for n in display_names)

                                        if not pass_match:
                                            continue

                                        try:
                                            uninstall_str = winreg.QueryValueEx(subkey, "UninstallString")[0]
                                        except OSError:
                                            uninstall_str = ""

                                        if not uninstall_str:
                                            continue

                                        # UninstallString can be quoted and/or include args.
                                        # Prefer to extract an unins*.exe path if present.
                                        candidate = uninstall_str.strip().strip('"')
                                        if (
                                            "unins" in candidate.lower()
                                            and candidate.lower().endswith(".exe")
                                            and os.path.exists(candidate)
                                        ):
                                            return Path(candidate)

                                        # Otherwise, return the first token if it exists.
                                        first = candidate.split(" ", 1)[0].strip('"')
                                        if first and os.path.exists(first):
                                            return Path(first)
                                except OSError:
                                    continue
                    except OSError:
                        continue
        except Exception:
            pass

        return None

    @staticmethod
    def _launch_windows_exe(exe_path: str) -> None:
        """Launch an EXE detached on Windows (with UAC prompt if required by the EXE)."""
        exe_path = os.path.abspath(exe_path)
        if not os.path.exists(exe_path):
            raise FileNotFoundError(exe_path)

        # Prefer ShellExecute so Windows handles UAC correctly.
        try:
            import ctypes

            r = ctypes.windll.shell32.ShellExecuteW(None, "open", exe_path, None, None, 1)
            if r <= 32:
                raise OSError(f"ShellExecuteW failed with code {r}")
            return
        except Exception:
            # Fallback.
            subprocess.Popen([exe_path], close_fds=True)
    
    def _setup_theme_menu(self):
        """Add View menu with theme switching options."""
        from PyQt6.QtWidgets import QApplication
        
        # Reuse the existing View menu (created by the legacy menu bar) instead
        # of adding a second one — appending theme/scale actions to it.
        view_menu = self._find_menu(("View",))
        if view_menu is None:
            view_menu = self.menuBar().addMenu("&View")
        else:
            view_menu.addSeparator()

        # Light theme action
        self.action_light_theme = QAction("Light Theme", self)
        self.action_light_theme.setCheckable(True)
        self.action_light_theme.setChecked(True)  # default
        self.action_light_theme.triggered.connect(self._set_light_theme)
        
        # Dark theme action
        self.action_dark_theme = QAction("Dark Theme", self)
        self.action_dark_theme.setCheckable(True)
        self.action_dark_theme.triggered.connect(self._set_dark_theme)
        
        view_menu.addAction(self.action_light_theme)
        view_menu.addAction(self.action_dark_theme)
        self._add_ui_scale_controls(view_menu)

    def _add_ui_scale_controls(self, view_menu) -> None:
        """Inject slider-based UI scale controls into the View menu."""
        if view_menu is None:
            return

        view_menu.addSeparator()
        container = QWidget(view_menu)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        title_label = QLabel("UI Scale", container)
        title_label.setMinimumWidth(60)

        slider = QSlider(Qt.Orientation.Horizontal, container)
        slider.setRange(80, 150)
        slider.setTickInterval(10)
        slider.setSingleStep(5)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setMinimumWidth(160)

        value_label = QLabel("100%", container)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setMinimumWidth(48)

        layout.addWidget(title_label)
        layout.addWidget(slider, 1)
        layout.addWidget(value_label)

        widget_action = QWidgetAction(self)
        widget_action.setDefaultWidget(container)
        view_menu.addAction(widget_action)

        reset_action = QAction("Reset UI Scale (100%)", self)
        reset_action.setToolTip("Restore default sizing for all controls")
        reset_action.triggered.connect(lambda: slider.setValue(100))
        view_menu.addAction(reset_action)

        self._ui_scale_slider = slider
        self._ui_scale_value_label = value_label

        initial_value = self._load_saved_ui_scale()
        slider.blockSignals(True)
        slider.setValue(initial_value)
        slider.blockSignals(False)
        self._update_ui_scale_label(initial_value)
        self._apply_ui_scale(initial_value)

        slider.valueChanged.connect(self._handle_ui_scale_changed)

    def _load_saved_ui_scale(self) -> int:
        settings = getattr(self, "settings", None)
        if isinstance(settings, dict):
            try:
                value = int(settings.get("ui_scale", 100))
            except Exception:
                value = 100
        else:
            value = 100
        return max(80, min(150, value))

    def _update_ui_scale_label(self, value: int) -> None:
        if self._ui_scale_value_label is not None:
            self._ui_scale_value_label.setText(f"{value}%")

    def _handle_ui_scale_changed(self, value: int) -> None:
        clamped = max(80, min(150, int(value)))
        self._update_ui_scale_label(clamped)
        self._apply_ui_scale(clamped)
        settings = getattr(self, "settings", None)
        if isinstance(settings, dict):
            settings["ui_scale"] = clamped
            try:
                self.save_settings()
            except Exception:
                pass

    def _apply_ui_scale(self, scale_percent: int) -> None:
        app = QApplication.instance()
        if not app:
            return
        ThemeManager.apply_scale(app, scale_percent)
        icon_px = max(16, int(20 * scale_percent / 100))
        for toolbar in self.findChildren(QToolBar):
            try:
                toolbar.setIconSize(QSize(icon_px, icon_px))
            except Exception:
                pass

    def _setup_tools_menu(self) -> None:
        """Add Tools menu and toolbar entry for sticky notes and user administration."""
        from dmelogic.security.permissions import has_permission

        tools_menu = self._find_menu(("Tools",))
        if tools_menu is None:
            tools_menu = self.menuBar().addMenu("&Tools")

        self.action_sticky_notes = QAction("Sticky Notes", self)
        self.action_sticky_notes.setToolTip("Open global sticky notes")
        self.action_sticky_notes.triggered.connect(self.open_sticky_notes)

        tools_menu.addAction(self.action_sticky_notes)
        
        # Import Rx → Order
        tools_menu.addSeparator()
        self.action_import_rx = QAction("📥 Import Rx → Order", self)
        self.action_import_rx.setShortcut("Ctrl+I")
        self.action_import_rx.setToolTip("Upload Rx PDF(s) and auto-create an order")
        self.action_import_rx.triggered.connect(self._open_rx_import)
        tools_menu.addAction(self.action_import_rx)

        self.action_batch_delivery_ocr = QAction("📚 Batch Delivery OCR Attach", self)
        self.action_batch_delivery_ocr.setToolTip("Batch process signed delivery tickets and auto-attach by OCR order number")
        self.action_batch_delivery_ocr.triggered.connect(self._run_batch_delivery_ocr)
        tools_menu.addAction(self.action_batch_delivery_ocr)
        
        # User Administration (only visible for users with users.manage permission)
        if has_permission("users.manage"):
            tools_menu.addSeparator()
            self.action_user_admin = QAction("User Administration", self)
            self.action_user_admin.setToolTip("Manage users, roles, and permissions")
            self.action_user_admin.triggered.connect(self._open_user_admin)
            tools_menu.addAction(self.action_user_admin)
        
        # Change Password (available to all logged-in users)
        self.action_change_password = QAction("Change Password", self)
        self.action_change_password.setToolTip("Change your password")
        self.action_change_password.triggered.connect(self._open_change_password)
        tools_menu.addAction(self.action_change_password)

    def _setup_quick_menu_fallback(self) -> None:
        """Provide guaranteed-click controls for Nova/Communications/Tools.

        On some Windows style/theme combinations, the native menu bar can appear
        clickable but fail to open reliably. This toolbar gives users a stable
        path to the same menus.
        """
        try:
            # Prefer augmenting the existing legacy Nova toolbar (single launch button).
            bar = self.findChild(QToolBar, "NovaToolBar")
            if bar is not None:
                if bool(bar.property("quick_access_augmented")):
                    return
                bar.addSeparator()
                bar.setProperty("quick_access_augmented", True)
            else:
                # Avoid duplicate fallback bars if MainWindow gets reconfigured.
                existing = self.findChild(QToolBar, "QuickAccessToolBar")
                if existing is not None:
                    return

                bar = QToolBar("Quick Access", self)
                bar.setObjectName("QuickAccessToolBar")
                bar.setMovable(False)

                # Push controls to the right so it behaves like a top-right utility strip.
                spacer = QWidget(bar)
                spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                bar.addWidget(spacer)

                launch_btn = QPushButton("Launch Nova")
                launch_btn.setToolTip("Start Nova (if needed) and open the assistant")
                launch_btn.setStyleSheet(
                    "QPushButton{background:#7c3aed;color:white;border-radius:4px;"
                    "padding:4px 12px;font-weight:bold;}"
                    "QPushButton:hover{background:#6d28d9;}"
                )
                if hasattr(self, "launch_nova"):
                    launch_btn.clicked.connect(self.launch_nova)
                else:
                    launch_btn.setEnabled(False)
                bar.addWidget(launch_btn)

            for title in ("Communications", "Tools"):
                menu = self._find_menu((title,))
                if menu is None:
                    continue
                btn = QToolButton(bar)
                btn.setText(title)
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                btn.setMenu(menu)
                bar.addWidget(btn)

            if bar.objectName() == "QuickAccessToolBar":
                self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        except Exception:
            pass
    
    def _open_user_admin(self) -> None:
        """Open the User Administration dialog."""
        from dmelogic.security.permissions import has_permission
        from dmelogic.ui.admin.users_admin_dialog import UsersAdminDialog
        
        if not has_permission("users.manage"):
            QMessageBox.warning(self, "Access Denied", "You do not have permission to manage users.")
            return
        
        dialog = UsersAdminDialog(self)
        dialog.exec()
    
    def _open_change_password(self) -> None:
        """Open the Change Password dialog."""
        from dmelogic.ui.change_password_dialog import ChangePasswordDialog
        
        dialog = ChangePasswordDialog(self)
        dialog.exec()

    def open_sticky_notes(self) -> None:
        """Open the notes board (global sticky notes) as a modeless window."""
        try:
            # Reuse existing board if open
            if hasattr(self, "_notes_board") and self._notes_board and self._notes_board.isVisible():
                self._notes_board.raise_()
                self._notes_board.activateWindow()
                return
        except Exception:
            pass

        notes_db.init_notes_db(getattr(self, "folder_path", None))
        self._notes_board = NotesBoardDialog(folder_path=getattr(self, "folder_path", None), parent=self)
        self._notes_board.setModal(False)
        self._notes_board.show()
        self._notes_board.raise_()
        self._notes_board.activateWindow()
    
    def _set_light_theme(self):
        """Switch to light theme."""
        app = QApplication.instance()
        if not app:
            return
        ThemeManager.apply_theme(app, "light")
        self.action_light_theme.setChecked(True)
        self.action_dark_theme.setChecked(False)
    
    def _set_dark_theme(self):
        """Switch to dark theme."""
        app = QApplication.instance()
        if not app:
            return
        ThemeManager.apply_theme(app, "dark")
        self.action_light_theme.setChecked(False)
        self.action_dark_theme.setChecked(True)

    # -------------------- Modern Order Editor --------------------

    def open_order_editor(self, order_id: int) -> None:
        """
        Open the modern Order Editor dialog for the specified order ID.
        
        This is the new unified interface for viewing/editing orders,
        powered by the domain model (fetch_order_with_items).
        """
        from dmelogic.ui.order_editor import OrderEditorDialog
        
        folder_path = getattr(self, "folder_path", None)
        
        dialog = OrderEditorDialog(
            order_id=order_id,
            folder_path=folder_path,
            parent=self
        )
        
        # Register as child window if parent supports it
        if hasattr(self, 'register_child_window'):
            self.register_child_window(dialog)
        
        # Connect order_updated signal to refresh orders table
        def on_order_updated():
            if hasattr(self, 'load_orders'):
                self.load_orders()
        
        dialog.order_updated.connect(on_order_updated)
        
        # Show dialog modally
        dialog.exec()

        # Ensure orders grid reflects any changes made while the editor was open
        if hasattr(self, 'load_orders'):
            try:
                self.load_orders()
            except Exception:
                pass

    def edit_order_by_id_modern(self, order_id: int) -> None:
        """
        Modern wrapper for edit_order_by_id that uses the new Order Editor.
        
        This can be called from anywhere that currently calls edit_order_by_id
        to get the new modern interface instead of the legacy one.
        """
        self.open_order_editor(order_id)

    # -------------------- Rx Import Wizard --------------------

    def _open_rx_import(self) -> None:
        """Open the Smart Rx Import Wizard — upload Rx PDFs and auto-create orders."""
        wizard = RxImportWizard(
            folder_path=getattr(self, "folder_path", None),
            parent=self,
        )
        wizard.order_created.connect(lambda oid: self.load_orders())
        # Also refresh patient list in case a new patient was added during import
        wizard.order_created.connect(lambda oid: self.load_patients_table() if hasattr(self, 'load_patients_table') else None)
        wizard.order_created.connect(lambda oid: self.load_patients() if hasattr(self, 'load_patients') else None)

        # Register as child window if parent supports it
        if hasattr(self, "register_child_window"):
            self.register_child_window(wizard)

        wizard.exec()

    def _run_batch_delivery_ocr(self) -> None:
        """Global batch delivery OCR attach flow (no order editor required)."""
        from dmelogic.paths import delivery_tickets_folder
        from dmelogic.scan import scan_batch_document

        source_modes = ["Scan batch from scanner", "Use existing files"]
        source_mode, ok = QInputDialog.getItem(
            self,
            "Batch Delivery OCR",
            "Choose source:",
            source_modes,
            0,
            False,
        )
        if not ok:
            return

        file_paths: list[str] = []
        if source_mode == "Scan batch from scanner":
            # Scanner-mode path: real acquisition only, no generic file picker fallback.
            scanned_path, error_msg = scan_batch_document(
                parent_widget=self,
                save_folder=delivery_tickets_folder(),
                suggested_name="Delivery Batch",
            )
            if scanned_path is None:
                if error_msg:
                    QMessageBox.warning(self, "Scanner Not Ready", error_msg)
                return
            if not scanned_path.exists():
                QMessageBox.warning(
                    self,
                    "Scanner Error",
                    f"Scanned file not found at expected path:\n{scanned_path}",
                )
                return
            file_paths = [str(scanned_path)]
        else:
            start_dir = str(delivery_tickets_folder())
            selected_paths, _ = QFileDialog.getOpenFileNames(
                self,
                "Select Delivery Tickets for OCR Batch Attach",
                start_dir,
                "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp);;All Files (*.*)",
            )
            if not selected_paths:
                return
            file_paths = selected_paths

        result = self._process_delivery_ticket_batch_global(file_paths)

        lines = [
            f"Selected: {result['selected']}",
            f"Sheets processed: {result['sheets']}",
            f"Attached: {result['attached']}",
            f"No order found: {result['no_match']}",
            f"OCR/read failures: {result['ocr_fail']}",
            f"Copy failures: {result['copy_fail']}",
            "",
        ]

        if result["details"]:
            lines.append("Details:")
            lines.extend(result["details"][:12])
            if len(result["details"]) > 12:
                lines.append(f"... and {len(result['details']) - 12} more")

        QMessageBox.information(self, "Batch Delivery OCR", "\n".join(lines))

        if hasattr(self, "load_orders"):
            try:
                self.load_orders()
            except Exception:
                pass

    def _process_delivery_ticket_batch_global(self, file_paths: list[str]) -> dict:
        """Attach each ticket sheet to matching orders by OCR-detected order number."""
        import shutil
        import sqlite3
        import tempfile
        from pathlib import Path
        from dmelogic.paths import delivery_tickets_folder

        result = {
            "selected": len(file_paths),
            "sheets": 0,
            "attached": 0,
            "no_match": 0,
            "ocr_fail": 0,
            "copy_fail": 0,
            "details": [],
        }

        orders_db_path = resolve_db_path("orders.db", folder_path=getattr(self, "folder_path", None))
        delivery_dir = delivery_tickets_folder()
        delivery_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(orders_db_path)
        cur = conn.cursor()
        temp_root = Path(tempfile.mkdtemp(prefix="dme_batch_delivery_"))

        try:
            for raw_path in file_paths:
                src = Path(raw_path)
                if not src.exists():
                    result["copy_fail"] += 1
                    result["details"].append(f"Missing file: {src.name}")
                    continue
                sheets, remove_original = self._split_delivery_source_into_sheets(src, temp_root)
                if not sheets:
                    result["copy_fail"] += 1
                    result["details"].append(f"Could not read: {src.name}")
                    continue

                for sheet_path in sheets:
                    result["sheets"] += 1
                    text = self._extract_text_for_global_order_match(sheet_path)
                    match = re.search(r"\bORD\s*[-#]?\s*(\d{1,6})(?:\s*[-/ ]\s*R\s*(\d{1,3}))?\b", text or "", re.IGNORECASE)
                    if not match:
                        result["ocr_fail"] += 1
                        result["details"].append(f"No order reference detected: {sheet_path.name}")
                        continue

                    root_id = int(match.group(1))
                    refill_number = int(match.group(2)) if match.group(2) else None

                    target_order_id = self._resolve_order_id_for_token(cur, root_id, refill_number)
                    if target_order_id is None:
                        result["no_match"] += 1
                        token = f"ORD-{root_id:03d}" + (f"-R{refill_number}" if refill_number is not None else "")
                        result["details"].append(f"Order not found {token}: {sheet_path.name}")
                        continue

                    target_name = self._build_delivery_ticket_filename(cur, target_order_id, root_id, refill_number)
                    saved_ticket = self._save_sheet_as_named_pdf(sheet_path, delivery_dir, target_name)
                    if saved_ticket is None:
                        result["copy_fail"] += 1
                        result["details"].append(f"Save failed: {sheet_path.name}")
                        continue

                    cur.execute("SELECT parent_order_id FROM orders WHERE id = ?", (target_order_id,))
                    row = cur.fetchone()
                    root_family_id = int(row[0]) if row and row[0] else target_order_id
                    cur.execute(
                        "SELECT id FROM orders WHERE id = ? OR parent_order_id = ? ORDER BY id",
                        (root_family_id, root_family_id),
                    )
                    family_ids = [int(r[0]) for r in cur.fetchall()]

                    added_any = False
                    for order_id in family_ids:
                        cur.execute("SELECT attached_signed_ticket_files FROM orders WHERE id = ?", (order_id,))
                        row = cur.fetchone()
                        current_files = row[0] if row and row[0] else ""
                        existing = [f.strip() for f in str(current_files).replace("\n", ";").split(";") if f.strip()]
                        if saved_ticket.name in existing:
                            continue

                        updated = f"{current_files};{saved_ticket.name}" if current_files else saved_ticket.name
                        cur.execute(
                            "UPDATE orders SET attached_signed_ticket_files = ? WHERE id = ?",
                            (updated, order_id),
                        )
                        added_any = True

                    token = f"ORD-{root_id:03d}" + (f"-R{refill_number}" if refill_number is not None else "")
                    if added_any:
                        conn.commit()
                        result["attached"] += 1
                        result["details"].append(f"Attached {saved_ticket.name} -> {token}")
                    else:
                        result["details"].append(f"Already attached: {saved_ticket.name}")

                if remove_original and src.exists():
                    try:
                        src.unlink()
                    except Exception:
                        pass
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
            conn.close()

        return result

    def _split_delivery_source_into_sheets(self, src: Path, temp_root: Path) -> tuple[list[Path], bool]:
        """Split multi-page source into one PDF per page. Returns (sheets, remove_original)."""
        suffix = src.suffix.lower()

        if suffix == ".pdf":
            try:
                import fitz

                doc = fitz.open(str(src))
                page_count = len(doc)
                if page_count <= 1:
                    doc.close()
                    return [src], False

                sheets: list[Path] = []
                for idx in range(page_count):
                    out = temp_root / f"{src.stem}_p{idx + 1}.pdf"
                    one = fitz.open()
                    one.insert_pdf(doc, from_page=idx, to_page=idx)
                    one.save(str(out))
                    one.close()
                    sheets.append(out)
                doc.close()
                return sheets, True
            except Exception:
                return [src], False

        if suffix in {".tif", ".tiff"}:
            try:
                from PIL import Image

                with Image.open(str(src)) as img:
                    frame_count = int(getattr(img, "n_frames", 1) or 1)
                    if frame_count <= 1:
                        return [src], False

                    sheets: list[Path] = []
                    for idx in range(frame_count):
                        img.seek(idx)
                        out = temp_root / f"{src.stem}_p{idx + 1}.pdf"
                        frame = img.convert("RGB")
                        frame.save(str(out), "PDF", resolution=200.0)
                        sheets.append(out)
                    return sheets, True
            except Exception:
                return [src], False

        return [src], False

    def _resolve_order_id_for_token(self, cur, root_id: int, refill_number: int | None) -> int | None:
        """Resolve ORD token (with optional refill) to an order id."""
        target_order_id = None
        if refill_number is not None:
            cur.execute(
                "SELECT id FROM orders WHERE parent_order_id = ? AND refill_number = ? ORDER BY id DESC LIMIT 1",
                (root_id, refill_number),
            )
            row = cur.fetchone()
            if row:
                target_order_id = int(row[0])

        if target_order_id is None:
            cur.execute("SELECT id FROM orders WHERE id = ?", (root_id,))
            row = cur.fetchone()
            if row:
                target_order_id = int(row[0])

        return target_order_id

    def _build_delivery_ticket_filename(self, cur, order_id: int, root_id: int, refill_number: int | None) -> str:
        """Build single-ticket filename similar to single-scan naming."""
        token = f"ORD-{root_id:03d}" + (f"-R{refill_number}" if refill_number is not None else "")
        cur.execute(
            "SELECT patient_last_name, patient_first_name FROM orders WHERE id = ?",
            (order_id,),
        )
        row = cur.fetchone()
        patient_part = ""
        if row:
            last = (row[0] or "").strip()
            first = (row[1] or "").strip()
            patient_part = f"{last}, {first}".strip(", ")

        base = f"{patient_part} {token} DC".strip()
        if not base:
            base = f"{token} DC"

        base = re.sub(r"[<>:\"/\\|?*]+", " ", base)
        base = re.sub(r"\s+", " ", base).strip().strip(".")
        if not base:
            base = f"{token} DC"
        return f"{base}.pdf"

    def _save_sheet_as_named_pdf(self, src: Path, delivery_dir: Path, requested_name: str) -> Path | None:
        """Save a sheet as a single PDF with a unique requested name in delivery folder."""
        import shutil

        target = delivery_dir / requested_name
        if target.exists():
            stem = target.stem
            ext = target.suffix or ".pdf"
            counter = 1
            while target.exists():
                target = delivery_dir / f"{stem}_{counter}{ext}"
                counter += 1

        try:
            if src.suffix.lower() == ".pdf":
                shutil.copy2(str(src), str(target))
                return target

            from PIL import Image

            with Image.open(str(src)) as img:
                pdf_img = img.convert("RGB")
                pdf_img.save(str(target), "PDF", resolution=200.0)
            return target
        except Exception:
            return None

    def _extract_text_for_global_order_match(self, file_path: Path) -> str:
        """Extract filename + OCR text for order token matching."""
        suffix = file_path.suffix.lower()
        parts = [file_path.name]

        try:
            if suffix == ".pdf":
                from dmelogic.ocr_tools import extract_text_from_pdf

                parts.append(extract_text_from_pdf(str(file_path)) or "")
            elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
                from PIL import Image
                import pytesseract

                with Image.open(str(file_path)) as img:
                    parts.append(pytesseract.image_to_string(img, config="--psm 6") or "")
        except Exception as e:
            print(f"Batch delivery OCR extraction failed for {file_path}: {e}")

        return "\n".join(p for p in parts if p)

    # -------------------- New Order Wizard --------------------

    def open_new_order_wizard(self, patient_context: dict | None = None, rx_context: dict | None = None) -> None:
        """
        Launch the New Order Wizard.

        patient_context: optional dict like {"name": "...", "dob": "...", "phone": "...", "patient_id": 123}
        rx_context: optional dict like {"rx_number": "...", "prescriber_name": "...", ...}
        """
        # Extract patient_id from context if available
        patient_id = (patient_context or {}).get("patient_id", 0)
        
        wizard = OrderWizard(
            self,
            patient_id=patient_id,
            folder_path=getattr(self, "folder_path", None),
            initial_patient=patient_context,
            rx_context=rx_context
        )
        
        # Register as child window if parent supports it
        if hasattr(self, 'register_child_window'):
            self.register_child_window(wizard)
        
        # Connect accepted signal
        def on_accepted():
            if wizard.result:
                self.create_order_from_wizard(wizard.result)
        
        wizard.accepted.connect(on_accepted)
        wizard.show()
        wizard.raise_()
        wizard.activateWindow()

    def create_order_from_wizard(self, result: OrderWizardResult) -> None:
        """
        Take the wizard result and create a real order in orders.db via the
        centralized DB layer (dmelogic.db.orders.create_order),
        then immediately open the full 'New DME Order' window for that order.

        - Pulls inventory data to fill item_number and cost_ea (bill amount).
        - Stores ICD-10 codes and doctor directions.
        - Tries to pull primary insurance from the patient DB (best-effort).
        """
        try:
            from datetime import datetime

            folder_path = getattr(self, "folder_path", None)

            # --- Split patient name "LAST, FIRST" (fallback to FIRST LAST) ---
            last_name = ""
            first_name = ""
            name = (result.patient_name or "").strip()
            if name:
                if "," in name:
                    parts = [p.strip() for p in name.split(",", 1)]
                    if len(parts) == 2:
                        last_name, first_name = parts[0], parts[1]
                else:
                    parts = name.split()
                    if len(parts) >= 2:
                        first_name = parts[0]
                        last_name = " ".join(parts[1:])

            # --- Insurance & address (best-effort) ---
            primary_insurance = ""
            primary_insurance_id = ""
            patient_address = ""

            # Use insurance from wizard result if available
            if result.insurance_name:
                primary_insurance = result.insurance_name or ""
                primary_insurance_id = result.insurance_policy_number or ""
            elif last_name or first_name:
                try:
                    ins = fetch_patient_insurance(
                        last_name=last_name,
                        first_name=first_name,
                        dob=None,  # we already have DOB in result, but legacy code used None
                        folder_path=folder_path,
                    )
                    if ins:
                        if not primary_insurance:
                            primary_insurance = ins.primary_insurance or ""
                            primary_insurance_id = ins.primary_insurance_id or ""
                        addr_parts = [
                            ins.address or "",
                            ins.city or "",
                            ins.state or "",
                            ins.zip_code or "",
                        ]
                        patient_address = ", ".join([p for p in addr_parts if p.strip()])
                except Exception as e:
                    print("⚠️ Could not read insurance from patients.db:", e)

            # --- Build OrderItemInput list (with inventory lookup) ---
            order_items: list[OrderItemInput] = []

            for ui_item in result.items:
                hcpcs = (ui_item.hcpcs or "").strip()
                desc = (ui_item.description or "").strip()

                # Skip fully empty lines
                if not hcpcs and not desc:
                    continue

                item_number = ""
                cost_ea_dec: Decimal | None = None

                if hcpcs:
                    try:
                        inv_row = fetch_latest_item_by_hcpcs(
                            hcpcs_code=hcpcs,
                            folder_path=folder_path,
                        )
                    except Exception as e:
                        print(f"⚠️ Inventory lookup failed for {hcpcs}: {e}")
                        inv_row = None

                    if inv_row:
                        item_number = inv_row.get("item_number") or ""
                        # Retail price is our billing amount
                        retail_price = inv_row.get("retail_price")
                        if retail_price not in (None, ""):
                            try:
                                cost_ea_dec = Decimal(str(retail_price))
                            except Exception:
                                cost_ea_dec = None

                        # If description empty in UI, fall back to inventory description
                        if not desc:
                            inv_desc = inv_row.get("description")
                            if inv_desc:
                                desc = inv_desc

                # If the line has no HCPCS AND no description after all that, skip it
                if not hcpcs and not desc:
                    continue

                # Directions priority: item-level, then order-level MD directions
                directions = ui_item.directions or result.doctor_directions or ""

                order_items.append(
                    OrderItemInput(
                        hcpcs=hcpcs,
                        description=desc,
                        quantity=ui_item.quantity,
                        refills=ui_item.refills,
                        days_supply=ui_item.days_supply,
                        directions=directions,
                        item_number=item_number or None,
                        cost_ea=cost_ea_dec,
                    )
                )

            if not order_items:
                QMessageBox.warning(
                    self,
                    "Order",
                    "No valid items to create an order. Please add at least one item.",
                )
                return

            # --- Build OrderInput DTO for the DB layer ---
            # If wizard didn't provide dates for some reason, fall back to today
            today_str = datetime.now().strftime("%m/%d/%Y")
            rx_date_str = (result.rx_date or "").strip() or today_str
            order_date_str = (result.order_date or "").strip() or today_str
            delivery_date_str = (result.delivery_date or "").strip() or None

            # Billing type / status from enums (keeps consistency with DB layer)
            billing_type = (result.billing_type or "").strip() or BillingType.INSURANCE.value
            order_status = OrderStatus.PENDING.value  # new orders start Pending

            # Auto-flag orders created by agent-designated users
            _agent_created = is_current_user_agent()

            order_input = OrderInput(
                patient_last_name=last_name,
                patient_first_name=first_name,
                patient_id=result.patient_id or None,
                patient_dob=(result.patient_dob or "").strip() or None,
                patient_phone=(result.patient_phone or "").strip() or None,
                patient_address=patient_address or None,
                prescriber_name=(result.prescriber_name or "").strip() or None,
                prescriber_npi=(result.prescriber_npi or "").strip() or None,
                rx_date_2=(result.rx_date_2 or "").strip() or None,
                prescriber_name_2=(result.prescriber_name_2 or "").strip() or None,
                prescriber_npi_2=(result.prescriber_npi_2 or "").strip() or None,
                rx_date=rx_date_str,
                order_date=order_date_str,
                delivery_date=delivery_date_str,
                billing_type=billing_type,
                order_status=order_status,
                primary_insurance=primary_insurance or None,
                primary_insurance_id=primary_insurance_id or None,
                icd_code_1=(result.icd_code_1 or "").strip() or None,
                icd_code_2=(result.icd_code_2 or "").strip() or None,
                icd_code_3=(result.icd_code_3 or "").strip() or None,
                icd_code_4=(result.icd_code_4 or "").strip() or None,
                icd_code_5=(result.icd_code_5 or "").strip() or None,
                doctor_directions=(result.doctor_directions or "").strip() or None,
                notes=(result.notes or "").strip() or None,
                items=order_items,
                agent_created=_agent_created,
            )

            # --- Smart Duplicate Detection ---
            try:
                from dmelogic.services.duplicate_detector import DuplicateDetector
                from dmelogic.ui.duplicate_warning_dialog import DuplicateWarningDialog

                detector = DuplicateDetector(folder_path=folder_path)
                hcpcs_list = [item.hcpcs for item in order_items if item.hcpcs]
                warnings = detector.check(
                    patient_last_name=last_name,
                    patient_first_name=first_name,
                    patient_dob=(result.patient_dob or "").strip() or None,
                    patient_id=result.patient_id or None,
                    hcpcs_codes=hcpcs_list,
                )
                if warnings:
                    action = DuplicateWarningDialog(warnings, parent=self).exec()
                    if action == DuplicateWarningDialog.ACTION_CANCEL:
                        return  # User cancelled
                    elif action == DuplicateWarningDialog.ACTION_VIEW:
                        # Try to open the existing order
                        try:
                            dup_id = warnings[0].order_id
                            if hasattr(self, "edit_order_by_id"):
                                self.edit_order_by_id(dup_id)
                        except Exception:
                            pass
                        return
                    # ACTION_CONTINUE → proceed with creation
            except ImportError:
                pass  # Duplicate detector not installed yet
            except Exception as e:
                print(f"⚠️ Duplicate detection skipped: {e}")

            # Let the DB layer validate and persist
            new_order_id = create_order(order_input, folder_path=folder_path)

            # Process attachment files from wizard (if any)
            if hasattr(result, 'attachment_paths') and result.attachment_paths:
                self._process_wizard_attachments(new_order_id, result.attachment_paths, folder_path, patient_id=result.patient_id)

            # Fetch the newly created order with items to show in EPACES dialog
            order = fetch_order_with_items(new_order_id, folder_path=folder_path)
            
            # Show EPACES billing cockpit instead of order editor
            epaces_dlg = EpacesHelperDialog(order=order, folder_path=folder_path, parent=self)
            epaces_dlg.exec()

            try:
                formatted = self.format_order_number(new_order_id)
            except Exception:
                formatted = str(new_order_id)

            QMessageBox.information(
                self,
                "Order Created",
                f"Order {formatted} created successfully.",
            )

            # Refresh order list if available
            try:
                if hasattr(self, "load_orders"):
                    self.load_orders()
            except Exception as e:
                print(f"⚠️ Could not refresh orders list: {e}")

        except Exception as e:
            print("❌ Error creating order from wizard:", e)
            QMessageBox.critical(
                self,
                "Error Creating Order",
                f"An error occurred while creating the order:\n{e}",
            )

    def _process_wizard_attachments(self, order_id: int, attachment_paths: list, folder_path: str = None, patient_id: int = None) -> None:
        """
        Store references to attachment files in the order's DB record.
        Stores filenames only (resolved at runtime via configurable ocr_folder).
        Also auto-links documents to the patient's profile.
        """
        from pathlib import Path
        import sqlite3
        
        if not attachment_paths:
            return
            
        try:
            filenames = []
            full_paths = []
            original_names = []
            for src_path in attachment_paths:
                try:
                    src = Path(src_path)
                    if not src.exists():
                        print(f"⚠️ Attachment not found: {src_path}")
                        continue
                    
                    filenames.append(src.name)
                    full_paths.append(str(src))
                    original_names.append(src.name)
                    print(f"✅ Linked: {src.name} → order {order_id}")
                except Exception as e:
                    print(f"⚠️ Failed to link attachment {src_path}: {e}")
            
            if filenames:
                # Update order's attached_rx_files field in DB (filenames only)
                from dmelogic.db.base import get_connection
                
                conn = get_connection("orders.db", folder_path)
                cur = conn.cursor()
                
                # Get existing attached files
                cur.execute("SELECT attached_rx_files FROM orders WHERE id = ?", (order_id,))
                row = cur.fetchone()
                current_files = row[0] if row and row[0] else ""
                
                # Build new list (append to existing if any)
                if current_files:
                    all_files = current_files + ";" + ";".join(filenames)
                else:
                    all_files = ";".join(filenames)
                
                cur.execute(
                    "UPDATE orders SET attached_rx_files = ? WHERE id = ?",
                    (all_files, order_id)
                )
                conn.commit()
                conn.close()
                
                print(f"✅ Linked {len(filenames)} documents to order {order_id}")
                
                # Auto-attach to patient profile (uses full paths for patient doc storage)
                if patient_id:
                    self._auto_attach_to_patient(patient_id, full_paths, original_names, order_id, folder_path)
                    
        except Exception as e:
            print(f"❌ Error processing wizard attachments: {e}")

    def _auto_attach_to_patient(self, patient_id: int, file_paths: list, original_names: list, order_id: int, folder_path: str = None):
        """Auto-attach order documents to patient profile."""
        try:
            import sqlite3
            from dmelogic.db.base import resolve_db_path
            
            patient_db_path = resolve_db_path("patients.db", folder_path=folder_path)
            conn = sqlite3.connect(patient_db_path)
            cur = conn.cursor()
            
            # Ensure table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS patient_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER NOT NULL,
                    description TEXT,
                    original_name TEXT,
                    stored_path TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            
            order_num = f"ORD-{order_id:03d}"
            
            for file_path, orig_name in zip(file_paths, original_names):
                # Check if already linked (avoid duplicates)
                cur.execute(
                    "SELECT id FROM patient_documents WHERE patient_id = ? AND stored_path = ?",
                    (patient_id, file_path)
                )
                if cur.fetchone():
                    continue
                
                description = f"From {order_num}"
                cur.execute(
                    "INSERT INTO patient_documents (patient_id, description, original_name, stored_path) VALUES (?, ?, ?, ?)",
                    (patient_id, description, orig_name, file_path)
                )
            
            conn.commit()
            conn.close()
            print(f"✅ Auto-attached {len(file_paths)} documents to patient {patient_id}")
            
        except Exception as e:
            print(f"⚠️ Failed to auto-attach documents to patient: {e}")

    def on_new_order_from_patients(self) -> None:
        """Start wizard using selected patient (if any) as context."""
        patient = None
        try:
            # If you have a patients_table model, pull name/dob/phone from current row
            model = self.patients_table.model()
            sel = self.patients_table.selectionModel()
            if sel.hasSelection():
                row = sel.currentIndex().row()
                # adjust column indices to your actual model
                last_name = model.index(row, 0).data() or ""
                first_name = model.index(row, 1).data() or ""
                dob = model.index(row, 2).data() or ""
                phone = model.index(row, 3).data() or ""
                
                # Try to get patient_id from Qt.UserRole stored in first column
                patient_id = 0
                first_item = self.patients_table.item(row, 0)
                if first_item:
                    stored_id = first_item.data(Qt.ItemDataRole.UserRole)
                    if stored_id is not None:
                        patient_id = int(stored_id)
                
                patient = {
                    "patient_id": patient_id,
                    "name": f"{last_name}, {first_name}".strip(", "),
                    "dob": dob,
                    "phone": phone,
                }
        except Exception:
            patient = None

        self.open_new_order_wizard(patient_context=patient)

    def on_create_order_from_rx(self) -> None:
        """
        Launch the order wizard using current document / OCR
        context to pre-fill RX & prescriber where possible.
        """
        rx_ctx = {}

        try:
            # If you already parse OCR into some fields, pull them here.
            # These are placeholders – wire to your real attributes later.
            if hasattr(self, "current_ocr_data") and self.current_ocr_data:
                data = self.current_ocr_data
                rx_ctx = {
                    "rx_date": data.get("rx_date", ""),  # Changed from rx_number to rx_date
                    "prescriber_name": data.get("prescriber_name", ""),
                    "prescriber_npi": data.get("prescriber_npi", ""),
                    "prescriber_phone": data.get("prescriber_phone", ""),
                }
        except Exception:
            rx_ctx = {}

        self.open_new_order_wizard(rx_context=rx_ctx)

    def setup_inventory_tab(self) -> QWidget:
        """
        Modern Inventory tab:
        - Compact header with search
        - Colored action buttons
        - Styled table
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # --- Header: title + search ---
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title = QLabel("Inventory Management System")
        title.setObjectName("inventoryTitle")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.inventory_search_edit = QLineEdit()
        self.inventory_search_edit.setPlaceholderText("Search inventory by name, HCPCS, or description…")
        self.inventory_search_edit.setClearButtonEnabled(True)
        self.inventory_search_edit.setMinimumWidth(260)
        header_layout.addWidget(self.inventory_search_edit)

        layout.addLayout(header_layout)

        # --- Action toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        def make_btn(text: str, color: str) -> QPushButton:
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("designRole", "inventoryAction")
            btn.setStyleSheet(
                f"""
                QPushButton[designRole="inventoryAction"] {{
                    background-color: {color};
                    color: #ffffff;
                    border-radius: 4px;
                    padding: 5px 12px;
                    font-weight: 500;
                }}
                QPushButton[designRole="inventoryAction"]:hover {{
                    background-color: {color};
                    border: 1px solid #ffffff;
                }}
                """
            )
            return btn

        self.btn_inventory_add = make_btn("Add Item", "#1E8E3E")          # green
        self.btn_inventory_edit = make_btn("Edit Item", "#1967D2")        # blue
        self.btn_inventory_delete = make_btn("Delete Item", "#D93025")    # red
        self.btn_inventory_duplicate = make_btn("Duplicate Item", "#5F6368")  # grey
        self.btn_inventory_reports = make_btn("Generate Reports", "#7B1FA2")
        self.btn_inventory_dashboard = make_btn("Reports Dashboard", "#512DA8")

        toolbar.addWidget(self.btn_inventory_add)
        toolbar.addWidget(self.btn_inventory_edit)
        toolbar.addWidget(self.btn_inventory_delete)
        toolbar.addWidget(self.btn_inventory_duplicate)
        toolbar.addWidget(self.btn_inventory_reports)
        toolbar.addWidget(self.btn_inventory_dashboard)
        toolbar.addStretch(1)

        layout.addLayout(toolbar)

        # --- Table ---
        # Reuse existing table or create new one
        if hasattr(self, 'inventory_table') and isinstance(self.inventory_table, QTableWidget):
            # Use existing table from legacy
            table = self.inventory_table
        else:
            # Create new table with proper column setup
            table = QTableWidget()
            self.inventory_table = table
            
            # Set up columns (12 columns for inventory)
            table.setColumnCount(12)
            table.setHorizontalHeaderLabels([
                "ITEM ID", "HCPCS", "DESCRIPTION", "CATEGORY", "COST", "BILL AMOUNT", 
                "BRAND", "SOURCE", "STOCK", "REORDER LEVEL", "LAST USED", "LAST RESTOCKED"
            ])
            
            # Set column widths
            header = table.horizontalHeader()
            header.resizeSection(0, 80)    # Item ID
            header.resizeSection(1, 180)   # HCPCS Code
            header.resizeSection(2, 300)   # Description
            header.resizeSection(3, 120)   # Category
            header.resizeSection(4, 80)    # Cost
            header.resizeSection(5, 100)   # Bill Amount
            header.resizeSection(6, 150)   # Brand
            header.resizeSection(7, 150)   # Source
            header.resizeSection(8, 80)    # Stock
            header.resizeSection(9, 120)   # Reorder Level
            header.resizeSection(10, 150)  # Last Used
            header.resizeSection(11, 150)  # Last Restocked
        
        table.setObjectName("inventoryTable")
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Enable double-click to edit
        if hasattr(self, 'edit_inventory_item'):
            table.itemDoubleClicked.connect(self.edit_inventory_item)

        layout.addWidget(table, 1)

        # --- Footer / status strip (low stock summary) ---
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(12)

        self.inventory_summary_label = QLabel("Ready • 0 inventory items")
        self.inventory_summary_label.setStyleSheet("color: #A0A0A0; font-size: 10px;")
        footer.addWidget(self.inventory_summary_label)

        footer.addStretch(1)

        self.inventory_low_stock_btn = QPushButton("⚠ Low Stock Report")
        self.inventory_low_stock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inventory_low_stock_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #F9AB00;
                color: #202124;
                border-radius: 4px;
                padding: 3px 10px;
                font-weight: 500;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #FBBC04;
                border: 1px solid #EA8600;
            }
            """
        )
        footer.addWidget(self.inventory_low_stock_btn)

        layout.addLayout(footer)

        # Wire interactions (using existing legacy methods)
        if hasattr(self, 'search_inventory'):
            self.inventory_search_edit.textChanged.connect(self.search_inventory)
        if hasattr(self, 'add_inventory_item'):
            self.btn_inventory_add.clicked.connect(self.add_inventory_item)
        if hasattr(self, 'edit_inventory_item'):
            self.btn_inventory_edit.clicked.connect(self.edit_inventory_item)
        if hasattr(self, 'delete_inventory_item'):
            self.btn_inventory_delete.clicked.connect(self.delete_inventory_item)
        if hasattr(self, 'duplicate_inventory_item'):
            self.btn_inventory_duplicate.clicked.connect(self.duplicate_inventory_item)
        if hasattr(self, 'open_inventory_reports_dialog'):
            self.btn_inventory_reports.clicked.connect(self.open_inventory_reports_dialog)
        if hasattr(self, 'open_reports_dashboard_standalone'):
            self.btn_inventory_dashboard.clicked.connect(self.open_reports_dashboard_standalone)
        if hasattr(self, 'show_low_stock_report'):
            self.inventory_low_stock_btn.clicked.connect(self.show_low_stock_report)
        return page

    def closeEvent(self, event):
        try:
            from dmelogic.ui.prefs import save_window_state
            save_window_state(self)
        except Exception:
            pass
        super().closeEvent(event)

    def update_inventory_summary(self) -> None:
        """Update the inventory summary label with item count and low stock info"""
        if not hasattr(self, 'inventory_table') or not self.inventory_table:
            if hasattr(self, 'inventory_summary_label'):
                self.inventory_summary_label.setText("Ready • 0 inventory items")
            return

        table = self.inventory_table
        total = table.rowCount()

        # Count low stock items (assuming STOCK is column 8, REORDER LEVEL is column 9)
        low_stock = 0
        try:
            for row in range(total):
                stock_item = table.item(row, 8)
                reorder_item = table.item(row, 9)
                if stock_item and reorder_item:
                    try:
                        stock = int(stock_item.text() or 0)
                        reorder = int(reorder_item.text() or 0)
                        if reorder > 0 and stock <= reorder:
                            low_stock += 1
                    except (ValueError, AttributeError):
                        pass
        except Exception:
            pass

        text = f"Loaded {total} inventory items"
        if low_stock:
            text += f" • ⚠ {low_stock} items low stock"

        if hasattr(self, 'inventory_summary_label'):
            self.inventory_summary_label.setText(text)


def get_main_window_class() -> Type[QMainWindow]:
    """Return the main window class used by the application."""
    return MainWindow


def create_main_window() -> QMainWindow:
    """Factory used by the new entrypoint (app.py)."""
    return MainWindow()
