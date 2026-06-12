"""
Rx Import Wizard
=================
Smart file-to-order import wizard with learning capabilities.

Upload Rx PDF(s) → Parse → Match Patient → Match Prescriber →
Match Items (with learning) → Review → Create Order

Feature: Smart Rx Import Pipeline

Integration:
    In MainWindow, add a menu/toolbar action:
    
        from dmelogic.ui.rx_import_wizard import RxImportWizard
        
        def open_rx_import(self):
            wizard = RxImportWizard(
                folder_path=getattr(self, 'folder_path', None),
                parent=self
            )
            wizard.order_created.connect(lambda oid: self.load_orders())
            wizard.exec()
        
        # Wire to menu:
        action = QAction("📥 Import Rx → Order", self)
        action.triggered.connect(self.open_rx_import)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QStackedWidget, QWidget, QFileDialog, QListWidget, QListWidgetItem,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QTextEdit, QGroupBox, QScrollArea, QSizePolicy,
    QProgressBar, QApplication, QCheckBox, QSplitter, QSlider,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QFont, QColor, QBrush, QPixmap, QImage

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

from dmelogic.services.rx_parser import RxParser, ParsedRx
from dmelogic.services.rx_matcher import RxMatcher, MatchResult, ItemMatchResult
from dmelogic.db.base import get_connection
from dmelogic.db.patients import search_patients
from dmelogic.db.prescribers import search_prescribers
from dmelogic.db.drug_mappings import DrugMapper


# ═══════════════════════════════════════════════════════════════════
# Step indicator bar
# ═══════════════════════════════════════════════════════════════════


class StepBar(QFrame):
    """Visual step indicator at the top of the wizard."""

    STEPS = ["Upload", "Patient", "Insurance", "Prescriber", "Items", "Review"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_step = 0
        self.setFixedHeight(52)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(4)

        self.step_labels: List[QLabel] = []
        for i, name in enumerate(self.STEPS):
            lbl = QLabel(f"  {i+1}. {name}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            layout.addWidget(lbl, 1)
            self.step_labels.append(lbl)

            if i < len(self.STEPS) - 1:
                arrow = QLabel("→")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setStyleSheet("color: #9CA3AF; font-size: 14px;")
                layout.addWidget(arrow)

        self._update_styles()

    def set_step(self, step: int):
        self.current_step = step
        self._update_styles()

    def _update_styles(self):
        for i, lbl in enumerate(self.step_labels):
            if i < self.current_step:
                lbl.setStyleSheet(
                    "background: #D1FAE5; color: #065F46; border-radius: 6px; padding: 4px 8px;"
                )
            elif i == self.current_step:
                lbl.setStyleSheet(
                    "background: #3B82F6; color: white; border-radius: 6px; padding: 4px 8px;"
                )
            else:
                lbl.setStyleSheet(
                    "background: #F3F4F6; color: #6B7280; border-radius: 6px; padding: 4px 8px;"
                )


# ═══════════════════════════════════════════════════════════════════
# Confirmation Card (reusable for patient/prescriber)
# ═══════════════════════════════════════════════════════════════════


class ConfirmationCard(QFrame):
    """Shows a matched record with accept/reject/add-new/search controls.
    
    Built-in DB search panel: call set_search_function(fn) where fn(term)->list[dict].
    When no match is found the search panel auto-shows.
    """

    confirmed = pyqtSignal(dict)     # user accepted this record
    add_new = pyqtSignal()           # user wants to add new
    search_again = pyqtSignal()      # user wants to search (legacy)
    search_selected = pyqtSignal(dict)  # user picked a record from search

    def __init__(self, title: str, placeholder: str = "Type name to search...", parent=None):
        super().__init__(parent)
        self.title_text = title
        self._record = None
        self._search_fn = None       # callable(term: str) -> list[dict]
        self._placeholder = placeholder
        self.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
        """)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.title_label = QLabel(self.title_text)
        self.title_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self.title_label.setStyleSheet("border: none; color: #111827;")
        layout.addWidget(self.title_label)

        self.status_label = QLabel("")
        self.status_label.setFont(QFont("Segoe UI", 10))
        self.status_label.setStyleSheet("border: none;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Rx data (what we parsed)
        rx_group = QGroupBox("📄 From Rx")
        rx_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #E5E7EB; border-radius: 6px; margin-top: 8px; padding-top: 16px; }")
        self.rx_info = QLabel("")
        self.rx_info.setWordWrap(True)
        self.rx_info.setStyleSheet("border: none; padding: 4px;")
        rx_layout = QVBoxLayout(rx_group)
        rx_layout.addWidget(self.rx_info)
        layout.addWidget(rx_group)

        # DB match data
        db_group = QGroupBox("🗄️ Database Match")
        db_group.setStyleSheet("QGroupBox { font-weight: bold; border: 1px solid #E5E7EB; border-radius: 6px; margin-top: 8px; padding-top: 16px; }")
        self.db_info = QLabel("Searching...")
        self.db_info.setWordWrap(True)
        self.db_info.setStyleSheet("border: none; padding: 4px;")
        db_layout = QVBoxLayout(db_group)
        db_layout.addWidget(self.db_info)
        layout.addWidget(db_group)

        # Candidates list (for fuzzy matches)
        self.candidates_label = QLabel("Other possibilities:")
        self.candidates_label.setStyleSheet("border: none; color: #6B7280; font-weight: bold;")
        self.candidates_label.setVisible(False)
        layout.addWidget(self.candidates_label)

        self.candidates_list = QListWidget()
        self.candidates_list.setMaximumHeight(120)
        self.candidates_list.setStyleSheet("border: 1px solid #E5E7EB; border-radius: 4px;")
        self.candidates_list.setVisible(False)
        self.candidates_list.itemDoubleClicked.connect(self._on_candidate_selected)
        layout.addWidget(self.candidates_list)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_confirm = QPushButton("✅ Confirm Match")
        self.btn_confirm.setStyleSheet(
            "background: #059669; color: white; border-radius: 6px; padding: 8px 20px; font-weight: 600; border: none;"
        )
        self.btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(self.btn_confirm)

        self.btn_add_new = QPushButton("➕ Add New")
        self.btn_add_new.setStyleSheet(
            "background: #3B82F6; color: white; border-radius: 6px; padding: 8px 20px; font-weight: 600; border: none;"
        )
        self.btn_add_new.clicked.connect(self.add_new.emit)
        btn_row.addWidget(self.btn_add_new)

        self.btn_search = QPushButton("🔍 Search Again")
        self.btn_search.setStyleSheet(
            "background: #6B7280; color: white; border-radius: 6px; padding: 8px 16px; border: none;"
        )
        self.btn_search.clicked.connect(self._toggle_search_panel)
        btn_row.addWidget(self.btn_search)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Built-in search panel (hidden by default) ──────────────
        self._search_frame = QFrame()
        self._search_frame.setStyleSheet(
            "QFrame { background: #F9FAFB; border: 1px solid #D1D5DB; border-radius: 8px; }"
        )
        sf_layout = QVBoxLayout(self._search_frame)
        sf_layout.setContentsMargins(12, 10, 12, 10)
        sf_layout.setSpacing(6)

        sf_title = QLabel("🔎 Search Database")
        sf_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        sf_title.setStyleSheet("border: none; color: #111827;")
        sf_layout.addWidget(sf_title)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(self._placeholder)
        self._search_input.setStyleSheet(
            "border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; font-size: 12px; background: white;"
        )
        self._search_input.returnPressed.connect(self._run_search)
        search_row.addWidget(self._search_input)

        btn_go = QPushButton("🔍 Search")
        btn_go.setStyleSheet(
            "background: #3B82F6; color: white; border-radius: 6px; padding: 8px 16px; "
            "font-weight: 600; border: none;"
        )
        btn_go.clicked.connect(self._run_search)
        search_row.addWidget(btn_go)
        sf_layout.addLayout(search_row)

        self._search_results = QListWidget()
        self._search_results.setMaximumHeight(180)
        self._search_results.setStyleSheet(
            "border: 1px solid #E5E7EB; border-radius: 4px; font-size: 11px; background: white;"
        )
        self._search_results.itemDoubleClicked.connect(self._on_search_item_dblclick)
        self._search_results.setVisible(False)
        sf_layout.addWidget(self._search_results)

        self._btn_use_selected = QPushButton("✅ Use Selected")
        self._btn_use_selected.setStyleSheet(
            "background: #059669; color: white; border-radius: 6px; padding: 8px 20px; "
            "font-weight: 600; border: none;"
        )
        self._btn_use_selected.clicked.connect(self._on_use_selected)
        self._btn_use_selected.setVisible(False)
        sf_layout.addWidget(self._btn_use_selected)

        self._search_frame.setVisible(False)
        layout.addWidget(self._search_frame)

    # ── Public API ─────────────────────────────────────────────────

    def set_search_function(self, fn):
        """Set the callable used to search: fn(term: str) -> list[dict]."""
        self._search_fn = fn

    def set_rx_data(self, text: str):
        self.rx_info.setText(text)

    def show_search_panel(self):
        """Programmatically reveal the search panel."""
        self._search_frame.setVisible(True)
        self._search_input.setFocus()

    def set_match_result(self, match: MatchResult):
        self._record = match.record

        # Status styling
        if match.found and match.confidence >= 0.9:
            self.status_label.setText(f"🟢 {match.message}")
            self.status_label.setStyleSheet("border: none; color: #059669; font-size: 11px;")
        elif match.found:
            self.status_label.setText(f"🟡 {match.message}")
            self.status_label.setStyleSheet("border: none; color: #D97706; font-size: 11px;")
        else:
            self.status_label.setText(f"🔴 {match.message}")
            self.status_label.setStyleSheet("border: none; color: #DC2626; font-size: 11px;")
            self.btn_confirm.setEnabled(False)
            self.btn_confirm.setStyleSheet(
                "background: #D1D5DB; color: #9CA3AF; border-radius: 6px; padding: 8px 20px; font-weight: 600; border: none;"
            )
            # Auto-show search panel when no match
            if self._search_fn:
                self._search_frame.setVisible(True)
                self._search_input.setFocus()

        # DB info
        if match.record:
            self.db_info.setText(self._format_record(match.record))
        else:
            self.db_info.setText("No match found in database.")

        # Candidates
        if match.candidates and len(match.candidates) > 1:
            self.candidates_label.setVisible(True)
            self.candidates_list.setVisible(True)
            self.candidates_list.clear()
            for c in match.candidates[:5]:
                name = f"{c.get('last_name', '')}, {c.get('first_name', '')}"
                extra = c.get('dob', '') or c.get('npi_number', '') or ''
                item = QListWidgetItem(f"{name}  —  {extra}")
                item.setData(Qt.ItemDataRole.UserRole, c)
                self.candidates_list.addItem(item)

    # ── Internal helpers ───────────────────────────────────────────

    def _format_record(self, rec: dict) -> str:
        lines = []
        for key in ["last_name", "first_name", "dob", "phone", "address",
                     "city", "state", "zip", "zip_code",
                     "npi_number", "npi", "title", "specialty",
                     "primary_insurance"]:
            val = rec.get(key)
            if val:
                label = key.replace("_", " ").title()
                lines.append(f"<b>{label}:</b> {val}")
        return "<br>".join(lines) if lines else "Record found (no details)"

    def _on_confirm(self):
        if self._record:
            self.confirmed.emit(self._record)

    def _on_candidate_selected(self, item):
        rec = item.data(Qt.ItemDataRole.UserRole)
        if rec:
            self._select_record(rec, "🟡 Selected from candidates — please confirm")

    def _toggle_search_panel(self):
        """Toggle search panel visibility (also emits legacy signal)."""
        visible = not self._search_frame.isVisible()
        self._search_frame.setVisible(visible)
        if visible:
            self._search_input.setFocus()
            self._search_input.selectAll()
        self.search_again.emit()

    def _run_search(self):
        """Execute the search callback and populate results."""
        if not self._search_fn:
            return
        term = self._search_input.text().strip()
        if not term:
            return
        results = self._search_fn(term)
        self._search_results.clear()
        self._search_results.setVisible(True)
        if not results:
            item = QListWidgetItem("No results found.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._search_results.addItem(item)
            self._btn_use_selected.setVisible(False)
            return
        for r in results:
            name = f"{r.get('last_name', '')}, {r.get('first_name', '')}"
            extra_parts = []
            if r.get('dob'):
                extra_parts.append(f"DOB: {r['dob']}")
            if r.get('npi_number') or r.get('npi'):
                extra_parts.append(f"NPI: {r.get('npi_number') or r.get('npi')}")
            if r.get('title'):
                extra_parts.append(r['title'])
            if r.get('phone'):
                extra_parts.append(f"Ph: {r['phone']}")
            if r.get('specialty'):
                extra_parts.append(r['specialty'])
            display = f"{name}   {'   '.join(extra_parts)}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self._search_results.addItem(item)
        self._btn_use_selected.setVisible(True)

    def _on_search_item_dblclick(self, item: QListWidgetItem):
        rec = item.data(Qt.ItemDataRole.UserRole)
        if rec:
            self._accept_search_result(rec)

    def _on_use_selected(self):
        item = self._search_results.currentItem()
        if item:
            rec = item.data(Qt.ItemDataRole.UserRole)
            if rec:
                self._accept_search_result(rec)

    def _accept_search_result(self, rec: dict):
        """Accept a record from the search results."""
        self._select_record(rec, f"🟢 Selected: {rec.get('last_name', '')}, {rec.get('first_name', '')}")
        self._search_frame.setVisible(False)
        self.search_selected.emit(rec)

    def _select_record(self, rec: dict, status_msg: str):
        """Update card UI to show the selected record."""
        self._record = rec
        self.db_info.setText(self._format_record(rec))
        self.btn_confirm.setEnabled(True)
        self.btn_confirm.setStyleSheet(
            "background: #059669; color: white; border-radius: 6px; padding: 8px 20px; font-weight: 600; border: none;"
        )
        self.status_label.setText(status_msg)
        if "🟡" in status_msg:
            self.status_label.setStyleSheet("border: none; color: #D97706; font-size: 11px;")
        else:
            self.status_label.setStyleSheet("border: none; color: #059669; font-size: 11px;")


# ═══════════════════════════════════════════════════════════════════
# Main Import Wizard
# ═══════════════════════════════════════════════════════════════════


class RxImportWizard(QDialog):
    """
    Smart Rx Import Wizard.
    
    Upload PDF(s) → Parse → Match Patient → Match Prescriber →
    Match Items → Review → Create Order
    
    Signals:
        order_created(int): Emitted with the new order ID after creation.
    """

    order_created = pyqtSignal(int)

    def __init__(self, folder_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.main_window = parent
        self.parser = RxParser()
        self.matcher = RxMatcher(folder_path=folder_path)

        # State
        self.parsed_rxs: List[ParsedRx] = []
        self.confirmed_patient: Optional[Dict] = None
        self.confirmed_prescriber: Optional[Dict] = None
        self.confirmed_insurance: Optional[Dict] = None   # {primary_insurance, policy_number, ...}
        self.no_insurance_override: bool = False
        self.confirmed_items: List[Dict] = []  # [{drug_name, hcpcs, description, qty, refills, ...}]
        self.icd_codes: List[str] = []
        self.rx_date: str = ""
        self.attachment_paths: List[str] = []

        self.setWindowTitle("📥 Smart Rx Import")
        self.setMinimumSize(1100, 680)
        self.resize(1300, 750)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Step bar
        self.step_bar = StepBar()
        main_layout.addWidget(self.step_bar)

        # Splitter: wizard steps (left) + Rx preview (right)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(4)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: #D1D5DB; border-radius: 2px; }"
        )

        # Content area
        self.stack = QStackedWidget()
        self._splitter.addWidget(self.stack)

        # Rx image preview panel
        self._preview_panel = self._build_preview_panel()
        self._splitter.addWidget(self._preview_panel)
        self._preview_panel.setVisible(False)  # hidden until files parsed

        self._splitter.setStretchFactor(0, 1)  # wizard gets 1/2
        self._splitter.setStretchFactor(1, 1)  # preview gets 1/2

        main_layout.addWidget(self._splitter, 1)

        # Build all step pages
        self.stack.addWidget(self._build_upload_page())      # 0
        self.stack.addWidget(self._build_patient_page())     # 1
        self.stack.addWidget(self._build_insurance_page())   # 2  ← NEW
        self.stack.addWidget(self._build_prescriber_page())  # 3
        self.stack.addWidget(self._build_items_page())       # 4
        self.stack.addWidget(self._build_review_page())      # 5

        # Bottom nav bar
        nav_bar = QFrame()
        nav_bar.setStyleSheet("background: #F9FAFB; border-top: 1px solid #E5E7EB;")
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(16, 10, 16, 10)

        self.btn_back = QPushButton("← Back")
        self.btn_back.setStyleSheet("background: #E5E7EB; color: #374151; border-radius: 6px; padding: 8px 20px; font-weight: 500;")
        self.btn_back.clicked.connect(self._go_back)
        nav_layout.addWidget(self.btn_back)

        nav_layout.addStretch()

        self._btn_show_preview = QPushButton("📄 Show Rx")
        self._btn_show_preview.setStyleSheet(
            "background: #F3F4F6; color: #374151; border-radius: 6px; "
            "padding: 8px 14px; font-weight: 500; border: 1px solid #D1D5DB;"
        )
        self._btn_show_preview.setToolTip("Toggle Rx image preview")
        self._btn_show_preview.clicked.connect(self._toggle_preview)
        self._btn_show_preview.setVisible(False)  # shown after files are loaded
        nav_layout.addWidget(self._btn_show_preview)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet("color: #6B7280; border: none; padding: 8px 16px;")
        self.btn_cancel.clicked.connect(self.reject)
        nav_layout.addWidget(self.btn_cancel)

        self.btn_next = QPushButton("Next →")
        self.btn_next.setStyleSheet("background: #3B82F6; color: white; border-radius: 6px; padding: 8px 24px; font-weight: 600;")
        self.btn_next.clicked.connect(self._go_next)
        nav_layout.addWidget(self.btn_next)

        main_layout.addWidget(nav_bar)

        self._update_nav()

    # ================================================================
    # Rx Image Preview Panel
    # ================================================================

    def _build_preview_panel(self) -> QFrame:
        """Build the persistent Rx image preview panel."""
        panel = QFrame()
        panel.setMinimumWidth(300)
        panel.setStyleSheet("background: #FFFFFF; border-left: 1px solid #E5E7EB;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        header.setSpacing(6)
        hdr_label = QLabel("📄 Rx Preview")
        hdr_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        hdr_label.setStyleSheet("border: none; color: #111827;")
        header.addWidget(hdr_label)
        header.addStretch()

        self._btn_preview_close = QPushButton("✕")
        self._btn_preview_close.setFixedSize(24, 24)
        self._btn_preview_close.setStyleSheet(
            "background: #E5E7EB; color: #374151; border-radius: 12px; "
            "font-weight: bold; font-size: 12px; border: none;"
        )
        self._btn_preview_close.setToolTip("Hide preview")
        self._btn_preview_close.clicked.connect(self._toggle_preview)
        header.addWidget(self._btn_preview_close)
        layout.addLayout(header)

        # Zoom slider
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(4)
        zoom_minus = QLabel("−")
        zoom_minus.setStyleSheet("border: none; color: #6B7280; font-size: 14px; font-weight: bold;")
        zoom_row.addWidget(zoom_minus)
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(50, 300)
        self._zoom_slider.setValue(150)
        self._zoom_slider.setFixedWidth(120)
        self._zoom_slider.setStyleSheet(
            "QSlider::groove:horizontal { background: #E5E7EB; height: 4px; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #3B82F6; width: 14px; height: 14px; "
            "margin: -5px 0; border-radius: 7px; }"
        )
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self._zoom_slider)
        zoom_plus = QLabel("+")
        zoom_plus.setStyleSheet("border: none; color: #6B7280; font-size: 14px; font-weight: bold;")
        zoom_row.addWidget(zoom_plus)
        self._zoom_label = QLabel("150%")
        self._zoom_label.setStyleSheet("border: none; color: #6B7280; font-size: 10px;")
        self._zoom_label.setFixedWidth(40)
        zoom_row.addWidget(self._zoom_label)
        zoom_row.addStretch()
        layout.addLayout(zoom_row)

        # Scrollable image area
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setStyleSheet(
            "QScrollArea { border: 1px solid #E5E7EB; border-radius: 6px; background: #F3F4F6; }"
        )
        self._preview_image_label = QLabel("No Rx loaded")
        self._preview_image_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self._preview_image_label.setStyleSheet("border: none; color: #9CA3AF; background: #F3F4F6; padding: 20px;")
        self._preview_image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._preview_scroll.setWidget(self._preview_image_label)
        layout.addWidget(self._preview_scroll, 1)

        # Page navigation
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self._btn_prev_page = QPushButton("◀")
        self._btn_prev_page.setFixedSize(32, 28)
        self._btn_prev_page.setStyleSheet(
            "background: #E5E7EB; color: #374151; border-radius: 4px; font-weight: bold; border: none;"
        )
        self._btn_prev_page.clicked.connect(self._prev_preview_page)
        nav_row.addWidget(self._btn_prev_page)

        self._page_label = QLabel("0 / 0")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setStyleSheet("border: none; color: #374151; font-size: 11px; font-weight: 600;")
        nav_row.addWidget(self._page_label, 1)

        self._btn_next_page = QPushButton("▶")
        self._btn_next_page.setFixedSize(32, 28)
        self._btn_next_page.setStyleSheet(
            "background: #E5E7EB; color: #374151; border-radius: 4px; font-weight: bold; border: none;"
        )
        self._btn_next_page.clicked.connect(self._next_preview_page)
        nav_row.addWidget(self._btn_next_page)
        layout.addLayout(nav_row)

        # Internal state
        self._preview_pages: list = []  # list of QPixmap
        self._preview_page_idx = 0
        self._preview_zoom = 150  # percent

        return panel

    def _toggle_preview(self):
        """Toggle the Rx preview panel visibility."""
        visible = self._preview_panel.isVisible()
        self._preview_panel.setVisible(not visible)

    def _load_preview(self):
        """Render all pages from all uploaded PDFs into preview pixmaps."""
        self._preview_pages = []
        self._preview_page_idx = 0

        if not HAS_FITZ or not self.attachment_paths:
            return

        for path in self.attachment_paths:
            try:
                doc = fitz.open(path)
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    # Render at 150 DPI for good quality
                    mat = fitz.Matrix(150 / 72, 150 / 72)
                    pix = page.get_pixmap(matrix=mat)
                    img = QImage(pix.samples, pix.width, pix.height,
                                 pix.stride, QImage.Format.Format_RGB888)
                    # Deep-copy so QImage doesn't hold a reference to fitz's buffer
                    img = img.copy()
                    pixmap = QPixmap.fromImage(img)
                    self._preview_pages.append(pixmap)
                doc.close()
                del doc
            except Exception as e:
                print(f"⚠️ Preview error for {os.path.basename(path)}: {e}")

        if self._preview_pages:
            self._preview_panel.setVisible(True)
            self._btn_show_preview.setVisible(True)
            self._show_preview_page(0)
            # Force true 50/50 split
            total = self._splitter.width()
            half = total // 2
            self._splitter.setSizes([half, half])
        else:
            self._preview_panel.setVisible(False)

    def _show_preview_page(self, idx: int):
        """Display a specific page in the preview panel."""
        if not self._preview_pages or idx < 0 or idx >= len(self._preview_pages):
            return
        self._preview_page_idx = idx
        pixmap = self._preview_pages[idx]

        # Scale by zoom factor, fit to panel width
        panel_w = self._preview_scroll.viewport().width() - 10
        scale = self._preview_zoom / 100.0
        target_w = int(panel_w * scale)
        scaled = pixmap.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)

        self._preview_image_label.setPixmap(scaled)
        self._preview_image_label.setMinimumSize(scaled.size())
        self._preview_image_label.resize(scaled.size())

        total = len(self._preview_pages)
        self._page_label.setText(f"{idx + 1} / {total}")
        self._btn_prev_page.setEnabled(idx > 0)
        self._btn_next_page.setEnabled(idx < total - 1)

    def _prev_preview_page(self):
        if self._preview_page_idx > 0:
            self._show_preview_page(self._preview_page_idx - 1)

    def _next_preview_page(self):
        if self._preview_page_idx < len(self._preview_pages) - 1:
            self._show_preview_page(self._preview_page_idx + 1)

    def _release_preview(self):
        """Release all preview resources and file handles before moving files."""
        import gc
        self._preview_pages.clear()
        self._preview_page_idx = 0
        self._preview_image_label.setPixmap(QPixmap())
        self._preview_image_label.clear()
        self._preview_panel.setVisible(False)
        gc.collect()

    def _on_zoom_changed(self, value: int):
        self._preview_zoom = value
        self._zoom_label.setText(f"{value}%")
        if self._preview_pages:
            self._show_preview_page(self._preview_page_idx)

    # ================================================================
    # STEP 0: Upload
    # ================================================================

    def _build_upload_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #F9FAFB;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("📥 Upload Rx Files")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        layout.addWidget(title)

        desc = QLabel("Drop in one or more Rx PDF files. Each will be parsed for patient, prescriber, and item data.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(desc)

        # Drop zone
        drop_zone = QFrame()
        drop_zone.setMinimumHeight(140)
        drop_zone.setStyleSheet("""
            QFrame {
                background: white;
                border: 2px dashed #93C5FD;
                border-radius: 12px;
            }
            QFrame:hover {
                border-color: #3B82F6;
                background: #EFF6FF;
            }
        """)
        drop_layout = QVBoxLayout(drop_zone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        drop_icon = QLabel("📄")
        drop_icon.setFont(QFont("Segoe UI Emoji", 32))
        drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_icon)

        self.btn_browse = QPushButton("Browse Files...")
        self.btn_browse.setStyleSheet(
            "background: #3B82F6; color: white; border-radius: 6px; padding: 8px 24px; font-weight: 600; font-size: 12px;"
        )
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.clicked.connect(self._browse_files)
        drop_layout.addWidget(self.btn_browse, alignment=Qt.AlignmentFlag.AlignCenter)

        drop_hint = QLabel("Supports: PDF files from pharmacy e-Rx systems")
        drop_hint.setStyleSheet("color: #9CA3AF; font-size: 10px;")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_hint)

        layout.addWidget(drop_zone)

        # File list
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(120)
        self.file_list.setStyleSheet("background: white; border: 1px solid #E5E7EB; border-radius: 6px;")
        layout.addWidget(self.file_list)

        # Parse status
        self.parse_status = QLabel("")
        self.parse_status.setStyleSheet("color: #059669; font-weight: 500;")
        layout.addWidget(self.parse_status)

        layout.addStretch()
        return page

    def _browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Rx PDF Files", "",
            "PDF Files (*.pdf);;All Files (*)"
        )
        if files:
            self.attachment_paths = files
            self.file_list.clear()
            for f in files:
                self.file_list.addItem(os.path.basename(f))
            self._parse_files()

    def _parse_files(self):
        """Parse all uploaded files."""
        self.parsed_rxs = []
        self.parse_status.setText("⏳ Parsing files...")
        QApplication.processEvents()

        for path in self.attachment_paths:
            try:
                results = self.parser.parse_pdf(path)
                self.parsed_rxs.extend(results)
            except Exception as e:
                self.parse_status.setText(f"❌ Error parsing {os.path.basename(path)}: {e}")
                return

        if self.parsed_rxs:
            n = len(self.parsed_rxs)
            patient = self.parsed_rxs[0].patient
            self.parse_status.setText(
                f"✅ Found {n} prescription{'s' if n > 1 else ''} "
                f"for {patient.last_name}, {patient.first_name} "
                f"(DOB: {patient.dob})"
            )

            # Load Rx preview panel
            self._load_preview()
            # Collect ICD codes and Rx date from all blocks
            self.icd_codes = []
            for rx in self.parsed_rxs:
                self.icd_codes.extend(rx.icd_codes)
            self.icd_codes = list(dict.fromkeys(self.icd_codes))  # unique, preserve order

            self.rx_date = self.parsed_rxs[0].rx_date
        else:
            self.parse_status.setText("⚠️ No prescriptions found in file. Check the PDF format.")

    # ================================================================
    # STEP 1: Patient
    # ================================================================

    def _build_patient_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #F9FAFB;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        title = QLabel("👤 Patient Confirmation")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        layout.addWidget(title)

        self.patient_card = ConfirmationCard("Patient", placeholder="Type patient name to search...")
        self.patient_card.set_search_function(lambda term: search_patients(term, folder_path=self.folder_path))
        self.patient_card.confirmed.connect(self._on_patient_confirmed)
        self.patient_card.add_new.connect(self._on_add_new_patient)
        self.patient_card.search_selected.connect(self._on_patient_search_selected)
        layout.addWidget(self.patient_card)
        layout.addStretch()
        return page

    def _run_patient_match(self):
        if not self.parsed_rxs:
            return
        patient = self.parsed_rxs[0].patient
        self.patient_card.set_rx_data(
            f"<b>Name:</b> {patient.last_name}, {patient.first_name}<br>"
            f"<b>DOB:</b> {patient.dob}<br>"
            f"<b>Gender:</b> {patient.gender}<br>"
            f"<b>Address:</b> {patient.address}<br>"
            f"<b>Phone:</b> {patient.phone}"
        )
        result = self.matcher.match_patient(patient)
        self.patient_card.set_match_result(result)
        
        # Auto-confirm high confidence matches
        if result.found and result.confidence >= 0.95:
            self.confirmed_patient = result.record

    def _on_patient_search_selected(self, rec: dict):
        """Patient selected from built-in search panel."""
        self.confirmed_patient = rec
        self._go_next()

    def _on_patient_confirmed(self, record: dict):
        self.confirmed_patient = record
        self._go_next()

    def _on_add_new_patient(self):
        """Insert parsed patient into patients.db and confirm."""
        if not self.parsed_rxs:
            return
        patient = self.parsed_rxs[0].patient
        try:
            conn = get_connection("patients.db", folder_path=self.folder_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO patients (last_name, first_name, dob, gender, phone, address, city, state, zip)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                patient.last_name.upper(),
                patient.first_name.upper(),
                patient.dob,
                patient.gender,
                patient.phone,
                patient.address,
                patient.city,
                patient.state,
                patient.zip_code,
            ))
            conn.commit()
            new_id = cur.lastrowid
            conn.close()

            self.confirmed_patient = {
                "id": new_id,
                "last_name": patient.last_name.upper(),
                "first_name": patient.first_name.upper(),
                "dob": patient.dob,
                "phone": patient.phone,
            }
            QMessageBox.information(self, "Patient Added",
                f"✅ Added: {patient.last_name}, {patient.first_name}")
            self._go_next()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add patient:\n{e}")

    # ================================================================
    # STEP 2: Insurance
    # ================================================================

    def _build_insurance_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #F9FAFB;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        title = QLabel("🏥 Insurance Information")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        layout.addWidget(title)

        desc = QLabel("Verify or enter the patient's insurance information. This will be used for billing.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(desc)

        # Insurance card
        ins_frame = QFrame()
        ins_frame.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
        """)
        ins_layout = QVBoxLayout(ins_frame)
        ins_layout.setContentsMargins(16, 16, 16, 16)
        ins_layout.setSpacing(10)

        # Status label (shows if insurance pulled from patient DB)
        self.ins_status_label = QLabel("")
        self.ins_status_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.ins_status_label.setStyleSheet("border: none;")
        ins_layout.addWidget(self.ins_status_label)

        # Primary Insurance
        lbl1 = QLabel("Primary Insurance:")
        lbl1.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        lbl1.setStyleSheet("border: none; color: #374151;")
        ins_layout.addWidget(lbl1)
        self.ins_primary = QLineEdit()
        self.ins_primary.setPlaceholderText("e.g. MEDICAID, HEALTH FIRST MEDICARE")
        self.ins_primary.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; font-size: 12px;")
        ins_layout.addWidget(self.ins_primary)

        # Policy / Member ID
        lbl2 = QLabel("Policy / Member ID:")
        lbl2.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        lbl2.setStyleSheet("border: none; color: #374151;")
        ins_layout.addWidget(lbl2)
        self.ins_policy = QLineEdit()
        self.ins_policy.setPlaceholderText("Policy or Member ID number")
        self.ins_policy.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; font-size: 12px;")
        ins_layout.addWidget(self.ins_policy)

        # Group Number
        lbl3 = QLabel("Group Number:")
        lbl3.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        lbl3.setStyleSheet("border: none; color: #374151;")
        ins_layout.addWidget(lbl3)
        self.ins_group = QLineEdit()
        self.ins_group.setPlaceholderText("Group number (optional)")
        self.ins_group.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; font-size: 12px;")
        ins_layout.addWidget(self.ins_group)

        # Secondary Insurance (optional)
        lbl4 = QLabel("Secondary Insurance (optional):")
        lbl4.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        lbl4.setStyleSheet("border: none; color: #6B7280;")
        ins_layout.addWidget(lbl4)
        self.ins_secondary = QLineEdit()
        self.ins_secondary.setPlaceholderText("Secondary insurance (if applicable)")
        self.ins_secondary.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; font-size: 12px;")
        ins_layout.addWidget(self.ins_secondary)

        self.ins_secondary_id = QLineEdit()
        self.ins_secondary_id.setPlaceholderText("Secondary policy / member ID")
        self.ins_secondary_id.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; font-size: 12px;")
        ins_layout.addWidget(self.ins_secondary_id)

        layout.addWidget(ins_frame)

        # No-insurance override
        self.chk_no_insurance = QCheckBox("⏳ No insurance available — Pend order for insurance follow-up")
        self.chk_no_insurance.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.chk_no_insurance.setStyleSheet("color: #DC2626; padding: 6px;")
        self.chk_no_insurance.toggled.connect(self._on_no_insurance_toggled)
        layout.addWidget(self.chk_no_insurance)

        # Confirm button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_ins_confirm = QPushButton("✅ Confirm Insurance")
        self.btn_ins_confirm.setStyleSheet(
            "background: #059669; color: white; border-radius: 6px; padding: 10px 28px; font-weight: 600; font-size: 12px; border: none;"
        )
        self.btn_ins_confirm.clicked.connect(self._on_insurance_confirmed)
        btn_row.addWidget(self.btn_ins_confirm)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        return page

    def _on_no_insurance_toggled(self, checked: bool):
        """Toggle input fields enabled/disabled when no-insurance is checked."""
        for field in (self.ins_primary, self.ins_policy, self.ins_group,
                      self.ins_secondary, self.ins_secondary_id):
            field.setEnabled(not checked)
        if checked:
            self.ins_status_label.setText("⚠️ Order will be placed On Hold — insurance needed")
            self.ins_status_label.setStyleSheet("border: none; color: #DC2626;")
        else:
            self._populate_insurance_status()

    def _populate_insurance_fields(self):
        """Auto-fill insurance from confirmed patient's DB record."""
        self.chk_no_insurance.setChecked(False)
        self.no_insurance_override = False

        # Clear fields
        for field in (self.ins_primary, self.ins_policy, self.ins_group,
                      self.ins_secondary, self.ins_secondary_id):
            field.clear()
            field.setEnabled(True)

        if not self.confirmed_patient:
            self.ins_status_label.setText("⚠️ No patient selected")
            self.ins_status_label.setStyleSheet("border: none; color: #DC2626;")
            return

        patient_id = self.confirmed_patient.get("id")
        if not patient_id:
            self._populate_insurance_status()
            return

        # Look up insurance from patients.db
        try:
            from dmelogic.db import get_connection
            conn = get_connection("patients.db", folder_path=self.folder_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT primary_insurance, policy_number, group_number, "
                "secondary_insurance, secondary_insurance_id "
                "FROM patients WHERE id = ?", (patient_id,)
            )
            row = cur.fetchone()
            conn.close()

            if row:
                if row[0]:
                    self.ins_primary.setText(str(row[0]))
                if row[1]:
                    self.ins_policy.setText(str(row[1]))
                if row[2]:
                    self.ins_group.setText(str(row[2]))
                if row[3]:
                    self.ins_secondary.setText(str(row[3]))
                if row[4]:
                    self.ins_secondary_id.setText(str(row[4]))
        except Exception as e:
            print(f"⚠️ Could not load insurance: {e}")

        self._populate_insurance_status()

    def _populate_insurance_status(self):
        """Update the insurance status label based on current field values."""
        has_ins = bool(self.ins_primary.text().strip())
        if has_ins:
            self.ins_status_label.setText("✅ Insurance on File")
            self.ins_status_label.setStyleSheet("border: none; color: #059669;")
        else:
            self.ins_status_label.setText("⚠️ No insurance on file — enter below or check override")
            self.ins_status_label.setStyleSheet("border: none; color: #D97706;")

    def _on_insurance_confirmed(self):
        """Confirm the insurance and advance."""
        if self.chk_no_insurance.isChecked():
            self.confirmed_insurance = None
            self.no_insurance_override = True
            self._go_next()
            return

        primary = self.ins_primary.text().strip()
        if not primary:
            QMessageBox.warning(
                self, "Insurance Required",
                "Please enter primary insurance information,\n"
                "or check the override to pend the order."
            )
            return

        self.confirmed_insurance = {
            "primary_insurance": primary.upper(),
            "policy_number": self.ins_policy.text().strip(),
            "group_number": self.ins_group.text().strip(),
            "secondary_insurance": self.ins_secondary.text().strip().upper(),
            "secondary_insurance_id": self.ins_secondary_id.text().strip(),
        }
        self.no_insurance_override = False

        # Update patient record in DB with insurance if it was new
        if self.confirmed_patient and self.confirmed_patient.get("id"):
            try:
                from dmelogic.db import get_connection
                conn = get_connection("patients.db", folder_path=self.folder_path)
                cur = conn.cursor()
                cur.execute(
                    "UPDATE patients SET primary_insurance=?, policy_number=?, group_number=?, "
                    "secondary_insurance=?, secondary_insurance_id=? WHERE id=?",
                    (
                        self.confirmed_insurance["primary_insurance"],
                        self.confirmed_insurance["policy_number"],
                        self.confirmed_insurance["group_number"],
                        self.confirmed_insurance["secondary_insurance"],
                        self.confirmed_insurance["secondary_insurance_id"],
                        self.confirmed_patient["id"],
                    )
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"⚠️ Could not update patient insurance: {e}")

        self._go_next()

    # ================================================================
    # STEP 3: Prescriber
    # ================================================================

    def _build_prescriber_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #F9FAFB;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        title = QLabel("🩺 Prescriber Confirmation")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        layout.addWidget(title)

        self.prescriber_card = ConfirmationCard("Prescriber", placeholder="Type prescriber name or NPI to search...")
        self.prescriber_card.set_search_function(lambda term: search_prescribers(term, folder_path=self.folder_path))
        self.prescriber_card.confirmed.connect(self._on_prescriber_confirmed)
        self.prescriber_card.add_new.connect(self._on_add_new_prescriber)
        self.prescriber_card.search_selected.connect(self._on_prescriber_search_selected)
        layout.addWidget(self.prescriber_card)
        layout.addStretch()
        return page

    def _run_prescriber_match(self):
        if not self.parsed_rxs:
            return
        pres = self.parsed_rxs[0].prescriber
        self.prescriber_card.set_rx_data(
            f"<b>Name:</b> {pres.full_name} ({pres.title})<br>"
            f"<b>NPI:</b> {pres.npi}<br>"
            f"<b>Phone:</b> {pres.phone}<br>"
            f"<b>Fax:</b> {pres.fax}<br>"
            f"<b>Address:</b> {pres.address}"
        )
        result = self.matcher.match_prescriber(pres)
        self.prescriber_card.set_match_result(result)
        
        if result.found and result.confidence >= 0.95:
            self.confirmed_prescriber = result.record

    def _on_prescriber_search_selected(self, rec: dict):
        """Prescriber selected from built-in search panel."""
        self.confirmed_prescriber = rec
        self._go_next()

    def _on_prescriber_confirmed(self, record: dict):
        self.confirmed_prescriber = record
        self._go_next()

    def _on_add_new_prescriber(self):
        """Insert parsed prescriber into prescribers.db and confirm."""
        if not self.parsed_rxs:
            return
        pres = self.parsed_rxs[0].prescriber
        try:
            conn = get_connection("prescribers.db", folder_path=self.folder_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO prescribers (
                    first_name, last_name, title, npi_number, phone, fax,
                    practice_name, address_line1, city, state, zip_code, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')
            """, (
                pres.first_name.upper(),
                pres.last_name.upper(),
                pres.title,
                pres.npi,
                pres.phone,
                pres.fax,
                pres.practice_name,
                pres.address,
                pres.city,
                pres.state,
                pres.zip_code,
            ))
            conn.commit()
            new_id = cur.lastrowid
            conn.close()

            self.confirmed_prescriber = {
                "id": new_id,
                "first_name": pres.first_name.upper(),
                "last_name": pres.last_name.upper(),
                "npi_number": pres.npi,
                "title": pres.title,
            }
            QMessageBox.information(self, "Prescriber Added",
                f"✅ Added: {pres.full_name} (NPI: {pres.npi})")
            self._go_next()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add prescriber:\n{e}")

    # ================================================================
    # STEP 3: Items (Learning)
    # ================================================================

    def _build_items_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #F9FAFB;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        title = QLabel("📦 Item Matching")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        layout.addWidget(title)

        desc = QLabel("Match each Rx item to your inventory. Confirmed matches are learned for next time.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(desc)

        # Items table
        self.items_table = QTableWidget()
        self.items_table.setColumnCount(9)
        self.items_table.setHorizontalHeaderLabels([
            "Status", "Drug (from Rx)", "HCPCS", "Description (Inventory)", "Qty", "Days", "Refills", "Action", "Remove"
        ])
        header = self.items_table.horizontalHeader()
        header.resizeSection(0, 60)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(2, 100)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(4, 60)
        header.resizeSection(5, 60)
        header.resizeSection(6, 60)
        header.resizeSection(7, 100)
        header.resizeSection(8, 70)
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setStyleSheet("""
            QTableWidget { background: white; border: 1px solid #E5E7EB; border-radius: 6px; }
            QHeaderView::section { background: #F3F4F6; border: none; padding: 8px; font-weight: 600; }
        """)
        layout.addWidget(self.items_table, 1)

        # Add Item button
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        btn_add_item = QPushButton("➕ Add Item")
        btn_add_item.setStyleSheet(
            "background: #3B82F6; color: white; border-radius: 6px; "
            "padding: 8px 20px; font-weight: 600; font-size: 11px; border: none;"
        )
        btn_add_item.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_item.clicked.connect(self._add_blank_item_row)
        add_row.addWidget(btn_add_item)
        add_row.addStretch()
        layout.addLayout(add_row)

        # Learning stats
        self.learning_label = QLabel("")
        self.learning_label.setStyleSheet("color: #6B7280; font-size: 10px;")
        layout.addWidget(self.learning_label)

        return page

    def _run_items_match(self):
        """Match all parsed items against inventory."""
        self.confirmed_items = []
        self.items_table.setRowCount(0)

        for rx in self.parsed_rxs:
            item = rx.item
            if not item.drug_name:
                continue

            match = self.matcher.match_item(item)
            row = self.items_table.rowCount()
            self.items_table.insertRow(row)

            # Status icon
            if match.found and match.confidence >= 0.8:
                status = "✅"
            elif match.found:
                status = "🟡"
            else:
                status = "❌"

            self.items_table.setItem(row, 0, QTableWidgetItem(status))
            self.items_table.setItem(row, 1, QTableWidgetItem(item.drug_name))

            # HCPCS — editable
            hcpcs_item = QTableWidgetItem(match.hcpcs if match.found else "")
            hcpcs_item.setFlags(hcpcs_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 2, hcpcs_item)

            # Description
            desc_item = QTableWidgetItem(match.description if match.found else "")
            self.items_table.setItem(row, 3, desc_item)

            # Qty, Days Supply, Refills
            self.items_table.setItem(row, 4, QTableWidgetItem(str(int(item.quantity))))
            days_val = str(item.days_supply) if item.days_supply else "30"
            days_item = QTableWidgetItem(days_val)
            days_item.setFlags(days_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 5, days_item)
            self.items_table.setItem(row, 6, QTableWidgetItem(str(item.refills)))

            # Action button
            if not match.found or match.confidence < 0.8:
                btn = QPushButton("🔍 Search")
                btn.setStyleSheet("background: #F59E0B; color: white; border-radius: 4px; padding: 4px 10px; font-size: 10px; font-weight: 600;")
                btn.clicked.connect(lambda checked, r=row, m=match: self._open_item_search(r, m))
                self.items_table.setCellWidget(row, 7, btn)
            else:
                confirm_btn = QPushButton("✅ OK")
                confirm_btn.setStyleSheet("background: #059669; color: white; border-radius: 4px; padding: 4px 10px; font-size: 10px;")
                confirm_btn.setEnabled(False)
                self.items_table.setCellWidget(row, 7, confirm_btn)

            # Remove button
            rm_btn = QPushButton("\u2715 Remove")
            rm_btn.setStyleSheet("background: #EF4444; color: white; border-radius: 4px; padding: 4px 8px; font-size: 10px; font-weight: 600;")
            rm_btn.clicked.connect(lambda checked, r=row: self._remove_item_row(r))
            self.items_table.setCellWidget(row, 8, rm_btn)

            # Store match data (including fee schedule pricing)
            self.items_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, {
                "drug_name": item.drug_name,
                "match": match,
                "quantity": int(item.quantity),
                "refills": item.refills,
                "days_supply": item.days_supply,
                "directions": item.directions,
                "retail_price": match.retail_price,
                "item_number": match.item_number,
                "fee": match.fee,
                "rental_fee": match.rental_fee,
                "max_units": match.max_units,
                "pa_required": match.pa_required,
            })

        # Show learning stats
        all_mappings = self.matcher.drug_mapper.get_all_mappings()
        self.learning_label.setText(
            f"🧠 Learning database: {len(all_mappings)} drug→HCPCS mappings stored"
        )

    def _remove_item_row(self, row: int):
        """Remove an item row from the items table and rebind remaining remove buttons."""
        if row < 0 or row >= self.items_table.rowCount():
            return
        drug = (self.items_table.item(row, 1) or QTableWidgetItem("")).text().strip()
        self.items_table.removeRow(row)
        # Rebind all remove buttons after row indices shift
        for r in range(self.items_table.rowCount()):
            rm_btn = QPushButton("\u2715 Remove")
            rm_btn.setStyleSheet("background: #EF4444; color: white; border-radius: 4px; padding: 4px 8px; font-size: 10px; font-weight: 600;")
            rm_btn.clicked.connect(lambda checked, rr=r: self._remove_item_row(rr))
            self.items_table.setCellWidget(r, 8, rm_btn)

    def _add_blank_item_row(self):
        """Add a blank editable row so the user can manually enter an item."""
        row = self.items_table.rowCount()
        self.items_table.insertRow(row)

        # Status — pending
        status_item = QTableWidgetItem("⬜")
        self.items_table.setItem(row, 0, status_item)

        # Drug name — editable
        drug_item = QTableWidgetItem("")
        drug_item.setFlags(drug_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items_table.setItem(row, 1, drug_item)

        # HCPCS — editable
        hcpcs_item = QTableWidgetItem("")
        hcpcs_item.setFlags(hcpcs_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items_table.setItem(row, 2, hcpcs_item)

        # Description — editable
        desc_item = QTableWidgetItem("")
        desc_item.setFlags(desc_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items_table.setItem(row, 3, desc_item)

        # Qty — editable, default 1
        qty_item = QTableWidgetItem("1")
        qty_item.setFlags(qty_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items_table.setItem(row, 4, qty_item)

        # Days — editable, default 30
        days_item = QTableWidgetItem("30")
        days_item.setFlags(days_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items_table.setItem(row, 5, days_item)

        # Refills — editable, default 0
        refills_item = QTableWidgetItem("0")
        refills_item.setFlags(refills_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.items_table.setItem(row, 6, refills_item)

        # Search button
        from dmelogic.services.rx_matcher import ItemMatchResult
        blank_match = ItemMatchResult(hcpcs="", drug_name="")
        btn = QPushButton("🔍 Search")
        btn.setStyleSheet(
            "background: #F59E0B; color: white; border-radius: 4px; "
            "padding: 4px 10px; font-size: 10px; font-weight: 600;"
        )
        btn.clicked.connect(lambda checked, r=row, m=blank_match: self._open_item_search(r, m))
        self.items_table.setCellWidget(row, 7, btn)

        # Remove button
        rm_btn = QPushButton("\u2715 Remove")
        rm_btn.setStyleSheet(
            "background: #EF4444; color: white; border-radius: 4px; "
            "padding: 4px 8px; font-size: 10px; font-weight: 600;"
        )
        rm_btn.clicked.connect(lambda checked, r=row: self._remove_item_row(r))
        self.items_table.setCellWidget(row, 8, rm_btn)

        # Store blank data
        status_item.setData(Qt.ItemDataRole.UserRole, {
            "drug_name": "",
            "match": blank_match,
            "quantity": 1,
            "refills": 0,
            "days_supply": 30,
            "directions": "",
            "retail_price": 0.0,
            "item_number": "",
            "fee": 0.0,
            "rental_fee": 0.0,
            "max_units": 0,
            "pa_required": "",
        })

        # Scroll to new row and start editing the drug name
        self.items_table.scrollToItem(drug_item)
        self.items_table.setCurrentItem(drug_item)
        self.items_table.editItem(drug_item)

    def _open_item_search(self, row: int, match: ItemMatchResult):
        """Open inventory search dialog for a specific item."""
        try:
            from dmelogic.ui.inventory_search_dialog import InventorySearchDialog
            
            dlg = InventorySearchDialog(self)
            dlg.set_initial_query(match.drug_name[:30] if match.drug_name else "")
            
            if dlg.exec() == QDialog.DialogCode.Accepted:
                selected = dlg.get_selected_item()
                if selected:
                    hcpcs = selected.get("hcpcs_code") or ""
                    desc = selected.get("description") or ""
                    drug_name = match.drug_name

                    # Update table
                    self.items_table.item(row, 0).setText("✅")
                    self.items_table.item(row, 2).setText(hcpcs)
                    self.items_table.item(row, 3).setText(desc)

                    # Replace action button
                    ok_btn = QPushButton("✅ OK")
                    ok_btn.setStyleSheet("background: #059669; color: white; border-radius: 4px; padding: 4px 10px; font-size: 10px;")
                    ok_btn.setEnabled(False)
                    self.items_table.setCellWidget(row, 7, ok_btn)

                    # Look up pricing via fee schedule + inventory
                    temp_result = ItemMatchResult(hcpcs=hcpcs, drug_name=drug_name)
                    self.matcher._apply_pricing(temp_result)
                    retail_price = temp_result.retail_price or (selected.get("retail_price") or 0.0)
                    item_num = temp_result.item_number or (selected.get("item_number") or "")
                    # Update stored row data with price + fee schedule info
                    existing_data = self.items_table.item(row, 0).data(Qt.ItemDataRole.UserRole) or {}
                    existing_data["retail_price"] = retail_price
                    existing_data["item_number"] = item_num
                    existing_data["fee"] = temp_result.fee
                    existing_data["rental_fee"] = temp_result.rental_fee
                    existing_data["max_units"] = temp_result.max_units
                    existing_data["pa_required"] = temp_result.pa_required
                    self.items_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, existing_data)

                    # LEARN: Store this mapping for future use
                    self.matcher.confirm_item_mapping(drug_name, hcpcs, desc)

                    # Update learning label
                    all_mappings = self.matcher.drug_mapper.get_all_mappings()
                    self.learning_label.setText(
                        f"🧠 Learned: \"{drug_name}\" → {hcpcs}  |  "
                        f"Total mappings: {len(all_mappings)}"
                    )
        except Exception as e:
            QMessageBox.warning(self, "Search Error", str(e))

    # ================================================================
    # STEP 4: Review & Create
    # ================================================================

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #F9FAFB;")
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        title = QLabel("📋 Order Review")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        layout.addWidget(title)

        self.review_text = QTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setMinimumHeight(300)
        self.review_text.setStyleSheet("""
            QTextEdit {
                background: white;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                padding: 16px;
                font-size: 11px;
                color: #111827;
            }
        """)
        layout.addWidget(self.review_text, 1)

        # ---- ICD-10 Section ----
        icd_frame = QFrame()
        icd_frame.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
        """)
        icd_layout = QVBoxLayout(icd_frame)
        icd_layout.setContentsMargins(16, 12, 16, 12)
        icd_layout.setSpacing(8)

        self.icd_status_label = QLabel("")
        self.icd_status_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.icd_status_label.setStyleSheet("border: none;")
        icd_layout.addWidget(self.icd_status_label)

        # Editable ICD code fields
        icd_fields_row = QHBoxLayout()
        icd_fields_row.setSpacing(6)
        self.icd_edits = []
        for i in range(5):
            edit = QLineEdit()
            edit.setPlaceholderText(f"ICD #{i+1}")
            edit.setMaximumWidth(120)
            edit.setStyleSheet("border: 1px solid #D1D5DB; border-radius: 4px; padding: 4px 6px; font-size: 11px;")
            icd_fields_row.addWidget(edit)
            self.icd_edits.append(edit)

        self.btn_icd_search = QPushButton("\U0001f50d Search ICD-10")
        self.btn_icd_search.setStyleSheet(
            "background: #3B82F6; color: white; border-radius: 6px; "
            "padding: 6px 14px; font-weight: 600; font-size: 11px; border: none;"
        )
        self.btn_icd_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_icd_search.clicked.connect(self._search_icd_code)
        icd_fields_row.addWidget(self.btn_icd_search)
        icd_fields_row.addStretch()
        icd_layout.addLayout(icd_fields_row)

        # Pend-without-ICD override
        self.chk_pend_no_icd = QCheckBox("\u23f3 Pend order without ICD-10 (note will be added to get diagnosis code)")
        self.chk_pend_no_icd.setStyleSheet("border: none; color: #D97706; font-weight: 600; font-size: 11px;")
        self.chk_pend_no_icd.setVisible(False)
        icd_layout.addWidget(self.chk_pend_no_icd)

        layout.addWidget(icd_frame)

        # Create order button
        self.btn_create_order = QPushButton("🚀 Create Order")
        self.btn_create_order.setStyleSheet(
            "background: #059669; color: white; border-radius: 8px; "
            "padding: 12px 32px; font-weight: 700; font-size: 14px;"
        )
        self.btn_create_order.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_create_order.clicked.connect(self._create_order)
        layout.addWidget(self.btn_create_order, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        scroll.setWidget(content)
        
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return page

    def _search_icd_code(self):
        """Open the ICD-10 search dialog and populate the next empty field."""
        try:
            from dmelogic.ui import create_icd10_search_dialog

            dlg = create_icd10_search_dialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_code:
                code = dlg.selected_code.strip().upper()
                # Put it in the first empty slot
                placed = False
                for edit in self.icd_edits:
                    if not edit.text().strip():
                        edit.setText(code)
                        placed = True
                        break
                if not placed:
                    # All slots full, overwrite the last one
                    self.icd_edits[-1].setText(code)
                # Update status
                self.icd_status_label.setText("✅ ICD-10 Code Added")
                self.icd_status_label.setStyleSheet("border: none; color: #059669;")
                self.chk_pend_no_icd.setChecked(False)
        except Exception as e:
            QMessageBox.warning(self, "ICD Search Error", str(e))

    def _build_review(self):
        """Populate the review page with summary."""
        lines = ["<h2>📋 Order Summary</h2>"]

        # Patient
        if self.confirmed_patient:
            p = self.confirmed_patient
            lines.append(f"<h3>👤 Patient</h3>")
            lines.append(f"<b>{p.get('last_name', '')}, {p.get('first_name', '')}</b><br>")
            if p.get('dob'):
                lines.append(f"DOB: {p['dob']}<br>")
            if p.get('phone'):
                lines.append(f"Phone: {p['phone']}<br>")

        # Prescriber
        if self.confirmed_prescriber:
            pr = self.confirmed_prescriber
            lines.append(f"<h3>🩺 Prescriber</h3>")
            name = f"{pr.get('last_name', '')}, {pr.get('first_name', '')}"
            if pr.get('title'):
                name += f" ({pr['title']})"
            lines.append(f"<b>{name}</b><br>")
            if pr.get('npi_number') or pr.get('npi'):
                lines.append(f"NPI: {pr.get('npi_number') or pr.get('npi')}<br>")

        # Insurance
        lines.append(f"<h3>🏥 Insurance</h3>")
        if self.no_insurance_override:
            lines.append("<span style='color:#DC2626; font-weight:bold;'>⚠️ No insurance — Order will be placed ON HOLD</span><br>")
        elif self.confirmed_insurance:
            ins = self.confirmed_insurance
            lines.append(f"<b>{ins.get('primary_insurance', '—')}</b><br>")
            if ins.get('policy_number'):
                lines.append(f"Policy/Member ID: {ins['policy_number']}<br>")
            if ins.get('group_number'):
                lines.append(f"Group: {ins['group_number']}<br>")
            if ins.get('secondary_insurance'):
                lines.append(f"Secondary: {ins['secondary_insurance']}")
                if ins.get('secondary_insurance_id'):
                    lines.append(f" (ID: {ins['secondary_insurance_id']})")
                lines.append("<br>")
        else:
            lines.append("—<br>")

        # Items
        lines.append(f"<h3>📦 Items ({self.items_table.rowCount()})</h3>")
        lines.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse; width:100%;'>")
        lines.append("<tr style='background:#F3F4F6;'><th>HCPCS</th><th>Description</th><th>Qty</th><th>Fee/Ea</th><th>Amount</th><th>Max Units</th><th>Refills</th></tr>")

        self.confirmed_items = []
        for row in range(self.items_table.rowCount()):
            hcpcs = (self.items_table.item(row, 2) or QTableWidgetItem("")).text().strip()
            desc = (self.items_table.item(row, 3) or QTableWidgetItem("")).text().strip()
            drug = (self.items_table.item(row, 1) or QTableWidgetItem("")).text().strip()
            qty = (self.items_table.item(row, 4) or QTableWidgetItem("1")).text().strip()
            days_supply_str = (self.items_table.item(row, 5) or QTableWidgetItem("30")).text().strip()
            refills = (self.items_table.item(row, 6) or QTableWidgetItem("0")).text().strip()
            
            row_data = self.items_table.item(row, 0).data(Qt.ItemDataRole.UserRole) or {}
            retail_price = row_data.get("retail_price", 0.0) or 0.0
            item_number = row_data.get("item_number", "") or ""
            fee = row_data.get("fee", 0.0) or 0.0
            rental_fee = row_data.get("rental_fee", 0.0) or 0.0
            max_units = row_data.get("max_units", 0) or 0
            pa_required = row_data.get("pa_required", "") or ""

            # If we still don't have a price, try fee schedule + inventory
            if not retail_price and hcpcs:
                from dmelogic.db.fee_schedule import lookup_fee
                fee_info = lookup_fee(hcpcs, folder_path=self.matcher.folder_path)
                if fee_info:
                    fee = fee_info.get("fee", 0.0)
                    rental_fee = fee_info.get("rental_fee", 0.0)
                    max_units = fee_info.get("max_units", 0)
                    pa_required = fee_info.get("pa", "")
                    retail_price = fee
                if not retail_price:
                    price_info = self.matcher._lookup_inventory_price(hcpcs)
                    if price_info:
                        retail_price = price_info.get("retail_price", 0.0)
                        item_number = price_info.get("item_number", "") or item_number

            qty_int = int(qty) if qty.isdigit() else 1
            amount = retail_price * qty_int if retail_price else 0.0

            # Learn confirmed items
            if hcpcs:
                self.matcher.confirm_item_mapping(drug, hcpcs, desc)

            cost_str = f"${retail_price:.4f}" if retail_price else "—"
            amount_str = f"${amount:.2f}" if amount else "—"
            max_str = str(max_units) if max_units else "—"
            # Highlight if qty exceeds max units
            qty_warn = ""
            if max_units and qty_int > max_units:
                qty_warn = f" <span style='color:red;'>⚠ MAX {max_units}</span>"
            lines.append(f"<tr><td>{hcpcs or '—'}</td><td>{desc or drug}</td><td>{qty}{qty_warn}</td><td>{cost_str}</td><td><b>{amount_str}</b></td><td>{max_str}</td><td>{refills}</td></tr>")
            
            self.confirmed_items.append({
                "drug_name": drug,
                "hcpcs": hcpcs,
                "description": desc or drug,
                "quantity": qty_int,
                "refills": int(refills) if refills.isdigit() else 0,
                "days_supply": int(days_supply_str) if days_supply_str.isdigit() else row_data.get("days_supply", 30),
                "directions": row_data.get("directions", ""),
                "retail_price": retail_price,
                "item_number": item_number,
                "fee": fee,
                "rental_fee": rental_fee,
                "max_units": max_units,
            })

        lines.append("</table>")

        # ICD codes
        if self.icd_codes:
            lines.append(f"<h3>🏥 ICD-10 Codes</h3>")
            lines.append(", ".join(self.icd_codes))

        # Rx date
        if self.rx_date:
            lines.append(f"<br><b>Rx Date:</b> {self.rx_date}")

        # Attachments
        if self.attachment_paths:
            lines.append(f"<h3>📎 Attachments ({len(self.attachment_paths)})</h3>")
            for p in self.attachment_paths:
                lines.append(f"• {os.path.basename(p)}<br>")

        self.review_text.setHtml("\n".join(lines))

        # ---- Populate ICD-10 editable fields ----
        for i, edit in enumerate(self.icd_edits):
            if i < len(self.icd_codes):
                edit.setText(self.icd_codes[i])
            else:
                edit.setText("")

        has_icd = bool(self.icd_codes)
        if has_icd:
            self.icd_status_label.setText("✅ ICD-10 Codes Detected from Rx")
            self.icd_status_label.setStyleSheet("border: none; color: #059669;")
            self.chk_pend_no_icd.setVisible(False)
            self.chk_pend_no_icd.setChecked(False)
        else:
            self.icd_status_label.setText("⚠️ No ICD-10 Codes Found — Enter below or Pend order")
            self.icd_status_label.setStyleSheet("border: none; color: #DC2626;")
            self.chk_pend_no_icd.setVisible(True)
            self.chk_pend_no_icd.setChecked(True)

    # ================================================================
    # Order Creation
    # ================================================================

    def _create_order(self):
        """Create the order using the existing order creation pipeline."""
        try:
            from dmelogic.db.orders import create_order
            from dmelogic.db.models import OrderInput, OrderItemInput, BillingType, OrderStatus
            from dmelogic.security.auth import is_current_user_agent

            _is_agent_user = is_current_user_agent()

            patient = self.confirmed_patient or {}
            prescriber = self.confirmed_prescriber or {}

            patient_last = patient.get("last_name", "")
            patient_first = patient.get("first_name", "")
            patient_id = patient.get("id", 0)

            today_str = datetime.now().strftime("%m/%d/%Y")
            rx_date_str = self.rx_date or today_str

            # Build items
            order_items = []
            for ci in self.confirmed_items:
                ds = ci.get("days_supply", 30)
                if not ds or ds <= 0:
                    ds = 30  # Default days supply for DME
                from decimal import Decimal
                cost_ea = None
                rp = ci.get("retail_price", 0.0)
                if rp and rp > 0:
                    cost_ea = Decimal(str(rp))
                order_items.append(OrderItemInput(
                    hcpcs=ci.get("hcpcs", ""),
                    description=ci.get("description", ""),
                    quantity=ci.get("quantity", 1),
                    refills=ci.get("refills", 0),
                    days_supply=ds,
                    directions=ci.get("directions", ""),
                    cost_ea=cost_ea,
                    item_number=ci.get("item_number", ""),
                ))

            if not order_items:
                QMessageBox.warning(self, "Error", "No items to create an order. Please match at least one item.")
                return

            # ICD codes — read from editable fields (user may have added/changed)
            icds = []
            for edit in self.icd_edits:
                val = edit.text().strip().upper()
                if val:
                    icds.append(val)
            icds = icds[:5]

            # Determine if we should pend without ICD
            pend_no_icd = False
            if not icds:
                if self.chk_pend_no_icd.isChecked():
                    pend_no_icd = True
                else:
                    QMessageBox.warning(
                        self, "ICD-10 Required",
                        "No ICD-10 diagnosis codes provided.\n\n"
                        "Either:\n"
                        "  • Enter ICD-10 codes in the fields above\n"
                        "  • Use \U0001f50d Search ICD-10 to find codes\n"
                        "  • Check \u23f3 Pend order to create as On Hold"
                    )
                    return

            pres_last = prescriber.get("last_name", "")
            pres_first = prescriber.get("first_name", "")
            pres_name = f"{pres_last}, {pres_first}".strip(", ") if (pres_last or pres_first) else ""

            # Determine hold reasons
            hold_reasons = []
            if pend_no_icd:
                hold_reasons.append("PENDING ICD-10: Diagnosis code(s) needed for billing. Contact prescriber for ICD-10.")
            if self.no_insurance_override:
                hold_reasons.append("PENDING INSURANCE: Insurance information needed for billing. Contact patient/guardian for insurance details.")

            should_hold = bool(hold_reasons)

            # Insurance info
            ins = self.confirmed_insurance or {}
            primary_ins = ins.get("primary_insurance", "") if ins else ""
            primary_ins_id = ins.get("policy_number", "") if ins else ""

            # Build notes
            if hold_reasons:
                notes_str = "⚠️ " + " | ".join(hold_reasons) + f" — Imported from Rx PDF on {datetime.now().strftime('%m/%d/%Y %I:%M %p')}"
            else:
                notes_str = f"Imported from Rx PDF on {datetime.now().strftime('%m/%d/%Y %I:%M %p')}"

            order_input = OrderInput(
                patient_last_name=patient_last,
                patient_first_name=patient_first,
                patient_id=patient_id or None,
                patient_dob=patient.get("dob"),
                patient_phone=patient.get("phone"),
                prescriber_id=prescriber.get("id"),
                prescriber_name=pres_name,
                prescriber_npi=prescriber.get("npi_number") or prescriber.get("npi", ""),
                rx_date=rx_date_str,
                order_date=today_str,
                billing_type=BillingType.INSURANCE.value,
                order_status=OrderStatus.ON_HOLD.value if should_hold else OrderStatus.PENDING.value,
                primary_insurance=primary_ins or None,
                primary_insurance_id=primary_ins_id or None,
                icd_code_1=icds[0] if len(icds) > 0 else None,
                icd_code_2=icds[1] if len(icds) > 1 else None,
                icd_code_3=icds[2] if len(icds) > 2 else None,
                icd_code_4=icds[3] if len(icds) > 3 else None,
                icd_code_5=icds[4] if len(icds) > 4 else None,
                notes=notes_str,
                items=order_items,
                skip_icd_validation=pend_no_icd,
                agent_created=_is_agent_user,
            )

            # --- Smart Duplicate Detection ---
            try:
                from dmelogic.services.duplicate_detector import DuplicateDetector
                from dmelogic.ui.duplicate_warning_dialog import DuplicateWarningDialog

                detector = DuplicateDetector(folder_path=self.folder_path)
                hcpcs_list = [item.hcpcs for item in order_items if item.hcpcs]
                dup_warnings = detector.check(
                    patient_last_name=patient_last,
                    patient_first_name=patient_first,
                    patient_dob=patient.get("dob"),
                    patient_id=patient_id or None,
                    hcpcs_codes=hcpcs_list,
                )
                if dup_warnings:
                    action = DuplicateWarningDialog(dup_warnings, parent=self).exec()
                    if action == DuplicateWarningDialog.ACTION_CANCEL:
                        return
                    elif action == DuplicateWarningDialog.ACTION_VIEW:
                        self.accept()
                        return
                    # ACTION_CONTINUE → proceed
            except ImportError:
                pass
            except Exception as e:
                print(f"⚠️ Duplicate detection skipped: {e}")

            new_order_id = create_order(order_input, folder_path=self.folder_path)

            # Process attachments
            if self.attachment_paths and hasattr(self.main_window, '_process_wizard_attachments'):
                self.main_window._process_wizard_attachments(
                    new_order_id, self.attachment_paths,
                    self.folder_path, patient_id=patient_id
                )

            status_msg = ""
            if should_hold:
                hold_parts = []
                if pend_no_icd:
                    hold_parts.append("ICD-10 codes needed")
                if self.no_insurance_override:
                    hold_parts.append("Insurance info needed")
                status_msg = "\n\n⚠️ Status: ON HOLD — " + ", ".join(hold_parts)

            QMessageBox.information(
                self, "Order Created",
                f"✅ Order ORD-{new_order_id:03d} created successfully!\n\n"
                f"Patient: {patient_last}, {patient_first}\n"
                f"Items: {len(order_items)}\n"
                f"ICD: {', '.join(icds) if icds else 'Pending'}"
                f"{status_msg}"
            )

            self.order_created.emit(new_order_id)

            # Release preview file handles before attempting to move files
            self._release_preview()

            # --- Move Rx files to a different folder ---
            self._offer_move_rx(new_order_id, patient_last, patient_first)

            # Open EPACES helper if available
            try:
                from dmelogic.db import fetch_order_with_items
                from dmelogic.ui.epaces_helper import EpacesHelperDialog
                order = fetch_order_with_items(new_order_id, folder_path=self.folder_path)
                epaces_dlg = EpacesHelperDialog(order=order, folder_path=self.folder_path, parent=self.main_window)
                self.accept()  # Close wizard first
                epaces_dlg.exec()
            except Exception:
                self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error Creating Order", f"Failed to create order:\n\n{e}")

    # ================================================================
    # Move Rx Files
    # ================================================================

    def _offer_move_rx(self, order_id: int, patient_last: str, patient_first: str):
        """Offer to move the Rx PDF file(s) to a different folder after order creation."""
        if not self.attachment_paths:
            return

        import shutil
        import json

        # Load last-used destination from settings
        last_dest = ""
        recent_dirs: list = []
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "settings.json")
        settings_path = os.path.normpath(settings_path)
        try:
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    settings = json.load(f)
                last_dest = settings.get("rx_move_last_folder", "")
                recent_dirs = settings.get("rx_move_recent_folders", [])
        except Exception:
            pass

        # Build the dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("📁 Move Rx Files")
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet("background: #F9FAFB;")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        title = QLabel("📁 Move Rx Files to Folder")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        layout.addWidget(title)

        desc = QLabel(
            f"Order ORD-{order_id:03d} created for {patient_last}, {patient_first}.\n"
            f"Move the {len(self.attachment_paths)} Rx file(s) to a completed/patient folder?"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(desc)

        # File list
        file_frame = QFrame()
        file_frame.setStyleSheet("background: white; border: 1px solid #E5E7EB; border-radius: 6px;")
        fl = QVBoxLayout(file_frame)
        fl.setContentsMargins(10, 8, 10, 8)
        for p in self.attachment_paths:
            fl.addWidget(QLabel(f"📄 {os.path.basename(p)}"))
        layout.addWidget(file_frame)

        # Destination folder
        dest_label = QLabel("Destination Folder:")
        dest_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        dest_label.setStyleSheet("color: #374151;")
        layout.addWidget(dest_label)

        # Recent folders combo + browse button
        dest_row = QHBoxLayout()
        dest_row.setSpacing(6)

        dest_combo = QComboBox()
        dest_combo.setEditable(True)
        dest_combo.setMinimumWidth(350)
        dest_combo.setStyleSheet(
            "QComboBox { border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; font-size: 12px; background: white; }"
        )
        # Populate recent folders
        if last_dest:
            dest_combo.addItem(last_dest)
        for d in recent_dirs:
            if d != last_dest:
                dest_combo.addItem(d)
        if dest_combo.count() == 0:
            dest_combo.setCurrentText("")

        # Auto-suggest a subfolder based on the patient's last name initial
        # e.g.  "C:/Faxes OCR'd"  →  "C:/Faxes OCR'd/T"  for TORIBIO
        if patient_last and dest_combo.currentText().strip():
            base_dest = dest_combo.currentText().strip()
            letter = patient_last[0].upper()

            # If the saved path already ends in a single letter subfolder
            # (from a previous move), use its parent as the base instead
            tail = os.path.basename(base_dest)
            if len(tail) == 1 and tail.isalpha():
                base_dest = os.path.dirname(base_dest)

            suggested = os.path.join(base_dest, letter)
            # Only suggest if the letter subfolder already exists OR
            # there are other letter-subfolders (A-Z) in the base
            if os.path.isdir(suggested):
                dest_combo.setCurrentText(suggested)
            elif os.path.isdir(base_dest):
                # Check if the base folder uses letter-based organization
                try:
                    subs = [d for d in os.listdir(base_dest)
                            if os.path.isdir(os.path.join(base_dest, d))
                            and len(d) == 1 and d.isalpha()]
                    if subs:  # Has letter subfolders — suggest the right one
                        dest_combo.setCurrentText(suggested)
                except Exception:
                    pass

        dest_row.addWidget(dest_combo, 1)

        btn_browse = QPushButton("📂 Browse...")
        btn_browse.setStyleSheet(
            "background: #3B82F6; color: white; border-radius: 6px; "
            "padding: 8px 16px; font-weight: 600; border: none;"
        )
        def _browse_dest():
            folder = QFileDialog.getExistingDirectory(dlg, "Select Destination Folder", dest_combo.currentText())
            if folder:
                dest_combo.setCurrentText(folder)
        btn_browse.clicked.connect(_browse_dest)
        dest_row.addWidget(btn_browse)
        layout.addLayout(dest_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        btn_skip = QPushButton("Skip — Don't Move")
        btn_skip.setStyleSheet(
            "background: #E5E7EB; color: #374151; border-radius: 6px; "
            "padding: 10px 20px; font-weight: 500; border: none;"
        )
        btn_skip.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_skip)

        btn_move = QPushButton("📁 Move Files")
        btn_move.setStyleSheet(
            "background: #059669; color: white; border-radius: 6px; "
            "padding: 10px 24px; font-weight: 700; font-size: 12px; border: none;"
        )
        btn_move.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_move)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        dest_folder = dest_combo.currentText().strip()
        if not dest_folder:
            return

        # Create destination folder if needed
        os.makedirs(dest_folder, exist_ok=True)

        moved = []
        errors = []
        for src_path in self.attachment_paths:
            try:
                basename = os.path.basename(src_path)
                dst_path = os.path.join(dest_folder, basename)
                # Handle name collision
                if os.path.exists(dst_path):
                    name, ext = os.path.splitext(basename)
                    dst_path = os.path.join(dest_folder, f"{name}_ORD{order_id:03d}{ext}")

                # Release any main-window file handle on this PDF
                self._close_main_viewer_if_open(src_path)

                # Small delay to let Windows release the file handle
                import time
                time.sleep(0.3)

                # Try move with retries (Windows may delay releasing handles)
                self._move_with_retry(src_path, dst_path)
                moved.append(basename)
            except Exception as e:
                errors.append(f"{os.path.basename(src_path)}: {e}")

        # Save last-used folder to settings
        try:
            settings = {}
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    settings = json.load(f)
            settings["rx_move_last_folder"] = dest_folder
            # Update recent folders list (max 10)
            recents = settings.get("rx_move_recent_folders", [])
            if dest_folder in recents:
                recents.remove(dest_folder)
            recents.insert(0, dest_folder)
            settings["rx_move_recent_folders"] = recents[:10]
            with open(settings_path, "w") as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass

        if errors:
            QMessageBox.warning(
                self, "Move Errors",
                f"Moved {len(moved)} file(s), but {len(errors)} failed:\n\n" + "\n".join(errors)
            )
        elif moved:
            QMessageBox.information(
                self, "Files Moved",
                f"✅ Moved {len(moved)} file(s) to:\n{dest_folder}"
            )

    def _close_main_viewer_if_open(self, file_path: str):
        """Close the main window's PDF viewer if it has this file open."""
        import gc
        try:
            mw = self.main_window
            if not mw:
                return

            target = os.path.normcase(os.path.abspath(file_path))

            # Check current_pdf_path (full path) — primary attribute
            mw_path = getattr(mw, 'current_pdf_path', '') or ''
            if mw_path:
                mw_path = os.path.normcase(os.path.abspath(mw_path))

            # Also check current_file (may be just a filename)
            mw_file = getattr(mw, 'current_file', '') or ''
            if mw_file and not os.path.isabs(mw_file):
                folder = getattr(mw, 'folder_path', '') or ''
                if folder:
                    mw_file = os.path.normcase(os.path.abspath(os.path.join(folder, mw_file)))
                else:
                    mw_file = ''
            elif mw_file:
                mw_file = os.path.normcase(os.path.abspath(mw_file))

            if target == mw_path or target == mw_file:
                for attr in ('doc', 'pdf_document'):
                    doc = getattr(mw, attr, None)
                    if doc:
                        try:
                            doc.close()
                        except Exception:
                            pass
                        setattr(mw, attr, None)
                mw.current_file = None
                mw.current_pdf_path = None if hasattr(mw, 'current_pdf_path') else None
                gc.collect()
                print(f"🔓 Released main viewer lock on {os.path.basename(file_path)}")
        except Exception as e:
            print(f"⚠️ Error releasing main viewer: {e}")

    @staticmethod
    def _move_with_retry(src: str, dst: str, retries: int = 4, delay: float = 0.5):
        """Move file with retry + copy-then-delete fallback for Windows locks."""
        import time, shutil, gc
        last_err = None
        for attempt in range(retries):
            try:
                shutil.move(src, dst)
                return  # success
            except PermissionError as e:
                last_err = e
                gc.collect()
                time.sleep(delay * (attempt + 1))

        # All retries failed — try copy + delete as a last resort
        try:
            shutil.copy2(src, dst)
            # Try deleting the source with retries
            for attempt in range(retries):
                try:
                    os.remove(src)
                    return  # success
                except PermissionError:
                    gc.collect()
                    time.sleep(delay * (attempt + 1))
            # Copy succeeded but delete failed — still acceptable
            return
        except Exception:
            pass

        raise last_err  # raise original error if everything failed

    # ================================================================
    # Navigation
    # ================================================================

    def _go_next(self):
        current = self.stack.currentIndex()
        
        # Validate current step
        if current == 0 and not self.parsed_rxs:
            QMessageBox.warning(self, "Upload Required", "Please upload and parse at least one Rx file.")
            return

        next_step = current + 1
        if next_step >= self.stack.count():
            return

        # Pages: 0=Upload, 1=Patient, 2=Insurance, 3=Prescriber, 4=Items, 5=Review
        if next_step == 1:
            self._run_patient_match()
        elif next_step == 2:
            if not self.confirmed_patient:
                QMessageBox.warning(self, "Patient Required", "Please confirm or add a patient before proceeding.")
                return
            self._populate_insurance_fields()
        elif next_step == 3:
            # Insurance step → Prescriber: insurance must be confirmed or overridden
            if not self.confirmed_insurance and not self.no_insurance_override:
                QMessageBox.warning(self, "Insurance Required",
                    "Please confirm insurance or check the override to continue.")
                return
            self._run_prescriber_match()
        elif next_step == 4:
            if not self.confirmed_prescriber:
                QMessageBox.warning(self, "Prescriber Required", "Please confirm or add a prescriber before proceeding.")
                return
            self._run_items_match()
        elif next_step == 5:
            self._build_review()

        self.stack.setCurrentIndex(next_step)
        self.step_bar.set_step(next_step)
        self._update_nav()

    def _go_back(self):
        current = self.stack.currentIndex()
        if current > 0:
            self.stack.setCurrentIndex(current - 1)
            self.step_bar.set_step(current - 1)
            self._update_nav()

    def _update_nav(self):
        current = self.stack.currentIndex()
        self.btn_back.setVisible(current > 0)
        if current == self.stack.count() - 1:
            self.btn_next.setText("Done")
            self.btn_next.setVisible(False)  # Use the Create Order button instead
        else:
            self.btn_next.setText("Next →")
            self.btn_next.setVisible(True)
