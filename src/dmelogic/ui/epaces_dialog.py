"""
ePACES Billing Helper Dialog

Provides a copy-friendly interface for manually entering order data into the ePACES portal.
Shows member insurance info, prescriber NPI, and HCPCS billing lines with PA# scratch fields.

This is NOT a replacement for the Order Editor - it's a specialized tool for the manual
portal entry workflow.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QLineEdit,
    QTableWidget, QTableWidgetItem, QPushButton, QFrame, QMessageBox,
    QHeaderView
)

from datetime import datetime

from dmelogic.db.orders import fetch_order_with_items
from dmelogic.models import Order
from dmelogic.db.base import resolve_db_path
from dmelogic.services.patient_address import get_patient_full_address


def _format_order_number(order: Order) -> str:
    """Format order number using continuous ID with optional refill context."""
    base = f"ORD-{int(order.id):03d}"
    parent_id = order.parent_order_id or 0
    refill_no = order.refill_number or 0
    if parent_id and refill_no > 0:
        return f"{base} (Refill of ORD-{int(parent_id):03d} R{int(refill_no)})"
    return base


class EpacesDialog(QDialog):
    """
    Helper dialog for manually billing an order in the ePACES portal.

    Features:
      - Member insurance information (all copyable)
      - Prescriber NPI (copyable)
      - HCPCS, units, amount per line (copyable)
      - Editable PA# field per line for pasting PA numbers from ePACES
      - Copy individual rows or all lines as formatted text
      
    Data Source:
      Uses fetch_order_with_items() domain model - no direct SQL
    """

    def __init__(
        self,
        order_id: int,
        folder_path: Optional[str],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.order_id = order_id
        self.folder_path = folder_path
        self.order: Optional[Order] = None

        self.setWindowTitle(f"Bill in ePACES – Order #{order_id}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setMinimumSize(900, 600)

        self._build_ui()
        self._load_order()

    # ---------- UI Construction ----------

    def _build_ui(self):
        """Build the complete UI layout."""
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Header
        header = QLabel(f"📋 ePACES Billing Helper")
        header.setProperty("class", "section-title")
        header.setStyleSheet("font-size: 14pt; font-weight: bold; color: #0078D4; padding: 8px 0;")
        root.addWidget(header)

        instruction = QLabel(
            "All fields are copyable. Use the Copy buttons or select text directly. "
            "PA# fields are for your scratch work while keying into ePACES."
        )
        instruction.setStyleSheet("color: #888888; padding-bottom: 8px;")
        instruction.setWordWrap(True)
        root.addWidget(instruction)

        # --- Member & Insurance Card ---
        member_card = self._build_member_card()
        root.addWidget(member_card)

        # --- Prescriber Card ---
        prescriber_card = self._build_prescriber_card()
        root.addWidget(prescriber_card)

        # --- Items Table ---
        items_label = QLabel("Billing Line Items")
        items_label.setProperty("class", "section-title")
        items_label.setStyleSheet("font-weight: bold; font-size: 11pt; color: #CFCFCF; padding-top: 8px;")
        root.addWidget(items_label)

        self.items_table = QTableWidget(0, 6)
        self.items_table.setHorizontalHeaderLabels(
            ["HCPCS", "Description", "Units", "Amount", "PA #", "Copy"]
        )
        
        # Column widths
        header = self.items_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # HCPCS
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)            # Description
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Units
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Amount
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)             # PA#
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)             # Copy button
        header.resizeSection(4, 150)  # PA# field width
        header.resizeSection(5, 80)   # Copy button width
        
        self.items_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items_table.setAlternatingRowColors(True)
        root.addWidget(self.items_table, 1)  # Stretch factor

        # --- Bottom Action Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_copy_all = QPushButton("📋 Copy All Lines")
        btn_copy_all.setProperty("class", "secondary")
        btn_copy_all.setToolTip("Copy all HCPCS lines as formatted text")
        btn_copy_all.clicked.connect(self._on_copy_all_lines)
        btn_row.addWidget(btn_copy_all)

        btn_close = QPushButton("Close")
        btn_close.setProperty("class", "secondary")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        root.addLayout(btn_row)

    def _build_member_card(self) -> QWidget:
        """Build the Member & Insurance information card."""
        card = QFrame()
        card.setProperty("class", "OrderCard")
        card.setStyleSheet("QFrame { background-color: #2B2B2B; border: 1px solid #3A3A3A; border-radius: 6px; padding: 8px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Card header
        header = QLabel("👤 Member & Insurance")
        header.setProperty("class", "section-title")
        header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #0078D4; padding-bottom: 4px;")
        layout.addWidget(header)

        # Fields
        self.ed_member_name = self._make_copy_row(layout, "Member Name")
        self.ed_ins_name = self._make_copy_row(layout, "Insurance Name")
        self.ed_member_id = self._make_copy_row(layout, "Member ID / CIN")
        self.ed_dob = self._make_copy_row(layout, "DOB")
        self.ed_phone = self._make_copy_row(layout, "Phone")
        self.ed_address = self._make_copy_row(layout, "Address")

        return card

    def _build_prescriber_card(self) -> QWidget:
        """Build the Prescriber information card."""
        card = QFrame()
        card.setProperty("class", "OrderCard")
        card.setStyleSheet("QFrame { background-color: #2B2B2B; border: 1px solid #3A3A3A; border-radius: 6px; padding: 8px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Card header
        header = QLabel("👨‍⚕️ Prescriber")
        header.setProperty("class", "section-title")
        header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #0078D4; padding-bottom: 4px;")
        layout.addWidget(header)

        # Fields
        self.ed_prescriber_name = self._make_copy_row(layout, "Prescriber Name")
        self.ed_prescriber_npi = self._make_copy_row(layout, "Prescriber NPI")

        return card

    def _make_copy_row(self, parent_layout: QVBoxLayout, label_text: str) -> QLineEdit:
        """
        Create a labeled field with a Copy button.
        
        Returns the QLineEdit for later data binding.
        """
        row = QHBoxLayout()
        row.setSpacing(8)
        
        lbl = QLabel(label_text + ":")
        lbl.setMinimumWidth(120)
        lbl.setStyleSheet("color: #CFCFCF; font-weight: 500;")
        
        ed = QLineEdit()
        ed.setReadOnly(True)
        ed.setCursorPosition(0)
        ed.setStyleSheet("""
            QLineEdit {
                background-color: #1E1E1E;
                color: #E5E5E5;
                border: 1px solid #3A3A3A;
                border-radius: 4px;
                padding: 6px 10px;
                selection-background-color: #0078D4;
                selection-color: white;
            }
        """)

        btn_copy = QPushButton("📋 Copy")
        btn_copy.setProperty("class", "secondary")
        btn_copy.setFixedWidth(80)
        btn_copy.setToolTip(f"Copy {label_text} to clipboard")

        def do_copy():
            text = ed.text() or ""
            QGuiApplication.clipboard().setText(text)
            # Brief visual feedback
            original = btn_copy.text()
            btn_copy.setText("✓ Copied")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1000, lambda: btn_copy.setText(original))

        btn_copy.clicked.connect(do_copy)

        row.addWidget(lbl)
        row.addWidget(ed, 1)  # Stretch
        row.addWidget(btn_copy)
        parent_layout.addLayout(row)
        
        return ed

    # ---------- Data Loading & Binding ----------

    def _load_order(self):
        """Load order from domain model and populate UI."""
        try:
            order = fetch_order_with_items(self.order_id, folder_path=self.folder_path)
            if not order:
                QMessageBox.critical(
                    self, 
                    "ePACES Error", 
                    f"Order #{self.order_id} not found in database."
                )
                self.reject()
                return
            
            self.order = order
            # Update window title with formatted order number
            self.setWindowTitle(f"Bill in ePACES – {_format_order_number(order)}")
            self._bind_order_to_ui()
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "ePACES Error",
                f"Failed to load order #{self.order_id}:\n{e}"
            )
            self.reject()

    def _bind_order_to_ui(self):
        """Populate all UI fields from the loaded order."""
        o = self.order
        if not o:
            return

        # Member & Insurance
        # Prefer snapshot fields, fall back to legacy columns so refills/old orders still display.
        patient_name = (
            o.patient_name_at_order_time
            or (f"{o.patient_last_name}, {o.patient_first_name}" if o.patient_last_name or o.patient_first_name else "")
        )

        def _fmt_mmddyyyy(val) -> str:
            """Display helper for dates as MM/DD/YYYY while tolerating ISO strings."""
            if not val:
                return ""
            if hasattr(val, "strftime"):
                return val.strftime("%m/%d/%Y")
            try:
                return datetime.strptime(str(val), "%Y-%m-%d").strftime("%m/%d/%Y")
            except Exception:
                return str(val)

        dob = _fmt_mmddyyyy(o.patient_dob_at_order_time or getattr(o, "patient_dob", None))
        phone = o.patient_phone_at_order_time if hasattr(o, "patient_phone_at_order_time") else None
        if not phone:
            phone = getattr(o, "patient_phone", "") or ""
        insurance_name = o.insurance_name_at_order_time or getattr(o, "primary_insurance", "") or ""
        member_id = o.insurance_id_at_order_time or getattr(o, "primary_insurance_id", "") or ""

        self.ed_member_name.setText(patient_name)
        self.ed_dob.setText(dob)
        self.ed_phone.setText(phone)
        self.ed_ins_name.setText(insurance_name)
        self.ed_member_id.setText(member_id)

        patient_db_path = resolve_db_path("patients.db", folder_path=self.folder_path)
        full_addr = get_patient_full_address(
            patient_db_path,
            getattr(o, "patient_id", None),
            getattr(o, "patient_last_name", None),
            getattr(o, "patient_first_name", None),
        )
        snapshot_addr = (
            getattr(o, "patient_address_at_order_time", None)
            or getattr(o, "patient_address", None)
            or ""
        )
        address_to_show = (full_addr or snapshot_addr or "").strip() or "N/A"
        self.ed_address.setText(address_to_show)

        # Prescriber
        prescriber_name = o.prescriber_name_at_order_time or getattr(o, "prescriber_name", "") or ""
        prescriber_npi = o.prescriber_npi_at_order_time or getattr(o, "prescriber_npi", "") or ""
        self.ed_prescriber_name.setText(prescriber_name)
        self.ed_prescriber_npi.setText(prescriber_npi)

        # Items Table
        self._populate_items_table()

    def _populate_items_table(self):
        """Populate the HCPCS items table."""
        from decimal import Decimal

        self.items_table.setRowCount(0)
        
        if not self.order or not self.order.items:
            return

        for item in self.order.items:
            row = self.items_table.rowCount()
            self.items_table.insertRow(row)

            # Extract item data - show full HCPCS code if multi-code (contains +)
            full_hcpcs = item.hcpcs_code or ""
            if "+" in full_hcpcs:
                # Multi-HCPCS code (e.g., E0244+E0243) - show full code without item suffix
                display_hcpcs = full_hcpcs.split("-")[0] if "-" in full_hcpcs else full_hcpcs
            else:
                # Single HCPCS - show as-is (may include hyphen suffix for inventory matching)
                display_hcpcs = full_hcpcs
            description = item.description or ""
            units = item.quantity
            unit_price = item.cost_ea or Decimal("0.00")
            line_total = item.total_cost or (unit_price * Decimal(str(units)))

            # Populate columns
            self.items_table.setItem(row, 0, QTableWidgetItem(display_hcpcs))
            self.items_table.setItem(row, 1, QTableWidgetItem(description))
            self.items_table.setItem(row, 2, QTableWidgetItem(str(units)))
            self.items_table.setItem(row, 3, QTableWidgetItem(f"${line_total:.2f}"))

            # PA # input field (editable, not persisted)
            pa_edit = QLineEdit()
            pa_edit.setPlaceholderText("Paste PA from ePACES")
            pa_edit.setStyleSheet("""
                QLineEdit {
                    background-color: #1E1E1E;
                    color: #E5E5E5;
                    border: 1px solid #3A3A3A;
                    border-radius: 4px;
                    padding: 4px 8px;
                }
                QLineEdit:focus {
                    border: 1px solid #0078D4;
                }
            """)
            self.items_table.setCellWidget(row, 4, pa_edit)

            # Copy button for this row
            btn_copy = QPushButton("📋")
            btn_copy.setProperty("class", "secondary")
            btn_copy.setFixedWidth(60)
            btn_copy.setToolTip("Copy this line as formatted text")

            def make_copy_fn(r=row):
                """Closure to capture current row index."""
                def _copy():
                    hc = self._get_table_text(r, 0)
                    desc = self._get_table_text(r, 1)
                    units_val = self._get_table_text(r, 2)
                    amount = self._get_table_text(r, 3)
                    
                    pa_widget = self.items_table.cellWidget(r, 4)
                    pa = pa_widget.text().strip() if isinstance(pa_widget, QLineEdit) else ""
                    
                    # Format: HCPCS | Description | Units: X | Amount: $XX.XX | PA: XXXXX
                    text = f"{hc} | {desc} | Units: {units_val} | Amount: {amount}"
                    if pa:
                        text += f" | PA: {pa}"
                    
                    QGuiApplication.clipboard().setText(text)
                    
                    # Visual feedback
                    original = btn_copy.text()
                    btn_copy.setText("✓")
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(800, lambda: btn_copy.setText(original))
                
                return _copy

            btn_copy.clicked.connect(make_copy_fn())
            self.items_table.setCellWidget(row, 5, btn_copy)

    def _get_table_text(self, row: int, col: int) -> str:
        """Get text from table cell, returns empty string if None."""
        item = self.items_table.item(row, col)
        return item.text().strip() if item else ""

    # ---------- Actions ----------

    def _on_copy_all_lines(self):
        """Copy all HCPCS lines as formatted text (one per line)."""
        if self.items_table.rowCount() == 0:
            QMessageBox.information(self, "ePACES", "No items to copy.")
            return

        lines: list[str] = []
        for row in range(self.items_table.rowCount()):
            hc = self._get_table_text(row, 0)
            desc = self._get_table_text(row, 1)
            units_val = self._get_table_text(row, 2)
            amount = self._get_table_text(row, 3)
            
            pa_widget = self.items_table.cellWidget(row, 4)
            pa = pa_widget.text().strip() if isinstance(pa_widget, QLineEdit) else ""
            
            line = f"{hc} | {desc} | Units: {units_val} | Amount: {amount}"
            if pa:
                line += f" | PA: {pa}"
            
            lines.append(line)

        text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)
        
        QMessageBox.information(
            self,
            "ePACES",
            f"Copied {len(lines)} line(s) to clipboard."
        )
