"""
dme_dialogs.py
==============
DMELogic — Styled Dialog Library
==================================

Drop into your project alongside dme_theme.py and dme_widgets.py.

Provides fully-styled, drop-in replacement dialogs for:

  1.  EpacesHelperDialog       — ePACES billing helper with copy-to-clipboard
  2.  ProcessRefillDialog      — Process/ship a refill with notes
  3.  UpdateStatusDialog       — Choose new order/patient status
  4.  AddInventoryDialog       — Add new inventory item
  5.  NewOrderWizard           — 4-step order creation wizard
  6.  PatientDetailsDialog     — Patient record viewer (6 sub-tabs)
  7.  AddPresciberDialog       — Add/edit prescriber
  8.  AddPatientDialog         — Add new patient

Each dialog can be used standalone:

    dlg = ProcessRefillDialog(
        parent=self,
        order_data={
            "order_id": "ORD-316",
            "new_order_id": "ORD-316-R1",
            "patient_name": "DANNER, WARREN",
            "hcpcs": "T4522-FRBRFMED",
            "refills_remaining": 4,
            "total_refills": 5,
        }
    )
    result = dlg.exec()
    if result == QDialog.DialogCode.Accepted:
        data = dlg.get_result()   # dict of user inputs

Or override the existing ones in your app:
    # Replace your existing ePACES dialog launch:
    dlg = EpacesHelperDialog(parent=self, order_data=self.get_selected_order())
    dlg.exec()
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QLineEdit, QTextEdit, QComboBox, QCheckBox, QFrame, QScrollArea,
    QFormLayout, QDialogButtonBox, QTabWidget, QSizePolicy,
    QSpinBox, QDoubleSpinBox, QDateEdit, QApplication, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QGridLayout,
    QRadioButton, QButtonGroup, QStackedWidget, QPlainTextEdit,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QDate, QTimer, QSize
from PyQt6.QtGui import QFont, QColor, QBrush

from dmelogic.dme_theme import COLORS, get_status_badge_style, style_table
from dmelogic.dme_widgets import (
    StatusBadge, EpacesFieldRow, RefillSummaryBox, WizardSteps,
    StatusRadioGroup, SectionBox, FormRow, AlertBox
)


# ─────────────────────────────────────────────────────────────────────────────
#  BASE DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class DMEDialog(QDialog):
    """Base dialog with consistent styling, layout, and header/footer helpers."""

    def __init__(self, title: str, subtitle: str = "",
                 width: int = 560, height: int = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        if height:
            self.setMinimumHeight(height)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS.SAGE_BG}; }}")
        self._result_data = {}
        self._setup_layout(title, subtitle)

    def _setup_layout(self, title: str, subtitle: str):
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # Header
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS.WHITE};
                border-bottom: 1px solid {COLORS.SLATE_200};
                min-height: 56px;
                max-height: 56px;
            }}
        """)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 0, 20, 0)
        h_layout.setSpacing(0)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {COLORS.SLATE_800}; background: transparent;"
        )
        title_col.addWidget(title_lbl)
        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setStyleSheet(
                f"font-size: 11px; color: {COLORS.TEXT_MUTED}; background: transparent;"
            )
            title_col.addWidget(sub_lbl)
        h_layout.addLayout(title_col)
        h_layout.addStretch()

        # Close X button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS.TEXT_MUTED};
                border: none;
                font-size: 14px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {COLORS.SLATE_100};
                color: {COLORS.TEXT_PRIMARY};
            }}
        """)
        close_btn.clicked.connect(self.reject)
        h_layout.addWidget(close_btn)
        self._outer.addWidget(header)

        # Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        self._body_widget = QWidget()
        self._body_widget.setStyleSheet(f"background-color: {COLORS.SAGE_BG};")
        self._body = QVBoxLayout(self._body_widget)
        self._body.setContentsMargins(20, 16, 20, 16)
        self._body.setSpacing(12)
        scroll.setWidget(self._body_widget)
        self._outer.addWidget(scroll, 1)

        # Footer
        self._footer_frame = QFrame()
        self._footer_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS.WHITE};
                border-top: 1px solid {COLORS.SLATE_200};
                min-height: 56px;
                max-height: 56px;
            }}
        """)
        self._footer = QHBoxLayout(self._footer_frame)
        self._footer.setContentsMargins(20, 0, 20, 0)
        self._footer.setSpacing(8)
        self._outer.addWidget(self._footer_frame)

    def add_cancel_save(self, save_text: str = "Save", cancel_text: str = "Cancel"):
        """Add standard Cancel + Save button pair to footer."""
        cancel_btn = QPushButton(cancel_text)
        cancel_btn.setProperty("class", "btn-ghost")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.clicked.connect(self.reject)
        self._footer.addStretch()
        self._footer.addWidget(cancel_btn)
        save_btn = QPushButton(save_text)
        save_btn.setProperty("class", "btn-primary")
        save_btn.setMinimumWidth(100)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        self._footer.addWidget(save_btn)
        return cancel_btn, save_btn

    def get_result(self) -> dict:
        return dict(self._result_data)


# ─────────────────────────────────────────────────────────────────────────────
#  1. EPACES HELPER DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class EpacesHelperDialog(DMEDialog):
    """
    ePACES Billing Helper — 4-step tabbed layout with copy-to-clipboard fields.

    Usage:
        dlg = EpacesHelperDialog(
            parent=self,
            order_data={
                "order_id":       "ORD-316",
                "patient_name":   "DANNER, WARREN",
                "patient_dob":    "03/15/1972",
                "patient_id":     "DN44821K",
                "insurance_id":   "DN44821K",
                "insurance_name": "Medicaid",
                "date_of_service":"02/18/2026",
                "npi":            "1234567890",
                "items": [
                    {"hcpcs": "T4522", "description": "Diaper L", "qty": 200, "mod": "KX", "pa": ""},
                    {"hcpcs": "T4533", "description": "Underpads", "qty": 100, "mod": "", "pa": "1"},
                ]
            }
        )
        dlg.exec()
    """

    def __init__(self, parent=None, order_data: dict = None):
        d = order_data or {}
        super().__init__(
            title="ePACES Billing Helper",
            subtitle=f"Order {d.get('order_id', '')} — {d.get('patient_name', '')}",
            width=640,
            height=520,
            parent=parent
        )
        self._data = d
        self._build_body()
        self._build_footer()

    def _build_body(self):
        d = self._data
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {COLORS.SLATE_200};
                border-radius: 0 0 6px 6px;
                background: {COLORS.WHITE};
            }}
            QTabBar::tab {{
                background: {COLORS.SLATE_100};
                color: {COLORS.TEXT_SECONDARY};
                padding: 6px 14px;
                border: 1px solid {COLORS.SLATE_200};
                border-bottom: none;
                border-radius: 5px 5px 0 0;
                margin-right: 2px;
                font-size: 11px;
                font-weight: 600;
            }}
            QTabBar::tab:selected {{
                background: {COLORS.WHITE};
                color: {COLORS.TEAL};
                border-bottom: 2px solid {COLORS.WHITE};
                margin-bottom: -1px;
            }}
        """)

        # Step 1: Patient Info
        p1 = self._make_tab_page([
            ("Patient Name",     d.get("patient_name", "")),
            ("Date of Birth",    d.get("patient_dob", "")),
            ("Patient ID",       d.get("patient_id", "")),
            ("Insurance ID",     d.get("insurance_id", "")),
            ("Insurance",        d.get("insurance_name", "")),
            ("Provider NPI",     d.get("npi", "")),
        ])
        tabs.addTab(p1, "① Patient & Insurance")

        # Step 2: Date of Service
        p2 = self._make_tab_page([
            ("Date of Service",  d.get("date_of_service", "")),
            ("Order #",          d.get("order_id", "")),
            ("Place of Service", "12 – Home"),
            ("Type of Bill",     ""),
        ])
        tabs.addTab(p2, "② Dates & Order")

        # Step 3: Items
        items_tab = QWidget()
        items_tab.setStyleSheet(f"background: {COLORS.WHITE};")
        items_layout = QVBoxLayout(items_tab)
        items_layout.setContentsMargins(16, 12, 16, 12)
        items_layout.setSpacing(8)

        items = d.get("items", [])
        if items:
            tbl = QTableWidget(len(items), 5)
            style_table(tbl, row_height=30)
            tbl.setHorizontalHeaderLabels(["HCPCS", "Description", "Qty", "Modifier", "PA Type"])
            tbl.setColumnWidth(0, 80)
            tbl.setColumnWidth(1, 220)
            tbl.setColumnWidth(2, 50)
            tbl.setColumnWidth(3, 70)
            tbl.setColumnWidth(4, 80)
            for r, item in enumerate(items):
                tbl.setItem(r, 0, QTableWidgetItem(item.get("hcpcs", "")))
                tbl.setItem(r, 1, QTableWidgetItem(item.get("description", "")))
                tbl.setItem(r, 2, QTableWidgetItem(str(item.get("qty", ""))))
                tbl.setItem(r, 3, QTableWidgetItem(item.get("mod", "")))
                pa_val = item.get("pa", "")
                pa_lbl = "No PA" if not pa_val or pa_val == "0" else f"PA Req ({pa_val})"
                pa_item = QTableWidgetItem(pa_lbl)
                pa_item.setForeground(QBrush(QColor(
                    COLORS.GREEN if pa_lbl == "No PA" else COLORS.AMBER
                )))
                tbl.setItem(r, 4, pa_item)
            items_layout.addWidget(tbl)
        else:
            items_layout.addWidget(QLabel("No items on this order."))
        tabs.addTab(items_tab, "③ Items")

        # Step 4: Notes
        p4 = QWidget()
        p4.setStyleSheet(f"background: {COLORS.WHITE};")
        p4l = QVBoxLayout(p4)
        p4l.setContentsMargins(16, 12, 16, 12)
        p4l.addWidget(QLabel("Billing Notes / PA Numbers:"))
        self._billing_notes = QPlainTextEdit()
        self._billing_notes.setPlaceholderText("Enter PA numbers, auth codes, or billing notes...")
        self._billing_notes.setMaximumHeight(100)
        p4l.addWidget(self._billing_notes)
        p4l.addStretch()
        tabs.addTab(p4, "④ Notes")

        self._body.addWidget(tabs)

    def _make_tab_page(self, fields: list) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {COLORS.WHITE};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        for label, value in fields:
            row = EpacesFieldRow(label, str(value), copyable=bool(value))
            layout.addWidget(row)
        layout.addStretch()
        return page

    def _build_footer(self):
        open_btn = QPushButton("🌐 Open ePACES Website")
        open_btn.setProperty("class", "btn-ghost")
        open_btn.clicked.connect(self._open_epaces)
        self._footer.addWidget(open_btn)
        self._footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setProperty("class", "btn-primary")
        close_btn.setMinimumWidth(80)
        close_btn.clicked.connect(self.accept)
        self._footer.addWidget(close_btn)

    def _open_epaces(self):
        import webbrowser
        webbrowser.open("https://www.emedny.org/selfservice/")


# ─────────────────────────────────────────────────────────────────────────────
#  2. PROCESS REFILL DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class ProcessRefillDialog(DMEDialog):
    """
    Process / ship a refill.

    Usage:
        dlg = ProcessRefillDialog(
            parent=self,
            order_data={
                "order_id": "ORD-316",
                "new_order_id": "ORD-316-R1",
                "patient_name": "DANNER, WARREN",
                "hcpcs": "T4522-FRBRFMED",
                "refills_remaining": 4,
                "total_refills": 5,
                "ship_to": "123 Main St, Brooklyn NY 11201",
                "last_shipped": "01/18/2026",
            }
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_result()
            # data: ship_date, tracking, carrier, notes, mark_shipped
    """

    def __init__(self, parent=None, order_data: dict = None):
        d = order_data or {}
        super().__init__(
            title="Process Refill",
            subtitle=f"Creating refill order for {d.get('patient_name', '')}",
            width=520,
            parent=parent
        )
        self._data = d
        self._build_body()
        self.add_cancel_save(save_text="✓ Process Refill")

    def _build_body(self):
        d = self._data

        # Summary box
        summary = RefillSummaryBox(
            patient=d.get("patient_name", ""),
            order_id=d.get("order_id", ""),
            new_order_id=d.get("new_order_id", ""),
            hcpcs=d.get("hcpcs", ""),
            refills_remaining=d.get("refills_remaining", 0),
            total_refills=d.get("total_refills", 0),
        )
        self._body.addWidget(summary)

        # Ship date
        date_row = QHBoxLayout()
        date_lbl = QLabel("SHIP DATE")
        date_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; "
            f"background: transparent; letter-spacing: 0.3px;"
        )
        self._ship_date = QDateEdit()
        self._ship_date.setDate(QDate.currentDate())
        self._ship_date.setCalendarPopup(True)
        self._ship_date.setDisplayFormat("MM/dd/yyyy")
        self._ship_date.setMinimumWidth(160)
        date_row.addWidget(date_lbl)
        date_row.addWidget(self._ship_date)
        date_row.addStretch()
        self._body.addLayout(date_row)

        # Carrier + Tracking
        carrier_row = QHBoxLayout()
        carrier_row.setSpacing(10)

        carrier_col = QVBoxLayout()
        c_lbl = QLabel("CARRIER")
        c_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._carrier = QComboBox()
        self._carrier.addItems(["UPS", "FedEx", "USPS", "Hand Delivery", "Other"])
        self._carrier.setMinimumWidth(140)
        carrier_col.addWidget(c_lbl)
        carrier_col.addWidget(self._carrier)
        carrier_row.addLayout(carrier_col)

        tracking_col = QVBoxLayout()
        t_lbl = QLabel("TRACKING NUMBER")
        t_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._tracking = QLineEdit()
        self._tracking.setPlaceholderText("Optional")
        tracking_col.addWidget(t_lbl)
        tracking_col.addWidget(self._tracking)
        carrier_row.addLayout(tracking_col)
        self._body.addLayout(carrier_row)

        # Notes
        notes_lbl = QLabel("NOTES")
        notes_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Optional shipping or refill notes...")
        self._notes.setMaximumHeight(72)
        self._body.addWidget(notes_lbl)
        self._body.addWidget(self._notes)

        # Options
        self._mark_shipped = QCheckBox("Mark as Shipped immediately")
        self._mark_shipped.setChecked(True)
        self._decrement_refills = QCheckBox("Decrement refills remaining")
        self._decrement_refills.setChecked(True)
        self._body.addWidget(self._mark_shipped)
        self._body.addWidget(self._decrement_refills)

        # Ship to
        if d.get("ship_to"):
            alert = AlertBox(f"📍 Ship to: {d['ship_to']}", kind="info")
            self._body.addWidget(alert)

    def accept(self):
        self._result_data = {
            "ship_date":           self._ship_date.date().toString("MM/dd/yyyy"),
            "carrier":             self._carrier.currentText(),
            "tracking":            self._tracking.text().strip(),
            "notes":               self._notes.toPlainText().strip(),
            "mark_shipped":        self._mark_shipped.isChecked(),
            "decrement_refills":   self._decrement_refills.isChecked(),
        }
        super().accept()


# ─────────────────────────────────────────────────────────────────────────────
#  3. UPDATE STATUS DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class UpdateStatusDialog(DMEDialog):
    """
    Change the status of an order/patient/item.

    Usage:
        dlg = UpdateStatusDialog(
            parent=self,
            entity_type="order",
            entity_id="ORD-316",
            current_status="pending",
            statuses=[
                ("pending",  "🟡 Pending",  "Created, not yet processed"),
                ("unbilled", "🟠 Unbilled", "Ready to bill in ePACES"),
                ("billed",   "🟣 Billed",   "Claim submitted to payer"),
                ("shipped",  "🟢 Shipped",  "Delivery confirmed"),
                ("rx_hold",  "🔴 RX Hold",  "Waiting for prescription"),
            ]
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_status = dlg.get_result()["status"]
    """

    def __init__(self, parent=None, entity_type: str = "order",
                 entity_id: str = "", current_status: str = "",
                 statuses: list = None):
        super().__init__(
            title="Update Status",
            subtitle=f"{'Order' if entity_type == 'order' else entity_type.title()} {entity_id}",
            width=460,
            parent=parent
        )
        self._current_status = current_status
        self._statuses = statuses or [
            ("pending",  "🟡 Pending",  "Created, not yet processed"),
            ("unbilled", "🟠 Unbilled", "Ready to bill in ePACES"),
            ("billed",   "🟣 Billed",   "Claim submitted to payer"),
            ("shipped",  "🟢 Shipped",  "Delivery confirmed"),
            ("rx_hold",  "🔴 RX Hold",  "Waiting for prescription"),
        ]
        self._build_body()
        self.add_cancel_save(save_text="Update Status")

    def _build_body(self):
        self._radio_group = StatusRadioGroup(
            statuses=self._statuses,
            current=self._current_status
        )
        self._body.addWidget(self._radio_group)

        # Optional note
        note_lbl = QLabel("NOTE (optional)")
        note_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._note = QLineEdit()
        self._note.setPlaceholderText("Reason for status change...")
        self._body.addWidget(note_lbl)
        self._body.addWidget(self._note)

    def accept(self):
        self._result_data = {
            "status": self._radio_group.get_selected(),
            "note":   self._note.text().strip(),
        }
        super().accept()


# ─────────────────────────────────────────────────────────────────────────────
#  4. ADD INVENTORY DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class AddInventoryDialog(DMEDialog):
    """
    Add or edit an inventory item.

    Usage:
        dlg = AddInventoryDialog(parent=self)
        # Or to edit:
        dlg = AddInventoryDialog(parent=self, item_data={
            "item_num":      "T4522-FRBRFMED",
            "description":   "Incontinence Brief, Medium",
            "hcpcs":         "T4522",
            "category":      "Incontinence",
            "unit_cost":     "0.92",
            "selling_price": "4.50",
            "stock":         "1250",
            "reorder_level": "200",
        })
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_result()
    """

    CATEGORIES = [
        "Incontinence", "Ostomy", "Wound Care", "Urological",
        "Nutritional", "Respiratory", "Compression", "Diabetic",
        "Mobility", "Bath Safety", "Other"
    ]

    def __init__(self, parent=None, item_data: dict = None, inline_mode: bool = False):
        """
        inline_mode: if True, the dialog is smaller and labeled as Quick Add
        (for use inside the Order Wizard without losing context).
        """
        d = item_data or {}
        is_edit = bool(d)
        title = "Quick Add Inventory Item" if inline_mode else ("Edit Inventory Item" if is_edit else "Add Inventory Item")
        super().__init__(
            title=title,
            subtitle="Item will be available immediately in the order wizard" if inline_mode else "",
            width=560,
            parent=parent
        )
        self._item_data = d
        self._build_body()
        self.add_cancel_save(
            save_text="Save & Add to Order" if inline_mode else ("Save Changes" if is_edit else "Add Item")
        )

    def _build_body(self):
        d = self._item_data

        # Section 1: Basic Info
        basic = SectionBox("Basic Information")
        basic_grid = QGridLayout()
        basic_grid.setSpacing(8)
        basic_grid.setColumnStretch(1, 1)
        basic_grid.setColumnStretch(3, 1)

        self._item_num = QLineEdit(d.get("item_num", ""))
        self._item_num.setPlaceholderText("e.g. T4522-FRBRFMED")
        self._hcpcs = QLineEdit(d.get("hcpcs", ""))
        self._hcpcs.setPlaceholderText("e.g. T4522")
        self._description = QLineEdit(d.get("description", ""))
        self._description.setPlaceholderText("Full item description")
        self._category = QComboBox()
        self._category.addItems(self.CATEGORIES)
        if d.get("category") in self.CATEGORIES:
            self._category.setCurrentText(d["category"])

        basic_grid.addWidget(QLabel("Item #"), 0, 0)
        basic_grid.addWidget(self._item_num, 0, 1)
        basic_grid.addWidget(QLabel("HCPCS Code"), 0, 2)
        basic_grid.addWidget(self._hcpcs, 0, 3)
        basic_grid.addWidget(QLabel("Description *"), 1, 0)
        basic_grid.addWidget(self._description, 1, 1, 1, 3)
        basic_grid.addWidget(QLabel("Category"), 2, 0)
        basic_grid.addWidget(self._category, 2, 1)
        basic.content_layout().addLayout(basic_grid)
        self._body.addWidget(basic)

        # Section 2: Financial
        fin = SectionBox("Pricing")
        fin_grid = QGridLayout()
        fin_grid.setSpacing(8)
        fin_grid.setColumnStretch(1, 1)
        fin_grid.setColumnStretch(3, 1)

        self._unit_cost = QLineEdit(d.get("unit_cost", ""))
        self._unit_cost.setPlaceholderText("0.00")
        self._selling_price = QLineEdit(d.get("selling_price", ""))
        self._selling_price.setPlaceholderText("0.00")
        self._rental_fee = QLineEdit(d.get("rental_fee", ""))
        self._rental_fee.setPlaceholderText("0.00")
        self._insurance_price = QLineEdit(d.get("insurance_price", ""))
        self._insurance_price.setPlaceholderText("0.00")

        fin_grid.addWidget(QLabel("Unit Cost"), 0, 0)
        fin_grid.addWidget(self._unit_cost, 0, 1)
        fin_grid.addWidget(QLabel("Selling Price"), 0, 2)
        fin_grid.addWidget(self._selling_price, 0, 3)
        fin_grid.addWidget(QLabel("Rental Fee"), 1, 0)
        fin_grid.addWidget(self._rental_fee, 1, 1)
        fin_grid.addWidget(QLabel("Insurance Price"), 1, 2)
        fin_grid.addWidget(self._insurance_price, 1, 3)
        fin.content_layout().addLayout(fin_grid)
        self._body.addWidget(fin)

        # Section 3: Inventory levels
        inv = SectionBox("Stock Levels")
        inv_grid = QGridLayout()
        inv_grid.setSpacing(8)
        inv_grid.setColumnStretch(1, 1)
        inv_grid.setColumnStretch(3, 1)

        self._stock = QSpinBox()
        self._stock.setRange(0, 99999)
        self._stock.setValue(int(d.get("stock", 0) or 0))
        self._reorder_level = QSpinBox()
        self._reorder_level.setRange(0, 99999)
        self._reorder_level.setValue(int(d.get("reorder_level", 100) or 100))
        self._reorder_qty = QSpinBox()
        self._reorder_qty.setRange(0, 99999)
        self._reorder_qty.setValue(int(d.get("reorder_qty", 500) or 500))
        self._max_stock = QSpinBox()
        self._max_stock.setRange(0, 99999)
        self._max_stock.setValue(int(d.get("max_stock", 2000) or 2000))

        inv_grid.addWidget(QLabel("Current Stock"), 0, 0)
        inv_grid.addWidget(self._stock, 0, 1)
        inv_grid.addWidget(QLabel("Reorder Level"), 0, 2)
        inv_grid.addWidget(self._reorder_level, 0, 3)
        inv_grid.addWidget(QLabel("Reorder Qty"), 1, 0)
        inv_grid.addWidget(self._reorder_qty, 1, 1)
        inv_grid.addWidget(QLabel("Max Stock"), 1, 2)
        inv_grid.addWidget(self._max_stock, 1, 3)
        inv.content_layout().addLayout(inv_grid)
        self._body.addWidget(inv)

        # Notes
        note_lbl = QLabel("NOTES")
        note_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._notes = QLineEdit(d.get("notes", ""))
        self._notes.setPlaceholderText("Optional notes...")
        self._body.addWidget(note_lbl)
        self._body.addWidget(self._notes)

    def accept(self):
        desc = self._description.text().strip()
        if not desc:
            QMessageBox.warning(self, "Validation", "Description is required.")
            return
        self._result_data = {
            "item_num":      self._item_num.text().strip(),
            "hcpcs":         self._hcpcs.text().strip().upper(),
            "description":   desc,
            "category":      self._category.currentText(),
            "unit_cost":     self._unit_cost.text().strip(),
            "selling_price": self._selling_price.text().strip(),
            "rental_fee":    self._rental_fee.text().strip(),
            "insurance_price": self._insurance_price.text().strip(),
            "stock":         self._stock.value(),
            "reorder_level": self._reorder_level.value(),
            "reorder_qty":   self._reorder_qty.value(),
            "max_stock":     self._max_stock.value(),
            "notes":         self._notes.text().strip(),
        }
        super().accept()


# ─────────────────────────────────────────────────────────────────────────────
#  5. NEW ORDER WIZARD
# ─────────────────────────────────────────────────────────────────────────────

class NewOrderWizard(DMEDialog):
    """
    4-step New Order Wizard with patient selection, items, Rx/docs, and review.

    Signals:
        order_created(dict) — emitted after successful submission

    Usage:
        wizard = NewOrderWizard(
            parent=self,
            patients_db_path=self.patients_db_path,
            inventory_db_path=self.inventory_db_path,
            orders_db_path=self.orders_db_path,
        )
        wizard.order_created.connect(self.on_order_created)
        wizard.exec()
    """

    order_created = pyqtSignal(dict)

    STEPS = ["Patient", "Items", "Rx / Docs", "Review"]

    def __init__(self, parent=None,
                 patients_db_path: str = "",
                 inventory_db_path: str = "",
                 orders_db_path: str = ""):
        super().__init__(
            title="New Order",
            subtitle="Step through the wizard to create a new order",
            width=680,
            height=560,
            parent=parent
        )
        self._patients_db = patients_db_path
        self._inventory_db = inventory_db_path
        self._orders_db = orders_db_path
        self._current_step = 0
        self._order_data = {}
        self._items: list = []
        self._build_body()
        self._build_footer()

    def _build_body(self):
        # Step indicator
        self._steps_widget = WizardSteps(self.STEPS)
        self._body.addWidget(self._steps_widget)

        # Page stack
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {COLORS.WHITE}; border: 1px solid {COLORS.SLATE_200}; border-radius: 6px;")
        self._body.addWidget(self._stack, 1)

        # Build each page
        self._pages = [
            self._build_step1(),
            self._build_step2(),
            self._build_step3(),
            self._build_step4(),
        ]
        for page in self._pages:
            self._stack.addWidget(page)

    def _build_step1(self):
        """Step 1: Patient selection."""
        page = QWidget()
        page.setStyleSheet(f"background: {COLORS.WHITE};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        search_lbl = QLabel("SEARCH PATIENT")
        search_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        layout.addWidget(search_lbl)

        self._patient_search = QLineEdit()
        self._patient_search.setPlaceholderText("Type name, DOB, or patient ID...")
        layout.addWidget(self._patient_search)

        # Patient results list (placeholder)
        results_label = QLabel("Recent patients:")
        results_label.setStyleSheet(f"color: {COLORS.TEXT_MUTED}; font-size: 11px; background: transparent;")
        layout.addWidget(results_label)

        # Patient detail fields (populated on selection)
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._pt_name = QLineEdit()
        self._pt_name.setPlaceholderText("Patient Name")
        self._pt_dob = QLineEdit()
        self._pt_dob.setPlaceholderText("MM/DD/YYYY")
        self._pt_insurance = QComboBox()
        self._pt_insurance.addItems(["", "Medicaid", "Medicare", "CDPHP", "MVP", "Private Pay"])
        self._pt_insurance_id = QLineEdit()
        self._pt_insurance_id.setPlaceholderText("Insurance Member ID")
        self._pt_prescriber = QLineEdit()
        self._pt_prescriber.setPlaceholderText("Prescriber name or NPI")

        grid.addWidget(QLabel("Patient Name *"), 0, 0)
        grid.addWidget(self._pt_name, 0, 1)
        grid.addWidget(QLabel("Date of Birth"), 0, 2)
        grid.addWidget(self._pt_dob, 0, 3)
        grid.addWidget(QLabel("Insurance"), 1, 0)
        grid.addWidget(self._pt_insurance, 1, 1)
        grid.addWidget(QLabel("Insurance ID"), 1, 2)
        grid.addWidget(self._pt_insurance_id, 1, 3)
        grid.addWidget(QLabel("Prescriber"), 2, 0)
        grid.addWidget(self._pt_prescriber, 2, 1, 1, 3)
        layout.addLayout(grid)

        # DOS + Refills
        row2 = QHBoxLayout()
        dos_col = QVBoxLayout()
        dos_lbl = QLabel("DATE OF SERVICE")
        dos_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._dos = QDateEdit()
        self._dos.setDate(QDate.currentDate())
        self._dos.setCalendarPopup(True)
        self._dos.setDisplayFormat("MM/dd/yyyy")
        dos_col.addWidget(dos_lbl)
        dos_col.addWidget(self._dos)
        row2.addLayout(dos_col)

        ref_col = QVBoxLayout()
        ref_lbl = QLabel("TOTAL REFILLS")
        ref_lbl.setStyleSheet(dos_lbl.styleSheet())
        self._total_refills = QSpinBox()
        self._total_refills.setRange(1, 36)
        self._total_refills.setValue(6)
        ref_col.addWidget(ref_lbl)
        ref_col.addWidget(self._total_refills)
        row2.addLayout(ref_col)
        row2.addStretch()
        layout.addLayout(row2)

        layout.addStretch()
        return page

    def _build_step2(self):
        """Step 2: Add items."""
        page = QWidget()
        page.setStyleSheet(f"background: {COLORS.WHITE};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText("Search inventory by name or HCPCS...")
        search_box.setMaximumWidth(280)
        toolbar.addWidget(search_box)
        toolbar.addStretch()

        add_item_btn = QPushButton("＋ Add Item")
        add_item_btn.setProperty("class", "btn-primary")
        add_item_btn.clicked.connect(self._add_item_row)
        toolbar.addWidget(add_item_btn)

        quick_add_btn = QPushButton("⚡ Quick Add New")
        quick_add_btn.setProperty("class", "btn-ghost")
        quick_add_btn.setToolTip("Add a new inventory item without leaving this wizard")
        quick_add_btn.clicked.connect(self._quick_add_inventory)
        toolbar.addWidget(quick_add_btn)

        layout.addLayout(toolbar)

        # Items table
        self._items_table = QTableWidget(0, 7)
        style_table(self._items_table, row_height=32)
        self._items_table.setHorizontalHeaderLabels(
            ["HCPCS", "Description", "Qty", "Refills", "Days Supply", "Modifier", ""]
        )
        self._items_table.setColumnWidth(0, 90)
        self._items_table.setColumnWidth(1, 200)
        self._items_table.setColumnWidth(2, 50)
        self._items_table.setColumnWidth(3, 55)
        self._items_table.setColumnWidth(4, 80)
        self._items_table.setColumnWidth(5, 80)
        self._items_table.setColumnWidth(6, 40)
        layout.addWidget(self._items_table, 1)

        hint = QLabel("Tip: Use ⚡ Quick Add to add a new inventory item without losing your order progress.")
        hint.setStyleSheet(
            f"font-size: 11px; color: {COLORS.TEXT_MUTED}; background: transparent; font-style: italic;"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _build_step3(self):
        """Step 3: Rx and Documents."""
        page = QWidget()
        page.setStyleSheet(f"background: {COLORS.WHITE};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Attach supporting documents (CMN, Rx, auth, etc.):"))

        # Rx attachment row
        rx_row = QHBoxLayout()
        rx_lbl = QLabel("📄 No Rx attached")
        rx_lbl.setStyleSheet(f"color: {COLORS.TEXT_MUTED}; font-size: 12px; background: transparent;")
        attach_rx = QPushButton("📎 Attach Rx PDF")
        attach_rx.setProperty("class", "btn-ghost")
        rx_row.addWidget(rx_lbl)
        rx_row.addStretch()
        rx_row.addWidget(attach_rx)
        layout.addLayout(rx_row)

        # CMN row
        cmn_row = QHBoxLayout()
        cmn_lbl = QLabel("📄 No CMN attached")
        cmn_lbl.setStyleSheet(rx_lbl.styleSheet())
        attach_cmn = QPushButton("📎 Attach CMN")
        attach_cmn.setProperty("class", "btn-ghost")
        cmn_row.addWidget(cmn_lbl)
        cmn_row.addStretch()
        cmn_row.addWidget(attach_cmn)
        layout.addLayout(cmn_row)

        # Rx on file checkbox
        self._rx_on_file = QCheckBox("RX on file — suppress last-refill warning")
        self._rx_on_file.setStyleSheet(f"color: {COLORS.TEXT_PRIMARY}; font-size: 13px; background: transparent;")
        layout.addWidget(self._rx_on_file)

        # Special instructions
        si_lbl = QLabel("SPECIAL INSTRUCTIONS")
        si_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._special_instructions = QPlainTextEdit()
        self._special_instructions.setPlaceholderText("Delivery instructions, substitutions, alerts...")
        self._special_instructions.setMaximumHeight(80)
        layout.addWidget(si_lbl)
        layout.addWidget(self._special_instructions)

        layout.addStretch()
        return page

    def _build_step4(self):
        """Step 4: Review summary."""
        page = QWidget()
        page.setStyleSheet(f"background: {COLORS.WHITE};")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        self._review_label = QLabel("Review your order before submitting.")
        self._review_label.setStyleSheet(
            f"font-size: 13px; color: {COLORS.TEXT_PRIMARY}; background: transparent;"
        )
        self._review_label.setWordWrap(True)
        layout.addWidget(self._review_label)

        self._review_box = QPlainTextEdit()
        self._review_box.setReadOnly(True)
        self._review_box.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS.SLATE_50};
                border: 1px solid {COLORS.SLATE_200};
                border-radius: 5px;
                font-family: DM Mono, Consolas, monospace;
                font-size: 12px;
                color: {COLORS.TEXT_PRIMARY};
                padding: 10px;
            }}
        """)
        layout.addWidget(self._review_box, 1)
        return page

    def _build_footer(self):
        self._back_btn = QPushButton("← Back")
        self._back_btn.setProperty("class", "btn-ghost")
        self._back_btn.setEnabled(False)
        self._back_btn.clicked.connect(self._go_back)
        self._footer.addWidget(self._back_btn)
        self._footer.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("class", "btn-ghost")
        self._cancel_btn.clicked.connect(self.reject)
        self._footer.addWidget(self._cancel_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setProperty("class", "btn-primary")
        self._next_btn.setMinimumWidth(100)
        self._next_btn.clicked.connect(self._go_next)
        self._footer.addWidget(self._next_btn)

    def _go_next(self):
        if self._current_step < len(self.STEPS) - 1:
            if self._current_step == 3:
                # Final step — submit
                self._submit_order()
                return
            self._current_step += 1
            self._steps_widget.set_step(self._current_step)
            self._stack.setCurrentIndex(self._current_step)
            self._back_btn.setEnabled(True)
            if self._current_step == len(self.STEPS) - 1:
                self._next_btn.setText("✓ Create Order")
                self._update_review()
        else:
            self._submit_order()

    def _go_back(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._steps_widget.set_step(self._current_step)
            self._stack.setCurrentIndex(self._current_step)
            self._next_btn.setText("Next →")
            self._back_btn.setEnabled(self._current_step > 0)

    def _update_review(self):
        name = self._pt_name.text().strip() or "(no patient selected)"
        items_count = self._items_table.rowCount()
        lines = [
            f"Patient:      {name}",
            f"Insurance:    {self._pt_insurance.currentText()}  {self._pt_insurance_id.text()}",
            f"Prescriber:   {self._pt_prescriber.text()}",
            f"DOS:          {self._dos.date().toString('MM/dd/yyyy')}",
            f"Total Refills: {self._total_refills.value()}",
            f"Items:        {items_count} item(s)",
            "",
            "ITEMS:",
        ]
        for r in range(self._items_table.rowCount()):
            hcpcs = self._items_table.item(r, 0)
            desc  = self._items_table.item(r, 1)
            qty   = self._items_table.item(r, 2)
            hcpcs_t = hcpcs.text() if hcpcs else ""
            desc_t  = desc.text() if desc else ""
            qty_t   = qty.text() if qty else ""
            lines.append(f"  {hcpcs_t:<10} {qty_t:<5} {desc_t}")
        self._review_box.setPlainText("\n".join(lines))

    def _add_item_row(self):
        r = self._items_table.rowCount()
        self._items_table.insertRow(r)
        self._items_table.setRowHeight(r, 32)
        hcpcs_input = QLineEdit()
        hcpcs_input.setPlaceholderText("HCPCS")
        self._items_table.setCellWidget(r, 0, hcpcs_input)
        desc_input = QLineEdit()
        desc_input.setPlaceholderText("Description")
        self._items_table.setCellWidget(r, 1, desc_input)
        qty_spin = QSpinBox()
        qty_spin.setRange(1, 9999)
        qty_spin.setValue(1)
        self._items_table.setCellWidget(r, 2, qty_spin)
        ref_spin = QSpinBox()
        ref_spin.setRange(1, 36)
        ref_spin.setValue(6)
        self._items_table.setCellWidget(r, 3, ref_spin)
        days_spin = QSpinBox()
        days_spin.setRange(1, 365)
        days_spin.setValue(30)
        self._items_table.setCellWidget(r, 4, days_spin)
        mod_input = QLineEdit()
        mod_input.setPlaceholderText("KX, NU...")
        self._items_table.setCellWidget(r, 5, mod_input)
        del_btn = QPushButton("✕")
        del_btn.setStyleSheet(
            f"background: transparent; color: {COLORS.RED}; border: none; font-size: 13px;"
        )
        del_btn.clicked.connect(lambda _, row=r: self._items_table.removeRow(row))
        self._items_table.setCellWidget(r, 6, del_btn)

    def _quick_add_inventory(self):
        """Open inline Add Inventory dialog without closing the wizard."""
        from dmelogic.dme_dialogs import AddInventoryDialog
        dlg = AddInventoryDialog(parent=self, inline_mode=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_result()
            # Auto-add the new item as a row in the wizard
            r = self._items_table.rowCount()
            self._add_item_row()
            # Populate the new row
            hcpcs_widget = self._items_table.cellWidget(r, 0)
            desc_widget  = self._items_table.cellWidget(r, 1)
            if hcpcs_widget:
                hcpcs_widget.setText(data.get("hcpcs", ""))
            if desc_widget:
                desc_widget.setText(data.get("description", ""))

    def _submit_order(self):
        patient = self._pt_name.text().strip()
        if not patient:
            QMessageBox.warning(self, "Validation", "Please select or enter a patient name.")
            return
        if self._items_table.rowCount() == 0:
            reply = QMessageBox.question(
                self, "No Items",
                "No items have been added to this order. Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._result_data = {
            "patient_name":    patient,
            "patient_dob":     self._pt_dob.text(),
            "insurance":       self._pt_insurance.currentText(),
            "insurance_id":    self._pt_insurance_id.text(),
            "prescriber":      self._pt_prescriber.text(),
            "dos":             self._dos.date().toString("MM/dd/yyyy"),
            "total_refills":   self._total_refills.value(),
            "rx_on_file":      self._rx_on_file.isChecked(),
            "special_instructions": self._special_instructions.toPlainText().strip(),
            "items": [],
        }
        for r in range(self._items_table.rowCount()):
            hcpcs = self._items_table.cellWidget(r, 0)
            desc  = self._items_table.cellWidget(r, 1)
            qty   = self._items_table.cellWidget(r, 2)
            ref   = self._items_table.cellWidget(r, 3)
            days  = self._items_table.cellWidget(r, 4)
            mod   = self._items_table.cellWidget(r, 5)
            self._result_data["items"].append({
                "hcpcs": hcpcs.text() if hcpcs else "",
                "description": desc.text() if desc else "",
                "qty": qty.value() if qty else 1,
                "refills": ref.value() if ref else 6,
                "days": days.value() if days else 30,
                "modifier": mod.text() if mod else "",
            })
        self.order_created.emit(dict(self._result_data))
        super().accept()


# ─────────────────────────────────────────────────────────────────────────────
#  6. PATIENT DETAILS DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class PatientDetailsDialog(DMEDialog):
    """
    Patient record dialog with 5 tabs: Demographics, Order History,
    Documents, Notes, Communications.

    Usage:
        dlg = PatientDetailsDialog(
            parent=self,
            patient_data={
                "name":           "ABREU, ELIANE",
                "dob":            "07/26/1955",
                "address":        "123 Park Ave, Brooklyn NY 11201",
                "phone":          "(718) 555-0124",
                "insurance":      "Medicaid",
                "insurance_id":   "AB55521K",
                "prescriber":     "Dr. Rodriguez",
            }
        )
        dlg.exec()
    """

    def __init__(self, parent=None, patient_data: dict = None):
        d = patient_data or {}
        super().__init__(
            title=d.get("name", "Patient Details"),
            subtitle=f"DOB: {d.get('dob', '')}  •  {d.get('insurance', '')}  {d.get('insurance_id', '')}",
            width=680,
            height=540,
            parent=parent
        )
        self._patient = d
        self._build_body()
        # Footer: Edit Patient + Close
        edit_btn = QPushButton("✏ Edit Patient")
        edit_btn.setProperty("class", "btn-ghost")
        close_btn = QPushButton("Close")
        close_btn.setProperty("class", "btn-primary")
        close_btn.setMinimumWidth(80)
        close_btn.clicked.connect(self.accept)
        self._footer.addWidget(edit_btn)
        self._footer.addStretch()
        self._footer.addWidget(close_btn)

    def _build_body(self):
        d = self._patient
        tabs = QTabWidget()
        tabs.setMinimumHeight(360)
        self._body.addWidget(tabs)

        # Tab 1: Demographics
        demo = QWidget()
        demo.setStyleSheet(f"background: {COLORS.WHITE};")
        dl = QVBoxLayout(demo)
        dl.setContentsMargins(16, 14, 16, 14)
        dl.setSpacing(6)

        fields = [
            ("Full Name",      d.get("name", "")),
            ("Date of Birth",  d.get("dob", "")),
            ("Address",        d.get("address", "")),
            ("Phone",          d.get("phone", "")),
            ("Insurance",      d.get("insurance", "")),
            ("Insurance ID",   d.get("insurance_id", "")),
            ("Prescriber",     d.get("prescriber", "")),
        ]
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        for i, (label, value) in enumerate(fields):
            r, c = divmod(i, 2)
            lbl = QLabel(label.upper())
            lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 600; color: {COLORS.SLATE_500}; "
                f"background: transparent; letter-spacing: 0.3px;"
            )
            val = QLabel(value or "—")
            val.setStyleSheet(
                f"font-size: 13px; color: {COLORS.TEXT_PRIMARY}; background: transparent;"
            )
            sub = QVBoxLayout()
            sub.setSpacing(1)
            sub.addWidget(lbl)
            sub.addWidget(val)
            grid.addLayout(sub, r, c * 2)
        dl.addLayout(grid)
        dl.addStretch()
        tabs.addTab(demo, "Demographics")

        # Tab 2–5: Placeholder pages
        for tab_name in ["Order History", "Documents", "Notes", "Communications"]:
            ph = QWidget()
            ph.setStyleSheet(f"background: {COLORS.WHITE};")
            ph_l = QVBoxLayout(ph)
            ph_l.setContentsMargins(16, 14, 16, 14)
            placeholder = QLabel(f"{tab_name} tab — connect to your existing {tab_name.lower()} widget or data loader here.")
            placeholder.setStyleSheet(
                f"color: {COLORS.TEXT_MUTED}; font-size: 12px; font-style: italic; background: transparent;"
            )
            placeholder.setWordWrap(True)
            ph_l.addWidget(placeholder)
            ph_l.addStretch()
            tabs.addTab(ph, tab_name)


# ─────────────────────────────────────────────────────────────────────────────
#  7. ADD PRESCRIBER DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class AddPrescriberDialog(DMEDialog):
    """
    Add or edit a prescriber record.

    Usage:
        dlg = AddPrescriberDialog(parent=self)
        # Edit mode:
        dlg = AddPrescriberDialog(parent=self, prescriber_data={
            "name": "DR. EMILY CHEN", "npi": "1234567890", ...
        })
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_result()
    """

    SPECIALTIES = [
        "", "General Practice", "Internal Medicine", "Family Medicine",
        "Pediatrics", "Cardiology", "Neurology", "Orthopedics",
        "Urology", "Oncology", "Endocrinology", "Other"
    ]

    def __init__(self, parent=None, prescriber_data: dict = None):
        d = prescriber_data or {}
        is_edit = bool(d)
        super().__init__(
            title="Edit Prescriber" if is_edit else "Add Prescriber",
            width=520,
            parent=parent
        )
        self._pdata = d
        self._build_body()
        self.add_cancel_save(save_text="Save Changes" if is_edit else "Add Prescriber")

    def _build_body(self):
        d = self._pdata

        basic = SectionBox("Prescriber Information")
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._name = QLineEdit(d.get("name", ""))
        self._name.setPlaceholderText("DR. LAST, FIRST M.")
        self._npi = QLineEdit(d.get("npi", ""))
        self._npi.setPlaceholderText("10-digit NPI")
        self._specialty = QComboBox()
        self._specialty.addItems(self.SPECIALTIES)
        if d.get("specialty") in self.SPECIALTIES:
            self._specialty.setCurrentText(d["specialty"])
        self._practice = QLineEdit(d.get("practice", ""))
        self._practice.setPlaceholderText("Practice / Clinic name")
        self._phone = QLineEdit(d.get("phone", ""))
        self._phone.setPlaceholderText("(000) 000-0000")
        self._fax = QLineEdit(d.get("fax", ""))
        self._fax.setPlaceholderText("(000) 000-0000")
        self._address = QLineEdit(d.get("address", ""))
        self._city = QLineEdit(d.get("city", ""))
        self._state = QLineEdit(d.get("state", ""))
        self._state.setMaximumWidth(50)
        self._zip = QLineEdit(d.get("zip", ""))
        self._zip.setMaximumWidth(80)

        grid.addWidget(QLabel("Name *"), 0, 0)
        grid.addWidget(self._name, 0, 1)
        grid.addWidget(QLabel("NPI"), 0, 2)
        grid.addWidget(self._npi, 0, 3)
        grid.addWidget(QLabel("Specialty"), 1, 0)
        grid.addWidget(self._specialty, 1, 1)
        grid.addWidget(QLabel("Practice"), 1, 2)
        grid.addWidget(self._practice, 1, 3)
        grid.addWidget(QLabel("Phone"), 2, 0)
        grid.addWidget(self._phone, 2, 1)
        grid.addWidget(QLabel("Fax"), 2, 2)
        grid.addWidget(self._fax, 2, 3)
        grid.addWidget(QLabel("Address"), 3, 0)
        grid.addWidget(self._address, 3, 1, 1, 3)

        addr_row = QHBoxLayout()
        addr_row.addWidget(QLabel("City"))
        addr_row.addWidget(self._city)
        addr_row.addWidget(QLabel("State"))
        addr_row.addWidget(self._state)
        addr_row.addWidget(QLabel("ZIP"))
        addr_row.addWidget(self._zip)

        basic.content_layout().addLayout(grid)
        basic.content_layout().addLayout(addr_row)
        self._body.addWidget(basic)

    def accept(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, "Validation", "Prescriber name is required.")
            return
        self._result_data = {
            "name":      self._name.text().strip().upper(),
            "npi":       self._npi.text().strip(),
            "specialty": self._specialty.currentText(),
            "practice":  self._practice.text().strip(),
            "phone":     self._phone.text().strip(),
            "fax":       self._fax.text().strip(),
            "address":   self._address.text().strip(),
            "city":      self._city.text().strip(),
            "state":     self._state.text().strip().upper(),
            "zip":       self._zip.text().strip(),
        }
        super().accept()


# ─────────────────────────────────────────────────────────────────────────────
#  8. ADD PATIENT DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class AddPatientDialog(DMEDialog):
    """
    Add a new patient record.

    Usage:
        dlg = AddPatientDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_result()
    """

    def __init__(self, parent=None, patient_data: dict = None):
        d = patient_data or {}
        is_edit = bool(d)
        super().__init__(
            title="Edit Patient" if is_edit else "Add New Patient",
            width=560,
            parent=parent
        )
        self._pdata = d
        self._build_body()
        self.add_cancel_save(save_text="Save Patient" if is_edit else "Add Patient")

    def _build_body(self):
        d = self._pdata

        # Personal info
        personal = SectionBox("Personal Information")
        pg = QGridLayout()
        pg.setSpacing(8)
        pg.setColumnStretch(1, 1)
        pg.setColumnStretch(3, 1)

        self._last_name = QLineEdit(d.get("last_name", ""))
        self._last_name.setPlaceholderText("LAST")
        self._first_name = QLineEdit(d.get("first_name", ""))
        self._first_name.setPlaceholderText("First")
        self._dob = QLineEdit(d.get("dob", ""))
        self._dob.setPlaceholderText("MM/DD/YYYY")
        self._phone = QLineEdit(d.get("phone", ""))
        self._phone.setPlaceholderText("(000) 000-0000")
        self._address = QLineEdit(d.get("address", ""))
        self._city = QLineEdit(d.get("city", ""))
        self._state = QLineEdit(d.get("state", ""))
        self._state.setMaximumWidth(50)
        self._zip = QLineEdit(d.get("zip", ""))
        self._zip.setMaximumWidth(80)

        pg.addWidget(QLabel("Last Name *"), 0, 0)
        pg.addWidget(self._last_name, 0, 1)
        pg.addWidget(QLabel("First Name *"), 0, 2)
        pg.addWidget(self._first_name, 0, 3)
        pg.addWidget(QLabel("Date of Birth"), 1, 0)
        pg.addWidget(self._dob, 1, 1)
        pg.addWidget(QLabel("Phone"), 1, 2)
        pg.addWidget(self._phone, 1, 3)
        pg.addWidget(QLabel("Address"), 2, 0)
        pg.addWidget(self._address, 2, 1, 1, 3)

        addr_row = QHBoxLayout()
        addr_row.addWidget(QLabel("City"))
        addr_row.addWidget(self._city)
        addr_row.addWidget(QLabel("St"))
        addr_row.addWidget(self._state)
        addr_row.addWidget(QLabel("ZIP"))
        addr_row.addWidget(self._zip)

        personal.content_layout().addLayout(pg)
        personal.content_layout().addLayout(addr_row)
        self._body.addWidget(personal)

        # Insurance
        ins = SectionBox("Insurance & Medicare")
        ig = QGridLayout()
        ig.setSpacing(8)
        ig.setColumnStretch(1, 1)
        ig.setColumnStretch(2, 1)
        ig.setColumnStretch(3, 1)
        ig.setColumnStretch(4, 1)

        # Primary Insurance
        self._primary_payer = QComboBox()
        self._primary_payer.addItems(["", "Medicaid", "Medicare", "CDPHP", "MVP", "Private Pay", "Other"])
        if d.get("insurance") in ["Medicaid", "Medicare", "CDPHP", "MVP", "Private Pay", "Other"]:
            self._primary_payer.setCurrentText(d["insurance"])
        self._primary_payer_name = QLineEdit(d.get("primary_payer", ""))
        self._primary_payer_name.setPlaceholderText("Payer Name")
        self._primary_policy = QLineEdit(d.get("insurance_id", ""))
        self._primary_policy.setPlaceholderText("Policy #")
        self._primary_billed_via = QLineEdit(d.get("primary_billed_via", ""))
        self._primary_billed_via.setPlaceholderText("Billed Via")
        
        ig.addWidget(QLabel("Primary Insurance"), 0, 0)
        ig.addWidget(self._primary_payer, 0, 1)
        ig.addWidget(self._primary_payer_name, 0, 2)
        ig.addWidget(self._primary_policy, 0, 3)
        ig.addWidget(self._primary_billed_via, 0, 4)

        # Secondary Insurance
        self._secondary_payer = QComboBox()
        self._secondary_payer.addItems(["", "Medicaid", "Medicare", "CDPHP", "MVP", "Private Pay", "Other"])
        if d.get("secondary_insurance") in ["Medicaid", "Medicare", "CDPHP", "MVP", "Private Pay", "Other"]:
            self._secondary_payer.setCurrentText(d["secondary_insurance"])
        self._secondary_payer_name = QLineEdit(d.get("secondary_payer", ""))
        self._secondary_payer_name.setPlaceholderText("Payer Name")
        self._secondary_policy = QLineEdit(d.get("secondary_insurance_id", ""))
        self._secondary_policy.setPlaceholderText("Policy #")
        self._secondary_billed_via = QLineEdit(d.get("secondary_billed_via", ""))
        self._secondary_billed_via.setPlaceholderText("Billed Via")
        
        ig.addWidget(QLabel("Secondary Insurance"), 1, 0)
        ig.addWidget(self._secondary_payer, 1, 1)
        ig.addWidget(self._secondary_payer_name, 1, 2)
        ig.addWidget(self._secondary_policy, 1, 3)
        ig.addWidget(self._secondary_billed_via, 1, 4)

        # Medicare and Medicaid Numbers
        self._medicare_number = QLineEdit(d.get("medicare_number", ""))
        self._medicare_number.setPlaceholderText("Medicare ID (if applicable)")
        self._medicaid_number = QLineEdit(d.get("medicaid_policy_number", ""))
        self._medicaid_number.setPlaceholderText("Medicaid ID (if applicable)")
        
        ig.addWidget(QLabel("Medicare Policy Number"), 2, 0)
        ig.addWidget(self._medicare_number, 2, 1, 1, 4)
        
        ig.addWidget(QLabel("Medicaid Policy Number"), 3, 0)
        ig.addWidget(self._medicaid_number, 3, 1, 1, 4)

        ins.content_layout().addLayout(ig)
        self._body.addWidget(ins)

        # Notes
        note_lbl = QLabel("NOTES")
        note_lbl.setStyleSheet(
            f"font-size: 10.5px; font-weight: 600; color: {COLORS.SLATE_600}; background: transparent;"
        )
        self._notes = QLineEdit(d.get("notes", ""))
        self._notes.setPlaceholderText("Optional notes...")
        self._body.addWidget(note_lbl)
        self._body.addWidget(self._notes)

    def accept(self):
        last = self._last_name.text().strip()
        first = self._first_name.text().strip()
        if not last or not first:
            QMessageBox.warning(self, "Validation", "First and last name are required.")
            return
        self._result_data = {
            "last_name":              last.upper(),
            "first_name":             first,
            "name":                   f"{last.upper()}, {first}",
            "dob":                    self._dob.text().strip(),
            "phone":                  self._phone.text().strip(),
            "address":                self._address.text().strip(),
            "city":                   self._city.text().strip(),
            "state":                  self._state.text().strip().upper(),
            "zip":                    self._zip.text().strip(),
            "insurance":              self._primary_payer.currentText(),
            "primary_payer":          self._primary_payer_name.text().strip(),
            "insurance_id":           self._primary_policy.text().strip(),
            "primary_billed_via":     self._primary_billed_via.text().strip(),
            "secondary_insurance":    self._secondary_payer.currentText(),
            "secondary_payer":        self._secondary_payer_name.text().strip(),
            "secondary_insurance_id": self._secondary_policy.text().strip(),
            "secondary_billed_via":   self._secondary_billed_via.text().strip(),
            "medicare_number":        self._medicare_number.text().strip(),
            "medicaid_policy_number": self._medicaid_number.text().strip(),
            "notes":                  self._notes.text().strip(),
        }
        super().accept()
