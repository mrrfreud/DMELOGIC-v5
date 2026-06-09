# dmelogic/ui/epaces_helper.py

from __future__ import annotations

import sqlite3
from typing import Optional
from decimal import Decimal

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QGuiApplication, QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLineEdit,
    QWidget,
    QSizePolicy,
    QSpacerItem,
    QPushButton,
    QMessageBox,
)

from dmelogic.models import Order, OrderItem, OrderStatus
from dmelogic.db.order_workflow import update_order_status_validated
from dmelogic.db.patients import fetch_patient_by_id
from dmelogic.db.base import resolve_db_path
from dmelogic.services.patient_address import get_patient_full_address
from dmelogic.fee_schedule_enhancements import (
    add_pa_type_column_to_epaces,
    add_max_units_column_to_epaces,
    DbFeeScheduleReader,
)


def _copy(text: str) -> None:
    """Copy text to clipboard (safe on empty)."""
    QGuiApplication.clipboard().setText(text or "")


def _make_value_copier(value: str):
    """Return a slot that copies a specific value."""
    def _slot(_checked: bool = False) -> None:
        _copy(value)
    return _slot


def _format_order_number(order: Order) -> str:
    """Format order number using continuous ID with optional refill context."""
    base = f"ORD-{int(order.id):03d}"
    parent_id = order.parent_order_id or 0
    refill_no = order.refill_number or 0
    if parent_id and refill_no > 0:
        return f"{base} (Refill of ORD-{int(parent_id):03d} R{int(refill_no)})"
    return base


class EpacesHelperDialog(QDialog):
    """
    Helper dialog for manually billing an order in ePACES.

    Header shows:
      - Patient
      - Primary Insurance + ID
      - Secondary Insurance + ID (if present)
      - Prescriber + NPI
      - ICD-10 codes (each with a copy button)

    Items grid columns:
      ITEM | HCPCS | QTY | AMOUNT | MD DIRECTIONS | PA #

      - ITEM: Item # from inventory profile + Copy
      - HCPCS: HCPCS code (e.g. A6530) + Copy
      - QTY: quantity + Copy
      - AMOUNT: line total + Copy
      - MD DIRECTIONS: prescription directions + Copy
      - PA #: editable text field with Copy button (saved to order)

    Bottom:
      - Claim Total: <sum of amounts> + Copy
      - Mark as Billed
      - Close
    """

    def __init__(
        self,
        order: Order,
        folder_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.order = order
        self.folder_path = folder_path

        # DEBUG: Check order at init
        print(f"EPACES __init__: Received order {order.id}")
        print(f"EPACES __init__: order.patient_last_name = {getattr(order, 'patient_last_name', 'NO ATTR')}")
        print(f"EPACES __init__: order.patient_first_name = {getattr(order, 'patient_first_name', 'NO ATTR')}")
        print(f"EPACES __init__: order.patient_id = {getattr(order, 'patient_id', 'NO ATTR')}")

        # raw numeric string for claim total
        self._claim_total_raw: str = "0.00"
        # for copying IDs
        self._primary_ins_id: str = ""
        self._secondary_ins_id: str = ""
        self._primary_payer_name: str = ""
        self._secondary_payer_name: str = ""
        self._billing_payer_selection: str = "Primary"
        
        # Track PA# edits for each item (item_id -> QLineEdit)
        self._pa_edits: dict[int, QLineEdit] = {}

        # Track item data for drag-and-drop reordering
        self._items_data: list[OrderItem] = []
        self._fee_reader = None

        self.setObjectName("EpacesHelperDialog")
        self.setWindowTitle(f"EPACES – {_format_order_number(order)}")
        self.setModal(False)

        self.resize(1400, 750)  # Increased from 1200x700 to accommodate wider columns
        self.setMinimumSize(1300, 700)  # Increased from 1100x650

        # Allow minimize/maximize
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        # Local styling
        self.setStyleSheet(
            """
            #EpacesHelperDialog {
                font-size: 11pt;
            }
            #EpacesHelperDialog QLabel {
                font-weight: 600;
            }
            #EpacesHelperDialog QLineEdit {
                font-size: 11pt;
                padding: 4px 6px;
            }
            #EpacesHelperDialog QTableWidget {
                font-size: 11pt;
            }
            #EpacesHelperDialog QToolButton#copyTool {
                padding: 0px 5px;
                min-width: 48px;
                min-height: 20px;
                border-radius: 4px;
                border: 1px solid #999;
                background-color: #f5f5f5;
                font-size: 8pt;
                font-weight: 500;
            }
            #EpacesHelperDialog QToolButton#copyTool:hover {
                background-color: #e0e0e0;
            }
            #EpacesHelperDialog QToolButton#copyTool:pressed {
                background-color: #d0d0d0;
            }
            """
        )

        self._build_ui()
        self._bind_data()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(18)  # Increased vertical spacing

        # === Header block ================================================
        header = QWidget(self)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(10, 10, 10, 10)  # Increased padding
        header_layout.setSpacing(10)  # Increased spacing

        # Row 1: Pt name + Primary Member ID
        row1 = QHBoxLayout()
        row1.setSpacing(10)  # Increased horizontal spacing

        self.lbl_patient_name = QLabel("")
        self.lbl_patient_name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row1.addWidget(QLabel("Pt Name:"))
        row1.addWidget(self.lbl_patient_name, 2)

        btn_pt = self._make_copy_button(
            "Copy patient name",
            lambda: _copy(self.lbl_patient_name.text()),
        )
        row1.addWidget(btn_pt)

        self.lbl_primary_member = QLabel("")
        self.lbl_primary_member.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row1.addSpacing(24)
        row1.addWidget(QLabel("Primary Payer Policy #:"))
        row1.addWidget(self.lbl_primary_member, 2)

        btn_primary_id = self._make_copy_button(
            "Copy primary member ID",
            lambda: _copy(self._primary_ins_id),
        )
        row1.addWidget(btn_primary_id)

        header_layout.addLayout(row1)

        # Row 2: Prescriber + NPI
        row2 = QHBoxLayout()
        row2.setSpacing(10)  # Increased horizontal spacing

        self.lbl_prescriber = QLabel("")
        self.lbl_prescriber.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row2.addWidget(QLabel("Prescriber:"))
        row2.addWidget(self.lbl_prescriber, 2)

        btn_presc = self._make_copy_button(
            "Copy prescriber name",
            lambda: _copy(self.lbl_prescriber.text()),
        )
        row2.addWidget(btn_presc)

        self.lbl_npi = QLabel("")
        self.lbl_npi.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row2.addSpacing(24)
        row2.addWidget(QLabel("NPI #:"))
        row2.addWidget(self.lbl_npi, 2)

        btn_npi = self._make_copy_button(
            "Copy NPI",
            lambda: _copy(self.lbl_npi.text()),
        )
        row2.addWidget(btn_npi)

        header_layout.addLayout(row2)

        # Row 2b: Second Prescriber + NPI (if exists)
        row2b = QHBoxLayout()
        row2b.setSpacing(10)

        self.lbl_prescriber_2 = QLabel("")
        self.lbl_prescriber_2.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row2b.addWidget(QLabel("Prescriber 2:"))
        row2b.addWidget(self.lbl_prescriber_2, 2)

        btn_presc_2 = self._make_copy_button(
            "Copy prescriber 2 name",
            lambda: _copy(self.lbl_prescriber_2.text()),
        )
        row2b.addWidget(btn_presc_2)

        self.lbl_npi_2 = QLabel("")
        self.lbl_npi_2.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row2b.addSpacing(24)
        row2b.addWidget(QLabel("NPI 2:"))
        row2b.addWidget(self.lbl_npi_2, 2)

        btn_npi_2 = self._make_copy_button(
            "Copy NPI 2",
            lambda: _copy(self.lbl_npi_2.text()),
        )
        row2b.addWidget(btn_npi_2)

        # Store reference for conditional display
        self._row2b_widget = QWidget()
        self._row2b_widget.setLayout(row2b)
        header_layout.addWidget(self._row2b_widget)

        # Row 3: Primary/Secondary insurance
        row3 = QHBoxLayout()
        row3.setSpacing(10)  # Increased horizontal spacing

        self.lbl_primary_ins = QLabel("")
        self.lbl_primary_ins.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row3.addWidget(QLabel("Primary Payer:"))
        row3.addWidget(self.lbl_primary_ins, 2)

        btn_copy_primary_line = self._make_copy_button(
            "Copy primary insurance ID",
            lambda: _copy(self._primary_ins_id),
        )
        row3.addWidget(btn_copy_primary_line)

        self.lbl_secondary_ins = QLabel("")
        self.lbl_secondary_ins.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row3.addSpacing(24)
        row3.addWidget(QLabel("Secondary Payer:"))
        row3.addWidget(self.lbl_secondary_ins, 2)

        btn_copy_secondary_line = self._make_copy_button(
            "Copy secondary insurance ID",
            lambda: _copy(self._secondary_ins_id),
        )
        row3.addWidget(btn_copy_secondary_line)

        header_layout.addLayout(row3)

        # Row 3a: Primary/Secondary billed via
        row3a = QHBoxLayout()
        row3a.setSpacing(10)

        self.lbl_primary_billed_via = QLabel("")
        self.lbl_primary_billed_via.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row3a.addWidget(QLabel("Primary Billed Via:"))
        row3a.addWidget(self.lbl_primary_billed_via, 2)

        btn_copy_primary_billed_via = self._make_copy_button(
            "Copy primary billed via",
            lambda: _copy(self.lbl_primary_billed_via.text()),
        )
        row3a.addWidget(btn_copy_primary_billed_via)

        self.lbl_secondary_billed_via = QLabel("")
        self.lbl_secondary_billed_via.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row3a.addSpacing(24)
        row3a.addWidget(QLabel("Secondary Billed Via:"))
        row3a.addWidget(self.lbl_secondary_billed_via, 2)

        btn_copy_secondary_billed_via = self._make_copy_button(
            "Copy secondary billed via",
            lambda: _copy(self.lbl_secondary_billed_via.text()),
        )
        row3a.addWidget(btn_copy_secondary_billed_via)

        header_layout.addLayout(row3a)

        # Row 3b: billing payer selection
        row3b = QHBoxLayout()
        row3b.setSpacing(10)
        row3b.addWidget(QLabel("Bill This Claim To:"))

        self.chk_bill_primary = QCheckBox("Primary")
        self.chk_bill_secondary = QCheckBox("Secondary")
        self.chk_bill_primary.setChecked(True)

        self.chk_bill_primary.toggled.connect(self._on_bill_primary_toggled)
        self.chk_bill_secondary.toggled.connect(self._on_bill_secondary_toggled)

        row3b.addWidget(self.chk_bill_primary)
        row3b.addWidget(self.chk_bill_secondary)

        self.lbl_billing_payer = QLabel("Primary")
        self.lbl_billing_payer.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row3b.addSpacing(24)
        row3b.addWidget(QLabel("Selected:"))
        row3b.addWidget(self.lbl_billing_payer, 2)

        btn_copy_billing_payer = self._make_copy_button(
            "Copy selected billing payer ID",
            lambda: _copy(self._selected_billing_payer_id()),
        )
        row3b.addWidget(btn_copy_billing_payer)

        header_layout.addLayout(row3b)

        # Row 4a: Address street
        row_addr_street = QHBoxLayout()
        row_addr_street.setSpacing(10)

        self.lbl_address_street = QLabel("")
        self.lbl_address_street.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        row_addr_street.addWidget(QLabel("Address:"))
        row_addr_street.addWidget(self.lbl_address_street, 1)

        btn_addr_street_copy = self._make_copy_button(
            "Copy street address",
            lambda: _copy(self.lbl_address_street.text()),
        )
        row_addr_street.addWidget(btn_addr_street_copy)

        header_layout.addLayout(row_addr_street)

        # Row 4b: City/State
        row_addr_city = QHBoxLayout()
        row_addr_city.setSpacing(10)

        self.lbl_address_city_state = QLabel("")
        self.lbl_address_city_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        row_addr_city.addWidget(QLabel("City/State:"))
        row_addr_city.addWidget(self.lbl_address_city_state, 1)

        btn_addr_city_copy = self._make_copy_button(
            "Copy city/state",
            lambda: _copy(self.lbl_address_city_state.text()),
        )
        row_addr_city.addWidget(btn_addr_city_copy)

        header_layout.addLayout(row_addr_city)

        # Row 4c: ZIP
        row_addr_zip = QHBoxLayout()
        row_addr_zip.setSpacing(10)

        self.lbl_address_zip = QLabel("")
        self.lbl_address_zip.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        row_addr_zip.addWidget(QLabel("ZIP:"))
        row_addr_zip.addWidget(self.lbl_address_zip, 1)

        btn_addr_zip_copy = self._make_copy_button(
            "Copy ZIP",
            lambda: _copy(self.lbl_address_zip.text()),
        )
        row_addr_zip.addWidget(btn_addr_zip_copy)

        header_layout.addLayout(row_addr_zip)

        # Row 5: ICD-10 list
        icd_row = QHBoxLayout()
        icd_row.setSpacing(10)  # Increased horizontal spacing
        icd_row.addWidget(QLabel("ICD-10:"))

        self.icd_codes_container = QWidget(self)
        self.icd_codes_layout = QVBoxLayout(self.icd_codes_container)
        self.icd_codes_layout.setContentsMargins(0, 0, 0, 0)
        self.icd_codes_layout.setSpacing(4)
        icd_row.addWidget(self.icd_codes_container, 1)

        header_layout.addLayout(icd_row)

        layout.addWidget(header)

        # === Items table ==================================================
        # Columns:
        # 0: ITEM (inventory item number)
        # 1: DESCRIPTION
        # 2: HCPCS
        # 3: QTY
        # 4: AMOUNT
        # 5: MD DIRECTIONS
        # 6: PA #
        self.items_table = QTableWidget(0, 7, self)
        self.items_table.setHorizontalHeaderLabels(
            ["ITEM", "DESCRIPTION", "HCPCS", "QTY", "AMOUNT", "MD DIRECTIONS", "PA #"]
        )
        self._configure_items_table_columns()

        # Enable drag-and-drop row reordering via vertical header
        vheader = self.items_table.verticalHeader()
        vheader.setVisible(True)
        vheader.setSectionsMovable(True)
        vheader.setSectionsClickable(False)
        vheader.setDefaultSectionSize(42)
        vheader.sectionMoved.connect(self._on_row_moved)
        vheader.setStyleSheet("""
            QHeaderView::section {
                background-color: #f0f0f0;
                border: 1px solid #d0d0d0;
                padding: 4px 6px;
                font-size: 10pt;
                color: #888;
                min-width: 28px;
                max-width: 28px;
            }
            QHeaderView::section:hover {
                background-color: #e0e0e0;
                color: #555;
            }
        """)

        self.items_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.items_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Enable context menu for prescriber assignment
        self.items_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.items_table.customContextMenuRequested.connect(self._show_item_context_menu)

        layout.addWidget(self.items_table, 1)

        # === Claim total row =============================================
        total_row = QHBoxLayout()
        total_row.setSpacing(10)  # Increased spacing

        total_row.addItem(
            QSpacerItem(
                20,
                20,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        )

        self.lbl_claim_total = QLabel("Claim Total: 0.00")
        total_row.addWidget(self.lbl_claim_total)

        btn_total_copy = self._make_copy_button(
            "Copy claim total",
            lambda: _copy(self._claim_total_raw),
        )
        total_row.addWidget(btn_total_copy)

        layout.addLayout(total_row)

        # === Delivery info row (moved from header) =======================
        delivery_row = self._build_delivery_info_row()
        layout.addLayout(delivery_row)

        # === Bottom buttons ===============================================
        bottom = QHBoxLayout()
        bottom.setSpacing(12)  # Increased button spacing

        self.btn_save_delivery = QPushButton("Save Delivery Info")
        self.btn_save_delivery.setToolTip("Save delivery date and tracking number to order")
        self.btn_save_delivery.setMinimumHeight(32)  # Ensure button is fully visible
        self.btn_save_delivery.clicked.connect(self._on_save_delivery_info)
        self.btn_save_delivery.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px;")
        bottom.addWidget(self.btn_save_delivery)

        self.btn_print_ticket = QPushButton("🎫 Print Delivery Ticket")
        self.btn_print_ticket.setToolTip("Print delivery ticket PDF")
        self.btn_print_ticket.setMinimumHeight(32)
        self.btn_print_ticket.clicked.connect(self._print_delivery_ticket)
        self.btn_print_ticket.setStyleSheet("background-color: #6c757d; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px;")
        bottom.addWidget(self.btn_print_ticket)

        self.btn_mark_shipped = QPushButton("Mark as Shipped")
        self.btn_mark_shipped.setMinimumHeight(32)
        self.btn_mark_shipped.clicked.connect(self._on_mark_shipped)
        self.btn_mark_shipped.setStyleSheet("background-color: #007bff; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px;")
        bottom.addWidget(self.btn_mark_shipped)

        self.btn_mark_billed = QPushButton("Mark as Billed")
        self.btn_mark_billed.setProperty("class", "primary")
        self.btn_mark_billed.setMinimumHeight(32)  # Ensure button is fully visible
        self.btn_mark_billed.clicked.connect(self._on_mark_billed)
        self.btn_mark_billed.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px;")
        bottom.addWidget(self.btn_mark_billed)

        bottom.addItem(
            QSpacerItem(
                20,
                20,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        )

        btn_close = QPushButton("Close")
        btn_close.setMinimumHeight(32)  # Ensure button is fully visible
        btn_close.clicked.connect(self._save_pa_and_close)
        btn_close.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px;")
        bottom.addWidget(btn_close)

        layout.addLayout(bottom)

    def _make_copy_button(self, tooltip: str, slot) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("copyTool")
        btn.setText("Copy")
        btn.setToolTip(tooltip)
        btn.setAutoRaise(True)
        btn.setMinimumHeight(28)  # Ensure button is fully visible
        btn.setMinimumWidth(60)  # Ensure button text fits
        btn.clicked.connect(slot)
        return btn

    def _configure_items_table_columns(self) -> None:
        """Apply stable widths so PA # always fits 12 characters plus Copy button."""
        header_view = self.items_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # ITEM
        header_view.resizeSection(0, 160)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)  # DESCRIPTION
        header_view.resizeSection(1, 260)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)  # HCPCS
        header_view.resizeSection(2, 180)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)  # QTY
        header_view.resizeSection(3, 120)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # AMOUNT
        header_view.resizeSection(4, 140)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)  # MD DIRECTIONS
        header_view.resizeSection(5, 200)
        header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)  # PA #
        header_view.resizeSection(6, 240)

    # ------------------------------------------------------------------ Data binding

    def _bind_data(self) -> None:
        o = self.order

        # Patient name - use patient_full_name property which has proper fallback
        patient_name = o.patient_full_name or ""
        self.lbl_patient_name.setText(patient_name)
        
        # Prescriber - try snapshot first, fall back to legacy fields
        prescriber_name = (
            o.prescriber_name_at_order_time
            or getattr(o, "prescriber_name", None)
            or ""
        )
        self.lbl_prescriber.setText(prescriber_name)
        
        prescriber_npi = (
            o.prescriber_npi_at_order_time
            or getattr(o, "prescriber_npi", None)
            or ""
        )
        self.lbl_npi.setText(prescriber_npi)

        # Prescriber 2 (secondary prescriber)
        prescriber_name_2 = getattr(o, "prescriber_name_2", None) or ""
        prescriber_npi_2 = getattr(o, "prescriber_npi_2", None) or ""
        self.lbl_prescriber_2.setText(prescriber_name_2 or "(none)")
        self.lbl_npi_2.setText(prescriber_npi_2 or "(none)")
        # Show/hide row based on whether prescriber 2 exists
        self._row2b_widget.setVisible(bool(prescriber_name_2 or prescriber_npi_2))

        # --- Fetch patient record FIRST for insurance/address fallback ---
        patient_record = None
        
        # DEBUG
        print(f"EPACES: order.patient_id = {getattr(o, 'patient_id', None)}")
        print(f"EPACES: order.patient_last_name = {getattr(o, 'patient_last_name', None)}")
        print(f"EPACES: order.patient_first_name = {getattr(o, 'patient_first_name', None)}")
        
        # Try to get patient record by patient_id first
        if getattr(o, "patient_id", None):
            try:
                patient_record = fetch_patient_by_id(o.patient_id, folder_path=self.folder_path)
                print(f"EPACES: fetch_patient_by_id returned: {patient_record}")
            except Exception as e:
                print(f"EPACES: fetch_patient_by_id failed: {e}")
                pass
        
        # If no patient_id or lookup failed, try by name using case-insensitive lookup
        if not patient_record and o.patient_last_name and o.patient_first_name:
            print(f"EPACES: Trying name lookup for last='{o.patient_last_name}', first='{o.patient_first_name}'")
            try:
                from dmelogic.db.patients import find_patient_by_name_and_dob
                # Try DOB in MM/DD/YYYY format first (patient DB format), then ISO, then without DOB
                dob_formats = []
                if getattr(o, 'patient_dob', None):
                    dob_formats = [
                        o.patient_dob.strftime("%m/%d/%Y"),  # Patient DB format
                        o.patient_dob.strftime("%Y-%m-%d"),  # ISO format
                    ]
                dob_formats.append(None)  # Fallback: no DOB filter
                
                for dob_str in dob_formats:
                    print(f"EPACES: Trying DOB = '{dob_str}'")
                    patient_record = find_patient_by_name_and_dob(
                        o.patient_last_name,
                        o.patient_first_name,
                        dob=dob_str,
                        folder_path=self.folder_path
                    )
                    if patient_record:
                        break
                if patient_record:
                    print(f"EPACES: FOUND patient! address='{patient_record['address']}'")
                else:
                    print(f"EPACES: Patient NOT FOUND by name lookup")
            except Exception as e:
                import traceback
                print(f"EPACES: name lookup failed: {e}")
                traceback.print_exc()
                pass

        # Primary / secondary insurance — always use patient record as
        # authoritative source.  The order's primary_insurance field stores
        # whichever payer the order is billed to (could be secondary), so
        # it is NOT a reliable source for the patient's actual primary.
        primary_name = ""
        primary_id = ""
        secondary_name = ""
        secondary_id = ""
        primary_billed_via = ""
        secondary_billed_via = ""

        def _get_field(row, *field_names):
            """Safely get a field from a sqlite Row using multiple possible field names."""
            if hasattr(row, "keys"):
                row_keys = row.keys()
                for k in field_names:
                    if k in row_keys:
                        val = row[k]
                        if val:
                            return str(val).strip()
            return ""

        if patient_record:
            try:
                # Prefer newly added payer-specific fields first, then legacy insurance fields.
                primary_name = _get_field(patient_record, "primary_payer", "primary_insurance")
                primary_id = _get_field(
                    patient_record,
                    "primary_payer_policy_number",
                    "policy_number",
                    "primary_insurance_id",
                )
                primary_billed_via = _get_field(patient_record, "primary_billed_via")
                secondary_name = _get_field(patient_record, "secondary_payer", "secondary_insurance")
                secondary_id = _get_field(patient_record, "secondary_insurance_id", "secondary_policy", "secondary_policy_number")
                secondary_billed_via = _get_field(patient_record, "secondary_billed_via")
                print(f"EPACES: Insurance from patient record - pri: {primary_name}/{primary_id}, sec: {secondary_name}/{secondary_id}")
            except Exception as e:
                print(f"EPACES: Failed to get insurance from patient: {e}")

        # Fallback to order fields only when patient record is unavailable
        if not primary_name and not primary_id:
            primary_name = (
                getattr(o, "primary_payer", None)
                or getattr(o, "primary_payer_at_order_time", None)
                or getattr(o, "insurance_name_at_order_time", None)
                or getattr(o, "primary_insurance", None)
                or getattr(o, "primary_insurance_name", "")
                or ""
            )
            primary_id = (
                getattr(o, "primary_payer_policy_number", None)
                or getattr(o, "primary_payer_policy_number_at_order_time", None)
                or getattr(o, "insurance_id_at_order_time", None)
                or getattr(o, "primary_insurance_id", None)
                or getattr(o, "policy_number", None)
                or ""
            )
        if not secondary_name and not secondary_id:
            secondary_name = (
                getattr(o, "secondary_payer", None)
                or getattr(o, "secondary_payer_at_order_time", None)
                or getattr(o, "secondary_insurance_name_at_order_time", None)
                or getattr(o, "secondary_insurance", None)
                or getattr(o, "secondary_insurance_name", "")
                or ""
            )
            secondary_id = (
                getattr(o, "secondary_insurance_id_at_order_time", None)
                or getattr(o, "secondary_insurance_id", None)
                or ""
            )
        if not primary_billed_via:
            primary_billed_via = getattr(o, "primary_billed_via", None) or ""
        if not secondary_billed_via:
            secondary_billed_via = getattr(o, "secondary_billed_via", None) or ""

        self._primary_ins_id = primary_id or ""
        self._secondary_ins_id = secondary_id or ""
        self._primary_payer_name = primary_name or ""
        self._secondary_payer_name = secondary_name or ""

        # Normalize insurance name: "EPACES..." -> "MEDICAID"
        if primary_name and "EPACES" in primary_name.upper():
            primary_name = "MEDICAID"
        
        prim_line = "None"
        if primary_name:
            prim_line = primary_name
            if primary_id:
                prim_line += f" (ID: {primary_id})"
        elif primary_id:
            prim_line = f"ID: {primary_id}"
        self.lbl_primary_ins.setText(prim_line)
        self.lbl_primary_member.setText(primary_id or "")

        # Normalize insurance name: "EPACES..." -> "MEDICAID"
        if secondary_name and "EPACES" in secondary_name.upper():
            secondary_name = "MEDICAID"
        
        sec_line = "None"
        if secondary_name:
            sec_line = secondary_name
            if secondary_id:
                sec_line += f" (ID: {secondary_id})"
        elif secondary_id:
            sec_line = f"ID: {secondary_id}"
        self.lbl_secondary_ins.setText(sec_line)
        self.lbl_primary_billed_via.setText(primary_billed_via or "None")
        self.lbl_secondary_billed_via.setText(secondary_billed_via or "None")

        has_secondary = bool((secondary_name or "").strip() or (secondary_id or "").strip())
        self.chk_bill_secondary.setEnabled(has_secondary)

        saved_selection = self._load_billing_payer_selection()
        if saved_selection == "Secondary" and has_secondary:
            self.chk_bill_secondary.setChecked(True)
            self.chk_bill_primary.setChecked(False)
        else:
            self.chk_bill_primary.setChecked(True)
            self.chk_bill_secondary.setChecked(False)

        self._update_selected_billing_payer_label()

        # --- Delivery Date and Tracking Number -----------------------
        from PyQt6.QtCore import QDate
        
        if hasattr(o, "delivery_date") and o.delivery_date:
            if isinstance(o.delivery_date, str):
                parsed = None
                # Try ISO first (YYYY-MM-DD), then fallback to MM/DD/YYYY
                try:
                    y, m, d = o.delivery_date.split("-")
                    parsed = QDate(int(y), int(m), int(d))
                except Exception:
                    try:
                        parts = o.delivery_date.split("/")
                        if len(parts) == 3:
                            month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
                            parsed = QDate(year, month, day)
                    except Exception:
                        parsed = None
                if parsed and parsed.isValid():
                    self.delivery_date_edit.setDate(parsed)
                else:
                    self.delivery_date_edit.setDate(QDate(2000, 1, 1))  # Blank
            else:
                # date object
                self.delivery_date_edit.setDate(QDate(o.delivery_date.year, o.delivery_date.month, o.delivery_date.day))
        else:
            # No delivery date - leave blank
            self.delivery_date_edit.setDate(QDate(2000, 1, 1))

        tracking_num = getattr(o, "tracking_number", None) or ""
        self.tracking_number_edit.setText(tracking_num)

        billing_conf = self._load_billing_confirmation_number()
        self.billing_confirmation_edit.setText(billing_conf)
        
        # Resolve patient address (patients.db first, then order snapshot)
        patient_db_path = resolve_db_path("patients.db", folder_path=self.folder_path)
        resolved_address = get_patient_full_address(
            patient_db_path,
            getattr(o, "patient_id", None),
            getattr(o, "patient_last_name", None),
            getattr(o, "patient_first_name", None),
        )

        def _address_parts() -> tuple[str, str, str, str]:
            """Return (street, city, state, zip_code) with best-available data."""

            def _field(row, key) -> str:
                try:
                    if hasattr(row, "keys") and key in row.keys():
                        return (row[key] or "").strip()
                except Exception:
                    return ""
                return ""

            # Prefer patient record fields if present
            street = _field(patient_record, "address") if patient_record else ""
            city = _field(patient_record, "city") if patient_record else ""
            state = _field(patient_record, "state") if patient_record else ""
            # Try both zip_code and zip column names
            zip_code = (_field(patient_record, "zip_code") or _field(patient_record, "zip")) if patient_record else ""

            # Fall back to order snapshot values if missing
            street = street or (
                getattr(o, "patient_address_at_order_time", None)
                or getattr(o, "patient_address", None)
                or ""
            ).strip()
            city = city or (
                getattr(o, "patient_city_at_order_time", None)
                or getattr(o, "patient_city", None)
                or ""
            ).strip()
            state = state or (
                getattr(o, "patient_state_at_order_time", None)
                or getattr(o, "patient_state", None)
                or ""
            ).strip()
            zip_code = zip_code or (
                getattr(o, "patient_zip_at_order_time", None)
                or getattr(o, "patient_zip", None)
                or ""
            ).strip()

            return street, city, state, zip_code

        street_line, city_line, state_line, zip_line = _address_parts()

        # If get_patient_full_address returned a single string, use it only to fill blanks
        if resolved_address:
            parts = [p.strip() for p in resolved_address.split(",") if p.strip()]
            if not street_line and parts:
                street_line = parts[0]
            if (not city_line or not state_line) and len(parts) > 1:
                # Assume next segment contains city/state
                remainder = ",".join(parts[1:]).strip()
                # naive parse city state zip
                tokens = remainder.replace("  ", " ").split()
                if tokens:
                    if not city_line:
                        city_line = tokens[0].rstrip(",")
                    if len(tokens) > 1 and not state_line:
                        state_line = tokens[1].rstrip(",")
                    if len(tokens) > 2 and not zip_line:
                        zip_line = tokens[2]

        if not street_line and not city_line and not state_line and not zip_line:
            street_line = "N/A"

        print(f"EPACES: Address split -> street='{street_line}', city='{city_line}', state='{state_line}', zip='{zip_line}'")
        city_state_line = ", ".join([p for p in [city_line, state_line] if p])
        self.lbl_address_street.setText(street_line)
        self.lbl_address_city_state.setText(city_state_line)
        self.lbl_address_zip.setText(zip_line)

        # ICD-10s
        if getattr(o, "icd_codes", None):
            for code in o.icd_codes:
                if not code:
                    continue
                row = QHBoxLayout()
                row.setSpacing(4)

                # Remove period from ICD-10 code for EPACES display only
                display_code = code.replace(".", "")
                
                lbl = QLabel(display_code)
                lbl.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                row.addWidget(lbl)

                btn = self._make_copy_button(
                    f"Copy ICD-10 {display_code}", _make_value_copier(display_code)
                )
                row.addWidget(btn)

                row.addStretch()
                container = QWidget()
                container.setLayout(row)
                self.icd_codes_layout.addWidget(container)
        else:
            self.icd_codes_layout.addWidget(QLabel("(no ICD-10 codes on order)"))

        self._populate_items_table(list(o.items or []))

        # Add fee schedule PA Type + Max Units columns
        try:
            self._fee_reader = DbFeeScheduleReader(folder_path=self.folder_path)
            add_pa_type_column_to_epaces(self.items_table, self._fee_reader, hcpcs_col=2)
            add_max_units_column_to_epaces(self.items_table, self._fee_reader, hcpcs_col=2, qty_col=3)
        except Exception as _e:
            print(f"[ePACES] Fee schedule columns skipped: {_e}")

    # ---------- Item rows (buttons above values) ------------------------

    def _make_value_cell(self, display: str, tooltip: str, copy_value: str) -> QWidget:
        """
        Create a horizontal layout widget:

            [ display ]  [Copy]

        Used for ITEM, HCPCS, QTY, AMOUNT, MODS.
        """
        w = QWidget()
        w.setMinimumHeight(36)  # Ensure cell height accommodates button
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 4, 6, 4)  # Increased horizontal padding
        h.setSpacing(8)  # Increased spacing between label and button

        lbl = QLabel(display)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)  # Vertical centering
        h.addWidget(lbl, 1)

        btn = self._make_copy_button(tooltip, _make_value_copier(copy_value))
        h.addWidget(btn)
        h.addStretch(0)  # Allow expansion to right

        return w

    def _make_pa_cell(self, item_id: int, initial_value: str = "") -> QWidget:
        """
        Horizontal layout for PA column:

            [ QLineEdit (PA #) ]  [Copy]
        
        Args:
            item_id: Order item ID (for saving)
            initial_value: Pre-fill the PA # if already saved
        """
        w = QWidget()
        w.setMinimumHeight(36)  # Ensure cell height accommodates widgets
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 4, 6, 4)  # Increased horizontal padding
        h.setSpacing(8)  # Increased spacing between input and button

        edit = QLineEdit()
        edit.setPlaceholderText("PA # (12 max)")
        edit.setMaxLength(12)
        edit.setMinimumHeight(28)  # Ensure input field is fully visible
        edit.setMinimumWidth(140)  # Ensure 12 characters fit comfortably
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        edit.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        # Pre-fill with saved PA# if available
        if initial_value:
            edit.setText(initial_value)
        
        # Store reference for saving later
        self._pa_edits[item_id] = edit
        
        h.addWidget(edit, 1)

        def copy_pa(_checked: bool = False) -> None:
            _copy(edit.text().strip())

        btn = self._make_copy_button("Copy PA #", copy_pa)
        h.addWidget(btn)
        h.addStretch(0)  # Allow expansion to right

        return w

    def _populate_items_table(self, items: list[OrderItem], pa_overrides: dict[int, str] | None = None) -> None:
        self.items_table.setRowCount(0)
        # Reset column count to base 7 (PA TYPE + MAX UNITS are re-added after)
        self.items_table.setColumnCount(7)
        self.items_table.setHorizontalHeaderLabels(
            ["ITEM", "DESCRIPTION", "HCPCS", "QTY", "AMOUNT", "MD DIRECTIONS", "PA #"]
        )
        self._configure_items_table_columns()
        self._items_data = list(items)
        claim_total = Decimal("0.00")

        for item in items:
            # Get full HCPCS code from order (e.g., "T4521-FRBRFSMAL" or "E0244+E0243-ITEMNUM")
            full_hcpcs = (item.hcpcs_code or "").strip()
            
            # HCPCS display - show full code if multi-code (contains +), otherwise base code only
            if "+" in full_hcpcs:
                # Multi-HCPCS code (e.g., E0244+E0243) - show full code without item suffix
                display_hcpcs = full_hcpcs.split("-")[0] if "-" in full_hcpcs else full_hcpcs
            else:
                # Single HCPCS - extract base code (first 5 characters)
                display_hcpcs = full_hcpcs[:5] if len(full_hcpcs) >= 5 else full_hcpcs
            base_hcpcs = display_hcpcs
            
            # Get item number from order and look up proper formatting in inventory
            item_number = ""
            
            # First try the item_number field from order
            if hasattr(item, "item_number") and item.item_number:
                item_number = item.item_number.strip()
            
            # If not in order, extract from HCPCS code (part after hyphen)
            if not item_number and "-" in full_hcpcs:
                item_number_raw = full_hcpcs.split("-", 1)[1].strip()
                # Look up in inventory to get properly formatted version
                try:
                    from dmelogic.db.inventory import fetch_latest_item_by_hcpcs
                    inv_data = fetch_latest_item_by_hcpcs(full_hcpcs, folder_path=self.folder_path)
                    if inv_data and inv_data.get("item_number"):
                        item_number = inv_data["item_number"]
                    else:
                        # Fallback to raw extracted value
                        item_number = item_number_raw
                except Exception:
                    item_number = item_number_raw

            qty = str(item.quantity or 0)

            # Amount: prefer total_cost; else cost_ea * qty
            line_total = Decimal("0.00")
            if item.total_cost is not None:
                line_total = item.total_cost
            elif item.cost_ea is not None:
                try:
                    line_total = (item.cost_ea or Decimal("0.00")) * Decimal(
                        str(item.quantity)
                    )
                except Exception:
                    line_total = Decimal("0.00")

            amount_val = f"{line_total:.2f}" if line_total != Decimal("0.00") else ""

            claim_total += line_total

            # MD Directions from item
            directions = getattr(item, "directions", None) or ""

            row = self.items_table.rowCount()
            self.items_table.insertRow(row)
            self.items_table.setRowHeight(row, 42)  # Increased to accommodate widgets fully

            description = item.description or ""

            # ITEM cell (inventory item number, e.g., "FITEXTRASM")
            item_cell = self._make_value_cell(
                display=item_number,
                tooltip="Copy item number",
                copy_value=item_number,
            )
            self.items_table.setCellWidget(row, 0, item_cell)

            # DESCRIPTION cell
            description_cell = self._make_value_cell(
                display=description,
                tooltip="Copy item description",
                copy_value=description,
            )
            self.items_table.setCellWidget(row, 1, description_cell)

            # HCPCS cell (shows full multi-HCPCS with + sign, or base code for single HCPCS)
            hcpcs_cell = self._make_value_cell(
                display=display_hcpcs,
                tooltip="Copy HCPCS code",
                copy_value=display_hcpcs,
            )
            self.items_table.setCellWidget(row, 2, hcpcs_cell)

            # QTY cell
            qty_cell = self._make_value_cell(
                display=qty,
                tooltip="Copy QTY",
                copy_value=qty,
            )
            self.items_table.setCellWidget(row, 3, qty_cell)

            # AMOUNT cell
            amt_cell = self._make_value_cell(
                display=amount_val,
                tooltip="Copy AMOUNT",
                copy_value=amount_val,
            )
            self.items_table.setCellWidget(row, 4, amt_cell)

            # MD DIRECTIONS cell
            directions_cell = self._make_value_cell(
                display=directions,
                tooltip="Copy MD directions",
                copy_value=directions,
            )
            self.items_table.setCellWidget(row, 5, directions_cell)

            # PA # cell (line edit + Copy button horizontally)
            # Use override value if available (from drag-and-drop rebuild), else saved value
            if pa_overrides and item.id in pa_overrides:
                saved_pa = pa_overrides[item.id]
            else:
                saved_pa = getattr(item, "pa_number", None) or ""
            pa_cell = self._make_pa_cell(item.id, saved_pa)
            self.items_table.setCellWidget(row, 6, pa_cell)

            # Set vertical header to drag grip icon
            grip_item = QTableWidgetItem("⋮⋮")
            grip_item.setToolTip("Drag to reorder")
            self.items_table.setVerticalHeaderItem(row, grip_item)

        # Update claim total label + raw copy value
        self._claim_total_raw = f"{claim_total:.2f}"
        self.lbl_claim_total.setText(f"Claim Total: {self._claim_total_raw}")

    # ------------------------------------------------------------------ Delivery info row

    def _on_row_moved(self, logical_index: int, old_visual: int, new_visual: int) -> None:
        """Handle row drag-and-drop reorder via vertical header."""
        vheader = self.items_table.verticalHeader()
        vheader.blockSignals(True)

        try:
            # Save current PA values from widgets before rebuild
            pa_overrides: dict[int, str] = {}
            for item_id, edit in self._pa_edits.items():
                pa_overrides[item_id] = edit.text().strip()

            # Compute new item order from current visual indices
            new_order: list[OrderItem] = []
            for visual_idx in range(vheader.count()):
                logical_idx = vheader.logicalIndex(visual_idx)
                if logical_idx < len(self._items_data):
                    new_order.append(self._items_data[logical_idx])

            # Update stored items
            self._items_data = new_order

            # Persist new sort order to database
            self._save_sort_order(new_order)

            # Update in-memory order object so parent views see the change
            if self.order:
                self.order.items = list(new_order)

            # Rebuild items table with the new order (resets header visual mapping)
            self._pa_edits.clear()
            self._populate_items_table(new_order, pa_overrides=pa_overrides)

            # Re-add fee schedule columns if reader is available
            if self._fee_reader:
                try:
                    add_pa_type_column_to_epaces(
                        self.items_table, self._fee_reader, hcpcs_col=2
                    )
                    add_max_units_column_to_epaces(
                        self.items_table, self._fee_reader, hcpcs_col=2, qty_col=3
                    )
                except Exception:
                    pass

            # Refresh parent order editor / order list
            self._refresh_parent_orders()
        finally:
            vheader.blockSignals(False)

    def _show_item_context_menu(self, pos) -> None:
        """Show context menu for assigning items to prescribers."""
        from PyQt6.QtWidgets import QMenu
        
        # Get clicked row
        row = self.items_table.rowAt(pos.y())
        if row < 0 or row >= len(self._items_data):
            return
        
        item = self._items_data[row]
        if not item.id:
            return
        
        # Get prescriber info from order
        presc1_name = getattr(self.order, 'prescriber_name', '') or 'Prescriber 1'
        presc2_name = getattr(self.order, 'prescriber_name_2', '') or ''
        
        menu = QMenu(self)
        
        # Action: Assign to Prescriber 1
        # Use default arg to capture item by value, not reference
        action1 = menu.addAction(f"Assign to: {presc1_name}")
        action1.triggered.connect(lambda checked=False, i=item: self._assign_item_to_prescriber(i, 1))
        
        # Action: Assign to Prescriber 2 (only if prescriber 2 exists)
        if presc2_name:
            action2 = menu.addAction(f"Assign to: {presc2_name}")
            action2.triggered.connect(lambda checked=False, i=item: self._assign_item_to_prescriber(i, 2))
        
        menu.exec(self.items_table.mapToGlobal(pos))

    def _assign_item_to_prescriber(self, item: OrderItem, prescriber_num: int) -> None:
        """Assign an item to prescriber 1 or 2 and update the database."""
        from dmelogic.db.orders import update_order_item
        
        print(f"[EPACES] Assigning item {item.id} to prescriber {prescriber_num}")
        
        if prescriber_num == 1:
            # Use order-level prescriber 1
            prescriber_name = getattr(self.order, 'prescriber_name', '') or ''
            prescriber_npi = getattr(self.order, 'prescriber_npi', '') or ''
        else:
            # Use order-level prescriber 2
            prescriber_name = getattr(self.order, 'prescriber_name_2', '') or ''
            prescriber_npi = getattr(self.order, 'prescriber_npi_2', '') or ''
        
        print(f"[EPACES] prescriber_name={prescriber_name}, prescriber_npi={prescriber_npi}")
        print(f"[EPACES] folder_path={self.folder_path}")
        
        try:
            # Update database
            update_order_item(
                item.id,
                {
                    "prescriber_name": prescriber_name,
                    "prescriber_npi": prescriber_npi,
                },
                folder_path=self.folder_path
            )
            
            # Update local item object
            item.prescriber_name = prescriber_name
            item.prescriber_npi = prescriber_npi
            
            # Refresh the items table
            self._populate_items_table(self._items_data)
            
            # Re-add fee schedule columns if available
            if self._fee_reader:
                try:
                    add_pa_type_column_to_epaces(self.items_table, self._fee_reader, hcpcs_col=2)
                    add_max_units_column_to_epaces(self.items_table, self._fee_reader, hcpcs_col=2, qty_col=3)
                except Exception:
                    pass
            
            print(f"✅ Assigned item {item.id} to {prescriber_name}")
        except Exception as e:
            print(f"❌ Failed to assign prescriber: {e}")
            QMessageBox.warning(self, "Error", f"Failed to assign prescriber: {e}")

    def _save_sort_order(self, items: list[OrderItem]) -> None:
        """Persist the new item display order to the database."""
        try:
            orders_db = resolve_db_path("orders.db", folder_path=self.folder_path)
            conn = sqlite3.connect(orders_db)
            cursor = conn.cursor()

            # Ensure sort_order column exists (safe for older DBs)
            try:
                cursor.execute("ALTER TABLE order_items ADD COLUMN sort_order INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Column already exists

            for position, item in enumerate(items):
                if item.id is not None:
                    cursor.execute(
                        "UPDATE order_items SET sort_order = ? WHERE id = ?",
                        (position, item.id),
                    )

            conn.commit()
            conn.close()
            print(f"✅ Saved item sort order for {len(items)} items")
        except Exception as e:
            print(f"❌ Failed to save sort order: {e}")

    def _build_delivery_info_row(self) -> QHBoxLayout:
        """Build delivery date, tracking number, and billing confirmation row."""
        from PyQt6.QtWidgets import QDateEdit
        from PyQt6.QtCore import QDate
        
        row_delivery = QHBoxLayout()
        row_delivery.setSpacing(10)  # Increased spacing

        # Delivery Date with calendar picker
        self.delivery_date_edit = QDateEdit()
        self.delivery_date_edit.setCalendarPopup(True)
        self.delivery_date_edit.setDisplayFormat("MM/dd/yyyy")
        self.delivery_date_edit.setMinimumDate(QDate(2000, 1, 1))
        self.delivery_date_edit.setSpecialValueText(" ")  # Allow clearing the date (displays as blank)
        self.delivery_date_edit.setDate(QDate(2000, 1, 1))  # Start blank
        self.delivery_date_edit.setMinimumHeight(28)  # Ensure fully visible
        self.delivery_date_edit.setMinimumWidth(120)  # Ensure date fits
        # Default calendar popup to today when date is blank
        self.delivery_date_edit.calendarWidget().installEventFilter(self)
        row_delivery.addWidget(QLabel("Delivery Date:"))
        row_delivery.addWidget(self.delivery_date_edit, 2)

        btn_delivery_copy = self._make_copy_button(
            "Copy delivery date",
            lambda: _copy(self.delivery_date_edit.date().toString("MM/dd/yyyy")),
        )
        row_delivery.addWidget(btn_delivery_copy)

        # Shipper's Order Number (editable)
        self.tracking_number_edit = QLineEdit()
        self.tracking_number_edit.setPlaceholderText("Enter shipper's order number")
        self.tracking_number_edit.setMinimumHeight(28)  # Ensure fully visible
        self.tracking_number_edit.setMinimumWidth(180)  # Ensure text fits
        self.tracking_number_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Auto-fill delivery date and mark shipped when tracking number entered
        self.tracking_number_edit.textChanged.connect(self._on_tracking_number_changed)
        row_delivery.addSpacing(24)
        row_delivery.addWidget(QLabel("Shipper's Order #:"))
        row_delivery.addWidget(self.tracking_number_edit, 2)

        btn_tracking_copy = self._make_copy_button(
            "Copy shipper's order number",
            lambda: _copy(self.tracking_number_edit.text()),
        )
        row_delivery.addWidget(btn_tracking_copy)

        # Billing Confirmation Number (editable)
        self.billing_confirmation_edit = QLineEdit()
        self.billing_confirmation_edit.setPlaceholderText("Enter billing confirmation #")
        self.billing_confirmation_edit.setMinimumHeight(28)
        self.billing_confirmation_edit.setMinimumWidth(180)
        self.billing_confirmation_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row_delivery.addSpacing(24)
        row_delivery.addWidget(QLabel("Billing Confirmation #:"))
        row_delivery.addWidget(self.billing_confirmation_edit, 2)

        btn_billing_conf_copy = self._make_copy_button(
            "Copy billing confirmation number",
            lambda: _copy(self.billing_confirmation_edit.text()),
        )
        row_delivery.addWidget(btn_billing_conf_copy)

        return row_delivery

    def _ensure_orders_column(self, column_name: str, column_type: str = "TEXT") -> None:
        """Ensure a column exists on orders table (safe no-op if already present)."""
        orders_db = resolve_db_path("orders.db", folder_path=self.folder_path)
        conn = sqlite3.connect(orders_db)
        try:
            cursor = conn.cursor()
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {column_name} {column_type}")
            conn.commit()
        except sqlite3.OperationalError:
            # Column already exists.
            pass
        finally:
            conn.close()

    def _load_billing_confirmation_number(self) -> str:
        """Load billing confirmation number from orders table for current order."""
        try:
            self._ensure_orders_column("billing_confirmation_number", "TEXT")
            orders_db = resolve_db_path("orders.db", folder_path=self.folder_path)
            conn = sqlite3.connect(orders_db)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(billing_confirmation_number, '') FROM orders WHERE id = ?",
                (self.order.id,),
            )
            row = cursor.fetchone()
            conn.close()
            return (row[0] if row and row[0] is not None else "").strip()
        except Exception:
            return ""

    def _load_billing_payer_selection(self) -> str:
        """Load saved billing payer selection (Primary/Secondary) for current order."""
        try:
            self._ensure_orders_column("billing_payer_selection", "TEXT")
            orders_db = resolve_db_path("orders.db", folder_path=self.folder_path)
            conn = sqlite3.connect(orders_db)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(billing_payer_selection, '') FROM orders WHERE id = ?",
                (self.order.id,),
            )
            row = cursor.fetchone()
            conn.close()
            value = (row[0] if row and row[0] is not None else "").strip()
            return value if value in ("Primary", "Secondary") else "Primary"
        except Exception:
            return "Primary"

    def _selected_billing_payer_id(self) -> str:
        if self._billing_payer_selection == "Secondary":
            return self._secondary_ins_id or ""
        return self._primary_ins_id or ""

    def _selected_billing_payer_name(self) -> str:
        if self._billing_payer_selection == "Secondary":
            return self._secondary_payer_name or ""
        return self._primary_payer_name or ""

    def _update_selected_billing_payer_label(self) -> None:
        payer_name = self._selected_billing_payer_name()
        payer_id = self._selected_billing_payer_id()
        label = self._billing_payer_selection
        if payer_name:
            label = payer_name
        if payer_id:
            label = f"{label} (ID: {payer_id})"
        self.lbl_billing_payer.setText(label)

    def _on_bill_primary_toggled(self, checked: bool) -> None:
        if not checked:
            if not self.chk_bill_secondary.isChecked():
                self.chk_bill_primary.blockSignals(True)
                self.chk_bill_primary.setChecked(True)
                self.chk_bill_primary.blockSignals(False)
            return
        if self.chk_bill_secondary.isChecked():
            self.chk_bill_secondary.blockSignals(True)
            self.chk_bill_secondary.setChecked(False)
            self.chk_bill_secondary.blockSignals(False)
        self._billing_payer_selection = "Primary"
        self._update_selected_billing_payer_label()

    def _on_bill_secondary_toggled(self, checked: bool) -> None:
        if not checked:
            if not self.chk_bill_primary.isChecked():
                self.chk_bill_primary.blockSignals(True)
                self.chk_bill_primary.setChecked(True)
                self.chk_bill_primary.blockSignals(False)
            return
        if self.chk_bill_primary.isChecked():
            self.chk_bill_primary.blockSignals(True)
            self.chk_bill_primary.setChecked(False)
            self.chk_bill_primary.blockSignals(False)
        self._billing_payer_selection = "Secondary"
        self._update_selected_billing_payer_label()

    def eventFilter(self, obj, event):
        """Default calendar popup to today's page when delivery date is blank."""
        from PyQt6.QtCore import QEvent, QDate
        if event.type() == QEvent.Type.Show:
            if hasattr(self, 'delivery_date_edit') and obj is self.delivery_date_edit.calendarWidget():
                if self.delivery_date_edit.date() == self.delivery_date_edit.minimumDate():
                    today = QDate.currentDate()
                    obj.setCurrentPage(today.year(), today.month())
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ Status action

    def _on_save_delivery_info(self, quiet: bool = False) -> None:
        """Save delivery fields to order without changing status."""
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import QDate

        try:
            # Check if date is blank (minimum date means blank)
            date_val = self.delivery_date_edit.date()
            if date_val.isValid() and date_val.year() > 1900:
                delivery_date = date_val.toString("MM/dd/yyyy")
            else:
                delivery_date = None  # Store as NULL in database
            tracking_number = self.tracking_number_edit.text().strip()
            billing_confirmation_number = self.billing_confirmation_edit.text().strip()
            billing_payer_selection = "Secondary" if self.chk_bill_secondary.isChecked() else "Primary"
            billed_payer_name = self._selected_billing_payer_name()
            billed_payer_id = self._selected_billing_payer_id()

            self._ensure_orders_column("billing_confirmation_number", "TEXT")
            self._ensure_orders_column("billing_payer_selection", "TEXT")
            self._ensure_orders_column("billed_payer_name", "TEXT")
            self._ensure_orders_column("billed_payer_id", "TEXT")
            
            orders_db = resolve_db_path("orders.db", folder_path=self.folder_path)
            conn = sqlite3.connect(orders_db)
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE orders
                SET delivery_date = ?,
                    tracking_number = ?,
                    billing_confirmation_number = ?,
                    billing_payer_selection = ?,
                    billed_payer_name = ?,
                    billed_payer_id = ?
                WHERE id = ?
                """,
                (
                    delivery_date,
                    tracking_number,
                    billing_confirmation_number,
                    billing_payer_selection,
                    billed_payer_name,
                    billed_payer_id,
                    self.order.id,
                )
            )
            conn.commit()
            conn.close()
            
            # Update order object
            self.order.delivery_date = delivery_date
            self.order.tracking_number = tracking_number
            setattr(self.order, "billing_confirmation_number", billing_confirmation_number)
            setattr(self.order, "billing_payer_selection", billing_payer_selection)
            setattr(self.order, "billed_payer_name", billed_payer_name)
            setattr(self.order, "billed_payer_id", billed_payer_id)
            
            if not quiet:
                QMessageBox.information(
                    self,
                    "Saved",
                    "Delivery info saved successfully.",
                )
            self._refresh_parent_orders()
        except Exception as e:
            if not quiet:
                QMessageBox.warning(
                    self,
                    "Save Error",
                    f"Failed to save delivery info: {e}",
                )

    def _on_mark_billed(self) -> None:
        """
        Mark this order as BILLED via the workflow engine.
        Also saves delivery date, tracking number, and PA numbers first.
        """
        from PyQt6.QtWidgets import QMessageBox

        # First, save delivery info and PA numbers
        self._on_save_delivery_info()
        self._save_pa_numbers()

        folder = self.folder_path

        current = self.order.order_status.value
        new_status = OrderStatus.BILLED.value

        success, error = update_order_status_validated(
            order_id=self.order.id,
            current_status=current,
            new_status=new_status,
            folder_path=folder,
        )

        if not success:
            QMessageBox.warning(
                self,
                "Mark as Billed",
                error or "Failed to mark order as BILLED.",
            )
            return

        self.order.order_status = OrderStatus.BILLED
        QMessageBox.information(
            self,
            "Mark as Billed",
            "Order has been marked as BILLED.",
        )
        self._refresh_parent_orders()

    def _on_mark_shipped(self) -> None:
        """Mark this order as SHIPPED and save delivery/tracking first."""
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import QDate

        # Auto-set delivery date to today if not already set
        current_date = self.delivery_date_edit.date()
        if not current_date.isValid() or current_date.year() <= 2000:
            self.delivery_date_edit.setDate(QDate.currentDate())

        self._on_save_delivery_info()
        self._save_pa_numbers()

        folder = self.folder_path
        current = self.order.order_status.value
        new_status = OrderStatus.SHIPPED.value

        success, error = update_order_status_validated(
            order_id=self.order.id,
            current_status=current,
            new_status=new_status,
            folder_path=folder,
        )

        if not success:
            QMessageBox.warning(
                self,
                "Mark as Shipped",
                error or "Failed to mark order as SHIPPED.",
            )
            return

        self.order.order_status = OrderStatus.SHIPPED
        QMessageBox.information(
            self,
            "Mark as Shipped",
            "Order has been marked as SHIPPED.",
        )
        self._refresh_parent_orders()

    def _save_pa_numbers(self) -> bool:
        """
        Save all PA numbers entered in the dialog to the order_items table.
        
        Returns:
            True if save was successful, False otherwise
        """
        if not self._pa_edits:
            return True
        
        try:
            orders_db = resolve_db_path("orders.db", folder_path=self.folder_path)
            conn = sqlite3.connect(orders_db)
            cursor = conn.cursor()
            
            saved_count = 0
            for item_id, edit in self._pa_edits.items():
                pa_value = edit.text().strip()
                cursor.execute(
                    "UPDATE order_items SET pa_number = ? WHERE id = ?",
                    (pa_value, item_id)
                )
                if pa_value:
                    saved_count += 1
            
            conn.commit()
            conn.close()
            
            if saved_count > 0:
                print(f"✅ Saved PA# for {saved_count} items")
            
            return True
        except Exception as e:
            print(f"❌ Failed to save PA numbers: {e}")
            QMessageBox.warning(
                self,
                "Save Error",
                f"Failed to save PA numbers: {e}",
            )
            return False

    def _save_pa_and_close(self) -> None:
        """Save delivery info, PA numbers and close the dialog."""
        self._on_save_delivery_info(quiet=True)
        self._save_pa_numbers()
        self._refresh_parent_orders()
        self.accept()

    def _on_tracking_number_changed(self, text: str) -> None:
        """Auto-fill delivery date to today and mark as Shipped when a tracking number is entered."""
        from PyQt6.QtCore import QDate
        text = text.strip()
        if text:
            # Auto-fill delivery date to today if blank
            current_date = self.delivery_date_edit.date()
            if not current_date.isValid() or current_date <= self.delivery_date_edit.minimumDate():
                self.delivery_date_edit.setDate(QDate.currentDate())
            # Auto-set status text to Shipped (visual indicator)
            if hasattr(self, '_auto_shipped_done'):
                return  # Only auto-ship once per session
            self._auto_shipped_done = True
            # Silently mark as shipped in DB
            try:
                folder = self.folder_path
                current = self.order.order_status.value
                new_status = OrderStatus.SHIPPED.value
                if current != new_status:
                    success, error = update_order_status_validated(
                        order_id=self.order.id,
                        current_status=current,
                        new_status=new_status,
                        folder_path=folder,
                    )
                    if success:
                        self.order.order_status = OrderStatus.SHIPPED
            except Exception:
                pass  # Non-critical - user can still click Mark as Shipped

    def _print_delivery_ticket(self) -> None:
        """Print delivery ticket using ReportLab."""
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib import colors
            from reportlab.lib.units import inch
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        except ImportError:
            QMessageBox.critical(
                self,
                "Print Delivery Ticket",
                "ReportLab is not available. Please install it:\n\npip install reportlab"
            )
            return

        import os
        import traceback
        from datetime import datetime
        from pathlib import Path

        order = self.order

        try:
            # File path - save to Downloads folder
            try:
                downloads = str(Path.home() / "Downloads")
                folder_path = downloads if os.path.exists(downloads) else str(Path.home())
            except Exception:
                folder_path = os.getcwd()
            
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            order_num = f"ORD-{order.id}"
            out_name = f"DeliveryTicket_{order_num}_{ts}.pdf"
            file_path = os.path.join(folder_path, out_name)

            # Setup PDF styles
            styles = getSampleStyleSheet()
            heading_style = ParagraphStyle(
                'ItemHeading',
                parent=styles['Heading3'],
                fontSize=11,
                textColor=colors.HexColor('#2c3e50'),
                spaceAfter=6,
            )

            # Format patient name
            patient_name = order.patient_full_name or "N/A"
            
            # Format dates
            def format_date(val):
                if not val:
                    return ""
                if isinstance(val, str):
                    try:
                        from datetime import datetime as dt
                        parsed = dt.strptime(val, "%Y-%m-%d")
                        return parsed.strftime("%m/%d/%Y")
                    except Exception:
                        return val
                else:
                    return val.strftime("%m/%d/%Y") if hasattr(val, "strftime") else str(val)
            
            rx_date = format_date(order.rx_date)
            order_date = format_date(order.order_date)
            patient_dob = format_date(order.patient_dob)
            patient_phone = order.patient_phone or ""
            patient_address = order.patient_address or ""
            prescriber_name = order.prescriber_name_at_order_time or ""
            prescriber_npi = order.prescriber_npi_at_order_time or ""
            doctor_directions = order.doctor_directions or ""
            special_instructions = order.special_instructions or ""

            # Build story
            story = []
            story.append(Paragraph("DELIVERY TICKET", styles['Title']))
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph(f"<b>Order #:</b> {order_num}", styles['Heading3']))
            story.append(Spacer(1, 0.15 * inch))

            # Patient/Prescriber block
            pt_tbl_data = [
                [
                    Paragraph("<b>Patient</b>", heading_style),
                    '',
                    Paragraph("<b>Prescriber</b>", heading_style)
                ],
                [
                    Paragraph(f"<b>Name:</b> {patient_name}", styles['Normal']),
                    Paragraph(
                        f"<b>DOB:</b> {patient_dob}<br/>"
                        f"<b>Phone:</b> {patient_phone}<br/>"
                        f"<b>Address:</b> {patient_address}",
                        styles['Normal']
                    ),
                    Paragraph(f"<b>Name:</b> {prescriber_name}", styles['Normal']),
                ],
                [
                    '',
                    '',
                    Paragraph(f"<b>NPI:</b> {prescriber_npi}", styles['Normal'])
                ],
            ]
            pt_tbl = Table(pt_tbl_data, colWidths=[1.5*inch, 3.5*inch, 3.0*inch])
            pt_tbl.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(pt_tbl)
            story.append(Spacer(1, 0.15 * inch))

            # Order metadata
            md_table = Table([
                [
                    Paragraph('<b>RX Date</b>', styles['Normal']),
                    rx_date or 'N/A',
                    Paragraph('<b>Order Date</b>', styles['Normal']),
                    order_date or 'N/A'
                ],
            ], colWidths=[1.2*inch, 2.5*inch, 1.5*inch, 2.8*inch])
            md_table.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey),
                ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ]))
            story.append(md_table)
            story.append(Spacer(1, 0.2 * inch))

            # Items table
            story.append(Paragraph("ITEMS", heading_style))
            item_rows = [['HCPCS', 'Description', 'Qty', 'Days']]
            
            for item in order.items:
                full_hcpcs = (item.hcpcs_code or "").strip()
                # Display full HCPCS code if multi-code (contains +), otherwise base code only
                if "+" in full_hcpcs:
                    # Multi-HCPCS code (e.g., E0244+E0243) - show full code without item suffix
                    display_hcpcs = full_hcpcs.split("-")[0].strip() if "-" in full_hcpcs else full_hcpcs
                else:
                    # Single HCPCS - extract base code (first 5 characters)
                    display_hcpcs = full_hcpcs[:5] if len(full_hcpcs) >= 5 else full_hcpcs
                desc = item.description or ""
                qty = str(item.quantity or 0)
                days = str(item.days_supply or 0)
                item_rows.append([display_hcpcs, desc, qty, days])
            
            t = Table(item_rows, colWidths=[1.2*inch, 4.5*inch, 0.7*inch, 0.8*inch])
            t.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,0), 10),
                ('ALIGN', (2,0), (3,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('FONTNAME', (0,1), (-1,-1), 'Helvetica'),
                ('FONTSIZE', (0,1), (-1,-1), 9),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8f9fa')]),
            ]))
            story.append(t)
            story.append(Spacer(1, 0.2 * inch))

            # Doctor's Directions
            if doctor_directions:
                story.append(Paragraph("DOCTOR'S DIRECTIONS", heading_style))
                story.append(Spacer(1, 0.1 * inch))
                directions_text = Paragraph(
                    doctor_directions.replace('\n', '<br/>'),
                    ParagraphStyle(
                        'Directions',
                        parent=styles['Normal'],
                        fontSize=10,
                        leading=14,
                        leftIndent=10,
                        rightIndent=10,
                        spaceAfter=10,
                        textColor=colors.HexColor('#2c3e50'),
                        backColor=colors.HexColor('#fffef0'),
                        borderPadding=8,
                        borderWidth=2,
                        borderColor=colors.HexColor('#f0ad4e')
                    )
                )
                story.append(directions_text)
                story.append(Spacer(1, 0.2 * inch))

            # Signature section
            story.append(Spacer(1, 0.4 * inch))
            sig_style = ParagraphStyle(
                'Signature',
                parent=styles['Normal'],
                fontSize=10,
                leading=14
            )
            
            name_table = Table(
                [[
                    Paragraph("Print Name:", sig_style),
                    '',
                    Paragraph("Relationship:", sig_style),
                    ''
                ]],
                colWidths=[1.2*inch, 3.0*inch, 1.3*inch, 2.2*inch],
                rowHeights=[0.4*inch]
            )
            name_table.setStyle(TableStyle([
                ('LINEBELOW', (1,0), (1,0), 1, colors.black),
                ('LINEBELOW', (3,0), (3,0), 1, colors.black),
                ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
            ]))
            story.append(name_table)

            story.append(Spacer(1, 0.15 * inch))
            sig_table = Table(
                [[
                    Paragraph("Signature:", sig_style),
                    '',
                    Paragraph("Date:", sig_style),
                    ''
                ]],
                colWidths=[1.2*inch, 4.6*inch, 0.8*inch, 1.2*inch],
                rowHeights=[0.4*inch]
            )
            sig_table.setStyle(TableStyle([
                ('LINEBELOW', (1,0), (1,0), 1, colors.black),
                ('LINEBELOW', (3,0), (3,0), 1, colors.black),
                ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
            ]))
            story.append(sig_table)

            story.append(Spacer(1, 0.15 * inch))
            story.append(
                Paragraph(
                    "I acknowledge receipt of the items listed above in good condition.",
                    ParagraphStyle(
                        'Acknowledgment',
                        parent=styles['Normal'],
                        fontSize=9,
                        textColor=colors.grey,
                        alignment=1
                    )
                )
            )

            # Note for Delivery
            if special_instructions:
                story.append(Spacer(1, 0.3 * inch))
                story.append(Paragraph("📋 NOTE FOR DELIVERY", heading_style))
                story.append(Spacer(1, 0.1 * inch))
                special_text = Paragraph(
                    special_instructions.replace('\n', '<br/>'),
                    ParagraphStyle(
                        'DeliveryNote',
                        parent=styles['Normal'],
                        fontSize=11,
                        leading=15,
                        leftIndent=10,
                        rightIndent=10,
                        spaceAfter=10,
                        textColor=colors.HexColor('#2c3e50'),
                        backColor=colors.HexColor('#e8f4fd'),
                        borderPadding=10,
                        borderWidth=2,
                        borderColor=colors.HexColor('#5bc0de')
                    )
                )
                story.append(special_text)

            # Build PDF
            doc = SimpleDocTemplate(
                file_path,
                pagesize=letter,
                leftMargin=0.5*inch,
                rightMargin=0.5*inch,
                topMargin=0.5*inch,
                bottomMargin=0.5*inch
            )
            doc.build(story)

            # Open PDF
            try:
                os.startfile(file_path)
            except Exception:
                pass
            
            QMessageBox.information(
                self,
                "Delivery Ticket",
                f"Delivery ticket saved:\n\n{file_path}"
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Print Error",
                f"Failed to print delivery ticket:\n\n{str(e)}\n\n{traceback.format_exc()}"
            )

    def _refresh_parent_orders(self) -> None:
        """Refresh parent order list/grid when available."""
        try:
            parent = self.parent()
            if parent and hasattr(parent, "load_orders"):
                parent.load_orders()
        except Exception as e:
            print(f"EPACES: Failed to refresh parent orders: {e}")

    def refresh_order(self, new_order) -> None:
        """
        Refresh the helper with a new/updated order.
        Called when order items change or when viewing a different order.
        """
        self.order = new_order
        self.setWindowTitle(f"EPACES – {_format_order_number(new_order)}")
        
        # Clear existing ICD codes
        while self.icd_codes_layout.count():
            child = self.icd_codes_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # Clear PA edits tracking and items data
        self._pa_edits.clear()
        self._items_data.clear()
        
        # Re-bind all data
        self._bind_data()
        
        # Bring to front
        self.raise_()
        self.activateWindow()
