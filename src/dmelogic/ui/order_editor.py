"""
Modern Order Editor / Viewer Dialog.

Central hub for all order-related actions:
- View complete order details (patient, prescriber, insurance, items)
- Edit order fields
- Change order status (with workflow validation)
- Export to State Portal
- Generate HCFA-1500 PDF
- Process refills
- Print delivery tickets

Uses domain model (Order) as single source of truth.
"""

from typing import Optional
from decimal import Decimal
from datetime import date, datetime
import re
from dmelogic.ui.inventory_search_dialog import InventorySearchDialog


def _safe_format_date(value, fmt: str = "%m/%d/%Y", default: str = "N/A") -> str:
    """Safely format a date/datetime/string to a display string."""
    if not value:
        return default
    if isinstance(value, (date, datetime)):
        return value.strftime(fmt)
    if isinstance(value, str):
        # Try to parse common formats
        for parse_fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value.split()[0], parse_fmt.split()[0]).strftime(fmt)
            except ValueError:
                continue
        return value  # Return as-is if can't parse
    return default

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QMessageBox, QSplitter,
    QHeaderView, QWidget, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from dmelogic.db import (
    fetch_order_with_items,
    Order,
    OrderItem,
    OrderStatus,
    BillingType,
)
from dmelogic.db.inventory import fetch_all_inventory
from dmelogic.db.orders import (
    update_order_item,
    add_order_item,
    delete_order_item,
    recompute_refill_due_date,
)
from dmelogic.db.order_workflow import (
    can_transition,
    get_allowed_next_statuses,
)
from dmelogic.db.rental_modifiers import format_modifiers_for_display
from dmelogic.config import debug_log
from dmelogic.ui.epaces_helper import EpacesHelperDialog
from dmelogic.db.base import resolve_db_path
from dmelogic.services.patient_address import get_patient_full_address
from dmelogic.prescriber_lookup_dialog import PrescriberLookupDialog
from dmelogic.refill_service import process_refill, RefillError
from dmelogic.ui.components.sticky_notes_panel import StickyNotesPanel
from dmelogic.ui.reorder_dialog import ReorderConfirmationDialog
from dmelogic.ui.dictation import enable_dictation
from dmelogic.services.order_pricing import line_pricing_for_order_item
from dmelogic.reserved_rx_manager import ReservedRxPanel, handle_last_refill, get_reserved_rx_data


class OrderEditorDialog(QDialog):
    """
    Modern order editor dialog - central hub for all order operations.
    
    Features:
    - Load order via fetch_order_with_items() domain model
    - Display all order details in organized sections
    - Edit fields with validation
    - Action buttons for common operations:
      * Send to State Portal
      * Generate HCFA-1500 PDF
      * Print Delivery Ticket
      * Process Refills
      * Change Status
    - Uses workflow engine for status transitions
    """
    
    order_updated = pyqtSignal()  # Emitted when order is modified
    
    def __init__(
        self,
        order_id: int,
        folder_path: Optional[str] = None,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        
        self.order_id = order_id
        self.folder_path = folder_path
        self.order: Optional[Order] = None
        self._item_row_meta: list[dict] = []  # tracks item ids per row
        self._deleted_item_ids: set[int] = set()
        self._suppress_item_change = False
        self._items_dirty = False
        self._epaces_dialog = None  # Track EPACES dialog for refresh
        
        self._setup_ui()
        self._load_order()
        
        # Refresh EPACES dialog when order is updated (e.g., prescriber changes)
        self.order_updated.connect(self._refresh_epaces_dialog)

    @property
    def orders_db_path(self) -> str:
        """Get the path to orders.db."""
        return resolve_db_path("orders.db", folder_path=self.folder_path)

    def _format_order_number(self, order: Optional[Order] = None) -> str:
        """Return continuous display number, optionally annotated with refill context."""
        try:
            src = order or getattr(self, "order", None)
            if src:
                display = f"ORD-{int(src.id):03d}"
                if src.parent_order_id and (src.refill_number or 0) > 0:
                    display += f" (Refill of ORD-{int(src.parent_order_id):03d} R{int(src.refill_number)})"
                return display
            if self.order_id:
                return f"ORD-{int(self.order_id):03d}"
        except Exception:
            pass
        return str(getattr(order or self, "order_id", "") or "Order")
    
    def _setup_ui(self):
        """Build the complete UI layout."""
        self.setWindowTitle("Order Editor")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setMinimumSize(1200, 800)
        self.resize(1280, 850)

        # Modern light theme: white-card group boxes and a clear button
        # hierarchy — only class="primary" actions (Save Changes, Update Status,
        # Save Items) are filled accent; everything else is a neutral ghost,
        # replacing the old wall of identical blue buttons.
        self.setStyleSheet("""
            QGroupBox {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 12px;
                font-weight: 600;
                color: #475569;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
            }
            QPushButton {
                background: #ffffff;
                color: #0f172a;
                font-weight: 600;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px 14px;
            }
            QPushButton:hover { background: #f1f5f9; border-color: #cbd5e1; }
            QPushButton:disabled { background: #f8fafc; color: #cbd5e1; }
            QPushButton[class="primary"] {
                background: #2563eb; color: #ffffff; border: none;
            }
            QPushButton[class="primary"]:hover { background: #1d4ed8; }
            QPushButton[class="primary"]:disabled { background: #cbd5e1; color: #ffffff; }
        """)
        
        # Main layout with splitter
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 4, 8, 8)
        main_layout.setSpacing(4)
        
        # Header with order ID and status (compact, non-stretching)
        header = self._create_header()
        header.setFixedHeight(40)
        main_layout.addWidget(header)
        
        # Splitter: Left (order details) | Right (actions)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left side: Order details
        left_panel = self._create_order_details_panel()
        splitter.addWidget(left_panel)
        
        # Right side: Action buttons (scrollable)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setMinimumWidth(320)
        right_panel = self._create_actions_panel()
        right_scroll.setWidget(right_panel)
        splitter.addWidget(right_scroll)
        
        splitter.setStretchFactor(0, 3)  # Order details take ~60%
        splitter.setStretchFactor(1, 2)  # Actions take ~40%
        splitter.setSizes([700, 480])  # Explicit initial sizes
        
        main_layout.addWidget(splitter, 1)  # Give splitter all remaining stretch
        
        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.save_button = QPushButton("Save Changes")
        self.save_button.setProperty("class", "primary")
        self.save_button.setEnabled(False)  # Enable when changes detected
        self.save_button.clicked.connect(self._save_changes)
        button_layout.addWidget(self.save_button)
        
        self.close_button = QPushButton("Close")
        self.close_button.setProperty("class", "secondary")
        self.close_button.clicked.connect(self.accept)
        button_layout.addWidget(self.close_button)
        
        main_layout.addLayout(button_layout)
    
    def _create_header(self) -> QWidget:
        """Create header with order ID and current status."""
        header = QFrame()
        header.setProperty("class", "section-header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 4, 10, 4)
        
        # Order ID
        self.order_id_label = QLabel(f"Order #: {self._format_order_number()}")
        self.order_id_label.setProperty("class", "wizard-title")
        layout.addWidget(self.order_id_label)
        
        layout.addStretch()
        
        # Current status badge
        status_label = QLabel("Status:")
        layout.addWidget(status_label)
        
        self.status_badge = QLabel("Loading...")
        self.status_badge.setObjectName("StatusBadge")
        layout.addWidget(self.status_badge)
        
        return header
    
    def _create_order_details_panel(self) -> QWidget:
        """Create left panel with all order details."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        
        # Patient Section
        layout.addWidget(self._create_patient_section())
        
        # Prescriber Section
        layout.addWidget(self._create_prescriber_section())
        
        # Insurance Section
        layout.addWidget(self._create_insurance_section())
        
        # Clinical Section (ICD codes, directions)
        layout.addWidget(self._create_clinical_section())
        
        # Items Section (table)
        layout.addWidget(self._create_items_section())
        
        # Notes Section
        layout.addWidget(self._create_notes_section())
        
        # Reserved RX on File Section
        layout.addWidget(self._create_reserved_rx_section())
        
        layout.addStretch()
        
        scroll.setWidget(container)
        return scroll
    
    def _create_patient_section(self) -> QGroupBox:
        """Create patient information section."""
        group = QGroupBox("Patient Information")
        layout = QVBoxLayout(group)
        
        # Display fields
        form_layout = QFormLayout()
        
        self.patient_name = QLabel()
        form_layout.addRow("Name:", self.patient_name)
        
        self.patient_dob = QLabel()
        form_layout.addRow("Date of Birth:", self.patient_dob)
        
        self.patient_phone = QLabel()
        form_layout.addRow("Phone:", self.patient_phone)
        
        self.patient_address = QLabel()
        self.patient_address.setWordWrap(True)
        form_layout.addRow("Address:", self.patient_address)
        
        layout.addLayout(form_layout)
        
        # Change patient button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.edit_patient_btn = QPushButton("Edit Patient")
        self.edit_patient_btn.clicked.connect(self._edit_patient)
        btn_layout.addWidget(self.edit_patient_btn)
        self.change_patient_btn = QPushButton("Change Patient")
        self.change_patient_btn.clicked.connect(self._change_patient)
        btn_layout.addWidget(self.change_patient_btn)
        layout.addLayout(btn_layout)
        
        return group
    
    def _create_prescriber_section(self) -> QGroupBox:
        """Create prescriber information section."""
        group = QGroupBox("Prescriber Information")
        layout = QVBoxLayout(group)
        
        # Primary Prescriber
        form_layout = QFormLayout()
        
        self.prescriber_name = QLabel()
        self.prescriber_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form_layout.addRow("Name:", self.prescriber_name)
        
        self.prescriber_npi = QLabel()
        self.prescriber_npi.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form_layout.addRow("NPI:", self.prescriber_npi)
        
        self.prescriber_phone_input = QLineEdit()
        self.prescriber_phone_input.setPlaceholderText("(555) 555-5555")
        form_layout.addRow("Phone (for this order):", self.prescriber_phone_input)
        
        self.prescriber_fax_input = QLineEdit()
        self.prescriber_fax_input.setPlaceholderText("(555) 555-5555")
        form_layout.addRow("Fax (for this order):", self.prescriber_fax_input)
        
        layout.addLayout(form_layout)
        
        # Change prescriber button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.change_prescriber_btn = QPushButton("Change Prescriber")
        self.change_prescriber_btn.clicked.connect(self._change_prescriber)
        btn_layout.addWidget(self.change_prescriber_btn)
        layout.addLayout(btn_layout)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #D1D5DB; margin: 8px 0;")
        layout.addWidget(separator)
        
        # Secondary Prescriber (Prescriber 2)
        form_layout_2 = QFormLayout()
        
        self.prescriber_name_2 = QLabel()
        self.prescriber_name_2.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form_layout_2.addRow("Prescriber 2:", self.prescriber_name_2)
        
        self.prescriber_npi_2 = QLabel()
        self.prescriber_npi_2.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form_layout_2.addRow("NPI 2:", self.prescriber_npi_2)
        
        layout.addLayout(form_layout_2)
        
        # Change prescriber 2 button
        btn_layout_2 = QHBoxLayout()
        btn_layout_2.addStretch()
        self.change_prescriber_2_btn = QPushButton("Change Prescriber 2")
        self.change_prescriber_2_btn.clicked.connect(self._change_prescriber_2)
        btn_layout_2.addWidget(self.change_prescriber_2_btn)
        layout.addLayout(btn_layout_2)
        
        return group
    
    def _create_insurance_section(self) -> QGroupBox:
        """Create insurance information section."""
        group = QGroupBox("Insurance Information")
        layout = QVBoxLayout(group)
        
        form_layout = QFormLayout()
        
        self.insurance_name = QLabel()
        form_layout.addRow("Primary Insurance:", self.insurance_name)
        
        self.insurance_id = QLabel()
        form_layout.addRow("Policy Number:", self.insurance_id)
        
        self.secondary_insurance_name = QLabel()
        form_layout.addRow("Secondary Insurance:", self.secondary_insurance_name)
        
        self.secondary_insurance_id = QLabel()
        form_layout.addRow("Secondary Policy #:", self.secondary_insurance_id)
        
        self.billing_type = QLabel()
        form_layout.addRow("Billing Type:", self.billing_type)

        # Place of Service — editable; required on the claim (11 Office / 12 Home).
        from dmelogic.place_of_service import place_of_service_labels
        self.place_of_service_combo = QComboBox()
        self.place_of_service_combo.addItems(place_of_service_labels())
        self.place_of_service_combo.setToolTip(
            "Billing Place of Service for the claim: 11 = Office, 12 = Home.\n"
            "Saved immediately when changed; shown in the ePACES helper for billing."
        )
        self._pos_loading = False
        self.place_of_service_combo.currentIndexChanged.connect(self._on_place_of_service_changed)
        form_layout.addRow("Place of Service:", self.place_of_service_combo)

        layout.addLayout(form_layout)
        
        # Change insurance button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.change_insurance_btn = QPushButton("Change Insurance")
        self.change_insurance_btn.clicked.connect(self._change_insurance)
        btn_layout.addWidget(self.change_insurance_btn)
        layout.addLayout(btn_layout)
        
        return group
    
    def _create_clinical_section(self) -> QGroupBox:
        """Create clinical information section."""
        group = QGroupBox("Clinical Information")
        layout = QFormLayout(group)
        
        self.rx_date = QLineEdit()
        self.rx_date.setPlaceholderText("MM/DD/YYYY")
        layout.addRow("RX Date:", self.rx_date)
        
        self.rx_date_2 = QLineEdit()
        self.rx_date_2.setPlaceholderText("MM/DD/YYYY")
        layout.addRow("RX Date 2:", self.rx_date_2)
        
        self.order_date = QLineEdit()
        self.order_date.setPlaceholderText("MM/DD/YYYY")
        layout.addRow("Order Date:", self.order_date)
        
        self.delivery_date = QLineEdit()
        self.delivery_date.setPlaceholderText("MM/DD/YYYY or leave empty")
        layout.addRow("Delivery Date:", self.delivery_date)
        
        self.pickup_date = QLineEdit()
        self.pickup_date.setPlaceholderText("MM/DD/YYYY or leave empty")
        layout.addRow("Pickup Date:", self.pickup_date)
        
        self.tracking_number = QLineEdit()
        self.tracking_number.setPlaceholderText("Enter tracking number...")
        layout.addRow("Tracking #:", self.tracking_number)
        
        # ICD-10 Codes - editable fields
        icd_container = QWidget()
        icd_layout = QHBoxLayout(icd_container)
        icd_layout.setContentsMargins(0, 0, 0, 0)
        icd_layout.setSpacing(4)
        
        self.icd_code_fields = []
        for i in range(5):
            icd_field = QLineEdit()
            icd_field.setPlaceholderText(f"ICD {i+1}")
            icd_field.setMaximumWidth(100)
            icd_field.textChanged.connect(self._on_text_changed)
            self.icd_code_fields.append(icd_field)
            icd_layout.addWidget(icd_field)
        
        icd_layout.addStretch()
        layout.addRow("ICD-10 Codes:", icd_container)
        
        self.doctor_directions = QTextEdit()
        self.doctor_directions.setMaximumHeight(80)
        self.doctor_directions.setPlaceholderText("Enter doctor directions...")
        enable_dictation(self.doctor_directions)
        layout.addRow("Doctor Directions:", self.doctor_directions)
        
        return group
    
    def _create_items_section(self) -> QGroupBox:
        """Create order items table section."""
        group = QGroupBox("Order Items")
        layout = QVBoxLayout(group)
        
        self.items_table = QTableWidget()
        self.items_table.setColumnCount(10)
        self.items_table.setHorizontalHeaderLabels([
            "HCPCS", "Item #", "Description", "Qty", "Refills", "Days", "Modifiers", "Bill Ea", "Amount", "Prescriber"
        ])
        
        # Set column widths
        header = self.items_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.SelectedClicked)
        self.items_table.itemChanged.connect(self._on_item_cell_changed)
        
        layout.addWidget(self.items_table)

        # Item actions
        btn_row = QHBoxLayout()
        self.add_item_btn = QPushButton("➕ Add Item")
        self.add_item_btn.setProperty("class", "secondary")
        self.add_item_btn.clicked.connect(self._add_item_row)
        btn_row.addWidget(self.add_item_btn)

        self.search_inventory_btn = QPushButton("🔍 Search Inventory")
        self.search_inventory_btn.setProperty("class", "secondary")
        self.search_inventory_btn.clicked.connect(self._open_inventory_search)
        btn_row.addWidget(self.search_inventory_btn)

        self.edit_items_btn = QPushButton("✏️ Edit Selected")
        self.edit_items_btn.clicked.connect(self._edit_items)
        btn_row.addWidget(self.edit_items_btn)

        self.remove_item_btn = QPushButton("🗑️ Remove Selected")
        self.remove_item_btn.setProperty("class", "secondary")
        self.remove_item_btn.clicked.connect(self._remove_selected_items)
        btn_row.addWidget(self.remove_item_btn)

        btn_row.addStretch()

        self.save_items_btn = QPushButton("💾 Save Item Changes")
        self.save_items_btn.setProperty("class", "primary")
        self.save_items_btn.clicked.connect(self._save_item_changes)
        self.save_items_btn.setEnabled(False)
        btn_row.addWidget(self.save_items_btn)

        layout.addLayout(btn_row)
        
        # Order total
        total_layout = QHBoxLayout()
        total_layout.addStretch()
        total_layout.addWidget(QLabel("Order Total:"))
        self.order_total_label = QLabel("$0.00")
        font = QFont()
        font.setBold(True)
        font.setPointSize(11)
        self.order_total_label.setFont(font)
        total_layout.addWidget(self.order_total_label)
        layout.addLayout(total_layout)
        
        return group
    
    def _create_notes_section(self) -> QGroupBox:
        """Create notes section."""
        group = QGroupBox("Notes")
        layout = QVBoxLayout(group)
        
        self.notes_text = QTextEdit()
        self.notes_text.setMaximumHeight(100)
        self.notes_text.setPlaceholderText("Enter order notes...")
        enable_dictation(self.notes_text)
        layout.addWidget(self.notes_text)

        # Special Instructions for delivery
        instructions_label = QLabel("Special Instructions (for delivery):")
        instructions_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(instructions_label)

        self.special_instructions_text = QTextEdit()
        self.special_instructions_text.setMaximumHeight(80)
        self.special_instructions_text.setPlaceholderText("Enter delivery instructions for the driver...")
        enable_dictation(self.special_instructions_text)
        layout.addWidget(self.special_instructions_text)

        # Billing Alert (popup note shown when EPACES helper opens)
        alert_label = QLabel("Billing Alert (popup when EPACES opens):")
        alert_label.setStyleSheet("font-weight: bold; margin-top: 10px; color: #d63384;")
        layout.addWidget(alert_label)

        self.epaces_alert_text = QTextEdit()
        self.epaces_alert_text.setMaximumHeight(60)
        self.epaces_alert_text.setPlaceholderText("Optional: leave a note for the biller (e.g. 'Call patient before billing')...")
        self.epaces_alert_text.setStyleSheet("border: 1px solid #d63384; border-radius: 4px;")
        enable_dictation(self.epaces_alert_text)
        layout.addWidget(self.epaces_alert_text)
        
        return group
    
    def _create_reserved_rx_section(self) -> QGroupBox:
        """Create Reserved RX on File section."""
        group = QGroupBox("Reserved RX on File")
        layout = QVBoxLayout(group)
        
        self.rx_panel = ReservedRxPanel(
            db_path=self.orders_db_path,
            order_id=str(self.order_id) if self.order_id else None
        )
        self.rx_panel.data_changed.connect(self._on_rx_data_changed)
        layout.addWidget(self.rx_panel)
        
        return group
    
    def _on_rx_data_changed(self, data: dict):
        """Handle changes from the Reserved RX panel — enable save button."""
        self.save_button.setEnabled(True)

    def _create_actions_panel(self) -> QWidget:
        """Create right panel with action buttons."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        
        # Title
        title = QLabel("Actions")
        title.setProperty("class", "section-title")
        layout.addWidget(title)
        
        # Status Management
        status_group = QGroupBox("Status Management")
        status_layout = QVBoxLayout(status_group)
        
        self.status_combo = QComboBox()
        self.status_combo.currentTextChanged.connect(self._on_status_change_requested)
        status_layout.addWidget(QLabel("Change Status To:"))
        status_layout.addWidget(self.status_combo)
        
        self.change_status_btn = QPushButton("Update Status")
        self.change_status_btn.setProperty("class", "primary")
        self.change_status_btn.clicked.connect(self._change_status)
        status_layout.addWidget(self.change_status_btn)
        
        layout.addWidget(status_group)
        
        # Separator
        layout.addWidget(self._create_separator())
        
        # Export & Forms
        export_group = QGroupBox("Export & Forms")
        export_layout = QVBoxLayout(export_group)
        
        self.portal_btn = QPushButton("📤 Send to State Portal")
        self.portal_btn.clicked.connect(self._send_to_portal)
        export_layout.addWidget(self.portal_btn)
        
        self.form_1500_btn = QPushButton("📄 Generate HCFA-1500")
        self.form_1500_btn.clicked.connect(self._generate_1500)
        export_layout.addWidget(self.form_1500_btn)
        
        self.epaces_btn = QPushButton("🔐 Bill in ePACES...")
        self.epaces_btn.setProperty("class", "secondary")
        self.epaces_btn.setToolTip("Open copy-friendly helper for manual ePACES portal entry")
        self.epaces_btn.clicked.connect(self._open_epaces_helper)
        export_layout.addWidget(self.epaces_btn)
        
        self.delivery_ticket_btn = QPushButton("🎫 Print Delivery Ticket")
        self.delivery_ticket_btn.clicked.connect(self._print_delivery_ticket)
        export_layout.addWidget(self.delivery_ticket_btn)
        
        layout.addWidget(export_group)
        
        # Separator
        layout.addWidget(self._create_separator())
        
        # Processing
        processing_group = QGroupBox("Processing")
        processing_layout = QVBoxLayout(processing_group)
        
        self.refill_btn = QPushButton("🔄 Process Refill")
        self.refill_btn.clicked.connect(self._process_refill)
        processing_layout.addWidget(self.refill_btn)

        self.reorder_btn = QPushButton("📋 Reorder (New RX)")
        self.reorder_btn.setToolTip(
            "Create a new order from this one when refills are exhausted\n"
            "but a new prescription is on file."
        )
        self.reorder_btn.clicked.connect(self._reorder)
        processing_layout.addWidget(self.reorder_btn)
        layout.addWidget(processing_group)
        
        # Separator
        layout.addWidget(self._create_separator())
        
        # Documents
        docs_group = QGroupBox("Documents")
        docs_layout = QVBoxLayout(docs_group)
        
        # Document buttons row
        docs_btn_row = QHBoxLayout()
        
        self.view_docs_btn = QPushButton("📁 View")
        self.view_docs_btn.setToolTip("View attached documents")
        self.view_docs_btn.clicked.connect(self._view_documents)
        docs_btn_row.addWidget(self.view_docs_btn)
        
        self.attach_doc_btn = QPushButton("📎 Attach")
        self.attach_doc_btn.setToolTip("Attach a document to this order")
        self.attach_doc_btn.clicked.connect(self._attach_document)
        docs_btn_row.addWidget(self.attach_doc_btn)
        
        self.scan_doc_btn = QPushButton("🖨️ Scan")
        self.scan_doc_btn.setToolTip("Scan a document from the scanner and attach to this order")
        self.scan_doc_btn.clicked.connect(self._scan_document)
        docs_btn_row.addWidget(self.scan_doc_btn)

        self.phone_scan_btn = QPushButton("📱 Phone")
        self.phone_scan_btn.setToolTip("Scan a document using your phone camera (same WiFi required)")
        self.phone_scan_btn.clicked.connect(self._scan_from_phone)
        docs_btn_row.addWidget(self.phone_scan_btn)

        docs_layout.addLayout(docs_btn_row)

        self.batch_delivery_btn = QPushButton("📚 Batch Delivery OCR")
        self.batch_delivery_btn.setToolTip("Process multiple signed delivery tickets and auto-attach by OCR order number")
        self.batch_delivery_btn.clicked.connect(self._batch_attach_delivery_tickets)
        docs_layout.addWidget(self.batch_delivery_btn)
        
        # Documents list
        self.docs_list = QTableWidget()
        self.docs_list.setColumnCount(3)
        self.docs_list.setHorizontalHeaderLabels(["Filename", "Type", ""])
        self.docs_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.docs_list.setMinimumHeight(120)
        self.docs_list.setMaximumHeight(200)
        self.docs_list.verticalHeader().setVisible(False)
        self.docs_list.verticalHeader().setDefaultSectionSize(26)
        docs_hdr = self.docs_list.horizontalHeader()
        docs_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        docs_hdr.setMinimumSectionSize(150)
        docs_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        docs_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.docs_list.setColumnWidth(2, 28)
        self.docs_list.doubleClicked.connect(self._open_selected_document)
        self.docs_list.cellClicked.connect(self._on_docs_cell_clicked)
        docs_layout.addWidget(self.docs_list)
        
        layout.addWidget(docs_group)

        # Separator
        layout.addWidget(self._create_separator())

        # Sticky Notes
        notes_group = QGroupBox("Sticky Notes")
        notes_group.setMinimumHeight(180)  # Ensure enough space for table
        notes_layout = QVBoxLayout(notes_group)
        self.sticky_panel = StickyNotesPanel(
            entity_type="order",
            entity_id=self.order_id,
            folder_path=self.folder_path,
            parent=notes_group,
        )
        notes_layout.addWidget(self.sticky_panel)
        layout.addWidget(notes_group, 1)  # Give it stretch priority
        
        layout.addStretch(0)  # Less stretch than notes group
        
        # Refresh button at bottom
        self.refresh_btn = QPushButton("🔄 Refresh Order")
        self.refresh_btn.setProperty("class", "secondary")
        self.refresh_btn.clicked.connect(self._load_order)
        layout.addWidget(self.refresh_btn)
        
        return panel
    
    def _on_place_of_service_changed(self, _index: int) -> None:
        """Persist the Place of Service immediately when the user changes it."""
        if getattr(self, "_pos_loading", False) or not self.order:
            return
        try:
            from dmelogic.place_of_service import place_of_service_code
            from dmelogic.db.orders import update_order_fields
            code = place_of_service_code(self.place_of_service_combo.currentText())
            update_order_fields(self.order.id, {"place_of_service": code}, folder_path=self.folder_path)
            setattr(self.order, "place_of_service", code)
            debug_log(f"Order {self._format_order_number()}: place_of_service -> {code}")
        except Exception as e:
            QMessageBox.warning(self, "Place of Service", f"Could not save Place of Service:\n{e}")

    def _maybe_warn_order_rules(self) -> None:
        """
        When an UNBILLED order is loaded, warn (once) if it exceeds Max-Units
        limits or contains an incompatible item combination, so the user can
        correct THIS order before billing. Path-independent: catches refills
        created by any code path, since the new order opens here.
        """
        try:
            from dmelogic.order_rules import (
                evaluate_order_object, warn_order_needs_edit,
                order_is_editable_prebilling,
            )
            if not self.order or not order_is_editable_prebilling(self.order):
                return
            # Warn once per order id (not on every refresh of the same order).
            if getattr(self, "_rule_warned_order_id", None) == self.order_id:
                return
            report = evaluate_order_object(self.order, self.folder_path)
            if report.has_issues:
                self._rule_warned_order_id = self.order_id
                warn_order_needs_edit(self, self.order, report)
        except Exception as e:
            debug_log(f"[order_rules] editor rule warning skipped: {e}")

    def _create_separator(self) -> QFrame:
        """Create a horizontal separator line."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line
    
    def _load_order(self):
        """Load order from database using domain model."""
        try:
            self.order = fetch_order_with_items(
                self.order_id,
                folder_path=self.folder_path
            )
            self._deleted_item_ids.clear()
            self._items_dirty = False
            
            if not self.order:
                QMessageBox.critical(
                    self,
                    "Order Not Found",
                    f"Order {self._format_order_number()} could not be loaded."
                )
                self.reject()
                return
            
            self._bind_order_to_ui()
            debug_log(f"Order {self._format_order_number()} loaded successfully")
            self._maybe_warn_order_rules()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Loading Order",
                f"Failed to load order: {str(e)}"
            )
            debug_log(f"Error loading order {self.order_id}: {e}")
            self.reject()

    def _fetch_live_patient_row(self):
        """Return patient row by patient_id with name fallback for legacy orders."""
        if not self.order:
            return None

        try:
            from dmelogic.db.base import get_connection

            conn = get_connection("patients.db", folder_path=self.folder_path)
            try:
                cursor = conn.cursor()

                patient_id = getattr(self.order, "patient_id", None)
                if patient_id:
                    cursor.execute(
                        """
                        SELECT last_name, first_name, dob, phone, address, city, state, zip
                        FROM patients WHERE id = ?
                        """,
                        (patient_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        return row

                # Fallback for legacy orders with missing patient_id.
                last_name = (getattr(self.order, "patient_last_name", "") or "").strip()
                first_name = (getattr(self.order, "patient_first_name", "") or "").strip()

                if (not last_name or not first_name) and getattr(self.order, "patient_name", None):
                    parts = str(self.order.patient_name).split(",", 1)
                    if len(parts) == 2:
                        last_name = last_name or parts[0].strip()
                        first_name = first_name or parts[1].strip()

                if last_name and first_name:
                    cursor.execute(
                        """
                        SELECT last_name, first_name, dob, phone, address, city, state, zip
                        FROM patients
                        WHERE UPPER(last_name) = UPPER(?) AND UPPER(first_name) = UPPER(?)
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (last_name, first_name),
                    )
                    return cursor.fetchone()

                return None
            finally:
                conn.close()
        except Exception as e:
            debug_log(f"[order-editor] Failed live patient lookup: {e}")
            return None
    
    def _bind_order_to_ui(self):
        """Populate UI fields from loaded order."""
        if not self.order:
            return
        
        # Update header
        self.order_id_label.setText(f"Order #: {self._format_order_number(self.order)}")
        self._update_status_badge()
        
        # Load Reserved RX panel data
        if hasattr(self, 'rx_panel'):
            self.rx_panel.load(str(self.order.id))

        live_patient = self._fetch_live_patient_row()
        
        # Patient section - use legacy flat fields (from orders table)
        self.patient_name.setText(self.order.patient_full_name or "N/A")
        if live_patient:
            self.patient_dob.setText(live_patient[2] or _safe_format_date(self.order.patient_dob))
            self.patient_phone.setText(live_patient[3] or self.order.patient_phone or "N/A")
            # Keep in-memory snapshot aligned for this session.
            self.order.patient_dob = live_patient[2] or self.order.patient_dob
            self.order.patient_phone = live_patient[3] or self.order.patient_phone
        else:
            self.patient_dob.setText(_safe_format_date(self.order.patient_dob))
            self.patient_phone.setText(
                self.order.patient_phone or "N/A"
            )
        
        # Patient address - prefer patients.db (by patient_id, else name), fallback to order snapshot
        patient_db_path = resolve_db_path("patients.db", folder_path=self.folder_path)
        patient_address = get_patient_full_address(
            patient_db_path,
            getattr(self.order, "patient_id", None),
            self.order.patient_last_name or "",
            self.order.patient_first_name or "",
        )
        if not patient_address:
            snapshot = (
                getattr(self.order, "patient_address_at_order_time", None)
                or getattr(self.order, "patient_address", None)
                or ""
            )
            patient_address = snapshot.strip()
        self.patient_address.setText(patient_address or "N/A")
        
        # Prescriber section - use legacy flat fields
        self.prescriber_name.setText(
            self.order.prescriber_name or "N/A"
        )
        self.prescriber_npi.setText(
            self.order.prescriber_npi or "N/A"
        )
        self.prescriber_phone_input.setText(
            self.order.prescriber_phone or ""
        )
        self.prescriber_fax_input.setText(
            self.order.prescriber_fax or ""
        )
        
        # Secondary prescriber (Prescriber 2)
        self.prescriber_name_2.setText(
            getattr(self.order, 'prescriber_name_2', '') or "N/A"
        )
        self.prescriber_npi_2.setText(
            getattr(self.order, 'prescriber_npi_2', '') or "N/A"
        )
        
        # Insurance section - use legacy flat fields
        self.insurance_name.setText(
            self.order.primary_insurance or "N/A"
        )
        self.insurance_id.setText(
            self.order.primary_insurance_id or "N/A"
        )
        
        # Secondary insurance - try order first, then patient record
        sec_ins = getattr(self.order, 'secondary_insurance', None) or ""
        sec_id = getattr(self.order, 'secondary_insurance_id', None) or ""
        
        # Fallback to patient record if order doesn't have secondary insurance
        if not sec_ins:
            try:
                from dmelogic.db.patients import find_patient_by_name_and_dob
                patient_record = None
                if self.order.patient_last_name and self.order.patient_first_name:
                    dob_str = None
                    if self.order.patient_dob:
                        dob_str = _safe_format_date(self.order.patient_dob, fmt="%Y-%m-%d")
                    patient_record = find_patient_by_name_and_dob(
                        self.order.patient_last_name,
                        self.order.patient_first_name,
                        dob=dob_str,
                        folder_path=self.folder_path
                    )
                if patient_record:
                    sec_ins = patient_record.get('secondary_insurance') if hasattr(patient_record, 'get') else (patient_record['secondary_insurance'] if 'secondary_insurance' in patient_record.keys() else '')
                    sec_id = patient_record.get('secondary_insurance_id') if hasattr(patient_record, 'get') else (patient_record['secondary_insurance_id'] if 'secondary_insurance_id' in patient_record.keys() else '')
            except Exception as e:
                debug_log(f"Failed to get secondary insurance from patient: {e}")
        
        self.secondary_insurance_name.setText(sec_ins or "N/A")
        self.secondary_insurance_id.setText(sec_id or "N/A")
        
        # Safely get billing type value
        billing_val = "Insurance"
        if hasattr(self.order, 'billing_type') and self.order.billing_type:
            billing_val = self.order.billing_type.value
        elif hasattr(self.order, 'billing_selection') and self.order.billing_selection:
            billing_val = self.order.billing_selection
        self.billing_type.setText(billing_val)

        # Place of Service (guard so programmatic set doesn't trigger a save)
        try:
            from dmelogic.place_of_service import place_of_service_code
            code = place_of_service_code(getattr(self.order, "place_of_service", None))
            self._pos_loading = True
            for idx in range(self.place_of_service_combo.count()):
                if place_of_service_code(self.place_of_service_combo.itemText(idx)) == code:
                    self.place_of_service_combo.setCurrentIndex(idx)
                    break
        finally:
            self._pos_loading = False

        # Clinical section (editable date fields)
        self.rx_date.setText(_safe_format_date(self.order.rx_date) or "")
        self.rx_date_2.setText(_safe_format_date(getattr(self.order, 'rx_date_2', None)) or "")
        self.order_date.setText(_safe_format_date(self.order.order_date) or "")
        self.delivery_date.setText(_safe_format_date(self.order.delivery_date) or "")
        self.pickup_date.setText(_safe_format_date(self.order.pickup_date) or "")
        self.tracking_number.setText(self.order.tracking_number or "")
        
        # Connect date/tracking field changes to enable Save button
        self.rx_date.textChanged.connect(self._on_text_changed)
        self.rx_date_2.textChanged.connect(self._on_text_changed)
        self.order_date.textChanged.connect(self._on_text_changed)
        self.delivery_date.textChanged.connect(self._on_text_changed)
        self.pickup_date.textChanged.connect(self._on_text_changed)
        self.tracking_number.textChanged.connect(self._on_text_changed)
        
        # Populate ICD-10 code fields
        icd_list = self.order.icd_codes or []
        # Also check individual fields if list is empty
        if not icd_list:
            icd_list = [
                getattr(self.order, 'icd_code_1', None) or '',
                getattr(self.order, 'icd_code_2', None) or '',
                getattr(self.order, 'icd_code_3', None) or '',
                getattr(self.order, 'icd_code_4', None) or '',
                getattr(self.order, 'icd_code_5', None) or '',
            ]
        for i, field in enumerate(self.icd_code_fields):
            field.setText(icd_list[i].strip() if i < len(icd_list) else '')
        
        # Doctor directions (editable) - clear placeholder text if empty
        directions_text = self.order.doctor_directions or ""
        self.doctor_directions.setText(directions_text)
        
        # Items table
        self._populate_items_table()
        
        # Notes (editable) - clear placeholder text if empty
        notes_text = self.order.notes or ""
        self.notes_text.setText(notes_text)
        
        # Special instructions (editable)
        special_instructions_text = getattr(self.order, 'special_instructions', '') or ""
        self.special_instructions_text.setText(special_instructions_text)
        
        # Billing alert (popup when EPACES helper opens)
        epaces_alert_text = getattr(self.order, 'epaces_alert', '') or ""
        self.epaces_alert_text.setText(epaces_alert_text)
        
        # Connect text change signals to enable Save button
        self.doctor_directions.textChanged.connect(self._on_text_changed)
        self.notes_text.textChanged.connect(self._on_text_changed)
        self.special_instructions_text.textChanged.connect(self._on_text_changed)
        self.epaces_alert_text.textChanged.connect(self._on_text_changed)
        
        # Update status combo with allowed transitions
        self._populate_status_combo()
        
        # Refresh documents list if order has attached documents
        self._refresh_documents_list()
        
        # Auto-open EPACES helper for Medicaid orders (once per editor instance)
        ins_name = (self.order.primary_insurance or "").upper()
        if "MEDICAID" in ins_name and not getattr(self, "_epaces_auto_opened", False):
            self._epaces_auto_opened = True
            # Non-blocking: open EPACES dialog without requiring it to close first
            QTimer.singleShot(100, self._open_epaces_helper_nonmodal)
    
    def _on_text_changed(self):
        """Enable save button when notes or directions are changed."""
        self.save_button.setEnabled(True)

    def _on_item_cell_changed(self, _item):
        if self._suppress_item_change:
            return
        self._refresh_item_amounts_from_table()
        self._items_dirty = True
        self.save_items_btn.setEnabled(True)
        self.save_button.setEnabled(True)

    def _table_decimal(self, row: int, column: int) -> Decimal:
        item = self.items_table.item(row, column)
        text = item.text().strip().replace("$", "").replace(",", "") if item else ""
        try:
            return Decimal(text) if text else Decimal("0")
        except Exception:
            return Decimal("0")

    def _table_int(self, row: int, column: int, default: int = 0) -> int:
        item = self.items_table.item(row, column)
        text = item.text().strip() if item else ""
        try:
            return int(text) if text else default
        except Exception:
            return default

    def _refresh_item_amounts_from_table(self) -> None:
        """Recalculate read-only line amounts and the order total from visible rows."""
        total = Decimal("0.00")
        self._suppress_item_change = True
        try:
            for row in range(self.items_table.rowCount()):
                qty_val = self._table_int(row, 3, 0)
                unit_price = self._table_decimal(row, 7)
                line_total = unit_price * Decimal(str(qty_val)) if qty_val else Decimal("0.00")
                amount_item = self.items_table.item(row, 8)
                if amount_item is None:
                    amount_item = QTableWidgetItem()
                    amount_item.setFlags(amount_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.items_table.setItem(row, 8, amount_item)
                amount_item.setText(f"{line_total:.2f}")
                total += line_total
            self.order_total_label.setText(f"${total:.2f}")
        finally:
            self._suppress_item_change = False

    def _on_prescriber_changed(self, row: int, combo_index: int):
        """Handle prescriber dropdown change for an item row."""
        if self._suppress_item_change:
            return
        
        # Get item ID from metadata
        if row >= len(self._item_row_meta):
            return
        
        item_meta = self._item_row_meta[row]
        item_id = item_meta.get("id")
        
        if not item_id:
            # New item - just mark dirty, will save on "Save Item Changes"
            self._items_dirty = True
            self.save_items_btn.setEnabled(True)
            return
        
        # Get selected prescriber data from combo
        combo = self.items_table.cellWidget(row, 9)
        if not combo:
            return
        
        presc_data = combo.currentData()
        if not presc_data:
            return
        
        prescriber_name = presc_data.get("name", "")
        prescriber_npi = presc_data.get("npi", "")
        
        # Update database immediately
        try:
            from dmelogic.db.orders import update_order_item
            update_order_item(
                item_id,
                {
                    "prescriber_name": prescriber_name,
                    "prescriber_npi": prescriber_npi,
                },
                folder_path=self.folder_path
            )
            
            # Update local order item object
            for item in self.order.items:
                if item.id == item_id:
                    item.prescriber_name = prescriber_name
                    item.prescriber_npi = prescriber_npi
                    break
            
            print(f"✅ Assigned item {item_id} to {prescriber_name}")
            
            # Notify listeners (e.g., EPACES helper) that order was updated
            self.order_updated.emit()
        except Exception as e:
            print(f"❌ Failed to assign prescriber: {e}")
            QMessageBox.warning(self, "Error", f"Failed to save prescriber: {e}")

    def _insert_item_row(
        self,
        hcpcs: str = "",
        desc: str = "",
        qty: str = "1",
        refills: str = "0",
        days: str = "30",
        mods: str = "",
        cost: str = "0.00",
        item_number: str = "",
    ):
        """Insert an item row with provided defaults and mark as new."""
        row = self.items_table.rowCount()
        self._suppress_item_change = True
        self.items_table.insertRow(row)
        try:
            amount = Decimal(str(cost or "0")) * Decimal(str(qty or "0"))
        except Exception:
            amount = Decimal("0.00")
        values = [hcpcs, item_number, desc, qty, refills, days, mods, cost, f"{amount:.2f}"]
        for col, val in enumerate(values):
            table_item = QTableWidgetItem(str(val))
            if col == 8:
                table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, col, table_item)
        
        # Add prescriber combo for new item
        prescriber_combo = QComboBox()
        presc1_name = getattr(self.order, 'prescriber_name', '') or 'Prescriber 1' if self.order else 'Prescriber 1'
        presc1_npi = getattr(self.order, 'prescriber_npi', '') or '' if self.order else ''
        presc2_name = getattr(self.order, 'prescriber_name_2', '') or '' if self.order else ''
        presc2_npi = getattr(self.order, 'prescriber_npi_2', '') or '' if self.order else ''
        
        presc1_short = presc1_name.split(",")[0][:12] if "," in presc1_name else presc1_name[:12]
        prescriber_combo.addItem(f"1: {presc1_short}", {"name": presc1_name, "npi": presc1_npi})
        
        if presc2_name:
            presc2_short = presc2_name.split(",")[0][:12] if "," in presc2_name else presc2_name[:12]
            prescriber_combo.addItem(f"2: {presc2_short}", {"name": presc2_name, "npi": presc2_npi})
        
        prescriber_combo.currentIndexChanged.connect(
            lambda idx, r=row: self._on_prescriber_changed(r, idx)
        )
        self.items_table.setCellWidget(row, 9, prescriber_combo)
        
        self._item_row_meta.append({"id": None, "is_new": True})
        self._suppress_item_change = False
        self._on_item_cell_changed(None)

    def _add_item_row(self):
        """Append a new editable item row (blank)."""
        self._insert_item_row()

    def _open_inventory_search(self):
        """Open inventory search and add selected item to the table."""
        try:
            dlg = InventorySearchDialog(self)
            # Seed search with current cell text (hcpcs or desc) if present
            current_row = self.items_table.currentRow()
            if current_row >= 0:
                seed = ""
                hcpcs_item = self.items_table.item(current_row, 0)
                desc_item = self.items_table.item(current_row, 2)  # Column 2 is description now
                if hcpcs_item and hcpcs_item.text().strip():
                    seed = hcpcs_item.text().strip()
                elif desc_item and desc_item.text().strip():
                    seed = desc_item.text().strip()
                if seed:
                    dlg.set_initial_query(seed)

            if dlg.exec() == QDialog.DialogCode.Accepted:
                data = dlg.get_selected_item() or {}
                hcpcs_code = str(
                    data.get("hcpcs_code")
                    or data.get("HCPCS")
                    or data.get("item_code")
                    or ""
                )
                desc = str(data.get("description") or data.get("DESCRIPTION") or "")
                item_number = str(data.get("item_number") or data.get("ITEM_NUMBER") or "")
                # Use retail_price (bill amount) for the cost field, fall back to cost if not set
                bill_val = (
                    data.get("retail_price")
                    or data.get("RETAIL_PRICE")
                    or data.get("bill_amount")
                    or data.get("BILL_AMOUNT")
                    or data.get("cost")
                    or data.get("COST")
                    or "0"
                )
                try:
                    bill_val = f"{Decimal(str(bill_val)):.2f}"
                except Exception:
                    bill_val = "0.00"
                self._insert_item_row(hcpcs_code, desc, "1", "0", "30", "", bill_val, item_number)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Inventory", f"Could not open inventory search: {exc}")

    def _remove_selected_items(self):
        """Remove selected rows and mark existing items for deletion."""
        selected = sorted({idx.row() for idx in self.items_table.selectionModel().selectedRows()}, reverse=True)
        if not selected:
            QMessageBox.information(self, "Remove Items", "Select at least one item row to remove.")
            return

        for row in selected:
            if 0 <= row < len(self._item_row_meta):
                meta = self._item_row_meta[row]
                item_id = meta.get("id")
                if item_id:
                    self._deleted_item_ids.add(int(item_id))
                self._item_row_meta.pop(row)
                self.items_table.removeRow(row)

        self._on_item_cell_changed(None)

    def _save_item_changes(self):
        """Persist item edits/additions/removals."""
        if not self.order:
            return

        debug_log(f"[SAVE_ITEMS] Starting save for order {self.order.id}, folder_path={self.folder_path}")
        debug_log(f"[SAVE_ITEMS] Row count: {self.items_table.rowCount()}, meta count: {len(self._item_row_meta)}")

        # Apply deletions first
        for item_id in list(self._deleted_item_ids):
            try:
                delete_order_item(item_id, folder_path=self.folder_path)
            except Exception as exc:  # noqa: BLE001
                debug_log(f"Failed deleting item {item_id}: {exc}")
        self._deleted_item_ids.clear()

        # Save additions/updates
        for row in range(self.items_table.rowCount()):
            meta = self._item_row_meta[row] if row < len(self._item_row_meta) else {"id": None}

            def _text(c: int) -> str:
                item = self.items_table.item(row, c)
                return item.text().strip() if item else ""

            hcpcs = _text(0)
            item_number = _text(1)
            desc = _text(2)
            qty = _text(3)
            refills = _text(4)
            days = _text(5)
            mods = _text(6)
            cost = _text(7)
            
            debug_log(f"[SAVE_ITEMS] Row {row}: meta={meta}, qty_text='{qty}', hcpcs='{hcpcs}'")

            # Parse modifiers (space-separated)
            mod_parts = [m for m in mods.replace(",", " ").split() if m]
            mod1 = mod_parts[0] if len(mod_parts) > 0 else None
            mod2 = mod_parts[1] if len(mod_parts) > 1 else None
            mod3 = mod_parts[2] if len(mod_parts) > 2 else None
            mod4 = mod_parts[3] if len(mod_parts) > 3 else None

            def _to_int(val: str, default: int = 0) -> int:
                try:
                    return int(val)
                except Exception:
                    return default

            def _to_decimal(val: str) -> Decimal:
                try:
                    return Decimal(val)
                except Exception:
                    return Decimal("0")

            qty_val = _to_int(qty, 0)
            refills_val = _to_int(refills, 0)
            days_val = _to_int(days, 0)
            cost_val = _to_decimal(cost)
            total_val = cost_val * Decimal(str(qty_val)) if qty_val else Decimal("0")

            # Get prescriber from combo box
            prescriber_combo = self.items_table.cellWidget(row, 9)
            prescriber_name = ""
            prescriber_npi = ""
            if prescriber_combo:
                presc_data = prescriber_combo.currentData()
                if presc_data:
                    prescriber_name = presc_data.get("name", "")
                    prescriber_npi = presc_data.get("npi", "")

            if meta.get("id") is None:
                # Skip empty new rows
                if not hcpcs and not desc:
                    continue
                try:
                    add_order_item(
                        self.order.id,
                        {
                            "hcpcs_code": hcpcs,
                            "description": desc,
                            "item_number": item_number,
                            "qty": qty_val,
                            "refills": refills_val,
                            "day_supply": days_val,
                            "cost_ea": str(cost_val),
                            "total": str(total_val),
                            "modifier1": mod1,
                            "modifier2": mod2,
                            "modifier3": mod3,
                            "modifier4": mod4,
                            "prescriber_name": prescriber_name,
                            "prescriber_npi": prescriber_npi,
                        },
                        folder_path=self.folder_path,
                    )
                except Exception as exc:  # noqa: BLE001
                    debug_log(f"Failed adding item: {exc}")
            else:
                try:
                    # Direct file trace to ensure logging works
                    import os
                    trace_path = os.path.join(self.folder_path or r"C:\ProgramData\DMELogic\Data", "save_trace.log")
                    with open(trace_path, "a") as tf:
                        tf.write(f"[{__import__('datetime').datetime.now()}] Updating item {meta['id']}, qty={qty_val}, folder={self.folder_path}\n")
                    
                    debug_log(f"[SAVE_ITEMS] Calling update_order_item(item_id={meta['id']}, qty={qty_val}, folder_path={self.folder_path})")
                    update_order_item(
                        meta["id"],
                        {
                            "qty": qty_val,
                            "refills": refills_val,
                            "day_supply": days_val,
                            "item_number": item_number,
                            "cost_ea": str(cost_val),
                            "total": str(total_val),
                            "modifier1": mod1,
                            "modifier2": mod2,
                            "modifier3": mod3,
                            "modifier4": mod4,
                        },
                        folder_path=self.folder_path,
                    )
                    
                    # Verify the update by reading back from DB
                    import sqlite3
                    db_path = os.path.join(self.folder_path or r"C:\ProgramData\DMELogic\Data", "orders.db")
                    verify_conn = sqlite3.connect(db_path)
                    verify_cur = verify_conn.cursor()
                    verify_cur.execute("SELECT qty FROM order_items WHERE id = ?", (meta['id'],))
                    verify_row = verify_cur.fetchone()
                    verify_qty = verify_row[0] if verify_row else "NOT FOUND"
                    verify_conn.close()
                    
                    with open(trace_path, "a") as tf:
                        tf.write(f"[{__import__('datetime').datetime.now()}] VERIFIED: item {meta['id']} qty in DB = {verify_qty}\n")
                    
                    debug_log(f"[SAVE_ITEMS] update_order_item completed for item {meta['id']}, verified qty={verify_qty}")
                except Exception as exc:  # noqa: BLE001
                    debug_log(f"Failed updating item {meta['id']}: {exc}")

        # Update order-level refill due date based on new day supply values
        try:
            recompute_refill_due_date(self.order_id, folder_path=self.folder_path)
        except Exception as exc:
            debug_log(f"Failed to recompute refill due for order {self.order_id}: {exc}")

        # Reload order to refresh totals and IDs for new rows
        self.order = fetch_order_with_items(self.order_id, folder_path=self.folder_path)
        self._items_dirty = False
        self.save_items_btn.setEnabled(False)
        self.save_button.setEnabled(False)
        self._populate_items_table()
        QMessageBox.information(self, "Items Saved", "Item changes have been saved.")
    
    def _update_status_badge(self):
        """Update status badge text and semantic properties for QSS."""
        if not self.order:
            return

        status_text = self.order.order_status.value
        self.status_badge.setText(status_text)

        # Use semantic properties so global QSS can style consistently
        self.status_badge.setProperty("badge", True)
        self.status_badge.setProperty("status", self.order.order_status.name.lower())

        # Force QSS refresh so changed properties take effect immediately
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
    
    def _populate_items_table(self):
        """Populate items table from order.items."""
        if not self.order:
            return
        
        self.items_table.setRowCount(0)
        self._item_row_meta = []
        self._suppress_item_change = True
        
        for item in self.order.items:
            row = self.items_table.rowCount()
            self.items_table.insertRow(row)
            
            # HCPCS - show full code if multi-code (contains +), otherwise base code only
            full_hcpcs = item.hcpcs_code or ""
            if "+" in full_hcpcs:
                # Multi-HCPCS code (e.g., E0244+E0243) - show full code
                display_hcpcs = full_hcpcs.split("-")[0] if "-" in full_hcpcs else full_hcpcs
            else:
                # Single HCPCS - show base code only (first 5 chars)
                display_hcpcs = full_hcpcs[:5] if len(full_hcpcs) >= 5 else full_hcpcs
            hcpcs_item = QTableWidgetItem(display_hcpcs)
            hcpcs_item.setFlags(hcpcs_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 0, hcpcs_item)
            
            # Item # (from inventory, read-only)
            item_number = getattr(item, "item_number", "") or ""
            if not item_number and "-" in full_hcpcs:
                # Extract from HCPCS and look up in inventory
                try:
                    from dmelogic.db.inventory import fetch_latest_item_by_hcpcs
                    inv_data = fetch_latest_item_by_hcpcs(full_hcpcs, folder_path=self.folder_path)
                    if inv_data and inv_data.get("item_number"):
                        item_number = inv_data["item_number"]
                    else:
                        item_number = full_hcpcs.split("-", 1)[1].strip()
                except Exception:
                    item_number = full_hcpcs.split("-", 1)[1].strip() if "-" in full_hcpcs else ""
            item_num_widget = QTableWidgetItem(item_number)
            item_num_widget.setFlags(item_num_widget.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 1, item_num_widget)
            
            # Description (read-only for existing rows)
            desc_item = QTableWidgetItem(item.description)
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 2, desc_item)
            
            # Quantity
            self.items_table.setItem(row, 3, QTableWidgetItem(str(item.quantity)))
            
            # Refills
            self.items_table.setItem(row, 4, QTableWidgetItem(str(item.refills)))
            
            # Days supply
            self.items_table.setItem(row, 5, QTableWidgetItem(str(item.days_supply)))
            
            # Modifiers (free-text; will split on save)
            modifiers = format_modifiers_for_display(item)
            self.items_table.setItem(row, 6, QTableWidgetItem(modifiers))
            
            pricing = line_pricing_for_order_item(
                item,
                folder_path=self.folder_path,
                hcpcs_candidates=(full_hcpcs, display_hcpcs),
                item_number=item_number,
            )

            # Billing unit price and calculated line amount
            self.items_table.setItem(row, 7, QTableWidgetItem(f"{pricing.unit_price:.2f}"))
            amount_item = QTableWidgetItem(f"{pricing.total:.2f}")
            amount_item.setFlags(amount_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.items_table.setItem(row, 8, amount_item)

            # Prescriber dropdown
            prescriber_combo = QComboBox()
            presc1_name = getattr(self.order, 'prescriber_name', '') or 'Prescriber 1'
            presc1_npi = getattr(self.order, 'prescriber_npi', '') or ''
            presc2_name = getattr(self.order, 'prescriber_name_2', '') or ''
            presc2_npi = getattr(self.order, 'prescriber_npi_2', '') or ''
            
            # Add prescriber 1 option
            presc1_short = presc1_name.split(",")[0][:12] if "," in presc1_name else presc1_name[:12]
            prescriber_combo.addItem(f"1: {presc1_short}", {"name": presc1_name, "npi": presc1_npi})
            
            # Add prescriber 2 option if exists
            if presc2_name:
                presc2_short = presc2_name.split(",")[0][:12] if "," in presc2_name else presc2_name[:12]
                prescriber_combo.addItem(f"2: {presc2_short}", {"name": presc2_name, "npi": presc2_npi})
            
            # Select current prescriber based on item's prescriber_npi
            item_presc_npi = getattr(item, 'prescriber_npi', '') or ''
            if item_presc_npi == presc2_npi and presc2_npi:
                prescriber_combo.setCurrentIndex(1)
            else:
                prescriber_combo.setCurrentIndex(0)
            
            # Connect change signal
            prescriber_combo.currentIndexChanged.connect(
                lambda idx, r=row: self._on_prescriber_changed(r, idx)
            )
            self.items_table.setCellWidget(row, 9, prescriber_combo)

            # Store full metadata for sync with EPACES helper
            self._item_row_meta.append({
                "id": item.id,
                "is_new": False,
                "item_number": item_number,  # Use the item_number we just looked up/extracted
                "pa_number": getattr(item, "pa_number", "") or "",
                "directions": getattr(item, "directions", "") or "",
                "is_rental": getattr(item, "is_rental", False),
                "rental_month": getattr(item, "rental_month", 0),
            })
        
        self._refresh_item_amounts_from_table()
        self._suppress_item_change = False
        self.save_items_btn.setEnabled(False)
    
    def _populate_status_combo(self):
        """Populate status combo with allowed next statuses."""
        if not self.order:
            return
        
        self.status_combo.clear()
        
        # Add current status as first option (disabled)
        current_status = self.order.order_status
        self.status_combo.addItem(f"Current: {current_status.value}", current_status)
        
        # Add allowed transitions
        allowed = get_allowed_next_statuses(current_status)
        for status in allowed:
            self.status_combo.addItem(f"→ {status.value}", status)
        
        # Disable first item (current status)
        model = self.status_combo.model()
        model.item(0).setEnabled(False)
    
    def _on_status_change_requested(self, text: str):
        """Enable/disable status change button based on selection."""
        self.change_status_btn.setEnabled(
            not text.startswith("Current:")
        )
    
    def _change_status(self):
        """Change order status with workflow validation."""
        if not self.order:
            return
        
        new_status = self.status_combo.currentData()
        if not new_status or new_status == self.order.order_status:
            return
        
        # Validate transition
        if not can_transition(self.order.order_status, new_status):
            QMessageBox.warning(
                self,
                "Invalid Status Change",
                f"Cannot transition from {self.order.order_status.value} "
                f"to {new_status.value}"
            )
            return
        
        # If changing to On Hold, prompt for hold settings BEFORE confirming
        hold_options = None
        if new_status == OrderStatus.ON_HOLD:
            hold_options = self._prompt_hold_options()
            if hold_options is None:
                # User cancelled
                return
        
        # Confirm change
        reply = QMessageBox.question(
            self,
            "Confirm Status Change",
            f"Change order status from {self.order.order_status.value} "
            f"to {new_status.value}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        try:
            # Update status (this would call repository update function)
            from dmelogic.db.order_workflow import update_order_status_with_hold
            success, error_msg = update_order_status_with_hold(
                order_id=self.order_id,
                current_status=self.order.order_status.value,
                new_status=new_status.value,
                folder_path=self.folder_path,
                hold_until_date=(hold_options[0] if hold_options else None),
                hold_resume_status=(hold_options[1] if hold_options else None),
                hold_note=(hold_options[2] if hold_options else ""),
            )
            if not success:
                QMessageBox.warning(
                    self,
                    "Invalid Status Change",
                    error_msg or "Status update failed due to validation rules.",
                )
                return
            
            # Reload order
            self._load_order()
            
            QMessageBox.information(
                self,
                "Status Updated",
                f"Order status changed to {new_status.value}"
            )
            
            self.order_updated.emit()
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to update status: {str(e)}"
            )
            debug_log(f"Error updating order status: {e}")
    
    def _prompt_hold_options(self):
        """Prompt user for hold release date, resume status, and note."""
        from PyQt6.QtWidgets import QDateEdit, QDialogButtonBox
        from PyQt6.QtCore import QDate
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Schedule Hold Release")
        dialog.setMinimumWidth(350)
        form = QFormLayout(dialog)
        
        # Release date
        hold_date = QDateEdit(QDate.currentDate().addDays(7))
        hold_date.setCalendarPopup(True)
        form.addRow("Release on:", hold_date)
        
        # Resume status
        resume_combo = QComboBox()
        allowed_after_hold = sorted(
            get_allowed_next_statuses(OrderStatus.ON_HOLD),
            key=lambda s: list(OrderStatus).index(s),
        )
        for status in allowed_after_hold:
            resume_combo.addItem(status.value, status.value)
        # Default to current status if allowed
        if self.order and self.order.order_status in allowed_after_hold:
            resume_combo.setCurrentText(self.order.order_status.value)
        form.addRow("Resume to:", resume_combo)
        
        # Note
        note_edit = QTextEdit()
        note_edit.setPlaceholderText("Reason / reminder for this hold")
        note_edit.setMaximumHeight(80)
        form.addRow(QLabel("Hold note:"))
        form.addRow(note_edit)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return (
                hold_date.date().toString("yyyy-MM-dd"),
                resume_combo.currentData(),
                note_edit.toPlainText().strip(),
            )
        return None
    
    def _send_to_portal(self):
        """Export order to State Portal."""
        if not self.order:
            return
        
        try:
            from dmelogic.db.order_workflow import build_state_portal_json_for_order
            
            json_data = build_state_portal_json_for_order(
                self.order_id,
                folder_path=self.folder_path
            )
            
            # For now, show success message
            # Later: actually POST to API
            QMessageBox.information(
                self,
                "Portal Export",
                f"Order {self._format_order_number()} exported to State Portal\n\n"
                f"JSON data generated successfully.\n"
                f"(API integration pending)"
            )
            
            debug_log(f"Order {self._format_order_number()} exported to portal: {len(json_data)} fields")
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Export Error",
                f"Failed to export to portal: {str(e)}"
            )
            debug_log(f"Error exporting order {self.order_id}: {e}")
    
    def _generate_1500(self):
        """Generate HCFA-1500 claim form."""
        if not self.order:
            return
        
        QMessageBox.information(
            self,
            "HCFA-1500 Generation",
            "HCFA-1500 form generation will be implemented here.\n\n"
            "Will use Hcfa1500ClaimView.from_order() pattern\n"
            "similar to State Portal export."
        )
        
        # TODO: Implement HCFA-1500 generation
        # from dmelogic.forms import Hcfa1500ClaimView
        # claim = Hcfa1500ClaimView.from_order(self.order)
        # pdf_bytes = claim.render_to_pdf()
    
    def _print_delivery_ticket(self):
        """Persist pending edits, then print via the shared delivery-ticket generator."""
        if not self.order:
            return
        try:
            # Save any unsaved special_instructions / notes / doctor_directions
            # so the freshly-reloaded order in the generator includes them.
            from dmelogic.db.orders import update_order_fields
            fields_to_save = {}

            new_special = self.special_instructions_text.toPlainText().strip()
            if new_special != (self.order.special_instructions or "").strip():
                fields_to_save["special_instructions"] = new_special if new_special else None

            new_notes = self.notes_text.toPlainText().strip()
            if new_notes != (self.order.notes or "").strip() and new_notes != "No notes":
                fields_to_save["notes"] = new_notes if new_notes else None

            new_directions = self.doctor_directions.toPlainText().strip()
            if new_directions != (self.order.doctor_directions or "").strip() and new_directions != "No directions provided":
                fields_to_save["doctor_directions"] = new_directions if new_directions else None

            if fields_to_save:
                update_order_fields(self.order.id, fields_to_save, folder_path=self.folder_path)

            from dmelogic.printing.delivery_ticket import build_delivery_ticket_pdf
            file_path = build_delivery_ticket_pdf(self.order_id, folder_path=self.folder_path)

            # Keep the in-memory order current for the rest of the editor.
            self.order = fetch_order_with_items(self.order_id, folder_path=self.folder_path)

            try:
                import os
                os.startfile(file_path)
            except Exception:
                pass

            QMessageBox.information(
                self,
                "Delivery Ticket",
                f"Delivery ticket saved:\n\n{file_path}",
            )
        except ImportError:
            QMessageBox.critical(
                self,
                "Print Delivery Ticket",
                "ReportLab is not available. Please install it:\n\npip install reportlab",
            )
        except Exception as e:
            import traceback
            QMessageBox.critical(
                self,
                "Print Error",
                f"Failed to print delivery ticket:\n\n{str(e)}\n\n{traceback.format_exc()}",
            )


    def _process_refill(self):
        """Process refill for current order - creates new refill order and opens ePACES dialog."""
        if not self.order:
            return

        has_refills_remaining = any(
            (item.refills or 0) > 0 for item in self.order.items
        )
        if not has_refills_remaining:
            patient_name = self.order.patient_full_name or "Unknown"
            handle_last_refill(
                parent_widget=self,
                db_path=self.orders_db_path,
                order_id=str(self.order.id),
                patient_name=patient_name,
                refills_remaining=0,
                on_create_order_callback=self._start_reorder_from_reserved_rx,
                on_fax_md_callback=None,
            )
            return
        
        # Confirm action with user
        reply = QMessageBox.question(
            self,
            "Process Refill",
            f"Create a refill order for Order {self._format_order_number(self.order)}?\n\n"
            f"This will:\n"
            f"• Create a new refill order with decremented refill counts\n"
            f"• Lock the current order to prevent duplicate refills\n"
            f"• Auto-increment rental K modifiers for rental items\n"
            f"• Open ePACES dialog for the new refill order\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        try:
            # Process the refill (creates new order, locks source order).
            # run_refill_with_override enforces Max-Units limits and incompatible
            # item combinations, prompting for an override when they are hit.
            from dmelogic.order_rules import run_refill_with_override
            refill_order = run_refill_with_override(
                self, self.order.id, folder_path=self.folder_path or ""
            )
            if refill_order is None:
                return  # user declined the override

            # Show success message
            refill_display = f"{refill_order.id}"
            if refill_order.parent_order_id and refill_order.refill_number > 0:
                refill_display = f"{refill_order.parent_order_id}-{refill_order.refill_number}"
            
            QMessageBox.information(
                self,
                "Refill Created",
                f"Refill order created successfully!\n\n"
                f"Refill Order: {refill_display}\n"
                f"Items: {len(refill_order.items)}\n\n"
                f"Opening ePACES dialog..."
            )
            
            # Auto-open ePACES dialog with the new refill order
            try:
                epaces_dialog = EpacesHelperDialog(
                    order=refill_order,
                    parent=self,
                    folder_path=self.folder_path
                )
                epaces_dialog.exec()
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "ePACES Dialog Error",
                    f"Refill order was created successfully, but ePACES dialog failed to open:\n\n{str(e)}"
                )
            
            # Refresh the current order to show locked status
            self._load_order()

            # Check reserved RX / last refill warning for source order
            try:
                min_refills = 999
                for item in refill_order.items:
                    try:
                        r = int(item.refills) if item.refills is not None else 0
                    except (ValueError, TypeError):
                        r = 0
                    min_refills = min(min_refills, r)
                if min_refills == 999:
                    min_refills = 0
                patient_name = self.order.patient_full_name or "Unknown"
                handle_last_refill(
                    parent_widget=self,
                    db_path=self.orders_db_path,
                    order_id=str(self.order.id),
                    patient_name=patient_name,
                    refills_remaining=min_refills,
                    on_create_order_callback=self._start_reorder_from_reserved_rx,
                    on_fax_md_callback=None
                )
            except Exception as rx_err:
                print(f"[ReservedRX] handle_last_refill error in editor: {rx_err}")
            
        except RefillError as e:
            QMessageBox.critical(
                self,
                "Refill Error",
                f"Cannot process refill:\n\n{str(e)}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to process refill:\n\n{str(e)}"
            )
            debug_log(f"Refill processing error: {e}")

    # ------------------------------------------------------------------
    #  Reorder (new RX)
    # ------------------------------------------------------------------

    def _start_reorder_from_reserved_rx(self, rx_data: Optional[dict] = None):
        """Open the reorder dialog using reserved RX details when available."""
        if not self.order:
            return

        dlg = ReorderConfirmationDialog(
            source_order=self.order,
            folder_path=self.folder_path,
            rx_data=rx_data or {},
            parent=self,
        )

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_id = dlg.get_new_order_id()
            if new_id:
                src_display = self._format_order_number(self.order)

                QMessageBox.information(
                    self,
                    "New Order Created",
                    f"New order (ID {new_id}) created successfully\n"
                    f"from {src_display}.\n\n"
                    f"Opening the new order now...",
                )

                self.order_updated.emit()

                try:
                    new_editor = OrderEditorDialog(
                        order_id=new_id,
                        folder_path=self.folder_path,
                        parent=self.parent(),
                    )
                    new_editor.order_updated.connect(self.order_updated.emit)
                    new_editor.exec()
                except Exception as e:
                    debug_log(f"Error opening new order editor: {e}")

                self._load_order()

    def _reorder(self):
        """Create a brand-new order from this one when refills are exhausted
        but the patient has an RX on file (or the user explicitly chooses to
        reorder with a new prescription)."""
        if not self.order:
            return

        # Check whether there's an RX on file
        rx_data = get_reserved_rx_data(self.orders_db_path, str(self.order.id))
        rx_on_file = bool(rx_data.get("rx_on_file", 0)) if rx_data else False

        # Check whether refills remain on *any* item
        has_refills = any(
            (item.refills or 0) > 0 for item in self.order.items
        )

        if has_refills and not rx_on_file:
            reply = QMessageBox.question(
                self,
                "Items Still Have Refills",
                "This order still has items with refills remaining.\n\n"
                "Are you sure you want to create a brand-new order\n"
                "instead of processing a refill?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if not rx_on_file:
            reply = QMessageBox.question(
                self,
                "No RX on File",
                "There is no reserved prescription on file for this order.\n\n"
                "Do you want to proceed with creating a new order anyway?\n"
                "(A new RX date will still be required.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._start_reorder_from_reserved_rx(rx_data)

    def _edit_items(self):
        """Open item editor dialog."""
        if not self.order or not self.order.items:
            QMessageBox.information(self, "Edit Items", "No items to edit.")
            return
        
        # Get selected row or use first item
        selected_rows = self.items_table.selectionModel().selectedRows()
        if selected_rows:
            row_index = selected_rows[0].row()
        else:
            row_index = 0
        
        if row_index >= len(self.order.items):
            return
        
        item = self.order.items[row_index]
        
        # Create item editor dialog
        dialog = ItemEditorDialog(item, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Get updated values
            updates = dialog.get_updates()
            
            try:
                # Update item in database
                from dmelogic.db.orders import update_order_item
                update_order_item(item.id, updates, folder_path=self.folder_path)
                
                # Reload order data and refresh UI
                self.order = fetch_order_with_items(self.order_id, folder_path=self.folder_path)
                self._populate_items_table()
                self.save_button.setEnabled(True)
                
                QMessageBox.information(self, "Success", "Item updated successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to update item:\n\n{str(e)}")
    
    def _view_documents(self):
        """View order-related documents."""
        if not self.order:
            return
        
        # Check selection before refresh (refresh clears selection)
        row = self.docs_list.currentRow()
        if row >= 0:
            self._open_selected_document()
            return
        
        # No selection — refresh list and check if there are any docs
        self._refresh_documents_list()
        
        if self.docs_list.rowCount() == 0:
            QMessageBox.information(
                self,
                "No Documents",
                "No documents attached to this order.\n\n"
                "Click 'Attach' to add documents."
            )
        elif self.docs_list.rowCount() == 1:
            # Only one document — open it directly
            self.docs_list.selectRow(0)
            self._open_selected_document()
        else:
            QMessageBox.information(
                self,
                "Select a Document",
                "Select a document from the list, then click View to open it."
            )
    
    def _attach_document(self):
        """Attach a document to this order, all related orders (parent + refills), and patient profile."""
        if not self.order:
            return
        
        # Ask document type
        from PyQt6.QtWidgets import QFileDialog, QInputDialog
        
        doc_types = ["RX / Prescription", "Delivery Confirmation"]
        doc_type, ok = QInputDialog.getItem(
            self, "Document Type",
            "What type of document are you attaching?",
            doc_types, 0, False
        )
        if not ok:
            return
        
        is_delivery = (doc_type == "Delivery Confirmation")
        db_col = 'attached_signed_ticket_files' if is_delivery else 'attached_rx_files'
        type_label = "Delivery Confirmation" if is_delivery else "RX"
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Attach {type_label}",
            "",
            "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.doc *.docx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            import os
            from pathlib import Path
            
            src = Path(file_path)
            if not src.exists():
                QMessageBox.warning(self, "File Not Found", f"The selected file does not exist:\n{file_path}")
                return
            
            # Store filename only (resolved at runtime via ocr_folder setting)
            filename = src.name
            
            # Get all related order IDs (parent + all refills)
            related_order_ids = self._get_related_order_ids()
            
            # Update the appropriate column for ALL related orders
            import sqlite3
            conn = sqlite3.connect(self.orders_db_path)
            cur = conn.cursor()
            
            attached_count = 0
            for order_id in related_order_ids:
                cur.execute(f"SELECT {db_col} FROM orders WHERE id = ?", (order_id,))
                row = cur.fetchone()
                current_files = row[0] if row and row[0] else ""
                
                # Check if file already attached to this order (compare by filename)
                existing = [os.path.basename(f.strip()) for f in current_files.replace('\n', ';').split(';') if f.strip()]
                if filename not in existing:
                    # Append filename
                    if current_files:
                        new_files = current_files + ";" + filename
                    else:
                        new_files = filename
                    
                    cur.execute(f"UPDATE orders SET {db_col} = ? WHERE id = ?", (new_files, order_id))
                    attached_count += 1
            
            conn.commit()
            conn.close()
            
            # Auto-attach to patient profile as well
            self._auto_attach_to_patient(str(src), filename)
            
            self._refresh_documents_list()
            
            # Build message showing what was attached
            if len(related_order_ids) > 1:
                order_list = ", ".join([f"ORD-{oid:03d}" for oid in related_order_ids[:3]])
                if len(related_order_ids) > 3:
                    order_list += f" (+{len(related_order_ids) - 3} more)"
                msg = f"Document linked successfully:\n{src.name}\n\n" \
                      f"Linked to {len(related_order_ids)} orders: {order_list}\n" \
                      f"(Also linked to patient profile)"
            else:
                msg = f"Document linked successfully:\n{src.name}\n\n" \
                      f"(Also linked to patient profile)"
            
            QMessageBox.information(self, "Document Linked", msg)
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to attach document:\n{e}"
            )
    
    def _scan_document(self):
        """Scan a document from the scanner and attach to this order."""
        if not self.order:
            return
        
        # Ask document type
        from PyQt6.QtWidgets import QInputDialog
        
        doc_types = ["RX / Prescription", "Delivery Confirmation"]
        doc_type, ok = QInputDialog.getItem(
            self, "Document Type",
            "What type of document are you scanning?",
            doc_types, 0, False
        )
        if not ok:
            return
        
        is_delivery = (doc_type == "Delivery Confirmation")
        db_col = 'attached_signed_ticket_files' if is_delivery else 'attached_rx_files'
        type_label = "Delivery Confirmation" if is_delivery else "RX"
        
        # Build a suggested filename from patient name + order number
        patient_name = ""
        try:
            last = self.order.patient_last_name or ""
            first = self.order.patient_first_name or ""
            patient_name = f"{last}, {first}".strip(", ")
        except Exception:
            pass
        
        order_num = self._format_order_number(self.order)
        label = "DT" if is_delivery else "RX"
        suggested = f"{patient_name} {order_num} {label}".strip()
        
        # Scan
        from dmelogic.scan import scan_document
        if is_delivery:
            from dmelogic.paths import delivery_ticket_split_folder
            filename = scan_document(
                parent_widget=self,
                suggested_name=suggested,
                save_folder=delivery_ticket_split_folder(),
            )
        else:
            filename = scan_document(
                parent_widget=self,
                suggested_name=suggested,
            )
        
        if not filename:
            return  # User cancelled or error
        
        try:
            import os
            import sqlite3
            
            # Get all related order IDs
            related_order_ids = self._get_related_order_ids()
            
            conn = sqlite3.connect(self.orders_db_path)
            cur = conn.cursor()
            
            for order_id in related_order_ids:
                cur.execute(f"SELECT {db_col} FROM orders WHERE id = ?", (order_id,))
                row = cur.fetchone()
                current_files = row[0] if row and row[0] else ""
                existing = [os.path.basename(f.strip()) for f in current_files.replace('\n', ';').split(';') if f.strip()]
                if filename not in existing:
                    new_files = (current_files + ";" + filename) if current_files else filename
                    cur.execute(f"UPDATE orders SET {db_col} = ? WHERE id = ?", (new_files, order_id))
            
            conn.commit()
            conn.close()
            
            # Auto-attach to patient profile
            from dmelogic.paths import resolve_document_path
            resolved = resolve_document_path(filename)
            self._auto_attach_to_patient(str(resolved), filename)
            
            self._refresh_documents_list()
            
            QMessageBox.information(
                self, "Scan Complete",
                f"Scanned and saved as {type_label}:\n{filename}"
            )
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Scan saved but failed to attach:\n{e}")

    def _scan_from_phone(self):
        """Upload a document from a phone camera via QR code and attach to this order."""
        if not self.order:
            return

        from PyQt6.QtWidgets import QInputDialog
        doc_types = ["RX / Prescription", "Delivery Confirmation"]
        doc_type, ok = QInputDialog.getItem(
            self, "Document Type",
            "What type of document are you scanning?",
            doc_types, 0, False,
        )
        if not ok:
            return

        is_delivery = (doc_type == "Delivery Confirmation")
        db_col = "attached_signed_ticket_files" if is_delivery else "attached_rx_files"
        type_label = "Delivery Confirmation" if is_delivery else "RX"

        # Build suggested filename
        try:
            last = self.order.patient_last_name or ""
            first = self.order.patient_first_name or ""
            patient_name = f"{last}, {first}".strip(", ")
        except Exception:
            patient_name = ""

        label = "DT" if is_delivery else "RX"
        order_num = self._format_order_number(self.order)
        suggested = f"{patient_name} {order_num} {label}".strip()

        from dmelogic.ui.mobile_scan_dialog import MobileScanDialog
        dialog = MobileScanDialog(suggested_name=suggested, parent=self)

        received: list[str] = []
        dialog.file_received.connect(received.append)

        result = dialog.exec()

        if result != QDialog.DialogCode.Accepted or not received:
            return

        filename = received[0]

        try:
            import os
            import sqlite3

            related_order_ids = self._get_related_order_ids()

            conn = sqlite3.connect(self.orders_db_path)
            cur = conn.cursor()
            for order_id in related_order_ids:
                cur.execute(f"SELECT {db_col} FROM orders WHERE id = ?", (order_id,))
                row = cur.fetchone()
                current_files = row[0] if row and row[0] else ""
                existing = [
                    os.path.basename(f.strip())
                    for f in current_files.replace("\n", ";").split(";")
                    if f.strip()
                ]
                if filename not in existing:
                    new_files = (current_files + ";" + filename) if current_files else filename
                    cur.execute(
                        f"UPDATE orders SET {db_col} = ? WHERE id = ?",
                        (new_files, order_id),
                    )
            conn.commit()
            conn.close()

            from dmelogic.paths import resolve_document_path
            resolved = resolve_document_path(filename)
            self._auto_attach_to_patient(str(resolved), filename)

            self._refresh_documents_list()
            QMessageBox.information(
                self, "Upload Complete",
                f"Phone scan saved as {type_label}:\n{filename}",
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"File received but failed to attach:\n{e}")

    def _batch_attach_delivery_tickets(self):
        """Batch-process delivery tickets and auto-attach by OCR-detected order number."""
        from PyQt6.QtWidgets import QFileDialog
        from dmelogic.paths import delivery_tickets_folder

        start_dir = str(delivery_tickets_folder())
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Delivery Tickets for OCR Batch Attach",
            start_dir,
            "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp);;All Files (*.*)",
        )
        if not file_paths:
            return

        result = self._process_delivery_ticket_batch(file_paths)

        if result["attached"]:
            self._refresh_documents_list()

        lines = [
            f"Selected: {result['selected']}",
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

    def _process_delivery_ticket_batch(self, file_paths: list[str]) -> dict:
        """Process each selected file and attach it to matched order(s)."""
        import os
        import shutil
        import sqlite3
        from pathlib import Path
        from dmelogic.paths import delivery_tickets_folder

        result = {
            "selected": len(file_paths),
            "attached": 0,
            "no_match": 0,
            "ocr_fail": 0,
            "copy_fail": 0,
            "details": [],
        }

        delivery_dir = delivery_tickets_folder()
        delivery_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.orders_db_path)
        cur = conn.cursor()

        try:
            for raw_path in file_paths:
                src = Path(raw_path)
                if not src.exists():
                    result["copy_fail"] += 1
                    result["details"].append(f"Missing file: {src.name}")
                    continue

                copied = self._copy_file_to_delivery_folder(src, delivery_dir)
                if copied is None:
                    result["copy_fail"] += 1
                    result["details"].append(f"Copy failed: {src.name}")
                    continue

                root_id, refill_number = self._extract_order_reference(copied)
                if root_id is None:
                    result["ocr_fail"] += 1
                    result["details"].append(f"No order reference detected: {copied.name}")
                    continue

                target_order_id = self._resolve_order_id_from_reference(cur, root_id, refill_number)
                if target_order_id is None:
                    result["no_match"] += 1
                    if refill_number is None:
                        result["details"].append(f"Order not found ORD-{root_id:03d}: {copied.name}")
                    else:
                        result["details"].append(
                            f"Order not found ORD-{root_id:03d}-R{refill_number}: {copied.name}"
                        )
                    continue

                family_ids = self._get_related_order_ids_for(cur, target_order_id)
                added_any = False
                for order_id in family_ids:
                    if self._append_delivery_attachment(cur, order_id, copied.name):
                        added_any = True

                if added_any:
                    conn.commit()
                    result["attached"] += 1
                    result["details"].append(
                        f"Attached {copied.name} -> {self._format_order_token(root_id, refill_number)}"
                    )
                else:
                    result["details"].append(f"Already attached: {copied.name}")
        finally:
            conn.close()

        return result

    def _copy_file_to_delivery_folder(self, src: "Path", delivery_dir: "Path") -> "Path | None":
        """Copy file into delivery folder if needed, preserving original when already there."""
        import shutil

        try:
            if src.parent.resolve() == delivery_dir.resolve():
                return src

            target = delivery_dir / src.name
            base = src.stem
            ext = src.suffix
            counter = 1
            while target.exists():
                target = delivery_dir / f"{base}_{counter}{ext}"
                counter += 1

            shutil.copy2(str(src), str(target))
            return target
        except Exception:
            return None

    def _extract_order_reference(self, file_path: "Path") -> tuple[Optional[int], Optional[int]]:
        """Return (root_order_id, refill_number) parsed from filename/OCR text."""
        text = self._extract_text_for_order_match(file_path)
        if not text:
            return None, None

        match = re.search(r"\bORD\s*[-#]?\s*(\d{1,6})(?:\s*[-/ ]\s*R\s*(\d{1,3}))?\b", text, re.IGNORECASE)
        if not match:
            return None, None

        root_id = int(match.group(1))
        refill_number = int(match.group(2)) if match.group(2) else None
        return root_id, refill_number

    def _extract_text_for_order_match(self, file_path: "Path") -> str:
        """Extract searchable text from filename and document contents."""
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
            debug_log(f"Batch OCR extraction failed for {file_path}: {e}")

        return "\n".join(p for p in parts if p)

    def _resolve_order_id_from_reference(self, cur, root_id: int, refill_number: Optional[int]) -> Optional[int]:
        """Resolve OCR order token to an actual order id."""
        if refill_number is not None:
            cur.execute(
                "SELECT id FROM orders WHERE parent_order_id = ? AND refill_number = ? ORDER BY id DESC LIMIT 1",
                (root_id, refill_number),
            )
            row = cur.fetchone()
            if row:
                return int(row[0])

        cur.execute("SELECT id FROM orders WHERE id = ?", (root_id,))
        row = cur.fetchone()
        return int(row[0]) if row else None

    def _get_related_order_ids_for(self, cur, order_id: int) -> list[int]:
        """Return root + refill family ids for a given order id."""
        cur.execute("SELECT parent_order_id FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        root_id = int(row[0]) if row and row[0] else int(order_id)

        cur.execute(
            "SELECT id FROM orders WHERE id = ? OR parent_order_id = ? ORDER BY id",
            (root_id, root_id),
        )
        return [int(r[0]) for r in cur.fetchall()]

    def _append_delivery_attachment(self, cur, order_id: int, filename: str) -> bool:
        """Append delivery attachment filename if not already present."""
        cur.execute("SELECT attached_signed_ticket_files FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        current_files = row[0] if row and row[0] else ""
        existing = [f.strip() for f in str(current_files).replace("\n", ";").split(";") if f.strip()]

        if filename in existing:
            return False

        updated = f"{current_files};{filename}" if current_files else filename
        cur.execute("UPDATE orders SET attached_signed_ticket_files = ? WHERE id = ?", (updated, order_id))
        return True

    def _format_order_token(self, root_id: int, refill_number: Optional[int]) -> str:
        """Format order token for summary messages."""
        token = f"ORD-{root_id:03d}"
        if refill_number is not None:
            token += f"-R{refill_number}"
        return token

    def _get_related_order_ids(self) -> list:
        """Get all order IDs related to this order (parent + all refills in the family)."""
        import sqlite3
        
        try:
            conn = sqlite3.connect(self.orders_db_path)
            cur = conn.cursor()
            
            # Determine the root order ID
            root_order_id = self.order.parent_order_id or self.order_id
            
            # Get all orders in this family (root + all refills)
            cur.execute(
                "SELECT id FROM orders WHERE id = ? OR parent_order_id = ? ORDER BY id",
                (root_order_id, root_order_id)
            )
            rows = cur.fetchall()
            conn.close()
            
            return [row[0] for row in rows]
        except Exception as e:
            debug_log(f"Error getting related orders: {e}")
            return [self.order_id]  # Fallback to just this order
    
    def _auto_attach_to_patient(self, file_path: str, original_filename: str):
        """Auto-attach order document to the linked patient's profile."""
        try:
            import sqlite3
            from pathlib import Path
            
            # Get patient_id from order
            patient_id = getattr(self.order, 'patient_id', None)
            
            if not patient_id:
                # Try to find patient by name and DOB
                patient_db_path = resolve_db_path("patients.db", folder_path=self.folder_path)
                conn = sqlite3.connect(patient_db_path)
                cur = conn.cursor()
                
                # Try exact match first
                cur.execute(
                    "SELECT id FROM patients WHERE last_name = ? AND first_name = ? AND dob = ?",
                    (
                        self.order.patient_last_name or "",
                        self.order.patient_first_name or "",
                        str(self.order.patient_dob) if self.order.patient_dob else ""
                    )
                )
                row = cur.fetchone()
                if row:
                    patient_id = row[0]
                conn.close()
            
            if not patient_id:
                debug_log(f"Cannot auto-attach to patient - patient_id not found for order {self.order_id}")
                return
            
            # Insert into patient_documents
            patient_db_path = resolve_db_path("patients.db", folder_path=self.folder_path)
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
            
            # Check if already linked (avoid duplicates)
            cur.execute(
                "SELECT id FROM patient_documents WHERE patient_id = ? AND stored_path = ?",
                (patient_id, file_path)
            )
            if cur.fetchone():
                conn.close()
                debug_log(f"Document already linked to patient {patient_id}")
                return
            
            # Create description from order context
            order_num = self._format_order_number(self.order)
            description = f"From {order_num}"
            
            cur.execute(
                "INSERT INTO patient_documents (patient_id, description, original_name, stored_path) VALUES (?, ?, ?, ?)",
                (patient_id, description, original_filename, file_path)
            )
            conn.commit()
            conn.close()
            
            debug_log(f"✅ Auto-attached document to patient {patient_id}: {original_filename}")
            
        except Exception as e:
            debug_log(f"⚠️ Failed to auto-attach document to patient: {e}")
    
    def _refresh_documents_list(self):
        """Refresh the documents list for this order (RX + Delivery Confirmation)."""
        self.docs_list.setRowCount(0)
        
        if not self.order:
            return
        
        try:
            import sqlite3
            from pathlib import Path
            
            conn = sqlite3.connect(self.orders_db_path)
            cur = conn.cursor()
            cur.execute("SELECT attached_rx_files, attached_signed_ticket_files FROM orders WHERE id = ?", (self.order_id,))
            row = cur.fetchone()
            conn.close()
            
            if not row:
                return
            
            # Collect all documents with their type
            all_docs = []  # list of (filename, doc_type, db_col)
            
            rx_raw = row[0] if row[0] else ''
            for f in str(rx_raw).replace(';', '\n').splitlines():
                f = f.strip()
                if f:
                    all_docs.append((f, 'RX', 'attached_rx_files'))
            
            dc_raw = row[1] if row[1] else ''
            for f in str(dc_raw).replace(';', '\n').splitlines():
                f = f.strip()
                if f:
                    all_docs.append((f, 'Delivery', 'attached_signed_ticket_files'))
            
            if not all_docs:
                return
            
            self.docs_list.setRowCount(len(all_docs))
            
            for i, (file_path, doc_type, db_col) in enumerate(all_docs):
                p = Path(file_path)
                name = p.name
                
                name_item = QTableWidgetItem(name)
                name_item.setData(Qt.ItemDataRole.UserRole, file_path)
                # Store db_col in UserRole+1 so remove knows which column to update
                name_item.setData(Qt.ItemDataRole.UserRole + 1, db_col)
                
                # Resolve path for tooltip
                from dmelogic.paths import resolve_document_path
                resolved = resolve_document_path(file_path)
                name_item.setToolTip(str(resolved))
                
                type_item = QTableWidgetItem(doc_type)
                
                # Remove icon (plain text item — no widget overflow)
                remove_item = QTableWidgetItem("✕")
                remove_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                remove_item.setToolTip("Remove this document")
                remove_item.setData(Qt.ItemDataRole.UserRole, file_path)
                remove_item.setData(Qt.ItemDataRole.UserRole + 1, db_col)
                
                self.docs_list.setItem(i, 0, name_item)
                self.docs_list.setItem(i, 1, type_item)
                self.docs_list.setItem(i, 2, remove_item)
                
        except Exception as e:
            debug_log(f"Error loading documents: {e}")
    
    def _on_docs_cell_clicked(self, row, col):
        """Handle click on the remove (✕) column."""
        if col != 2:
            return
        item = self.docs_list.item(row, 2)
        if not item:
            return
        fp = item.data(Qt.ItemDataRole.UserRole)
        db_col = item.data(Qt.ItemDataRole.UserRole + 1)
        if fp and db_col:
            self._remove_document(fp, db_col)
    
    def _open_selected_document(self):
        """Open the selected document."""
        row = self.docs_list.currentRow()
        if row < 0:
            return
        
        item = self.docs_list.item(row, 0)
        if not item:
            return
        
        file_ref = item.data(Qt.ItemDataRole.UserRole)
        if not file_ref:
            return
        
        import os
        from dmelogic.paths import resolve_document_path
        
        resolved = resolve_document_path(file_ref)
        if not resolved.exists():
            QMessageBox.warning(
                self,
                "File Not Found",
                f"Document not found:\n{resolved}"
            )
            return
        
        try:
            os.startfile(str(resolved))
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to open document:\n{e}"
            )
    
    def _remove_document(self, file_path: str, db_col: str = 'attached_rx_files'):
        """Remove a document from the order (keeps file on disk)."""
        reply = QMessageBox.question(
            self,
            "Remove Document",
            f"Remove this document from the order?\n\n"
            f"The file will remain on disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        try:
            import sqlite3
            
            conn = sqlite3.connect(self.orders_db_path)
            cur = conn.cursor()
            cur.execute(f"SELECT {db_col} FROM orders WHERE id = ?", (self.order_id,))
            row = cur.fetchone()
            
            if row and row[0]:
                files = [f.strip() for f in str(row[0]).replace(';', '\n').splitlines() if f.strip() and f.strip() != file_path]
                new_files = ";".join(files) if files else None
                cur.execute(f"UPDATE orders SET {db_col} = ? WHERE id = ?", (new_files, self.order_id))
                conn.commit()
            
            conn.close()
            self._refresh_documents_list()
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to remove document:\n{e}"
            )

    def _sync_items_from_table(self):
        """Sync current UI table state to self.order.items so EPACES helper shows current data."""
        if not self.order:
            return
        
        from decimal import Decimal
        
        updated_items = []
        for row in range(self.items_table.rowCount()):
            meta = self._item_row_meta[row] if row < len(self._item_row_meta) else {"id": None}
            
            def _text(c: int) -> str:
                item = self.items_table.item(row, c)
                return item.text().strip() if item else ""
            
            hcpcs = _text(0)
            item_num = _text(1)  # Item # column
            desc = _text(2)
            qty_str = _text(3)
            refills_str = _text(4)
            days_str = _text(5)
            mods = _text(6)
            cost_str = _text(7)
            
            # Skip empty rows
            if not hcpcs and not desc:
                continue
            
            # Parse values
            try:
                qty_val = int(qty_str) if qty_str else 0
            except ValueError:
                qty_val = 0
            
            try:
                refills_val = int(refills_str) if refills_str else 0
            except ValueError:
                refills_val = 0
            
            try:
                days_val = int(days_str) if days_str else 30
            except ValueError:
                days_val = 30
            
            try:
                cost_val = Decimal(cost_str) if cost_str else Decimal("0")
            except Exception:
                cost_val = Decimal("0")
            
            total_val = cost_val * Decimal(str(qty_val)) if qty_val else Decimal("0")
            
            # Parse modifiers
            mod_parts = [m for m in mods.replace(",", " ").split() if m]
            
            # Get prescriber from combo
            prescriber_combo = self.items_table.cellWidget(row, 9)
            prescriber_name = ""
            prescriber_npi = ""
            if prescriber_combo:
                presc_data = prescriber_combo.currentData()
                if presc_data:
                    prescriber_name = presc_data.get("name", "")
                    prescriber_npi = presc_data.get("npi", "")
            
            # Create OrderItem with current table values
            order_item = OrderItem(
                id=meta.get("id"),
                order_id=self.order.id,
                hcpcs_code=hcpcs,
                description=desc,
                quantity=qty_val,
                refills=refills_val,
                days_supply=days_val,
                cost_ea=cost_val,
                total_cost=total_val,
                modifier1=mod_parts[0] if len(mod_parts) > 0 else None,
                modifier2=mod_parts[1] if len(mod_parts) > 1 else None,
                modifier3=mod_parts[2] if len(mod_parts) > 2 else None,
                modifier4=mod_parts[3] if len(mod_parts) > 3 else None,
                item_number=item_num or meta.get("item_number") or "",
                pa_number=meta.get("pa_number") or "",
                directions=meta.get("directions") or "",
                is_rental=meta.get("is_rental", False),
                rental_month=meta.get("rental_month", 0),
                prescriber_name=prescriber_name,
                prescriber_npi=prescriber_npi,
            )
            updated_items.append(order_item)
        
        # Update order's items list with current table state
        self.order.items = updated_items

    def _open_epaces_helper(self):
        """Open the ePACES billing helper dialog (modal)."""
        if not self.order:
            return
        
        try:
            # Sync current table items to order before opening helper
            self._sync_items_from_table()
            
            dialog = EpacesHelperDialog(
                order=self.order,
                folder_path=self.folder_path,
                parent=self
            )
            self._show_billing_alert(dialog)
            self._show_rx_on_file_alert(dialog)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(
                self,
                "ePACES Error",
                f"Failed to open ePACES helper:\n{e}"
            )
    
    def _open_epaces_helper_nonmodal(self):
        """Open the ePACES billing helper dialog (non-modal, stays open while editing)."""
        if not self.order:
            return
        
        try:
            # Sync current table items to order before opening helper
            self._sync_items_from_table()
            
            # If dialog already exists and is visible, refresh it instead of creating new
            if hasattr(self, '_epaces_dialog') and self._epaces_dialog is not None:
                try:
                    if self._epaces_dialog.isVisible():
                        self._epaces_dialog.refresh_order(self.order)
                        return
                except RuntimeError:
                    # Dialog was deleted, create new one
                    pass
            
            # Store dialog instance to prevent garbage collection
            self._epaces_dialog = EpacesHelperDialog(
                order=self.order,
                folder_path=self.folder_path,
                parent=self
            )
            self._show_billing_alert(self._epaces_dialog)
            self._show_rx_on_file_alert(self._epaces_dialog)
            self._epaces_dialog.show()
            self._epaces_dialog.raise_()
            self._epaces_dialog.activateWindow()
        except Exception as e:
            QMessageBox.critical(
                self,
                "ePACES Error",
                f"Failed to open ePACES helper:\n{e}"
            )
    
    def _refresh_epaces_dialog(self):
        """Refresh the EPACES dialog if it's open (called when prescriber assignment changes)."""
        if not hasattr(self, '_epaces_dialog') or self._epaces_dialog is None:
            return
        try:
            if self._epaces_dialog.isVisible():
                # Sync items from table to ensure prescriber data is included
                self._sync_items_from_table()
                self._epaces_dialog.refresh_order(self.order)
        except RuntimeError:
            # Dialog was deleted
            self._epaces_dialog = None
    
    def _show_billing_alert(self, parent_dialog):
        """Show a billing alert popup if one is set on this order."""
        try:
            alert_text = getattr(self.order, 'epaces_alert', None)
            if not alert_text or not alert_text.strip():
                return
            alert_text = alert_text.strip()
            msg = QMessageBox(parent_dialog)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("⚠️  Billing Alert")
            msg.setText(
                f"<b>Alert for {self._format_order_number(self.order)}:</b>"
            )
            msg.setInformativeText(alert_text)
            msg.setStandardButtons(
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Discard
            )
            ok_btn = msg.button(QMessageBox.StandardButton.Ok)
            ok_btn.setText("Got it")
            discard_btn = msg.button(QMessageBox.StandardButton.Discard)
            discard_btn.setText("Clear Alert")
            msg.setDefaultButton(QMessageBox.StandardButton.Ok)
            msg.setStyleSheet("""
                QMessageBox { font-size: 11pt; }
                QMessageBox QLabel { min-width: 350px; }
            """)
            result = msg.exec()
            if result == QMessageBox.StandardButton.Discard:
                from dmelogic.db.orders import update_order_fields
                update_order_fields(
                    self.order.id,
                    {"epaces_alert": None},
                    folder_path=self.folder_path,
                )
                self.order.epaces_alert = None
                self.epaces_alert_text.setPlainText("")
                print(f"🔔 Billing alert cleared for ORD-{self.order.id}")
        except Exception as e:
            print(f"[Billing Alert check] {e}")

    def _show_rx_on_file_alert(self, parent_dialog):
        """Show an alert if this order has an RX on File."""
        try:
            import os
            from dmelogic.reserved_rx_manager import get_reserved_rx_data
            db_path = self.orders_db_path
            order_id = str(self.order.id)
            rx_data = get_reserved_rx_data(db_path, order_id)
            if rx_data and int(rx_data.get("rx_on_file", 0)):
                md_name = rx_data.get("reserved_rx_md", "") or "Unknown"
                rx_date = rx_data.get("reserved_rx_date", "") or "N/A"
                rx_doc  = rx_data.get("reserved_rx_path", "") or ""
                doc_display = os.path.basename(rx_doc) if rx_doc else "—"

                alert = QMessageBox(parent_dialog)
                alert.setIcon(QMessageBox.Icon.Information)
                alert.setWindowTitle("RX Already on File")
                alert.setText(
                    "<b>This patient already has an RX on file.</b><br>"
                    "<b>Do NOT contact the prescriber again.</b>"
                )
                alert.setInformativeText(
                    f"<table style='font-size:12px;'>"
                    f"<tr><td><b>Prescriber:</b></td><td>&nbsp;{md_name}</td></tr>"
                    f"<tr><td><b>Date Received:</b></td><td>&nbsp;{rx_date}</td></tr>"
                    f"<tr><td><b>Document:</b></td><td>&nbsp;{doc_display}</td></tr>"
                    f"</table>"
                )
                alert.setStandardButtons(QMessageBox.StandardButton.Ok)
                alert.exec()
        except Exception as e:
            print(f"[RX on File check] {e}")

    def _change_prescriber(self):
        """Open prescriber lookup dialog to change prescriber."""
        if not self.order:
            return
        
        dialog = PrescriberLookupDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_prescriber:
            prescriber = dialog.selected_prescriber
            
            # Update order's prescriber info using correct model attributes
            self.order.prescriber_name = f"{prescriber.get('last_name', '').upper()}, {prescriber.get('first_name', '').upper()}"
            self.order.prescriber_npi = prescriber.get('npi_number') or ""
            self.order.prescriber_phone = prescriber.get('phone') or ""
            self.order.prescriber_fax = prescriber.get('fax') or ""
            
            # Mark prescriber as changed for save
            self._prescriber_changed = True
            
            # Update display
            self.prescriber_name.setText(self.order.prescriber_name)
            self.prescriber_npi.setText(self.order.prescriber_npi or "N/A")
            self.prescriber_phone_input.setText(self.order.prescriber_phone or "")
            self.prescriber_fax_input.setText(self.order.prescriber_fax or "")
            
            # Mark as changed
            self.save_button.setEnabled(True)
            
            QMessageBox.information(
                self,
                "Prescriber Updated",
                f"Prescriber changed to: {self.order.prescriber_name}\n\n"
                f"Click 'Save Changes' to save this to the database."
            )
    
    def _change_prescriber_2(self):
        """Open prescriber lookup dialog to change secondary prescriber."""
        if not self.order:
            return
        
        dialog = PrescriberLookupDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_prescriber:
            prescriber = dialog.selected_prescriber
            
            # Update order's secondary prescriber info
            self.order.prescriber_name_2 = f"{prescriber.get('last_name', '').upper()}, {prescriber.get('first_name', '').upper()}"
            self.order.prescriber_npi_2 = prescriber.get('npi_number') or ""
            
            # Mark prescriber 2 as changed for save
            self._prescriber_2_changed = True
            
            # Update display
            self.prescriber_name_2.setText(self.order.prescriber_name_2)
            self.prescriber_npi_2.setText(self.order.prescriber_npi_2 or "N/A")
            
            # Mark as changed
            self.save_button.setEnabled(True)
            
            QMessageBox.information(
                self,
                "Prescriber 2 Updated",
                f"Secondary prescriber changed to: {self.order.prescriber_name_2}\n\n"
                f"Click 'Save Changes' to save this to the database."
            )
    
    def _change_insurance(self):
        """Open dialog to change insurance information for this order."""
        if not self.order:
            return
        
        # Safely get current billing type
        current_billing = "Insurance"
        if hasattr(self.order, 'billing_type') and self.order.billing_type:
            current_billing = self.order.billing_type.value
        elif hasattr(self.order, 'billing_selection') and self.order.billing_selection:
            current_billing = self.order.billing_selection
        
        dialog = InsuranceEditDialog(
            primary_insurance=self.order.primary_insurance or "",
            primary_id=self.order.primary_insurance_id or "",
            secondary_insurance=getattr(self.order, 'secondary_insurance', '') or "",
            secondary_id=getattr(self.order, 'secondary_insurance_id', '') or "",
            billing_type=current_billing,
            folder_path=self.folder_path,
            parent=self
        )
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Get values from dialog
            new_primary = dialog.primary_insurance.currentText().strip()
            new_primary_id = dialog.primary_id.text().strip()
            new_secondary = dialog.secondary_insurance.currentText().strip()
            new_secondary_id = dialog.secondary_id.text().strip()
            new_billing = dialog.billing_type.currentText()
            
            # Update order object
            self.order.primary_insurance = new_primary
            self.order.primary_insurance_id = new_primary_id
            self.order.secondary_insurance = new_secondary
            self.order.secondary_insurance_id = new_secondary_id
            
            # Update billing type
            from dmelogic.db.models import BillingType
            try:
                self.order.billing_type = BillingType(new_billing)
            except ValueError:
                self.order.billing_type = BillingType.INSURANCE
            
            # Mark insurance as changed for save
            self._insurance_changed = True
            
            # Also update the patient profile with the new insurance
            self._update_patient_insurance(new_primary, new_primary_id, new_secondary, new_secondary_id)
            
            # Update display
            self.insurance_name.setText(new_primary or "N/A")
            self.insurance_id.setText(new_primary_id or "N/A")
            self.secondary_insurance_name.setText(new_secondary or "N/A")
            self.secondary_insurance_id.setText(new_secondary_id or "N/A")
            self.billing_type.setText(new_billing)
            
            # Mark as changed
            self.save_button.setEnabled(True)
            
            QMessageBox.information(
                self,
                "Insurance Updated",
                f"Insurance changed to: {new_primary or '(None)'}\n\n"
                f"Order and patient profile have been updated.\n"
                f"Click 'Save Changes' to save to the database."
            )
    
    def _update_patient_insurance(self, primary: str, primary_id: str, secondary: str, secondary_id: str):
        """Update the patient's insurance in the patient database."""
        if not self.order or not self.order.patient_id:
            return
        
        try:
            from dmelogic.db.base import get_connection
            
            conn = get_connection("patients.db", folder_path=self.folder_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE patients SET
                    primary_insurance = ?,
                    policy_number = ?,
                    secondary_insurance = ?,
                    secondary_insurance_id = ?
                WHERE id = ?
            """, (primary, primary_id, secondary, secondary_id, self.order.patient_id))
            
            conn.commit()
            conn.close()
            
            debug_log(f"[order-editor] Updated patient {self.order.patient_id} insurance")
            
        except Exception as e:
            debug_log(f"[order-editor] Failed to update patient insurance: {e}")

    def _edit_patient(self):
        """Open the patient details dialog to edit the current patient."""
        from PyQt6.QtWidgets import QMessageBox
        from dmelogic.db.base import get_connection
        
        if not self.order:
            QMessageBox.warning(self, "No Order", "No order is currently loaded.")
            return
        
        try:
            # Get patient info from order
            patient_id = self.order.patient_id
            if self.order.patient_first_name:
                first_name = self.order.patient_first_name
                last_name = self.order.patient_last_name or ""
            elif self.order.patient_name:
                # Parse "LAST, FIRST" format
                parts = self.order.patient_name.split(",", 1)
                if len(parts) == 2:
                    last_name = parts[0].strip()
                    first_name = parts[1].strip()
                else:
                    last_name = self.order.patient_name.strip()
                    first_name = ""
            else:
                QMessageBox.warning(self, "No Patient", "No patient is associated with this order.")
                return
            
            # Get DOB from display label
            dob = self.patient_dob.text()
            if dob in ("N/A", "[Select Patient]", ""):
                dob = ""
            
            # FIRST: Check if patient exists in the database
            conn = get_connection("patients.db", folder_path=self.folder_path)
            cursor = conn.cursor()
            if patient_id:
                cursor.execute("""
                    SELECT id, last_name, first_name, dob, gender, ssn, phone, secondary_contact, email,
                           address, city, state, zip,
                           primary_insurance, policy_number, group_number, 
                           secondary_insurance, secondary_insurance_id, notes
                    FROM patients WHERE id = ?
                """, (patient_id,))
            else:
                cursor.execute("""
                    SELECT id, last_name, first_name, dob, gender, ssn, phone, secondary_contact, email,
                           address, city, state, zip,
                           primary_insurance, policy_number, group_number, 
                           secondary_insurance, secondary_insurance_id, notes
                    FROM patients WHERE last_name = ? AND first_name = ?
                """, (last_name, first_name))
            
            patient_data = cursor.fetchone()
            conn.close()
            
            if not patient_data:
                # Patient not found - offer to create new patient
                reply = QMessageBox.question(
                    self,
                    "Patient Not Found",
                    f"Patient '{last_name}, {first_name}' was not found in the database.\n\n"
                    f"This order may have been created by the agent without a matching patient record.\n\n"
                    f"Would you like to CREATE a new patient record?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    # Create new patient with prefilled info from the order
                    self._create_new_patient_for_order(last_name, first_name, dob)
                return
            
            # Patient exists - open edit dialog
            self._open_patient_edit_dialog(patient_data)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open patient editor:\n{e}")
    
    def _create_new_patient_for_order(self, last_name: str, first_name: str, dob: str):
        """Create a new patient record and link it to the current order."""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
            QTextEdit, QDialogButtonBox, QMessageBox, QScrollArea, QWidget
        )
        
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Create New Patient - {first_name} {last_name}")
        dialog.resize(500, 600)
        
        main_layout = QVBoxLayout(dialog)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        
        # Create editable fields with prefilled values from order
        fields = {}
        field_defs = [
            ("first_name", "First Name *", first_name),
            ("last_name", "Last Name *", last_name),
            ("dob", "Date of Birth", dob),
            ("gender", "Gender", ""),
            ("ssn", "SSN", ""),
            ("phone", "Phone", ""),
            ("secondary_contact", "Secondary Contact", ""),
            ("email", "Email", ""),
            ("address", "Address", ""),
            ("city", "City", ""),
            ("state", "State", ""),
            ("zip", "ZIP", ""),
            ("primary_insurance", "Primary Insurance", ""),
            ("policy_number", "Policy #", ""),
            ("group_number", "Group #", ""),
            ("secondary_insurance", "Secondary Insurance", ""),
            ("secondary_insurance_id", "Secondary Policy #", ""),
        ]
        
        for key, label, value in field_defs:
            if key == "gender":
                field = QComboBox()
                field.addItems(["", "Male", "Female", "Other"])
            else:
                field = QLineEdit()
                field.setText(value or "")
            fields[key] = field
            form.addRow(f"{label}:", field)
        
        # Notes field
        notes_field = QTextEdit()
        notes_field.setPlaceholderText("Patient notes...")
        notes_field.setMaximumHeight(100)
        fields["notes"] = notes_field
        form.addRow("Notes:", notes_field)
        
        scroll.setWidget(content)
        main_layout.addWidget(scroll)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        main_layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Validate required fields
            if not fields["first_name"].text().strip() or not fields["last_name"].text().strip():
                QMessageBox.warning(dialog, "Required Fields", "First Name and Last Name are required.")
                return
            
            # Create the patient
            try:
                from dmelogic.db.base import get_connection
                
                conn = get_connection("patients.db", folder_path=self.folder_path)
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO patients (
                        first_name, last_name, dob, gender, ssn,
                        phone, secondary_contact, email,
                        address, city, state, zip,
                        primary_insurance, policy_number, group_number,
                        secondary_insurance, secondary_insurance_id, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    fields["first_name"].text().strip(),
                    fields["last_name"].text().strip(),
                    fields["dob"].text().strip(),
                    fields["gender"].currentText() if isinstance(fields["gender"], QComboBox) else "",
                    fields["ssn"].text().strip(),
                    fields["phone"].text().strip(),
                    fields["secondary_contact"].text().strip(),
                    fields["email"].text().strip(),
                    fields["address"].text().strip(),
                    fields["city"].text().strip(),
                    fields["state"].text().strip(),
                    fields["zip"].text().strip(),
                    fields["primary_insurance"].text().strip(),
                    fields["policy_number"].text().strip(),
                    fields["group_number"].text().strip(),
                    fields["secondary_insurance"].text().strip(),
                    fields["secondary_insurance_id"].text().strip(),
                    fields["notes"].toPlainText().strip(),
                ))
                
                new_patient_id = cursor.lastrowid
                conn.commit()
                conn.close()
                
                # Update the order with the new patient_id
                self.order.patient_id = new_patient_id
                self.order.patient_first_name = fields["first_name"].text().strip()
                self.order.patient_last_name = fields["last_name"].text().strip()
                
                # Save the patient_id to the order in the database
                conn = get_connection("orders.db", folder_path=self.folder_path)
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE orders SET patient_id = ? WHERE id = ?
                """, (new_patient_id, self.order.id))
                conn.commit()
                conn.close()
                
                # Refresh display
                self._refresh_patient_display()
                
                QMessageBox.information(
                    self,
                    "Success",
                    f"Patient '{fields['last_name'].text()}, {fields['first_name'].text()}' created successfully.\n\n"
                    f"Patient ID: {new_patient_id}\n"
                    f"The order has been linked to this patient."
                )
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create patient:\n{e}")

    def _refresh_patient_display(self):
        """Refresh patient display after editing."""
        try:
            row = self._fetch_live_patient_row()
            
            if row:
                patient_name = f"{row[0]}, {row[1]}" if row[1] else row[0]
                self.patient_name.setText(patient_name)
                self.patient_dob.setText(row[2] or "N/A")
                self.patient_phone.setText(row[3] or "N/A")
                addr_parts = [p for p in [row[4], row[5], row[6], row[7]] if p]
                self.patient_address.setText(", ".join(addr_parts) if addr_parts else "N/A")

                # Keep in-memory order snapshot in sync with latest patient values.
                self.order.patient_last_name = row[0] or getattr(self.order, "patient_last_name", None)
                self.order.patient_first_name = row[1] or getattr(self.order, "patient_first_name", None)
                self.order.patient_dob = row[2] or None
                self.order.patient_phone = row[3] or None
        except Exception as e:
            print(f"Error refreshing patient display: {e}")
    
    def _open_patient_edit_dialog(self, patient_data):
        """Open a simple patient edit dialog."""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
            QTextEdit, QDialogButtonBox, QMessageBox, QScrollArea, QWidget
        )
        
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit Patient - {patient_data[2]} {patient_data[1]}")
        dialog.resize(500, 600)
        
        main_layout = QVBoxLayout(dialog)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        
        # Create editable fields
        fields = {}
        field_defs = [
            ("first_name", "First Name", patient_data[2]),
            ("last_name", "Last Name", patient_data[1]),
            ("dob", "Date of Birth", patient_data[3]),
            ("gender", "Gender", patient_data[4]),
            ("ssn", "SSN", patient_data[5]),
            ("phone", "Phone", patient_data[6]),
            ("secondary_contact", "Secondary Contact", patient_data[7]),
            ("email", "Email", patient_data[8]),
            ("address", "Address", patient_data[9]),
            ("city", "City", patient_data[10]),
            ("state", "State", patient_data[11]),
            ("zip", "ZIP", patient_data[12]),
            ("primary_insurance", "Primary Insurance", patient_data[13]),
            ("policy_number", "Policy #", patient_data[14]),
            ("group_number", "Group #", patient_data[15]),
            ("secondary_insurance", "Secondary Insurance", patient_data[16]),
            ("secondary_insurance_id", "Secondary Policy #", patient_data[17]),
        ]
        
        for key, label, value in field_defs:
            if key == "gender":
                field = QComboBox()
                field.addItems(["", "Male", "Female", "Other"])
                if value:
                    idx = field.findText(value)
                    if idx >= 0:
                        field.setCurrentIndex(idx)
            else:
                field = QLineEdit()
                field.setText(value or "")
            fields[key] = field
            form.addRow(f"{label}:", field)
        
        # Notes field
        notes_field = QTextEdit()
        notes_field.setPlainText(patient_data[18] or "")
        notes_field.setMaximumHeight(100)
        fields["notes"] = notes_field
        form.addRow("Notes:", notes_field)
        
        scroll.setWidget(content)
        main_layout.addWidget(scroll)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        main_layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Save changes
            try:
                from dmelogic.db.base import get_connection
                
                conn = get_connection("patients.db", folder_path=self.folder_path)
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE patients SET
                        first_name = ?, last_name = ?, dob = ?, gender = ?, ssn = ?,
                        phone = ?, secondary_contact = ?, email = ?,
                        address = ?, city = ?, state = ?, zip = ?,
                        primary_insurance = ?, policy_number = ?, group_number = ?,
                        secondary_insurance = ?, secondary_insurance_id = ?, notes = ?
                    WHERE id = ?
                """, (
                    fields["first_name"].text(),
                    fields["last_name"].text(),
                    fields["dob"].text(),
                    fields["gender"].currentText() if isinstance(fields["gender"], QComboBox) else fields["gender"].text(),
                    fields["ssn"].text(),
                    fields["phone"].text(),
                    fields["secondary_contact"].text(),
                    fields["email"].text(),
                    fields["address"].text(),
                    fields["city"].text(),
                    fields["state"].text(),
                    fields["zip"].text(),
                    fields["primary_insurance"].text(),
                    fields["policy_number"].text(),
                    fields["group_number"].text(),
                    fields["secondary_insurance"].text(),
                    fields["secondary_insurance_id"].text(),
                    fields["notes"].toPlainText(),
                    patient_data[0]  # patient ID
                ))
                
                conn.commit()
                conn.close()

                # Keep this order's patient snapshot in sync so DOB changes stick
                # after closing and reopening the order editor.
                if self.order and self.order.id:
                    patient_last = fields["last_name"].text().strip()
                    patient_first = fields["first_name"].text().strip()
                    patient_dob = fields["dob"].text().strip() or None
                    patient_phone = fields["phone"].text().strip() or None
                    patient_primary_ins = fields["primary_insurance"].text().strip() or None
                    patient_primary_id = fields["policy_number"].text().strip() or None
                    patient_secondary_ins = fields["secondary_insurance"].text().strip() or None
                    patient_secondary_id = fields["secondary_insurance_id"].text().strip() or None

                    addr_parts = [
                        fields["address"].text().strip(),
                        fields["city"].text().strip(),
                        fields["state"].text().strip(),
                        fields["zip"].text().strip(),
                    ]
                    patient_address = ", ".join([part for part in addr_parts if part]) or None
                    patient_name = f"{patient_last}, {patient_first}" if patient_first else patient_last

                    orders_conn = get_connection("orders.db", folder_path=self.folder_path)
                    try:
                        orders_cursor = orders_conn.cursor()
                        orders_cursor.execute(
                            """
                            UPDATE orders SET
                                patient_last_name = ?,
                                patient_first_name = ?,
                                patient_dob = ?,
                                patient_phone = ?,
                                patient_address = ?,
                                patient_name = ?,
                                primary_insurance = ?,
                                primary_insurance_id = ?,
                                secondary_insurance = ?,
                                secondary_insurance_id = ?,
                                updated_date = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (
                                patient_last or None,
                                patient_first or None,
                                patient_dob,
                                patient_phone,
                                patient_address,
                                patient_name or None,
                                patient_primary_ins,
                                patient_primary_id,
                                patient_secondary_ins,
                                patient_secondary_id,
                                self.order.id,
                            ),
                        )
                        orders_conn.commit()
                    finally:
                        orders_conn.close()

                    # Keep in-memory order data aligned with the database snapshot.
                    self.order.patient_last_name = patient_last or None
                    self.order.patient_first_name = patient_first or None
                    self.order.patient_name = patient_name or None
                    self.order.patient_dob = patient_dob
                    self.order.patient_phone = patient_phone
                    self.order.patient_address = patient_address
                    self.order.primary_insurance = patient_primary_ins
                    self.order.primary_insurance_id = patient_primary_id
                    self.order.secondary_insurance = patient_secondary_ins
                    self.order.secondary_insurance_id = patient_secondary_id
                
                # Refresh display
                self._refresh_patient_display()
                
                QMessageBox.information(dialog, "Success", "Patient information saved successfully.")
                
            except Exception as e:
                QMessageBox.critical(dialog, "Error", f"Failed to save patient:\n{e}")

    def _change_patient(self):
        """Change the patient associated with this order."""
        from PyQt6.QtWidgets import QInputDialog, QMessageBox
        
        # Show confirmation warning first
        reply = QMessageBox.warning(
            self,
            "Change Patient",
            "⚠️ WARNING: You are about to reassign this order to a DIFFERENT PATIENT.\n\n"
            "This should only be done if the order was created under the wrong patient.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        try:
            # Get list of patients from database
            from dmelogic.db.base import get_connection
            
            conn = get_connection("patients.db", folder_path=self.folder_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, last_name, first_name 
                FROM patients 
                ORDER BY last_name, first_name
            """)
            patients = cursor.fetchall()
            conn.close()
            
            if not patients:
                QMessageBox.warning(self, "No Patients", "No patients found in the database.")
                return
            
            # Build list of patient names for selection
            patient_names = [f"{p[1]}, {p[2]} (ID: {p[0]})" for p in patients]
            
            # Show selection dialog
            selected, ok = QInputDialog.getItem(
                self,
                "Select Patient",
                "Choose the correct patient for this order:",
                patient_names,
                0,
                False  # not editable
            )
            
            if not ok or not selected:
                return
            
            # Extract selected patient info
            selected_index = patient_names.index(selected)
            new_patient_id = patients[selected_index][0]
            new_last_name = patients[selected_index][1]
            new_first_name = patients[selected_index][2]
            new_patient_name = f"{new_last_name}, {new_first_name}"
            
            # Store original patient info for the note - compute name directly to avoid property issues
            if self.order.patient_first_name:
                old_patient_name = f"{self.order.patient_last_name}, {self.order.patient_first_name}"
            elif self.order.patient_last_name:
                old_patient_name = self.order.patient_last_name
            elif self.order.patient_name:
                old_patient_name = self.order.patient_name
            else:
                old_patient_name = "Unknown"
            old_patient_id = self.order.patient_id
            
            # Update order object using correct model attributes
            self.order.patient_id = new_patient_id
            self.order.patient_last_name = new_last_name
            self.order.patient_first_name = new_first_name
            
            # Store patient change info for save
            self._patient_changed = True
            self._old_patient_name = old_patient_name
            self._old_patient_id = old_patient_id
            
            # Update display
            self.patient_name.setText(new_patient_name)
            
            # Try to fetch additional patient info
            try:
                conn = get_connection("patients.db", folder_path=self.folder_path)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT dob, phone, address, city, state, zip
                    FROM patients WHERE id = ?
                """, (new_patient_id,))
                row = cursor.fetchone()
                conn.close()
                
                if row:
                    self.patient_dob.setText(row[0] or "N/A")
                    self.patient_phone.setText(row[1] or "N/A")
                    addr_parts = [p for p in [row[2], row[3], row[4], row[5]] if p]
                    patient_address = ", ".join(addr_parts) if addr_parts else "N/A"
                    self.patient_address.setText(patient_address)

                    # Keep in-memory snapshot consistent with what is shown in UI.
                    self.order.patient_dob = row[0] or None
                    self.order.patient_phone = row[1] or None
                    self.order.patient_address = patient_address if patient_address != "N/A" else None
            except:
                pass  # Keep whatever is displayed
            
            # Mark as changed
            self.save_button.setEnabled(True)
            
            QMessageBox.information(
                self,
                "Patient Updated",
                f"Patient changed from: {old_patient_name}\n"
                f"To: {new_patient_name}\n\n"
                f"Click 'Save Changes' to save this to the database.\n"
                f"A note will be added documenting this change."
            )
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to change patient:\n{e}"
            )
    
    def _save_changes(self):
        """Save any changes made to the order."""
        if not self.order:
            return
        
        try:
            from dmelogic.db.base import get_connection
            from dmelogic.db.orders import update_order_fields
            from datetime import datetime
            
            conn = get_connection("orders.db", folder_path=self.folder_path)
            try:
                cursor = conn.cursor()
                
                # Build dynamic UPDATE based on what changed
                update_parts = []
                update_values = []
                
                # Only update prescriber if it was changed
                if getattr(self, '_prescriber_changed', False):
                    update_parts.append("prescriber_name = ?")
                    update_values.append(self.order.prescriber_name)
                    update_parts.append("prescriber_npi = ?")
                    update_values.append(self.order.prescriber_npi)
                    self._prescriber_changed = False
                
                # Only update prescriber 2 if it was changed
                if getattr(self, '_prescriber_2_changed', False):
                    update_parts.append("prescriber_name_2 = ?")
                    update_values.append(self.order.prescriber_name_2)
                    update_parts.append("prescriber_npi_2 = ?")
                    update_values.append(self.order.prescriber_npi_2)
                    self._prescriber_2_changed = False
                
                # Only update patient if it was changed
                if getattr(self, '_patient_changed', False):
                    update_parts.append("patient_id = ?")
                    update_values.append(self.order.patient_id)
                    # Compute patient name instead of using property
                    if self.order.patient_first_name:
                        computed_name = f"{self.order.patient_last_name}, {self.order.patient_first_name}"
                    else:
                        computed_name = self.order.patient_last_name or self.order.patient_name or ""
                    update_parts.append("patient_name = ?")
                    update_values.append(computed_name)
                    update_parts.append("patient_last_name = ?")
                    update_values.append(self.order.patient_last_name)
                    update_parts.append("patient_first_name = ?")
                    update_values.append(self.order.patient_first_name)

                    # Persist patient snapshot fields so DOB edits are retained
                    # on the order after reassignment.
                    snapshot_dob = (self.patient_dob.text() or "").strip()
                    if snapshot_dob in ("N/A", "[Select Patient]"):
                        snapshot_dob = ""
                    snapshot_phone = (self.patient_phone.text() or "").strip()
                    if snapshot_phone in ("N/A", "[Select Patient]"):
                        snapshot_phone = ""
                    snapshot_address = (self.patient_address.text() or "").strip()
                    if snapshot_address in ("N/A", "[Select Patient]"):
                        snapshot_address = ""

                    update_parts.append("patient_dob = ?")
                    update_values.append(snapshot_dob or None)
                    update_parts.append("patient_phone = ?")
                    update_values.append(snapshot_phone or None)
                    update_parts.append("patient_address = ?")
                    update_values.append(snapshot_address or None)

                    self.order.patient_dob = snapshot_dob or None
                    self.order.patient_phone = snapshot_phone or None
                    self.order.patient_address = snapshot_address or None
                
                # Only update insurance if it was changed
                if getattr(self, '_insurance_changed', False):
                    update_parts.append("primary_insurance = ?")
                    update_values.append(self.order.primary_insurance or None)
                    update_parts.append("primary_insurance_id = ?")
                    update_values.append(self.order.primary_insurance_id or None)
                    update_parts.append("secondary_insurance = ?")
                    update_values.append(getattr(self.order, 'secondary_insurance', None) or None)
                    update_parts.append("secondary_insurance_id = ?")
                    update_values.append(getattr(self.order, 'secondary_insurance_id', None) or None)
                    update_parts.append("billing_selection = ?")
                    update_values.append(self.order.billing_type.value if self.order.billing_type else "Insurance")
                    self._insurance_changed = False
                
                # Execute update if there are changes
                if update_parts:
                    update_parts.append("updated_date = CURRENT_TIMESTAMP")
                    update_values.append(self.order.id)
                    sql = f"UPDATE orders SET {', '.join(update_parts)} WHERE id = ?"
                    cursor.execute(sql, update_values)
                    conn.commit()
            finally:
                conn.close()
            
            # Save notes, doctor directions, special instructions, and date fields
            new_directions = self.doctor_directions.toPlainText().strip()
            new_notes = self.notes_text.toPlainText().strip()
            new_special_instructions = self.special_instructions_text.toPlainText().strip()
            
            # Get date field values
            new_rx_date = self.rx_date.text().strip()
            new_rx_date_2 = self.rx_date_2.text().strip() or None
            new_order_date = self.order_date.text().strip() or None
            new_delivery_date = self.delivery_date.text().strip() or None
            new_pickup_date = self.pickup_date.text().strip() or None
            new_tracking = self.tracking_number.text().strip() or None
            
            # Compare with original values (handle "No directions provided" and "No notes" placeholders)
            orig_directions = (self.order.doctor_directions or "").strip()
            orig_notes = (self.order.notes or "").strip()
            orig_rx_date = _safe_format_date(self.order.rx_date) or ""
            orig_rx_date_2 = _safe_format_date(getattr(self.order, 'rx_date_2', None)) or ""
            orig_order_date = _safe_format_date(self.order.order_date) or ""
            orig_delivery_date = _safe_format_date(self.order.delivery_date) or ""
            orig_pickup_date = _safe_format_date(self.order.pickup_date) or ""
            orig_tracking = (self.order.tracking_number or "").strip()

            # orders.rx_date is NOT NULL in production DBs.
            # If the UI field is blank, fall back to an existing meaningful date.
            if not new_rx_date:
                new_rx_date = (
                    orig_rx_date
                    or (new_order_date or "")
                    or orig_order_date
                    or datetime.now().strftime("%m/%d/%Y")
                )
                self.rx_date.setText(new_rx_date)
            
            fields_to_update = {}
            
            # Check date field changes
            if (new_rx_date or "") != orig_rx_date:
                fields_to_update["rx_date"] = new_rx_date
            if (new_rx_date_2 or "") != orig_rx_date_2:
                fields_to_update["rx_date_2"] = new_rx_date_2
            if (new_order_date or "") != orig_order_date:
                fields_to_update["order_date"] = new_order_date
            if (new_delivery_date or "") != orig_delivery_date:
                fields_to_update["delivery_date"] = new_delivery_date
            if (new_pickup_date or "") != orig_pickup_date:
                fields_to_update["pickup_date"] = new_pickup_date
            if (new_tracking or "") != orig_tracking:
                fields_to_update["tracking_number"] = new_tracking
            
            # Check prescriber phone/fax changes
            new_prescriber_phone = self.prescriber_phone_input.text().strip()
            orig_prescriber_phone = (self.order.prescriber_phone or "").strip()
            if new_prescriber_phone != orig_prescriber_phone:
                fields_to_update["prescriber_phone"] = new_prescriber_phone or None
            
            new_prescriber_fax = self.prescriber_fax_input.text().strip()
            orig_prescriber_fax = (self.order.prescriber_fax or "").strip()
            if new_prescriber_fax != orig_prescriber_fax:
                fields_to_update["prescriber_fax"] = new_prescriber_fax or None
            
            # Check ICD-10 code changes
            for i, field in enumerate(self.icd_code_fields, 1):
                new_icd = field.text().strip().upper() or None
                orig_icd = (getattr(self.order, f'icd_code_{i}', None) or "").strip()
                # Also check from icd_codes list
                if not orig_icd and self.order.icd_codes and i-1 < len(self.order.icd_codes):
                    orig_icd = (self.order.icd_codes[i-1] or "").strip()
                if (new_icd or "") != orig_icd:
                    fields_to_update[f"icd_code_{i}"] = new_icd
            
            if new_directions != orig_directions and new_directions != "No directions provided":
                fields_to_update["doctor_directions"] = new_directions if new_directions else None
            if new_notes != orig_notes and new_notes != "No notes":
                fields_to_update["notes"] = new_notes if new_notes else None
            
            # Check special instructions change
            orig_special_instructions = (getattr(self.order, 'special_instructions', '') or "").strip()
            if new_special_instructions != orig_special_instructions:
                fields_to_update["special_instructions"] = new_special_instructions if new_special_instructions else None
            
            # Check billing alert change
            new_epaces_alert = self.epaces_alert_text.toPlainText().strip()
            orig_epaces_alert = (getattr(self.order, 'epaces_alert', '') or "").strip()
            if new_epaces_alert != orig_epaces_alert:
                fields_to_update["epaces_alert"] = new_epaces_alert if new_epaces_alert else None
            
            # If patient was changed, add a note documenting it
            if getattr(self, '_patient_changed', False):
                timestamp = datetime.now().strftime("%m/%d/%Y %H:%M")
                # Compute new patient name for note
                if self.order.patient_first_name:
                    new_name = f"{self.order.patient_last_name}, {self.order.patient_first_name}"
                else:
                    new_name = self.order.patient_last_name or self.order.patient_name or "Unknown"
                change_note = f"[{timestamp}] PATIENT CHANGED: From '{self._old_patient_name}' to '{new_name}'"
                
                # Append to existing notes
                current_notes = fields_to_update.get("notes") or self.order.notes or ""
                if current_notes and current_notes != "No notes":
                    fields_to_update["notes"] = f"{current_notes}\n\n{change_note}"
                else:
                    fields_to_update["notes"] = change_note
                
                # Clear the flag
                self._patient_changed = False
            
            if fields_to_update:
                update_order_fields(self.order.id, fields_to_update, folder_path=self.folder_path)
                # Update local order object
                if "doctor_directions" in fields_to_update:
                    self.order.doctor_directions = fields_to_update["doctor_directions"]
                if "notes" in fields_to_update:
                    self.order.notes = fields_to_update["notes"]
                    # Update the notes display
                    self.notes_text.setPlainText(self.order.notes)
                # Update date fields in local order object
                if "rx_date" in fields_to_update:
                    self.order.rx_date = fields_to_update["rx_date"]
                if "rx_date_2" in fields_to_update:
                    self.order.rx_date_2 = fields_to_update["rx_date_2"]
                if "order_date" in fields_to_update:
                    self.order.order_date = fields_to_update["order_date"]
                if "delivery_date" in fields_to_update:
                    self.order.delivery_date = fields_to_update["delivery_date"]
                if "pickup_date" in fields_to_update:
                    self.order.pickup_date = fields_to_update["pickup_date"]
                if "tracking_number" in fields_to_update:
                    self.order.tracking_number = fields_to_update["tracking_number"]
                if "special_instructions" in fields_to_update:
                    self.order.special_instructions = fields_to_update["special_instructions"]
                if "epaces_alert" in fields_to_update:
                    self.order.epaces_alert = fields_to_update["epaces_alert"]
                # Update prescriber phone/fax in local order object
                if "prescriber_phone" in fields_to_update:
                    self.order.prescriber_phone = fields_to_update["prescriber_phone"]
                if "prescriber_fax" in fields_to_update:
                    self.order.prescriber_fax = fields_to_update["prescriber_fax"]
                # Update ICD code fields in local order object
                for i in range(1, 6):
                    field_name = f"icd_code_{i}"
                    if field_name in fields_to_update:
                        setattr(self.order, field_name, fields_to_update[field_name])
                # Update the icd_codes list as well
                new_icd_list = [f.text().strip().upper() for f in self.icd_code_fields if f.text().strip()]
                self.order.icd_codes = new_icd_list
            
            # Save item changes (deletions / edits / additions)
            self._save_item_changes()

            # Save Reserved RX panel data
            if hasattr(self, 'rx_panel'):
                self.rx_panel.save()

            QMessageBox.information(
                self,
                "Changes Saved",
                "Order has been updated successfully."
            )
            
            self.save_button.setEnabled(False)
            self.order_updated.emit()
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Save Error",
                f"Failed to save changes:\n{e}"
            )


class InsuranceEditDialog(QDialog):
    """Dialog for editing insurance information on an order."""
    
    def __init__(self, primary_insurance: str = "", primary_id: str = "",
                 secondary_insurance: str = "", secondary_id: str = "",
                 billing_type: str = "Insurance", folder_path: str = None, parent=None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.setWindowTitle("Change Insurance")
        self.setMinimumWidth(450)
        self._init_ui(primary_insurance, primary_id, secondary_insurance, 
                      secondary_id, billing_type)
    
    def _init_ui(self, primary_insurance, primary_id, secondary_insurance, 
                 secondary_id, billing_type):
        layout = QVBoxLayout(self)
        
        form_layout = QFormLayout()
        
        # Primary Insurance - combo box with autocomplete
        self.primary_insurance = QComboBox()
        self.primary_insurance.setEditable(True)
        self.primary_insurance.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._load_insurance_names(self.primary_insurance)
        self.primary_insurance.setEditText(primary_insurance)
        # Auto-uppercase
        line_edit = self.primary_insurance.lineEdit()
        if line_edit:
            line_edit.textChanged.connect(
                lambda text: self._convert_to_uppercase(self.primary_insurance, text)
            )
        form_layout.addRow("Primary Insurance:", self.primary_insurance)
        
        # Primary Policy Number
        self.primary_id = QLineEdit(primary_id)
        self.primary_id.setPlaceholderText("Policy Number")
        self.primary_id.textChanged.connect(lambda text: self.primary_id.setText(text.upper()))
        form_layout.addRow("Policy Number:", self.primary_id)
        
        # Secondary Insurance - combo box with autocomplete
        self.secondary_insurance = QComboBox()
        self.secondary_insurance.setEditable(True)
        self.secondary_insurance.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._load_insurance_names(self.secondary_insurance)
        self.secondary_insurance.setEditText(secondary_insurance)
        # Auto-uppercase
        line_edit2 = self.secondary_insurance.lineEdit()
        if line_edit2:
            line_edit2.textChanged.connect(
                lambda text: self._convert_to_uppercase(self.secondary_insurance, text)
            )
        form_layout.addRow("Secondary Insurance:", self.secondary_insurance)
        
        # Secondary Policy Number
        self.secondary_id = QLineEdit(secondary_id)
        self.secondary_id.setPlaceholderText("Secondary Policy Number")
        self.secondary_id.textChanged.connect(lambda text: self.secondary_id.setText(text.upper()))
        form_layout.addRow("Secondary Policy #:", self.secondary_id)
        
        # Billing Type
        self.billing_type = QComboBox()
        self.billing_type.addItems(["Insurance", "Cash", "Worker's Comp", "Medicaid Pending"])
        index = self.billing_type.findText(billing_type)
        if index >= 0:
            self.billing_type.setCurrentIndex(index)
        form_layout.addRow("Billing Type:", self.billing_type)
        
        layout.addLayout(form_layout)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("Apply Changes")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        btn_layout.addWidget(save_btn)
        
        layout.addLayout(btn_layout)
    
    def _load_insurance_names(self, combo: QComboBox):
        """Load insurance names from database into combo box."""
        try:
            from dmelogic.db.insurance import fetch_all_insurance
            rows = fetch_all_insurance(folder_path=self.folder_path)
            
            combo.clear()
            combo.addItem("")  # Empty option
            
            for row in rows:
                try:
                    name = row['name']
                except (KeyError, IndexError):
                    try:
                        name = row['insurance_name']
                    except (KeyError, IndexError):
                        continue
                    
                if name and name.strip():
                    combo.addItem(name)
                    
        except Exception as e:
            print(f"Error loading insurance names: {e}")
    
    def _convert_to_uppercase(self, combo: QComboBox, text: str):
        """Convert combo box text to uppercase."""
        if text != text.upper():
            line_edit = combo.lineEdit()
            cursor_pos = line_edit.cursorPosition()
            combo.setEditText(text.upper())
            line_edit.setCursorPosition(cursor_pos)


class ItemEditorDialog(QDialog):
    """Dialog for editing a single order item's quantity, modifiers, etc."""
    
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.folder_path = getattr(parent, "folder_path", None)
        self._pricing = line_pricing_for_order_item(item, folder_path=self.folder_path)
        self.setWindowTitle(f"Edit Item - {item.hcpcs_code}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setMinimumWidth(450)
        self._init_ui()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # Item info (read-only)
        info_group = QGroupBox("Item Information")
        info_layout = QFormLayout(info_group)
        
        self.hcpcs_label = QLabel(self.item.hcpcs_code)
        self.hcpcs_label.setStyleSheet("font-weight: bold;")
        info_layout.addRow("HCPCS:", self.hcpcs_label)
        
        self.desc_label = QLabel(self.item.description or "N/A")
        self.desc_label.setWordWrap(True)
        info_layout.addRow("Description:", self.desc_label)
        
        if self.item.item_number:
            self.item_num_label = QLabel(self.item.item_number)
            info_layout.addRow("Item #:", self.item_num_label)
        
        layout.addWidget(info_group)
        
        # Editable fields
        edit_group = QGroupBox("Edit Values")
        edit_layout = QFormLayout(edit_group)
        
        # Quantity
        self.qty_edit = QLineEdit(str(self.item.quantity))
        self.qty_edit.setMaximumWidth(80)
        edit_layout.addRow("Quantity:", self.qty_edit)
        
        # Refills
        self.refills_edit = QLineEdit(str(self.item.refills))
        self.refills_edit.setMaximumWidth(80)
        edit_layout.addRow("Refills:", self.refills_edit)
        
        # Days Supply
        self.days_edit = QLineEdit(str(self.item.days_supply))
        self.days_edit.setMaximumWidth(80)
        edit_layout.addRow("Days Supply:", self.days_edit)
        
        # Billing price
        cost_val = f"{self._pricing.unit_price:.2f}"
        self.cost_edit = QLineEdit(cost_val)
        self.cost_edit.setMaximumWidth(100)
        edit_layout.addRow("Bill Each ($):", self.cost_edit)

        self.amount_label = QLabel(f"${self._pricing.total:.2f}")
        edit_layout.addRow("Line Amount:", self.amount_label)
        
        layout.addWidget(edit_group)
        
        # Modifiers
        mod_group = QGroupBox("Billing Modifiers")
        mod_layout = QGridLayout(mod_group)
        
        mod_layout.addWidget(QLabel("Modifier 1:"), 0, 0)
        self.mod1_edit = QLineEdit(self.item.modifier1 or "")
        self.mod1_edit.setMaximumWidth(60)
        self.mod1_edit.setPlaceholderText("e.g. NU")
        mod_layout.addWidget(self.mod1_edit, 0, 1)
        
        mod_layout.addWidget(QLabel("Modifier 2:"), 0, 2)
        self.mod2_edit = QLineEdit(self.item.modifier2 or "")
        self.mod2_edit.setMaximumWidth(60)
        mod_layout.addWidget(self.mod2_edit, 0, 3)
        
        mod_layout.addWidget(QLabel("Modifier 3:"), 1, 0)
        self.mod3_edit = QLineEdit(self.item.modifier3 or "")
        self.mod3_edit.setMaximumWidth(60)
        mod_layout.addWidget(self.mod3_edit, 1, 1)
        
        mod_layout.addWidget(QLabel("Modifier 4:"), 1, 2)
        self.mod4_edit = QLineEdit(self.item.modifier4 or "")
        self.mod4_edit.setMaximumWidth(60)
        mod_layout.addWidget(self.mod4_edit, 1, 3)
        
        # Common modifiers hint
        hint_label = QLabel("Common: NU (new), RR (rental), UE (used), KX (medical necessity)")
        hint_label.setStyleSheet("color: gray; font-size: 10px;")
        mod_layout.addWidget(hint_label, 2, 0, 1, 4)
        
        layout.addWidget(mod_group)
        
        # Directions
        dir_group = QGroupBox("Directions")
        dir_layout = QVBoxLayout(dir_group)
        self.directions_edit = QTextEdit()
        self.directions_edit.setPlaceholderText("Enter item-specific directions...")
        self.directions_edit.setText(self.item.directions or "")
        self.directions_edit.setMaximumHeight(80)
        dir_layout.addWidget(self.directions_edit)
        layout.addWidget(dir_group)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("Save Changes")
        save_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        save_btn.clicked.connect(self.accept)
        btn_layout.addWidget(save_btn)
        
        layout.addLayout(btn_layout)
    
    def get_updates(self) -> dict:
        """Get the updated field values."""
        updates = {}
        
        qty_for_total = self.item.quantity or 0

        # Quantity
        try:
            qty = int(self.qty_edit.text().strip())
            qty_for_total = qty
            if qty != self.item.quantity:
                updates["qty"] = qty
        except ValueError:
            pass
        
        # Refills
        try:
            refills = int(self.refills_edit.text().strip())
            if refills != self.item.refills:
                updates["refills"] = refills
        except ValueError:
            pass
        
        # Days Supply
        try:
            days = int(self.days_edit.text().strip())
            if days != self.item.days_supply:
                updates["day_supply"] = days
        except ValueError:
            pass
        
        # Cost
        try:
            cost = Decimal(self.cost_edit.text().strip())
            saved_cost = self.item.cost_ea or Decimal("0")
            total = cost * Decimal(str(qty_for_total)) if qty_for_total else Decimal("0")
            if cost != saved_cost:
                updates["cost_ea"] = str(cost)
            if updates.get("qty") is not None or cost != saved_cost or total != (self.item.total_cost or Decimal("0")):
                updates["total"] = str(total)
        except:
            pass
        
        # Modifiers
        mod1 = self.mod1_edit.text().strip().upper() or None
        mod2 = self.mod2_edit.text().strip().upper() or None
        mod3 = self.mod3_edit.text().strip().upper() or None
        mod4 = self.mod4_edit.text().strip().upper() or None
        
        if mod1 != self.item.modifier1:
            updates["modifier1"] = mod1
        if mod2 != self.item.modifier2:
            updates["modifier2"] = mod2
        if mod3 != self.item.modifier3:
            updates["modifier3"] = mod3
        if mod4 != self.item.modifier4:
            updates["modifier4"] = mod4
        
        # Directions
        directions = self.directions_edit.toPlainText().strip() or None
        if directions != (self.item.directions or None):
            updates["directions"] = directions
        
        return updates
