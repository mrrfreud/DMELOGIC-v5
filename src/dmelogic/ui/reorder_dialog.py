"""
Reorder Confirmation Dialog for DMELogic.

When an order has no refills remaining but the patient has a prescription (RX)
on file, this dialog allows the user to create a brand-new order copying the
same patient, prescriber, insurance, ICD-10, and item information from the
source order — but with:
  - A new, required RX date
  - Editable prescriber, item quantities, refills, and days-supply fields
  - A fresh order number (not a refill chain — no parent_order_id)
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QDateEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QSpinBox, QWidget,
    QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont, QColor

from dmelogic.db.models import Order, OrderInput, OrderItemInput, OrderStatus
from dmelogic.db import orders as orders_repo
from dmelogic.db.rental_modifiers import format_modifiers_for_display
from dmelogic.config import debug_log


class ReorderConfirmationDialog(QDialog):
    """
    Dialog that lets the user confirm / edit details before creating a
    brand-new order from an existing (exhausted) order.

    The caller supplies the source ``Order`` domain object; after the user
    confirms, call ``get_new_order_id()`` to retrieve the ID of the newly
    persisted order.
    """

    def __init__(
        self,
        source_order: Order,
        folder_path: Optional[str] = None,
        rx_data: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.source = source_order
        self.folder_path = folder_path
        self.rx_data = rx_data or {}
        self._new_order_id: Optional[int] = None

        self._build_ui()
        self._populate_from_source()
        self._populate_rx_info()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def get_new_order_id(self) -> Optional[int]:
        """Return the newly created order ID (set only after ``accept``)."""
        return self._new_order_id

    # ------------------------------------------------------------------
    #  UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setWindowTitle("Create New Order from Existing")
        self.setMinimumSize(720, 640)
        self.resize(780, 700)

        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(12)
        root_layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QLabel("📋  Reorder — Confirm Details")
        header.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #1a4a7a; padding: 4px 0;"
        )
        root_layout.addWidget(header)

        info = QLabel(
            "A new order will be created with: new RX date, new order number, today's order date.\n"
            "Verify prescriber, items, quantities, and refills below before confirming."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #555; font-size: 11px; margin-bottom: 6px;")
        root_layout.addWidget(info)

        # Scroll area for the form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setSpacing(10)

        # --- RX Date (required) ---
        rx_group = QGroupBox("New Prescription Date (Required)")
        rx_group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 2px solid #0d6efd; "
            "border-radius: 6px; margin-top: 10px; padding-top: 14px; } "
            "QGroupBox::title { color: #0d6efd; }"
        )
        rx_lay = QFormLayout(rx_group)
        self.rx_date_edit = QDateEdit()
        self.rx_date_edit.setCalendarPopup(True)
        self.rx_date_edit.setDisplayFormat("MM/dd/yyyy")
        self.rx_date_edit.setDate(QDate.currentDate())
        self.rx_date_edit.setMaximumDate(QDate.currentDate())
        rx_lay.addRow("RX Date:", self.rx_date_edit)
        form_layout.addWidget(rx_group)

        # --- Attached RX Document ---
        self.rx_doc_group = QGroupBox("\U0001f4c4  Attached RX Document")
        self.rx_doc_group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 2px solid #dc6900; "
            "border-radius: 6px; margin-top: 10px; padding-top: 14px; } "
            "QGroupBox::title { color: #dc6900; }"
        )
        rx_doc_lay = QVBoxLayout(self.rx_doc_group)

        rx_info_lay = QFormLayout()
        self.lbl_rx_prescriber = QLabel("N/A")
        self.lbl_rx_date_received = QLabel("N/A")
        self.lbl_rx_document = QLabel("N/A")
        self.lbl_rx_document.setWordWrap(True)
        self.lbl_rx_notes = QLabel("")
        self.lbl_rx_notes.setWordWrap(True)
        rx_info_lay.addRow("Prescriber:", self.lbl_rx_prescriber)
        rx_info_lay.addRow("Date Received:", self.lbl_rx_date_received)
        rx_info_lay.addRow("Document:", self.lbl_rx_document)
        rx_info_lay.addRow("Notes:", self.lbl_rx_notes)
        rx_doc_lay.addLayout(rx_info_lay)

        self.view_rx_btn = QPushButton("\U0001f50d  View Attached RX Document")
        self.view_rx_btn.setMinimumHeight(36)
        self.view_rx_btn.setStyleSheet(
            "QPushButton { background: #dc6900; color: white; "
            "border-radius: 5px; padding: 6px 16px; font-size: 12px; font-weight: bold; } "
            "QPushButton:hover { background: #b35600; } "
            "QPushButton:disabled { background: #ccc; color: #888; }"
        )
        self.view_rx_btn.clicked.connect(self._view_rx_document)
        rx_doc_lay.addWidget(self.view_rx_btn)

        form_layout.addWidget(self.rx_doc_group)

        # --- Patient (read-only) ---
        pt_group = QGroupBox("Patient Information")
        pt_lay = QFormLayout(pt_group)
        self.lbl_patient_name = QLabel()
        self.lbl_patient_dob = QLabel()
        self.lbl_patient_phone = QLabel()
        self.lbl_patient_address = QLabel()
        self.lbl_patient_address.setWordWrap(True)
        pt_lay.addRow("Name:", self.lbl_patient_name)
        pt_lay.addRow("DOB:", self.lbl_patient_dob)
        pt_lay.addRow("Phone:", self.lbl_patient_phone)
        pt_lay.addRow("Address:", self.lbl_patient_address)
        form_layout.addWidget(pt_group)

        # --- Prescriber (editable name/NPI) ---
        md_group = QGroupBox("Prescriber Information — Verify / Edit")
        md_lay = QFormLayout(md_group)
        self.prescriber_name_edit = QLineEdit()
        self.prescriber_npi_edit = QLineEdit()
        md_lay.addRow("Prescriber Name:", self.prescriber_name_edit)
        md_lay.addRow("NPI:", self.prescriber_npi_edit)
        form_layout.addWidget(md_group)

        # --- Insurance (read-only) ---
        ins_group = QGroupBox("Insurance Information")
        ins_lay = QFormLayout(ins_group)
        self.lbl_insurance = QLabel()
        self.lbl_policy = QLabel()
        ins_lay.addRow("Primary Insurance:", self.lbl_insurance)
        ins_lay.addRow("Policy #:", self.lbl_policy)
        form_layout.addWidget(ins_group)

        # --- ICD-10 (read-only) ---
        icd_group = QGroupBox("ICD-10 Diagnosis Codes")
        icd_lay = QFormLayout(icd_group)
        self.lbl_icd_codes = QLabel()
        self.lbl_icd_codes.setWordWrap(True)
        icd_lay.addRow("Codes:", self.lbl_icd_codes)
        form_layout.addWidget(icd_group)

        # --- Items (editable qty / refills / days) ---
        items_group = QGroupBox("Order Items — Verify / Edit")
        items_group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 2px solid #198754; "
            "border-radius: 6px; margin-top: 10px; padding-top: 14px; } "
            "QGroupBox::title { color: #198754; }"
        )
        items_lay = QVBoxLayout(items_group)

        self.items_table = QTableWidget()
        self.items_table.setColumnCount(7)
        self.items_table.setHorizontalHeaderLabels([
            "HCPCS", "Item #", "Description", "Qty", "Refills", "Days", "Cost"
        ])
        hdr = self.items_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items_table.setMinimumHeight(140)
        items_lay.addWidget(self.items_table)

        form_layout.addWidget(items_group)

        scroll.setWidget(form_widget)
        root_layout.addWidget(scroll, 1)

        # --- Buttons ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root_layout.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.confirm_btn = QPushButton("✚  Create New Order")
        self.confirm_btn.setMinimumWidth(180)
        self.confirm_btn.setStyleSheet(
            "QPushButton { background: #198754; color: white; "
            "border-radius: 5px; padding: 8px 20px; font-size: 12px; font-weight: bold; } "
            "QPushButton:hover { background: #145c27; }"
        )
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self.confirm_btn)

        root_layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    #  Populate from source order
    # ------------------------------------------------------------------

    def _populate_rx_info(self):
        """Populate the Attached RX Document section from rx_data."""
        rx = self.rx_data
        if not rx or not int(rx.get("rx_on_file", 0)):
            # No RX data — hide the section
            self.rx_doc_group.setVisible(False)
            return

        self.rx_doc_group.setVisible(True)

        md_name = rx.get("reserved_rx_md", "") or "Unknown"
        self.lbl_rx_prescriber.setText(md_name)

        rx_date_str = rx.get("reserved_rx_date", "") or "N/A"
        self.lbl_rx_date_received.setText(rx_date_str)

        # Pre-fill the RX Date field from the reserved data if available
        if rx_date_str and rx_date_str != "N/A":
            try:
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                    try:
                        parsed = datetime.strptime(rx_date_str, fmt)
                        self.rx_date_edit.setDate(QDate(parsed.year, parsed.month, parsed.day))
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        doc_path = rx.get("reserved_rx_path", "") or ""
        if doc_path:
            self.lbl_rx_document.setText(os.path.basename(doc_path))
            self.lbl_rx_document.setToolTip(doc_path)
            self.view_rx_btn.setEnabled(os.path.exists(doc_path))
            if not os.path.exists(doc_path):
                self.view_rx_btn.setText("\u26a0  Document Not Found")
        else:
            self.lbl_rx_document.setText("No document attached")
            self.view_rx_btn.setEnabled(False)

        notes = rx.get("reserved_rx_notes", "") or ""
        self.lbl_rx_notes.setText(notes if notes else "—")

    def _view_rx_document(self):
        """Open the attached RX document in the system viewer."""
        path = self.rx_data.get("reserved_rx_path", "") if self.rx_data else ""
        if not path or not os.path.exists(path):
            QMessageBox.warning(
                self, "File Not Found",
                "The attached RX document could not be found."
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            QMessageBox.warning(
                self, "Error Opening File",
                f"Could not open document:\n\n{e}"
            )

    # ------------------------------------------------------------------

    def _populate_from_source(self):
        src = self.source

        # Patient
        self.lbl_patient_name.setText(src.patient_full_name or "N/A")
        dob = src.patient_dob
        if isinstance(dob, (date, datetime)):
            dob_str = dob.strftime("%m/%d/%Y")
        elif isinstance(dob, str):
            dob_str = dob
        else:
            dob_str = "N/A"
        self.lbl_patient_dob.setText(dob_str)
        self.lbl_patient_phone.setText(src.patient_phone or "N/A")
        self.lbl_patient_address.setText(
            getattr(src, "patient_address", "") or "N/A"
        )

        # Prescriber
        self.prescriber_name_edit.setText(src.prescriber_name or "")
        self.prescriber_npi_edit.setText(src.prescriber_npi or "")

        # Insurance
        self.lbl_insurance.setText(src.primary_insurance or "N/A")
        self.lbl_policy.setText(src.primary_insurance_id or "N/A")

        # ICD codes
        codes = list(src.icd_codes) if src.icd_codes else []
        codes_display = ", ".join(c for c in codes if c) or "N/A"
        self.lbl_icd_codes.setText(codes_display)

        # Items
        self.items_table.setRowCount(0)
        for item in src.items:
            row = self.items_table.rowCount()
            self.items_table.insertRow(row)

            # HCPCS (read-only)
            hcpcs_text = (item.hcpcs_code or "")[:5] if item.hcpcs_code else ""
            hcpcs_item = QTableWidgetItem(hcpcs_text)
            hcpcs_item.setFlags(hcpcs_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 0, hcpcs_item)

            # Item # (read-only)
            item_num = getattr(item, "item_number", "") or ""
            num_item = QTableWidgetItem(item_num)
            num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 1, num_item)

            # Description (read-only)
            desc_item = QTableWidgetItem(item.description or "")
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 2, desc_item)

            # Qty (editable)
            self.items_table.setItem(row, 3, QTableWidgetItem(str(item.quantity or 1)))

            # Refills (editable - default to what the source had originally)
            self.items_table.setItem(row, 4, QTableWidgetItem(str(item.refills or 0)))

            # Days supply (editable)
            self.items_table.setItem(row, 5, QTableWidgetItem(str(item.days_supply or 30)))

            # Cost (editable)
            cost_val = item.cost_ea or Decimal("0")
            self.items_table.setItem(row, 6, QTableWidgetItem(f"{cost_val:.2f}"))

    # ------------------------------------------------------------------
    #  Confirm & create
    # ------------------------------------------------------------------

    def _on_confirm(self):
        """Validate inputs and create the new order."""
        # --- Validate RX date ---
        qdate = self.rx_date_edit.date()
        if not qdate.isValid():
            QMessageBox.warning(self, "Invalid Date", "Please enter a valid RX date.")
            return
        rx_date_str = qdate.toString("yyyy-MM-dd")

        # --- Validate prescriber ---
        prescriber_name = self.prescriber_name_edit.text().strip()
        prescriber_npi = self.prescriber_npi_edit.text().strip()
        if not prescriber_name:
            QMessageBox.warning(
                self, "Missing Prescriber",
                "Prescriber name is required. Please enter or verify the prescriber."
            )
            self.prescriber_name_edit.setFocus()
            return

        # --- Collect items from table ---
        items: list[OrderItemInput] = []
        for row in range(self.items_table.rowCount()):
            hcpcs = (self.items_table.item(row, 0).text() or "").strip()
            item_number = (self.items_table.item(row, 1).text() or "").strip()
            description = (self.items_table.item(row, 2).text() or "").strip()

            try:
                qty = int(self.items_table.item(row, 3).text())
            except (ValueError, AttributeError):
                qty = 1
            if qty <= 0:
                QMessageBox.warning(
                    self, "Invalid Quantity",
                    f"Row {row + 1}: Quantity must be > 0."
                )
                return

            try:
                refills = int(self.items_table.item(row, 4).text())
            except (ValueError, AttributeError):
                refills = 0
            if refills < 0:
                QMessageBox.warning(
                    self, "Invalid Refills",
                    f"Row {row + 1}: Refills cannot be negative."
                )
                return

            try:
                days = int(self.items_table.item(row, 5).text())
            except (ValueError, AttributeError):
                days = 30
            if days <= 0:
                QMessageBox.warning(
                    self, "Invalid Days Supply",
                    f"Row {row + 1}: Days supply must be > 0."
                )
                return

            try:
                cost_ea = Decimal(self.items_table.item(row, 6).text())
            except (InvalidOperation, AttributeError):
                cost_ea = Decimal("0")

            # Carry over modifier/rental data from source item if available
            src_item = (
                self.source.items[row]
                if row < len(self.source.items)
                else None
            )

            items.append(
                OrderItemInput(
                    hcpcs=hcpcs,
                    description=description,
                    quantity=qty,
                    refills=refills,
                    days_supply=days,
                    item_number=item_number or None,
                    cost_ea=cost_ea if cost_ea > 0 else None,
                    is_rental=getattr(src_item, "is_rental", False) if src_item else False,
                    modifier1=getattr(src_item, "modifier1", None) if src_item else None,
                    modifier2=getattr(src_item, "modifier2", None) if src_item else None,
                    modifier3=getattr(src_item, "modifier3", None) if src_item else None,
                    modifier4=getattr(src_item, "modifier4", None) if src_item else None,
                )
            )

        if not items:
            QMessageBox.warning(self, "No Items", "At least one order item is required.")
            return

        # --- Build OrderInput (brand-new order, NOT a refill) ---
        from dmelogic.security.auth import is_current_user_agent
        _is_agent_user = is_current_user_agent()

        src = self.source
        order_input = OrderInput(
            patient_last_name=src.patient_last_name or "",
            patient_first_name=src.patient_first_name or "",
            patient_dob=str(src.patient_dob) if src.patient_dob else None,
            patient_phone=src.patient_phone,
            patient_address=getattr(src, "patient_address", None),
            patient_id=src.patient_id,
            prescriber_name=prescriber_name,
            prescriber_npi=prescriber_npi,
            prescriber_id=src.prescriber_id,
            insurance_id=src.insurance_id,
            primary_insurance=src.primary_insurance,
            primary_insurance_id=src.primary_insurance_id,
            rx_date=rx_date_str,
            order_date=date.today().strftime("%Y-%m-%d"),
            order_status=OrderStatus.PENDING.value,
            billing_type=(
                src.billing_type.value
                if hasattr(src.billing_type, "value")
                else str(src.billing_type)
            ),
            icd_codes=list(src.icd_codes) if src.icd_codes else [],
            notes=f"Reorder from ORD-{int(src.parent_order_id or src.id):03d}"
                  + (f"-R{int(src.refill_number)}" if (src.refill_number or 0) > 0 else "")
                  + f" (new RX {rx_date_str})",
            items=items,
            # NOT a refill chain — this is a brand-new order
            parent_order_id=None,
            refill_number=0,
            agent_created=_is_agent_user,
        )

        # --- Persist ---
        try:
            new_id = orders_repo.create_order(
                order_input, folder_path=self.folder_path
            )
            self._new_order_id = new_id
            debug_log(
                f"Reorder created: new order ID {new_id} from source order {src.id}"
            )

            # Clear the reserved RX flag on the source order (it's been used)
            try:
                from dmelogic.reserved_rx_manager import clear_reserved_rx
                from dmelogic.db.base import resolve_db_path

                db_path = resolve_db_path(
                    "orders.db", folder_path=self.folder_path
                )
                clear_reserved_rx(db_path, str(src.id))
                debug_log(
                    f"Cleared reserved RX on source order {src.id} after reorder"
                )
            except Exception as rx_err:
                debug_log(f"Could not clear reserved RX: {rx_err}")

            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Creating Order",
                f"Failed to create order:\n\n{str(e)}",
            )
            debug_log(f"Reorder creation error: {e}")
