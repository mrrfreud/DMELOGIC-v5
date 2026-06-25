from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import re

from PyQt6.QtCore import Qt, QDate, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QDateEdit,
    QTextEdit,
    QPushButton,
    QStackedWidget,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QSpinBox,
    QCheckBox,
    QHeaderView,
    QDialogButtonBox,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QScrollArea,
    QFileDialog,
    QListWidget,
    QGroupBox,
    QSizePolicy,
)
from PyQt6.QtCore import QStringListModel
from PyQt6.QtWidgets import QCompleter

from .prescriber_search_dialog import PrescriberSearchDialog
from dmelogic.db.patients import fetch_patient_insurance, fetch_patient_by_id
from dmelogic.security.auth import is_current_user_agent
from .inventory_search_dialog import InventorySearchDialog
from dmelogic.db.inventory import fetch_item_by_code  # auto-fill by HCPCS
from dmelogic.fee_schedule_enhancements import (
    setup_wizard_fee_columns,
    populate_wizard_row_fee_info,
    validate_qty_vs_max,
    DbFeeScheduleReader,
)


class LinkOrdersDialog(QDialog):
    """
    Dialog for selecting related orders to link for refill reminders.
    
    Shows a list of refill-eligible orders for the same patient,
    letting the user select which to link to this new order.
    """

    def __init__(self, orders: List[Dict[str, Any]], parent: QWidget = None):
        super().__init__(parent)
        self.setWindowTitle("Link Related Orders?")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setModal(False)  # Non-modal so user can work on main window
        self.resize(550, 400)

        self._orders = orders
        self._checks: List[QCheckBox] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header label
        header = QLabel("🔗 Link Related Orders")
        header.setStyleSheet("font-size: 14pt; font-weight: bold; color: #0078D4;")
        layout.addWidget(header)

        # Explanation
        label = QLabel(
            "This patient has other refill-eligible orders.\n"
            "You may link this new order to them for future refill reminders.\n\n"
            "Select the orders you want to link:"
        )
        label.setWordWrap(True)
        label.setStyleSheet("color: #333; margin-bottom: 8px;")
        layout.addWidget(label)

        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ccc; background: white; }")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(6)

        for o in orders:
            # Build display text
            text_parts = [o["display_number"]]
            if o.get("prescriber_name"):
                text_parts.append(f"– Dr. {o['prescriber_name']}")
            if o.get("refills_remaining"):
                text_parts.append(f"– {o['refills_remaining']} refill(s) left")
            if o.get("refill_due_date"):
                text_parts.append(f"(Due: {o['refill_due_date']})")
            line = " ".join(text_parts)

            cb = QCheckBox(line)
            cb.setChecked(False)
            cb.setStyleSheet("QCheckBox { padding: 4px; }")
            scroll_layout.addWidget(cb)
            self._checks.append(cb)

        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # Select All / Deselect All buttons
        select_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self._deselect_all)
        select_layout.addWidget(select_all_btn)
        select_layout.addWidget(deselect_all_btn)
        select_layout.addStretch(1)
        layout.addLayout(select_layout)

        # Action buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch(1)
        
        skip_btn = QPushButton("Skip")
        skip_btn.setStyleSheet("padding: 8px 20px;")
        skip_btn.clicked.connect(self.reject)
        
        link_btn = QPushButton("Link Selected")
        link_btn.setStyleSheet("padding: 8px 20px; background-color: #0078D4; color: white; font-weight: bold;")
        link_btn.clicked.connect(self.accept)
        link_btn.setDefault(True)
        
        buttons_layout.addWidget(skip_btn)
        buttons_layout.addWidget(link_btn)
        layout.addLayout(buttons_layout)

    def _select_all(self) -> None:
        for cb in self._checks:
            cb.setChecked(True)

    def _deselect_all(self) -> None:
        for cb in self._checks:
            cb.setChecked(False)

    def selected_orders(self) -> List[Dict[str, Any]]:
        """Return list of selected orders from the original list."""
        selected: List[Dict[str, Any]] = []
        for order, cb in zip(self._orders, self._checks):
            if cb.isChecked():
                selected.append(order)
        return selected


@dataclass
class OrderItem:
    hcpcs: str = ""
    description: str = ""
    quantity: int = 1
    refills: int = 0
    days_supply: int = 30
    # these are filled during save, not necessarily in the UI
    item_number: str = ""
    cost_each: float | None = None
    line_total: float | None = None
    directions: str = ""

    # NEW: rentals + 4 modifiers
    is_rental: bool = False           # True if this line is a rental
    modifier1: str = ""               # typically RR for rentals
    modifier2: str = ""               # KH / KI / KJ based on rental month
    modifier3: str = ""               # often KX for medical necessity
    modifier4: str = ""               # extra slot if needed
    modifiers: str = ""               # free-form modifiers; parsed into modifier1-4

    def __post_init__(self):
        # Accept legacy free-form modifiers input and map into explicit slots.
        if self.modifiers and not any([self.modifier1, self.modifier2, self.modifier3, self.modifier4]):
            parsed = _parse_modifiers(self.modifiers)
            for idx, mod in enumerate(parsed[:4]):
                setattr(self, f"modifier{idx+1}", mod)


@dataclass
class OrderWizardResult:
    patient_name: str
    patient_dob: str
    patient_phone: str
    order_date: str
    rx_date: str
    rx_origin: str
    prescriber_name: str
    prescriber_npi: str
    prescriber_phone: str
    prescriber_fax: str = ""
    
    # Second prescriber (for orders with multiple RXs from different doctors)
    rx_date_2: str = ""
    prescriber_name_2: str = ""
    prescriber_npi_2: str = ""
    prescriber_phone_2: str = ""
    prescriber_fax_2: str = ""
    
    items: List[OrderItem] = field(default_factory=list)

    # diagnosis codes (up to 5)
    icd_code_1: str = ""
    icd_code_2: str = ""
    icd_code_3: str = ""
    icd_code_4: str = ""
    icd_code_5: str = ""

    # MD directions for the order (will also be copied to each line)
    doctor_directions: str = ""

    delivery_date: str = ""
    billing_type: str = ""
    notes: str = ""

    # NEW: patient identifier for persistence
    patient_id: int = 0

    # NEW: insurance information resolved from patients.db
    insurance_name: str = ""
    insurance_kind: str = ""          # "Primary" | "Secondary" | ""
    insurance_policy_number: str = ""
    insurance_member_id: str = ""     # Member/subscriber ID
    insurance_group_number: str = ""

    # NEW: refill group ID for linking related orders
    refill_group_id: Optional[int] = None

    # NEW: Flag to create order in On Hold status
    on_hold: bool = False

    # NEW: Document attachment paths
    attachment_paths: List[str] = field(default_factory=list)

    # Existing incomplete order being resumed, if any
    resume_order_id: int = 0


def _k_modifier_for_month(month: int) -> str | None:
    """Return KH/KI/KJ modifier based on rental month."""
    if month <= 1:
        return "KH"
    if month <= 3:
        return "KI"
    if month <= 13:
        return "KJ"
    return None


def _parse_modifiers(raw: str) -> List[str]:
    """Split free-form modifier text into up to four codes."""
    if not raw:
        return []
    return [part.strip().upper() for part in re.split(r"[\s,\/]+", raw) if part.strip()][:4]


class OrderWizard(QDialog):
    """
    Multi-step New Order Wizard.

    Usage:
        wizard = OrderWizard(self, patient_id=123, folder_path="...", initial_patient=..., rx_context=...)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            data = wizard.result  # OrderWizardResult
    """

    order_saved_for_later = pyqtSignal(int)

    def __init__(
        self,
        parent=None,
        patient_id: int = 0,
        folder_path: Optional[str] = None,
        initial_patient: Optional[Dict[str, Any]] = None,
        rx_context: Optional[Dict[str, Any]] = None,
        resume_order_id: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Order Wizard")
        self.setModal(False)  # Non-modal so it can be minimized
        
        # Enable window controls: minimize, maximize, resize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        self.resize(1120, 640)
        self.setMinimumSize(900, 560)

        # Core patient identification
        self.patient_id = patient_id
        self.folder_path = folder_path
        self._patient = None  # Will hold full patient record from DB

        self.initial_patient = initial_patient or {}
        self.rx_context = rx_context or {}
        self.resume_order_id = int(resume_order_id or 0)
        self._resume_order_data: Optional[Dict[str, Any]] = None
        self._result: Optional[OrderWizardResult] = None
        self._items_page_first_visit = True  # Track first entry to Items page

        # Fee schedule reader for Max Units + PA Type columns
        try:
            p = parent
            while p and not hasattr(p, 'fee_reader'):
                p = p.parent() if hasattr(p, 'parent') and callable(p.parent) else None
            self._fee_reader = getattr(p, 'fee_reader', None) if p else None
            if not self._fee_reader:
                self._fee_reader = DbFeeScheduleReader(folder_path=folder_path)
        except Exception:
            self._fee_reader = DbFeeScheduleReader(folder_path=folder_path)
        
        # Refill group linking state
        self._refill_group_id: Optional[int] = None

        self._build_ui()
        if self.resume_order_id:
            self._load_incomplete_order_for_resume()
        # Insurance loading now happens in _build_patient_page() after combo is created
        
        # Check for linkable orders after patient is loaded
        if self.patient_id and not self.resume_order_id:
            QTimer.singleShot(500, self._maybe_link_orders_for_patient)

    # ------------------------- public API -------------------------

    @property
    def result(self) -> Optional[OrderWizardResult]:
        return self._result

    def _clean_resume_text(self, value: Any) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            return ""
        lowered = text.lower()
        if lowered in {"none", "null", "[none]", "[null]", "[incomplete]", "[unknown]"}:
            return ""
        return text

    def _resume_patient_name(self, order: Dict[str, Any]) -> str:
        name = self._clean_resume_text(order.get("patient_name"))
        if name:
            if "," in name:
                last, first = [self._clean_resume_text(part) for part in name.split(",", 1)]
                return f"{last}, {first}".strip(", ")
            return name
        last = self._clean_resume_text(order.get("patient_last_name"))
        first = self._clean_resume_text(order.get("patient_first_name"))
        return f"{last}, {first}".strip(", ")

    def _set_date_edit_from_value(self, edit: QDateEdit, value: Any) -> None:
        text = self._clean_resume_text(value)
        if not text:
            try:
                edit.setDate(edit.minimumDate())
            except Exception:
                pass
            return

        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m-%d-%Y", "%Y/%m/%d"):
            try:
                from datetime import datetime

                parsed = datetime.strptime(text.split(".")[0], fmt)
                edit.setDate(QDate(parsed.year, parsed.month, parsed.day))
                return
            except Exception:
                continue

    def _set_combo_text(self, combo: QComboBox, value: Any) -> None:
        text = self._clean_resume_text(value)
        if not text:
            return
        idx = combo.findText(text)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _safe_resume_int(self, value: Any, default: int = 0) -> int:
        try:
            if value in (None, ""):
                return default
            return int(str(value).strip())
        except Exception:
            return default

    def _load_incomplete_order_for_resume(self) -> None:
        from dmelogic.db.orders import (
            INCOMPLETE_ORDER_DRAFT_NOTE,
            fetch_incomplete_order_wizard_data,
        )

        data = fetch_incomplete_order_wizard_data(
            self.resume_order_id,
            folder_path=self.folder_path,
        )
        if not data:
            raise ValueError(f"Order ORD-{self.resume_order_id:03d} is not an incomplete order.")

        self._resume_order_data = data
        order = data.get("order") or {}
        items = data.get("items") or []
        self.setWindowTitle(f"Continue Incomplete Order ORD-{self.resume_order_id:03d}")
        self._items_page_first_visit = False

        self.patient_id = self._safe_resume_int(order.get("patient_id"), 0)
        self._refill_group_id = order.get("refill_group_id") or self._refill_group_id

        self.patient_name_edit.setText(self._resume_patient_name(order))
        self.patient_dob_edit.setText(self._clean_resume_text(order.get("patient_dob")))
        self.patient_phone_edit.setText(self._clean_resume_text(order.get("patient_phone")))

        if self.patient_id:
            self._load_patient_for_wizard()
            self._load_patient_insurance()
            if not self.patient_name_edit.text().strip() and self._patient:
                last = self._clean_resume_text(self._patient["last_name"] if "last_name" in self._patient.keys() else "")
                first = self._clean_resume_text(self._patient["first_name"] if "first_name" in self._patient.keys() else "")
                self.patient_name_edit.setText(f"{last}, {first}".strip(", "))

        self._set_date_edit_from_value(self.order_date_edit, order.get("order_date"))
        self._set_date_edit_from_value(self.rx_date_edit, order.get("rx_date"))
        self._set_combo_text(self.rx_origin_combo, order.get("rx_origin"))
        self.prescriber_name_edit.setText(self._clean_resume_text(order.get("prescriber_name")))
        self.prescriber_npi_edit.setText(self._clean_resume_text(order.get("prescriber_npi")))
        self.prescriber_phone_edit.setText(self._clean_resume_text(order.get("prescriber_phone")))
        self.prescriber_fax_edit.setText(self._clean_resume_text(order.get("prescriber_fax")))

        second_prescriber = any(
            self._clean_resume_text(order.get(key))
            for key in ("rx_date_2", "prescriber_name_2", "prescriber_npi_2")
        )
        self.prescriber2_checkbox.setChecked(second_prescriber)
        if second_prescriber:
            self._set_date_edit_from_value(self.rx_date_2_edit, order.get("rx_date_2"))
            self.prescriber_name_2_edit.setText(self._clean_resume_text(order.get("prescriber_name_2")))
            self.prescriber_npi_2_edit.setText(self._clean_resume_text(order.get("prescriber_npi_2")))
            self.prescriber_phone_2_edit.setText(self._clean_resume_text(order.get("prescriber_phone_2")))
            self.prescriber_fax_2_edit.setText(self._clean_resume_text(order.get("prescriber_fax_2")))

        for idx, edit in enumerate(getattr(self, "dx_edits", []), start=1):
            edit.setText(self._clean_resume_text(order.get(f"icd_code_{idx}")))
        self.doctor_directions_edit.setPlainText(self._clean_resume_text(order.get("doctor_directions")))

        self.items_table.setRowCount(0)
        for item in items:
            self._add_resume_item_row(item)

        self._set_date_edit_from_value(self.delivery_date_edit, order.get("delivery_date"))
        self._set_combo_text(self.billing_type_combo, order.get("billing_selection"))
        notes = self._clean_resume_text(order.get("notes"))
        notes = notes.replace(INCOMPLETE_ORDER_DRAFT_NOTE, "")
        self.notes_edit.setPlainText("\n".join(line.rstrip() for line in notes.splitlines()).strip())

        attached_raw = self._clean_resume_text(order.get("attached_rx_files"))
        self._attachment_paths = []
        self.attachment_list.clear()
        if attached_raw:
            from pathlib import Path

            for path_text in [part.strip() for part in attached_raw.replace("\n", ";").split(";") if part.strip()]:
                if path_text not in self._attachment_paths:
                    self._attachment_paths.append(path_text)
                    self.attachment_list.addItem(f"📄 {Path(path_text).name}")
        self._update_attachment_status()

        resume_page = self._infer_resume_page()
        self.stack.setCurrentIndex(resume_page)
        if resume_page == 3:
            self._sync_review_page()
        self._update_buttons()

    def _add_resume_item_row(self, item: Dict[str, Any]) -> None:
        self.add_item_row()
        row = self.items_table.rowCount() - 1
        self.items_table.setItem(row, 0, QTableWidgetItem(self._clean_resume_text(item.get("hcpcs_code"))))
        self.items_table.setItem(row, 1, QTableWidgetItem(self._clean_resume_text(item.get("description"))))

        qty = self.items_table.cellWidget(row, 2)
        if isinstance(qty, QSpinBox):
            qty.setValue(max(0, self._safe_resume_int(item.get("qty"), 0)))
        refills = self.items_table.cellWidget(row, 3)
        if isinstance(refills, QSpinBox):
            refills.setValue(max(0, self._safe_resume_int(item.get("refills"), 0)))
        days = self.items_table.cellWidget(row, 4)
        if isinstance(days, QSpinBox):
            days.setValue(max(1, self._safe_resume_int(item.get("day_supply"), 30)))

        self.items_table.setItem(row, 5, QTableWidgetItem(self._clean_resume_text(item.get("directions"))))

        rental_container = self.items_table.cellWidget(row, 6)
        rental_box = rental_container.findChild(QCheckBox) if rental_container else None
        if rental_box:
            rental_box.setChecked(bool(self._safe_resume_int(item.get("is_rental"), 0)))

        for offset, key in enumerate(("modifier1", "modifier2", "modifier3", "modifier4"), start=7):
            edit = self.items_table.cellWidget(row, offset)
            if isinstance(edit, QLineEdit):
                edit.setText(self._clean_resume_text(item.get(key)).upper())

    def _infer_resume_page(self) -> int:
        if not getattr(self, "patient_id", 0):
            return 0

        has_rx_date = self._date_edit_text(self.rx_date_edit) if hasattr(self, "rx_date_edit") else ""
        has_prescriber = bool(self.prescriber_name_edit.text().strip())
        has_contact = bool(self.prescriber_phone_edit.text().strip() or self.prescriber_fax_edit.text().strip())
        has_icd = any(edit.text().strip() for edit in getattr(self, "dx_edits", []))
        if not all([has_rx_date, has_prescriber, has_contact, has_icd]):
            return 1

        valid_item = False
        for item in self._collect_items(allow_incomplete=True):
            if (item.hcpcs or item.description) and item.quantity > 0:
                valid_item = True
                break
        if not valid_item:
            return 2

        return 3

    def _load_patient_for_wizard(self) -> None:
        """
        Fetch patient once for wizard – used for insurance + review.
        Stores full patient record in self._patient.
        """
        print(f"[DEBUG] _load_patient_for_wizard: patient_id={self.patient_id}, folder_path={self.folder_path}")
        
        if not self.patient_id:
            self._patient = None
            print("[DEBUG] No patient_id provided, skipping patient load")
            return

        try:
            from dmelogic.db.patients import fetch_patient_by_id
            self._patient = fetch_patient_by_id(
                self.patient_id,
                folder_path=self.folder_path,
            )
            print(f"[DEBUG] Loaded patient: {self._patient['first_name'] if self._patient else 'None'} {self._patient['last_name'] if self._patient else ''}")
        except Exception as e:
            print(f"OrderWizard: failed to load patient record: {e}")
            self._patient = None

    def _load_patient_insurance(self) -> None:
        """
        Populate Step 4 'Insurance' combo from patient's primary/secondary.
        If patient has no insurance, leave 'No insurance on file' and disabled.
        Uses self._patient loaded by _load_patient_for_wizard().
        """
        # Prepare storage for the options
        self._insurance_options: List[Dict[str, str]] = []

        # If the combo hasn't been built yet, bail out
        if not hasattr(self, "insurance_combo"):
            return

        # Clear and set default state
        self.insurance_combo.clear()
        self.insurance_combo.addItem("No insurance on file", {"kind": "none"})

        # If no patient loaded, disable combo
        if not self._patient:
            self.insurance_combo.setEnabled(False)
            return

        p = self._patient

        # Extract insurance fields from patient record
        # ADAPT THESE FIELD NAMES to match your patients table schema
        primary_name = (p["primary_insurance"] if "primary_insurance" in p.keys() else "") or ""
        primary_policy = (p["policy_number"] if "policy_number" in p.keys() else "") or ""
        primary_group = (p["group_number"] if "group_number" in p.keys() else "") or ""
        
        secondary_name = (p["secondary_insurance"] if "secondary_insurance" in p.keys() else "") or ""
        secondary_policy = (p["secondary_insurance_id"] if "secondary_insurance_id" in p.keys() else "") or ""

        # Add Primary insurance option
        if primary_name:
            data = {
                "kind": "Primary",
                "name": primary_name,
                "policy_number": primary_policy,
                "member_id": primary_policy,  # Often same as policy number
                "group_number": primary_group,
            }
            label = f"Primary – {primary_name}"
            if primary_policy:
                label += f" (Policy {primary_policy})"
            self.insurance_combo.addItem(label, data)
            self._insurance_options.append(data)

        # Add Secondary insurance option
        if secondary_name:
            data = {
                "kind": "Secondary",
                "name": secondary_name,
                "policy_number": secondary_policy,
                "member_id": secondary_policy,  # Often same as policy ID
                "group_number": "",
            }
            label = f"Secondary – {secondary_name}"
            if secondary_policy:
                label += f" (Policy {secondary_policy})"
            self.insurance_combo.addItem(label, data)
            self._insurance_options.append(data)

        # Enable combo if any insurance exists
        has_insurance = bool(primary_name or secondary_name)
        self.insurance_combo.setEnabled(has_insurance)

        # Default selection: primary if present, else "none"
        if primary_name:
            self.insurance_combo.setCurrentIndex(1)  # 0 = none, 1 = primary
        else:
            self.insurance_combo.setCurrentIndex(0)

    # ---- Patient search / add helpers ---------------------------------------

    def _on_search_patient(self) -> None:
        """Open the Find Patient dialog and apply the selection to Step 1."""
        try:
            from dmelogic.ui.find_patient_dialog import FindPatientDialog

            dialog = FindPatientDialog(self, folder_path=self.folder_path)

            # Seed dialog with whatever the user has already typed
            name = self.patient_name_edit.text().strip()
            dob = self.patient_dob_edit.text().strip()
            phone = self.patient_phone_edit.text().strip()
            if hasattr(dialog, "set_initial_query"):
                dialog.set_initial_query(name=name, dob=dob, phone=phone)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                patient = dialog.get_selected_patient() or {}
                if not patient:
                    return

                # Update internal patient id and reload DB-backed context
                self.patient_id = int(patient.get("id", 0) or 0)

                # Update visible fields on Step 1
                last = (patient.get("last_name") or "").strip()
                first = (patient.get("first_name") or "").strip()
                display_name = f"{last}, {first}".strip(", ")
                self.patient_name_edit.setText(display_name)
                self.patient_dob_edit.setText(patient.get("dob", ""))
                self.patient_phone_edit.setText(patient.get("phone", ""))

                # Refresh full patient record + insurance combo
                self._load_patient_for_wizard()
                self._load_patient_insurance()
        except Exception as e:
            print(f"Error searching for patient from wizard: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(
                self,
                "Find Patient",
                f"Could not open patient search: {e}",
            )

    def _on_add_patient(self) -> None:
        """Delegate to the main window's Add Patient workflow."""
        parent = self.parent()
        try:
            if parent and hasattr(parent, "add_new_patient"):
                parent.add_new_patient()
            else:
                QMessageBox.information(
                    self,
                    "Add Patient",
                    "Please add patients from the main Patients screen.",
                )
        except Exception as e:
            print(f"Error launching Add Patient from wizard: {e}")
            QMessageBox.warning(
                self,
                "Add Patient",
                f"Could not open Add Patient workflow: {e}",
            )

    def _maybe_link_orders_for_patient(self) -> None:
        """
        Check if patient has other refill-eligible orders and ask if we should link.
        
        Called after wizard opens with a known patient_id.
        Shows LinkOrdersDialog if there are existing orders that could be grouped
        for refill reminders.
        """
        if not self.patient_id:
            return

        try:
            from dmelogic.db.orders import find_refill_eligible_orders_for_patient
            from dmelogic.db.base import get_connection
            
            candidates = find_refill_eligible_orders_for_patient(
                self.patient_id,
                folder_path=self.folder_path,
            )
        except Exception as e:
            print(f"Error checking refill-eligible orders: {e}")
            return

        if not candidates:
            # No other refill-eligible orders, nothing to link
            self._refill_group_id = None
            return

        # Show linking dialog
        dlg = LinkOrdersDialog(candidates, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            # User clicked Skip / Cancel
            self._refill_group_id = None
            return

        selected = dlg.selected_orders()
        if not selected:
            self._refill_group_id = None
            return

        # Determine group id:
        # 1) If any selected order already has a refill_group_id, use that
        # 2) Otherwise, use the smallest selected order_id as root
        existing_group_ids = {
            o["refill_group_id"] for o in selected if o.get("refill_group_id")
        }
        if existing_group_ids:
            group_id = sorted(existing_group_ids)[0]
        else:
            group_id = min(o["order_id"] for o in selected)

        self._refill_group_id = group_id

        # Update selected orders that don't have a group id yet
        try:
            from dmelogic.db.base import get_connection
            conn = get_connection("orders.db", folder_path=self.folder_path)
            try:
                cur = conn.cursor()
                for o in selected:
                    if not o.get("refill_group_id"):
                        cur.execute(
                            "UPDATE orders SET refill_group_id = ? WHERE id = ?",
                            (group_id, o["order_id"]),
                        )
                conn.commit()
                print(f"[DEBUG] Linked {len(selected)} orders to refill_group_id={group_id}")
            finally:
                conn.close()
        except Exception as e:
            print(f"Error updating refill_group_id on existing orders: {e}")

    # ------------------------- UI layout -------------------------

    def _build_ui(self) -> None:
        """Build the overall wizard chrome: sidebar + content card."""
        from PyQt6.QtWidgets import QFrame

        # Root layout for the dialog
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        # Two-column layout: left step list, right content card
        main_row = QHBoxLayout()
        main_row.setSpacing(16)
        root_layout.addLayout(main_row, 1)

        # --- Left: step indicator sidebar ---------------------------------
        sidebar_frame = QFrame()
        sidebar_frame.setObjectName("wizardSidebar")
        sidebar_layout = QVBoxLayout(sidebar_frame)
        sidebar_layout.setContentsMargins(16, 16, 16, 16)
        sidebar_layout.setSpacing(8)

        sidebar_title = QLabel("New Order")
        sidebar_title.setProperty("role", "pageTitle")
        sidebar_title.setProperty("typo", "title")
        sidebar_layout.addWidget(sidebar_title)

        sidebar_subtitle = QLabel("4 simple steps")
        sidebar_subtitle.setProperty("role", "subtitle")
        sidebar_subtitle.setProperty("typo", "caption")
        sidebar_layout.addWidget(sidebar_subtitle)

        sidebar_layout.addSpacing(12)

        self._step_labels: list[QLabel] = []
        step_titles = ["Patient", "RX / Prescriber", "Items", "Review & Finish"]
        for idx, label in enumerate(step_titles):
            step_label = QLabel(f"{idx + 1}. {label}")
            step_label.setProperty("role", "wizardStep")
            step_label.setProperty("stepIndex", idx)
            self._step_labels.append(step_label)
            sidebar_layout.addWidget(step_label)

        sidebar_layout.addStretch(1)
        main_row.addWidget(sidebar_frame)

        # --- Right: main content card -------------------------------------
        card_frame = QFrame()
        card_frame.setObjectName("wizardCard")
        card_layout = QVBoxLayout(card_frame)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(12)

        # Title strip inside the card
        self.title_label = QLabel("Step 1 of 4  Patient")
        self.title_label.setProperty("role", "pageTitle")
        self.title_label.setProperty("typo", "section")
        card_layout.addWidget(self.title_label)

        # Stacked pages live inside the card
        self.stack = QStackedWidget()
        card_layout.addWidget(self.stack, 1)

        self._build_patient_page()
        self._build_rx_page()
        self._build_items_page()
        self._build_review_page()

        # Navigation buttons - Manual layout for proper button order on Windows
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch(1)  # Push buttons to the right
        
        self.back_button = QPushButton("Back")
        self.next_button = QPushButton("Next")
        self.finish_button = QPushButton("Finish")
        self.save_later_button = QPushButton("Save For Later")
        self.cancel_button = QPushButton("Cancel")

        # Button hierarchy using existing theme classes
        self.back_button.setProperty("class", "secondary")
        self.save_later_button.setProperty("class", "secondary")
        self.cancel_button.setProperty("class", "secondary")
        # Next and Finish use primary styling by default
        
        # Order: Back  Next  Finish  Cancel
        buttons_layout.addWidget(self.back_button)
        buttons_layout.addWidget(self.next_button)
        buttons_layout.addWidget(self.finish_button)
        buttons_layout.addWidget(self.save_later_button)
        buttons_layout.addWidget(self.cancel_button)
        buttons_layout.setSpacing(8)

        self.back_button.clicked.connect(self.go_back)
        self.next_button.clicked.connect(self.go_next)
        self.finish_button.clicked.connect(self.finish)
        self.save_later_button.clicked.connect(self.save_for_later)
        self.cancel_button.clicked.connect(self.reject)

        card_layout.addLayout(buttons_layout)
        main_row.addWidget(card_frame, 1)

        # Connect currentChanged AFTER buttons and step labels exist
        self.stack.currentChanged.connect(self._update_buttons)
        
        self._update_buttons()

    # ---- Patient page ----

    def _build_patient_page(self) -> None:
        page = QWidget()

        # Use a vertical layout so we can place the form
        # and then a dedicated Search/Add patient action row.
        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(40, 20, 40, 20)
        main_layout.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)

        self.patient_name_edit = QLineEdit()
        self.patient_name_edit.setPlaceholderText("LAST, FIRST")
        self.patient_dob_edit = QLineEdit()
        self.patient_dob_edit.setPlaceholderText("MM/DD/YYYY")
        self.patient_phone_edit = QLineEdit()
        self.patient_phone_edit.setPlaceholderText("(XXX) XXX-XXXX")

        # Pre-fill if we got an initial patient dict
        self.patient_name_edit.setText(self.initial_patient.get("name", ""))
        self.patient_dob_edit.setText(self.initial_patient.get("dob", ""))
        self.patient_phone_edit.setText(self.initial_patient.get("phone", ""))

        # Insurance selection (populated by _load_patient_insurance)
        self.insurance_combo = QComboBox()
        self.insurance_combo.addItem("No insurance on file", None)

        form.addRow("Patient name:", self.patient_name_edit)
        form.addRow("Date of birth:", self.patient_dob_edit)
        form.addRow("Phone:", self.patient_phone_edit)
        form.addRow("Insurance:", self.insurance_combo)

        main_layout.addLayout(form)

        # --- Search / Add patient action row ---------------------------------
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        actions_label = QLabel("Find or add patient:")
        actions_label.setStyleSheet("font-weight: 600;")
        actions_row.addWidget(actions_label)

        actions_row.addStretch(1)

        self.search_patient_btn = QPushButton("Search patients…")
        self.search_patient_btn.setToolTip("Search existing patients in the database")
        self.search_patient_btn.clicked.connect(self._on_search_patient)
        actions_row.addWidget(self.search_patient_btn)

        self.add_patient_btn = QPushButton("Add new patient…")
        self.add_patient_btn.setToolTip("Open the standard Add Patient workflow")
        self.add_patient_btn.clicked.connect(self._on_add_patient)
        actions_row.addWidget(self.add_patient_btn)

        main_layout.addLayout(actions_row)

        # Spacer so content stays toward the top
        main_layout.addStretch(1)

        self.stack.addWidget(page)
        
        # Load patient record and insurance after combo box is created
        self._load_patient_for_wizard()
        self._load_patient_insurance()

    # ---- RX / Prescriber page ----

    def _build_rx_page(self) -> None:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        # Keep Step 2 fully usable on smaller screens/scales.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 20, 40, 20)
        layout.setSpacing(10)

        # Patient info header
        patient_info_label = QLabel()
        patient_info_label.setStyleSheet("font-weight: 600; color: #2563eb; padding: 5px; background-color: #f0f9ff; border-radius: 3px;")
        layout.addWidget(patient_info_label)
        self.rx_patient_info_label = patient_info_label

        title = QLabel("RX / Prescriber")
        title.setStyleSheet("font-weight: 600; font-size: 11pt;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)

        # --- RX info ---
        # Order Date - defaults to today
        self.order_date_edit = QDateEdit()
        self.order_date_edit.setCalendarPopup(True)
        self.order_date_edit.setDisplayFormat("MM/dd/yyyy")
        self.order_date_edit.setDate(QDate.currentDate())
        form.addRow("Order Date:", self.order_date_edit)
        
        # RX Date - unfilled by default to force user to enter correct date
        self.rx_date_edit = QDateEdit()
        self.rx_date_edit.setCalendarPopup(True)
        self.rx_date_edit.setDisplayFormat("MM/dd/yyyy")
        self.rx_date_edit.setSpecialValueText("  ")  # Shows blank when date is null/minimum
        # Set minimum date and initialize to minimum to show as blank
        min_date = QDate(1900, 1, 1)
        self.rx_date_edit.setMinimumDate(min_date)
        self.rx_date_edit.setDate(min_date)  # Set to minimum to trigger special value text
        
        # Use event filter to set calendar to today's date when it opens
        class CalendarEventFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == event.Type.Show:
                    # When calendar popup shows, set it to today's date
                    if hasattr(obj, 'setSelectedDate'):
                        obj.setSelectedDate(QDate.currentDate())
                return False
        
        self._rx_calendar_filter = CalendarEventFilter()
        
        # Install event filter on the calendar widget
        def install_filter():
            calendar = self.rx_date_edit.calendarWidget()
            if calendar:
                calendar.installEventFilter(self._rx_calendar_filter)
        
        # Need to install filter after calendar is created (on first click)
        QTimer.singleShot(100, install_filter)
        
        form.addRow("RX Date *:", self.rx_date_edit)

        # RX Origin dropdown
        self.rx_origin_combo = QComboBox()
        self.rx_origin_combo.addItems(["", "FAXED", "WALK-IN", "E-PRESCRIBED", "PORTAL"])
        self.rx_origin_combo.setMinimumWidth(150)
        form.addRow("RX Origin:", self.rx_origin_combo)

        # --- Prescriber (free text; we also support search button elsewhere) ---
        self.prescriber_name_edit = QLineEdit()
        self.prescriber_name_edit.setPlaceholderText("Prescriber name (LAST, FIRST)")
        self.prescriber_npi_edit = QLineEdit()
        self.prescriber_phone_edit = QLineEdit()
        self.prescriber_phone_edit.setPlaceholderText("(555) 555-5555")
        self.prescriber_fax_edit = QLineEdit()
        self.prescriber_fax_edit.setPlaceholderText("(555) 555-5555")

        # Prescriber row with Search and Add buttons
        prescriber_layout = QHBoxLayout()
        prescriber_layout.setContentsMargins(0, 0, 0, 0)
        prescriber_layout.setSpacing(6)
        prescriber_layout.addWidget(self.prescriber_name_edit, 1)
        
        self.search_prescriber_btn = QPushButton("Search…")
        self.search_prescriber_btn.setToolTip("Search for prescriber in database")
        self.search_prescriber_btn.clicked.connect(self._on_search_prescriber)
        prescriber_layout.addWidget(self.search_prescriber_btn)
        
        self.add_prescriber_btn = QPushButton("➕ Add")
        self.add_prescriber_btn.setToolTip("Add new prescriber to database")
        self.add_prescriber_btn.clicked.connect(self._on_add_prescriber)
        prescriber_layout.addWidget(self.add_prescriber_btn)
        
        prescriber_widget = QWidget()
        prescriber_widget.setLayout(prescriber_layout)

        form.addRow("Prescriber:", prescriber_widget)
        form.addRow("NPI:", self.prescriber_npi_edit)
        form.addRow("Phone (for this order):", self.prescriber_phone_edit)
        form.addRow("Fax (for this order):", self.prescriber_fax_edit)

        # ---- Second Prescriber Section (Collapsible) ----
        self.prescriber2_checkbox = QCheckBox("Add 2nd Prescriber / RX Date")
        self.prescriber2_checkbox.setStyleSheet("font-weight: 500; color: #2563eb; margin-top: 10px;")
        self.prescriber2_checkbox.setToolTip("Check this to add a second prescriber for orders with multiple RXs from different doctors")
        form.addRow("", self.prescriber2_checkbox)

        # Container for second prescriber fields
        self.prescriber2_container = QWidget()
        prescriber2_form = QFormLayout(self.prescriber2_container)
        prescriber2_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        prescriber2_form.setHorizontalSpacing(18)
        prescriber2_form.setVerticalSpacing(8)
        prescriber2_form.setContentsMargins(0, 0, 0, 0)

        # RX Date 2
        self.rx_date_2_edit = QDateEdit()
        self.rx_date_2_edit.setCalendarPopup(True)
        self.rx_date_2_edit.setDisplayFormat("MM/dd/yyyy")
        self.rx_date_2_edit.setSpecialValueText("  ")
        min_date = QDate(1900, 1, 1)
        self.rx_date_2_edit.setMinimumDate(min_date)
        self.rx_date_2_edit.setDate(min_date)
        prescriber2_form.addRow("RX Date 2:", self.rx_date_2_edit)

        # Second prescriber name with Search and Add buttons
        self.prescriber_name_2_edit = QLineEdit()
        self.prescriber_name_2_edit.setPlaceholderText("Prescriber 2 name (LAST, FIRST)")
        self.prescriber_npi_2_edit = QLineEdit()
        self.prescriber_npi_2_edit.setPlaceholderText("NPI")
        self.prescriber_phone_2_edit = QLineEdit()
        self.prescriber_phone_2_edit.setPlaceholderText("(555) 555-5555")
        self.prescriber_fax_2_edit = QLineEdit()
        self.prescriber_fax_2_edit.setPlaceholderText("(555) 555-5555")

        prescriber2_layout = QHBoxLayout()
        prescriber2_layout.setContentsMargins(0, 0, 0, 0)
        prescriber2_layout.setSpacing(6)
        prescriber2_layout.addWidget(self.prescriber_name_2_edit, 1)

        self.search_prescriber_2_btn = QPushButton("Search…")
        self.search_prescriber_2_btn.setToolTip("Search for prescriber in database")
        self.search_prescriber_2_btn.clicked.connect(self._on_search_prescriber_2)
        prescriber2_layout.addWidget(self.search_prescriber_2_btn)

        self.add_prescriber_2_btn = QPushButton("➕ Add")
        self.add_prescriber_2_btn.setToolTip("Add new prescriber to database")
        self.add_prescriber_2_btn.clicked.connect(self._on_add_prescriber_2)
        prescriber2_layout.addWidget(self.add_prescriber_2_btn)

        prescriber2_widget = QWidget()
        prescriber2_widget.setLayout(prescriber2_layout)

        prescriber2_form.addRow("Prescriber 2:", prescriber2_widget)
        prescriber2_form.addRow("NPI 2:", self.prescriber_npi_2_edit)
        prescriber2_form.addRow("Phone 2 (for this order):", self.prescriber_phone_2_edit)
        prescriber2_form.addRow("Fax 2 (for this order):", self.prescriber_fax_2_edit)

        # Initially hide second prescriber fields
        self.prescriber2_container.setVisible(False)

        # Toggle visibility when checkbox is checked
        self.prescriber2_checkbox.toggled.connect(self.prescriber2_container.setVisible)

        form.addRow("", self.prescriber2_container)

        # ---- Diagnosis codes ----
        self.dx_edits: list[QLineEdit] = []
        for i in range(5):
            e = QLineEdit()
            e.setPlaceholderText(f"ICD-10 {i+1}")
            self.dx_edits.append(e)
            label = "ICD-10 1:" if i == 0 else f"ICD-10 {i+1}:"
            form.addRow(label, e)

        # ---- MD directions (order-level) ----
        self.doctor_directions_edit = QTextEdit()
        self.doctor_directions_edit.setPlaceholderText(
            "MD directions / sig (will be copied to each line item by default)."
        )
        self.doctor_directions_edit.setMinimumHeight(72)
        form.addRow("Directions:", self.doctor_directions_edit)

        # Ensure each row control remains fully visible with larger fonts/DPI.
        for w in [
            self.order_date_edit,
            self.rx_date_edit,
            self.rx_origin_combo,
            self.prescriber_name_edit,
            self.search_prescriber_btn,
            self.add_prescriber_btn,
            self.prescriber_npi_edit,
            self.prescriber_phone_edit,
            self.prescriber_fax_edit,
            self.prescriber2_checkbox,
            self.rx_date_2_edit,
            self.prescriber_name_2_edit,
            self.search_prescriber_2_btn,
            self.add_prescriber_2_btn,
            self.prescriber_npi_2_edit,
            self.prescriber_phone_2_edit,
            self.prescriber_fax_2_edit,
            *self.dx_edits,
        ]:
            try:
                w.setMinimumHeight(30)
                w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            except Exception:
                pass

        # Pre-fill from rx_context if available
        if self.rx_context.get("rx_date"):
            rx_date_str = self.rx_context.get("rx_date")
            try:
                from datetime import datetime
                date_obj = datetime.strptime(rx_date_str, "%m/%d/%Y")
                self.rx_date_edit.setDate(QDate(date_obj.year, date_obj.month, date_obj.day))
            except:
                pass
        self.prescriber_name_edit.setText(self.rx_context.get("prescriber_name", ""))
        self.prescriber_npi_edit.setText(self.rx_context.get("prescriber_npi", ""))
        self.prescriber_phone_edit.setText(self.rx_context.get("prescriber_phone", ""))
        self.prescriber_fax_edit.setText(self.rx_context.get("prescriber_fax", ""))

        layout.addLayout(form)
        layout.addStretch(1)

        self.stack.addWidget(page)
    
    def _on_search_prescriber(self) -> None:
        """Open prescriber search dialog."""
        try:
            # Get database path from parent window
            # Get folder path from parent for database resolution
            folder_path = getattr(self.parent(), 'current_folder', None)
            
            # Open search dialog with folder_path
            dialog = PrescriberSearchDialog(folder_path, self)
            
            # Register as child window if parent supports it
            parent = self.parent()
            if parent and hasattr(parent, 'register_child_window'):
                parent.register_child_window(dialog)
            
            # Seed with whatever the user already typed
            initial_query = self.prescriber_name_edit.text().strip()
            if initial_query and hasattr(dialog, 'set_initial_query'):
                dialog.set_initial_query(initial_query)
            
            # Connect accepted signal
            def on_accepted():
                prescriber = dialog.get_selected_prescriber()
                if prescriber:
                    # ✅ EXACT format expected by orders dialog: "LAST, FIRST"
                    display_name = f"{prescriber['last_name'].upper()}, {prescriber['first_name'].upper()}"
                    self.prescriber_name_edit.setText(display_name)
                    self.prescriber_npi_edit.setText(prescriber.get('npi') or "")
                    self.prescriber_phone_edit.setText(prescriber.get('phone') or "")
                    self.prescriber_fax_edit.setText(prescriber.get('fax') or "")
            
            dialog.accepted.connect(on_accepted)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        
        except Exception as e:
            print(f"Error opening prescriber search: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(
                self,
                "Search Error",
                f"Failed to open prescriber search: {e}"
            )
    
    def _on_add_prescriber(self) -> None:
        """Open the Add Prescriber dialog from parent window."""
        try:
            from dmelogic.ui import create_prescriber_dialog

            dialog = create_prescriber_dialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                # Get the prescriber data that was just added
                data = dialog.get_prescriber_data()

                ok, err = self._upsert_prescriber_from_dialog(data)
                if not ok:
                    QMessageBox.warning(self, "Prescriber Save", f"Prescriber form was filled, but save failed:\n{err}")

                # ✅ Store as "LAST, FIRST" to match orders UI
                display_name = f"{data['last_name'].upper()}, {data['first_name'].upper()}"

                self.prescriber_name_edit.setText(display_name)
                self.prescriber_npi_edit.setText(data.get('npi_number') or "")
                self.prescriber_phone_edit.setText(data.get('phone') or "")
                self.prescriber_fax_edit.setText(data.get('fax') or "")
                
                QMessageBox.information(
                    self, 
                    "Prescriber Added", 
                    f"Prescriber {display_name} has been added successfully."
                )
        except Exception as e:
            print(f"Error opening prescriber dialog: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Error", f"Failed to open prescriber dialog: {e}")

    def _upsert_prescriber_from_dialog(self, data: dict) -> tuple[bool, str]:
        """Insert/update a prescriber row based on dialog data."""
        try:
            from dmelogic.db.base import get_connection

            conn = get_connection("prescribers.db", folder_path=self.folder_path)
            cur = conn.cursor()

            first_name = (data.get('first_name') or '').strip().upper()
            last_name = (data.get('last_name') or '').strip().upper()
            npi_raw = (data.get('npi_number') or '').strip()
            npi_number = npi_raw if npi_raw else None

            if not first_name or not last_name:
                conn.close()
                return False, "First and last name are required."

            existing = None
            if npi_number:
                cur.execute("SELECT id FROM prescribers WHERE npi_number = ?", (npi_number,))
                existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    "SELECT id FROM prescribers WHERE UPPER(last_name)=? AND UPPER(first_name)=? LIMIT 1",
                    (last_name, first_name),
                )
                existing = cur.fetchone()

            values = (
                first_name,
                last_name,
                (data.get('title') or '').strip().upper(),
                npi_number,
                (data.get('license_number') or '').strip(),
                (data.get('specialty') or '').strip(),
                (data.get('phone') or '').strip(),
                (data.get('fax') or '').strip(),
                (data.get('email') or '').strip(),
                (data.get('practice_name') or '').strip(),
                (data.get('address_line1') or '').strip(),
                (data.get('address_line2') or '').strip(),
                (data.get('city') or '').strip(),
                (data.get('state') or '').strip(),
                (data.get('zip_code') or '').strip(),
                (data.get('dea_number') or '').strip(),
                (data.get('tax_id') or '').strip(),
                (data.get('status') or 'Active').strip(),
                (data.get('notes') or '').strip(),
            )

            if existing:
                cur.execute(
                    """
                    UPDATE prescribers
                    SET first_name=?, last_name=?, title=?, npi_number=?,
                        license_number=?, specialty=?, phone=?, fax=?, email=?,
                        practice_name=?, address_line1=?, address_line2=?, city=?,
                        state=?, zip_code=?, dea_number=?, tax_id=?, status=?,
                        notes=?, updated_date=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    values + (existing[0],),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO prescribers (
                        first_name, last_name, title, npi_number, license_number,
                        specialty, phone, fax, email, practice_name,
                        address_line1, address_line2, city, state, zip_code,
                        dea_number, tax_id, status, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )

            conn.commit()
            conn.close()
            return True, ""
        except Exception as e:
            return False, str(e)

    def _on_search_prescriber_2(self) -> None:
        """Open prescriber search dialog for second prescriber."""
        try:
            folder_path = getattr(self.parent(), 'current_folder', None)
            dialog = PrescriberSearchDialog(folder_path, self)

            parent = self.parent()
            if parent and hasattr(parent, 'register_child_window'):
                parent.register_child_window(dialog)

            initial_query = self.prescriber_name_2_edit.text().strip()
            if initial_query and hasattr(dialog, 'set_initial_query'):
                dialog.set_initial_query(initial_query)

            def on_accepted():
                prescriber = dialog.get_selected_prescriber()
                if prescriber:
                    display_name = f"{prescriber['last_name'].upper()}, {prescriber['first_name'].upper()}"
                    self.prescriber_name_2_edit.setText(display_name)
                    self.prescriber_npi_2_edit.setText(prescriber.get('npi') or "")
                    self.prescriber_phone_2_edit.setText(prescriber.get('phone') or "")
                    self.prescriber_fax_2_edit.setText(prescriber.get('fax') or "")

            dialog.accepted.connect(on_accepted)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        except Exception as e:
            print(f"Error opening prescriber search: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Search Error", f"Failed to open prescriber search: {e}")

    def _on_add_prescriber_2(self) -> None:
        """Open the Add Prescriber dialog for second prescriber."""
        try:
            from dmelogic.ui import create_prescriber_dialog

            dialog = create_prescriber_dialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                data = dialog.get_prescriber_data()

                ok, err = self._upsert_prescriber_from_dialog(data)
                if not ok:
                    QMessageBox.warning(self, "Prescriber Save", f"Prescriber form was filled, but save failed:\n{err}")

                display_name = f"{data['last_name'].upper()}, {data['first_name'].upper()}"

                self.prescriber_name_2_edit.setText(display_name)
                self.prescriber_npi_2_edit.setText(data.get('npi_number') or "")
                self.prescriber_phone_2_edit.setText(data.get('phone') or "")
                self.prescriber_fax_2_edit.setText(data.get('fax') or "")

                QMessageBox.information(
                    self,
                    "Prescriber Added",
                    f"Prescriber {display_name} has been added successfully."
                )
        except Exception as e:
            print(f"Error opening prescriber dialog: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Error", f"Failed to open prescriber dialog: {e}")

    # ---- Items page -------------------------------------------------

    def _build_items_page(self) -> None:
        page = QWidget()
        vbox = QVBoxLayout(page)
        # a bit more padding so the table feels centered and not glued to edges
        vbox.setContentsMargins(30, 20, 30, 20)
        vbox.setSpacing(10)

        # Patient info header
        patient_info_label = QLabel()
        patient_info_label.setStyleSheet(
            """
            QLabel {
                font-weight: 600;
                color: #2563eb;
                padding: 6px 10px;
                background-color: #f0f9ff;
                border-radius: 4px;
            }
            """
        )
        vbox.addWidget(patient_info_label)
        self.items_patient_info_label = patient_info_label

        title = QLabel("Order items (DME / HCPCS)")
        title.setStyleSheet("font-weight: 600; font-size: 12px; margin-top: 4px;")
        vbox.addWidget(title)

        # --- Items table -------------------------------------------------
        self.items_table = QTableWidget(0, 11)
        self.items_table.setHorizontalHeaderLabels(
            [
                "HCPCS / Item",  # 0
                "Description",   # 1
                "Qty",           # 2
                "Refills",       # 3
                "Days",          # 4
                "Directions",    # 5
                "Rental?",       # 6
                "Mod 1",         # 7
                "Mod 2",         # 8
                "Mod 3",         # 9
                "Mod 4",         # 10
            ]
        )
        self.items_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setMinimumHeight(160)   # keeps the row visually in the middle
        self.items_table.setStyleSheet(
            """
            QTableWidget {
                font-size: 10pt;
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                gridline-color: #e2e8f0;
                alternate-background-color: #f8fafc;
                selection-background-color: #e8f0fe;
                selection-color: #0f172a;
            }
            QTableWidget::item { padding: 4px 8px; }
            QHeaderView::section {
                font-weight: 600;
                color: #64748b;
                padding: 6px 8px;
                background-color: #f8fafc;
                border: none;
                border-bottom: 1px solid #e2e8f0;
            }
            """
        )

        # Cells with an explicit background (the orange "fill me in" tint) ignore
        # the stylesheet's selection color and fall back to the system palette
        # highlight (a hard blue that buries the text you're typing). Set the
        # palette highlight to a soft tint with dark text so cells stay readable
        # while selected/edited.
        from PyQt6.QtGui import QPalette
        _pal = self.items_table.palette()
        _pal.setColor(QPalette.ColorRole.Highlight, QColor("#e8f0fe"))
        _pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#0f172a"))
        self.items_table.setPalette(_pal)

        header = self.items_table.horizontalHeader()
        # Keep sensible resize behaviour
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.ResizeToContents)

        # Make sure small fields are wide enough for the spinboxes
        header.setMinimumSectionSize(60)
        try:
            self.items_table.setColumnWidth(0, 200)  # HCPCS / Item (room for full codes)
            self.items_table.setColumnWidth(1, 280)  # Description
            self.items_table.setColumnWidth(2, 70)   # Qty
            self.items_table.setColumnWidth(3, 70)   # Refills
            self.items_table.setColumnWidth(4, 70)   # Days
            self.items_table.setColumnWidth(5, 280)  # Directions
        except Exception:
            pass

        # Taller rows so the inputs and text aren't cramped.
        self.items_table.verticalHeader().setDefaultSectionSize(34)
        self.items_table.setWordWrap(False)

        # React when the HCPCS cell changes
        self.items_table.itemChanged.connect(self._on_item_cell_changed)

        # Add Max Units + PA Type columns from fee schedule
        try:
            setup_wizard_fee_columns(self.items_table)
        except Exception as _e:
            print(f"⚠️ Fee schedule columns setup skipped: {_e}")

        vbox.addWidget(self.items_table, 1)

        # --- Buttons row -------------------------------------------------
        buttons_layout = QHBoxLayout()

        self.search_inventory_button = QPushButton("Search inventory…")
        self.search_inventory_button.clicked.connect(self.search_inventory_item)
        buttons_layout.addWidget(self.search_inventory_button)

        self.add_item_button = QPushButton("Add item")
        self.remove_item_button = QPushButton("Remove selected")

        buttons_layout.addWidget(self.add_item_button)
        buttons_layout.addWidget(self.remove_item_button)

        # --- Agent-only: Add Placeholder Item button ---
        self.add_placeholder_button = QPushButton("\U0001f4cb Add Placeholder Item")
        self.add_placeholder_button.setToolTip(
            "Add a placeholder row — fill in a description for items you\n"
            "cannot identify. The admin will resolve it during approval."
        )
        self.add_placeholder_button.setStyleSheet(
            """
            QPushButton {
                background-color: #ff9800;
                color: white;
                font-weight: bold;
                padding: 4px 12px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #f57c00; }
            """
        )
        self.add_placeholder_button.clicked.connect(self.add_placeholder_item_row)
        # Only visible when agent is logged in
        self.add_placeholder_button.setVisible(is_current_user_agent())
        buttons_layout.addWidget(self.add_placeholder_button)

        buttons_layout.addStretch(1)

        vbox.addLayout(buttons_layout)

        self.add_item_button.clicked.connect(self.add_item_row)
        self.remove_item_button.clicked.connect(self.remove_selected_items)

        self.stack.addWidget(page)

    def _rental_month_for_row(self, row: int) -> int:
        """
        Placeholder: determine which rental month this order item represents.

        For now:
          - New rentals → 1st month (KH)
        Later:
          - Hook into refill logic / prior rentals to compute 2nd, 3rd, etc.
        """
        return 1

    def _k_modifier_for_month(self, month: int) -> str | None:
        """
        Map rental month → K* modifier.
          KH: initial claim & first month
          KI: second & third months
          KJ: fourth through thirteenth months
        """
        if month <= 1:
            return "KH"
        if month <= 3:
            return "KI"
        if month <= 13:
            return "KJ"
        return None

    def _on_rental_toggled(self, row: int, checked: bool) -> None:
        """
        When Rental? is checked, auto-fill:
          Mod1 = RR
          Mod2 = KH / KI / KJ (based on rental month)
          Mod3 = KX
        BUT:
          - Only fill if those fields are currently empty
          - User can still overwrite them
        When unchecked, clear all 4 modifiers.
        """
        # get current widgets
        mod1 = self.items_table.cellWidget(row, 7)
        mod2 = self.items_table.cellWidget(row, 8)
        mod3 = self.items_table.cellWidget(row, 9)
        mod4 = self.items_table.cellWidget(row, 10)

        if not all(isinstance(w, QLineEdit) for w in (mod1, mod2, mod3, mod4)):
            return

        if not checked:
            # Unchecked → clear all modifiers
            mod1.clear()
            mod2.clear()
            mod3.clear()
            mod4.clear()
            return

        # decide month + K-modifier
        month = self._rental_month_for_row(row)
        k_mod = self._k_modifier_for_month(month)

        # Only fill empty fields so user edits are preserved
        if not mod1.text().strip():
            mod1.setText("RR")
        if k_mod and not mod2.text().strip():
            mod2.setText(k_mod)
        if not mod3.text().strip():
            mod3.setText("KX")
        # mod4 left empty by default

    def add_item_row(self) -> None:
        row = self.items_table.rowCount()
        self.items_table.insertRow(row)

        # HCPCS + Description
        self.items_table.setItem(row, 0, QTableWidgetItem(""))
        self.items_table.setItem(row, 1, QTableWidgetItem(""))

        # Qty
        qty = QSpinBox()
        qty.setRange(0, 999)
        qty.setValue(0)
        qty.setSpecialValueText(" ")   # shows blank when 0
        qty.setMinimumWidth(60)
        self.items_table.setCellWidget(row, 2, qty)

        # Refills
        refills = QSpinBox()
        refills.setRange(0, 99)
        refills.setValue(0)
        refills.setSpecialValueText(" ")
        refills.setMinimumWidth(60)
        self.items_table.setCellWidget(row, 3, refills)

        # Days
        days = QSpinBox()
        days.setRange(1, 3650)
        days.setValue(30)
        days.setMinimumWidth(60)
        self.items_table.setCellWidget(row, 4, days)

        # Directions (pre-filled from MD directions if available)
        directions_text = ""
        if hasattr(self, "doctor_directions_edit"):
            directions_text = self.doctor_directions_edit.toPlainText().strip()

        directions_item = QTableWidgetItem(directions_text)
        self.items_table.setItem(row, 5, directions_item)

        # 6: Rental? checkbox
        from PyQt6.QtWidgets import QCheckBox
        chk_rental = QCheckBox()
        chk_rental.setTristate(False)
        chk_rental.setChecked(False)
        chk_rental.toggled.connect(lambda checked, r=row: self._on_rental_toggled(r, checked))
        # Center the checkbox in the cell
        chk_widget = QWidget()
        chk_layout = QHBoxLayout(chk_widget)
        chk_layout.addWidget(chk_rental)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        self.items_table.setCellWidget(row, 6, chk_widget)

        # helper for modifiers
        def _make_mod_edit() -> QLineEdit:
            edit = QLineEdit()
            edit.setMaxLength(2)
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return edit

        # 7..10: Mod 1–4
        self.items_table.setCellWidget(row, 7, _make_mod_edit())
        self.items_table.setCellWidget(row, 8, _make_mod_edit())
        self.items_table.setCellWidget(row, 9, _make_mod_edit())
        self.items_table.setCellWidget(row, 10, _make_mod_edit())

        # Populate Max Units + PA Type from fee schedule (deferred after HCPCS is set)
        hcpcs_text = self.items_table.item(row, 0)
        if hcpcs_text and hcpcs_text.text().strip():
            try:
                populate_wizard_row_fee_info(
                    self.items_table, row, hcpcs_text.text().strip(), self._fee_reader
                )
            except Exception:
                pass

        # Wire qty spinbox to validate against max units
        try:
            qty.valueChanged.connect(lambda _val, r=row: validate_qty_vs_max(
                self.items_table, r, self._fee_reader
            ))
        except Exception:
            pass

    def add_placeholder_item_row(self) -> None:
        """Add a placeholder row for agents who cannot identify the exact HCPCS."""
        row = self.items_table.rowCount()
        self.items_table.insertRow(row)

        # HCPCS = [TBD] (placeholder sentinel)
        hcpcs_item = QTableWidgetItem("[TBD]")
        hcpcs_item.setBackground(QBrush(QColor("#fff3e0")))
        self.items_table.setItem(row, 0, hcpcs_item)

        # Description — editable, pre-filled with prompt
        desc_item = QTableWidgetItem("")
        desc_item.setBackground(QBrush(QColor("#fff3e0")))
        self.items_table.setItem(row, 1, desc_item)

        # Qty = 1 by default
        qty = QSpinBox()
        qty.setRange(0, 999)
        qty.setValue(1)
        qty.setSpecialValueText(" ")
        qty.setMinimumWidth(60)
        self.items_table.setCellWidget(row, 2, qty)

        # Refills
        refills = QSpinBox()
        refills.setRange(0, 99)
        refills.setValue(0)
        refills.setSpecialValueText(" ")
        refills.setMinimumWidth(60)
        self.items_table.setCellWidget(row, 3, refills)

        # Days
        days = QSpinBox()
        days.setRange(1, 3650)
        days.setValue(30)
        days.setMinimumWidth(60)
        self.items_table.setCellWidget(row, 4, days)

        # Directions
        directions_text = ""
        if hasattr(self, "doctor_directions_edit"):
            directions_text = self.doctor_directions_edit.toPlainText().strip()
        self.items_table.setItem(row, 5, QTableWidgetItem(directions_text))

        # Rental checkbox
        chk_rental = QCheckBox()
        chk_rental.setTristate(False)
        chk_rental.setChecked(False)
        chk_rental.toggled.connect(lambda checked, r=row: self._on_rental_toggled(r, checked))
        chk_widget = QWidget()
        chk_layout = QHBoxLayout(chk_widget)
        chk_layout.addWidget(chk_rental)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        self.items_table.setCellWidget(row, 6, chk_widget)

        # Mod 1–4
        def _make_mod_edit() -> QLineEdit:
            edit = QLineEdit()
            edit.setMaxLength(2)
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return edit

        self.items_table.setCellWidget(row, 7, _make_mod_edit())
        self.items_table.setCellWidget(row, 8, _make_mod_edit())
        self.items_table.setCellWidget(row, 9, _make_mod_edit())
        self.items_table.setCellWidget(row, 10, _make_mod_edit())

        # Start editing the description cell so the agent can type immediately
        self.items_table.setCurrentCell(row, 1)
        self.items_table.editItem(self.items_table.item(row, 1))

    def _rental_month_for_row(self, row: int) -> int:
        """Determine rental month for a row. TODO: plug real rental logic."""
        # For now always treat as month 1
        return 1

    def _on_rental_toggled(self, row: int, checked: bool) -> None:
        """Handle rental checkbox toggle - auto-fill or clear modifiers."""
        # Get the checkbox widget (it's wrapped in a QWidget container)
        chk_widget = self.items_table.cellWidget(row, 6)
        if not chk_widget:
            return
        
        # Get modifier line edits
        m1 = self.items_table.cellWidget(row, 7)
        m2 = self.items_table.cellWidget(row, 8)
        m3 = self.items_table.cellWidget(row, 9)
        m4 = self.items_table.cellWidget(row, 10)

        if not all(isinstance(w, QLineEdit) for w in (m1, m2, m3, m4)):
            return

        if not checked:
            # uncheck → clear mods
            m1.clear()
            m2.clear()
            m3.clear()
            m4.clear()
            return

        month = self._rental_month_for_row(row)
        k_mod = _k_modifier_for_month(month)

        if not m1.text().strip():
            m1.setText("RR")
        if k_mod and not m2.text().strip():
            m2.setText(k_mod)
        if not m3.text().strip():
            m3.setText("KX")
        # m4 left empty by default

    def _on_item_selection_changed(self) -> None:
        """Update rental controls when item selection changes."""
        selected_rows = self.items_table.selectionModel().selectedRows()
        if not selected_rows:
            # No selection - disable controls
            self.rental_checkbox.setEnabled(False)
            self.rental_checkbox.setChecked(False)
            self.mod1_edit.setEnabled(False)
            self.mod2_edit.setEnabled(False)
            self.mod3_edit.setEnabled(False)
            self.mod4_edit.setEnabled(False)
            self.mod1_edit.clear()
            self.mod2_edit.clear()
            self.mod3_edit.clear()
            self.mod4_edit.clear()
            return
        
        # Get first selected row
        row = selected_rows[0].row()
        
        # Enable controls
        self.rental_checkbox.setEnabled(True)
        self.mod1_edit.setEnabled(True)
        self.mod2_edit.setEnabled(True)
        self.mod3_edit.setEnabled(True)
        self.mod4_edit.setEnabled(True)
        
        # Load data for this row
        if not hasattr(self, "_item_rental_data"):
            self._item_rental_data = {}
        
        if row not in self._item_rental_data:
            self._item_rental_data[row] = {
                "is_rental": False,
                "mod1": "",
                "mod2": "",
                "mod3": "",
                "mod4": ""
            }
        
        data = self._item_rental_data[row]
        
        # Block signals to prevent triggering save while loading
        self.rental_checkbox.blockSignals(True)
        self.rental_checkbox.setChecked(data["is_rental"])
        self.rental_checkbox.blockSignals(False)
        
        self.mod1_edit.setText(data["mod1"])
        self.mod2_edit.setText(data["mod2"])
        self.mod3_edit.setText(data["mod3"])
        self.mod4_edit.setText(data["mod4"])
    
    def _on_rental_checkbox_toggled(self, checked: bool) -> None:
        """Handle rental checkbox toggle - auto-fill or clear modifiers."""
        selected_rows = self.items_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        row = selected_rows[0].row()
        
        if not hasattr(self, "_item_rental_data"):
            self._item_rental_data = {}
        
        if row not in self._item_rental_data:
            self._item_rental_data[row] = {
                "is_rental": False,
                "mod1": "",
                "mod2": "",
                "mod3": "",
                "mod4": ""
            }
        
        self._item_rental_data[row]["is_rental"] = checked
        
        if checked:
            # Auto-fill modifiers if empty
            if not self.mod1_edit.text().strip():
                self.mod1_edit.setText("RR")
                self._item_rental_data[row]["mod1"] = "RR"
            
            # Determine K-modifier based on rental month
            month = self._rental_month_for_row(row)
            k_mod = self._k_modifier_for_month(month)
            if k_mod and not self.mod2_edit.text().strip():
                self.mod2_edit.setText(k_mod)
                self._item_rental_data[row]["mod2"] = k_mod
            
            if not self.mod3_edit.text().strip():
                self.mod3_edit.setText("KX")
                self._item_rental_data[row]["mod3"] = "KX"
        else:
            # Clear all modifiers
            self.mod1_edit.clear()
            self.mod2_edit.clear()
            self.mod3_edit.clear()
            self.mod4_edit.clear()
            self._item_rental_data[row]["mod1"] = ""
            self._item_rental_data[row]["mod2"] = ""
            self._item_rental_data[row]["mod3"] = ""
            self._item_rental_data[row]["mod4"] = ""
    
    def _save_modifier_data(self) -> None:
        """Save current modifier values to selected row data."""
        selected_rows = self.items_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        row = selected_rows[0].row()
        
        if not hasattr(self, "_item_rental_data"):
            self._item_rental_data = {}
        
        if row in self._item_rental_data:
            self._item_rental_data[row]["mod1"] = self.mod1_edit.text().strip()
            self._item_rental_data[row]["mod2"] = self.mod2_edit.text().strip()
            self._item_rental_data[row]["mod3"] = self.mod3_edit.text().strip()
            self._item_rental_data[row]["mod4"] = self.mod4_edit.text().strip()

    def _on_rental_toggled(self, row: int, checked: bool) -> None:
        """Legacy method - no longer used with new design."""
        pass


    def remove_selected_items(self) -> None:
        selected = self.items_table.selectionModel().selectedRows()
        if not selected:
            return
        # Remove rows in reverse order to maintain indices
        for idx in sorted([r.row() for r in selected], reverse=True):
            self.items_table.removeRow(idx)
    
    def _on_item_cell_changed(self, item: QTableWidgetItem) -> None:
        """Legacy method - no longer used with widget-based items."""
        pass
    
    def search_inventory_item(self) -> None:
        """
        Open the inventory search dialog and add a new row
        with the selected inventory data.
        """
        try:
            dlg = InventorySearchDialog(self)
            
            # Register as child window if parent supports it
            parent = self.parent()
            if parent and hasattr(parent, 'register_child_window'):
                parent.register_child_window(dlg)
            
            # Connect accepted signal to handle selection
            def on_accepted():
                selected = dlg.get_selected_item()
                if not selected:
                    return
                    
                # Add a new row
                row = self.items_table.rowCount()
                self.items_table.insertRow(row)
                
                # Populate with selected inventory data
                hcpcs = (
                    selected.get("hcpcs_code")
                    or selected.get("HCPCS")
                    or selected.get("item_code")
                    or ""
                )
                desc = selected.get("description") or selected.get("DESCRIPTION") or ""
                
                # Set HCPCS and Description
                self.items_table.setItem(row, 0, QTableWidgetItem(str(hcpcs)))
                self.items_table.setItem(row, 1, QTableWidgetItem(str(desc)))
                
                # Add spinboxes for Qty, Refills, Days
                qty = QSpinBox()
                qty.setRange(0, 999)
                qty.setValue(0)
                qty.setSpecialValueText(" ")
                qty.setMinimumWidth(60)
                self.items_table.setCellWidget(row, 2, qty)
                
                refills = QSpinBox()
                refills.setRange(0, 99)
                refills.setValue(0)
                refills.setSpecialValueText(" ")
                refills.setMinimumWidth(60)
                self.items_table.setCellWidget(row, 3, refills)
                
                days = QSpinBox()
                days.setRange(1, 3650)
                days.setValue(30)
                days.setMinimumWidth(60)
                self.items_table.setCellWidget(row, 4, days)
                
                # Directions
                directions_text = ""
                if hasattr(self, "doctor_directions_edit"):
                    directions_text = self.doctor_directions_edit.toPlainText().strip()
                self.items_table.setItem(row, 5, QTableWidgetItem(directions_text))

                # Populate Max Units + PA Type from fee schedule
                try:
                    populate_wizard_row_fee_info(
                        self.items_table, row, str(hcpcs), self._fee_reader
                    )
                except Exception:
                    pass

                # Wire qty spinbox to validate against max units
                try:
                    qty.valueChanged.connect(lambda _val, r=row: validate_qty_vs_max(
                        self.items_table, r, self._fee_reader
                    ))
                except Exception:
                    pass

                # Set focus to quantity field
                qty.setFocus()
                qty.selectAll()
            
            dlg.accepted.connect(on_accepted)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

        except Exception as e:
            QMessageBox.warning(
                self,
                "Inventory Search",
                f"Failed to open inventory search: {e}"
            )

    # ---- Review page ----

    def _build_review_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 20, 40, 20)
        layout.setSpacing(10)

        # Patient info header
        patient_info_label = QLabel()
        patient_info_label.setStyleSheet("font-weight: 600; color: #2563eb; padding: 5px; background-color: #f0f9ff; border-radius: 3px;")
        layout.addWidget(patient_info_label)
        self.review_patient_info_label = patient_info_label

        self.review_label = QLabel("Review order")
        self.review_label.setStyleSheet("font-weight: 600; font-size: 11pt;")
        layout.addWidget(self.review_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        # Delivery date – optional
        self.delivery_date_edit = QDateEdit()
        self.delivery_date_edit.setCalendarPopup(True)
        self.delivery_date_edit.setDisplayFormat("MM/dd/yyyy")
        # Use special value text so it *looks* blank / placeholder
        self.delivery_date_edit.setSpecialValueText("MM/DD/YYYY (optional)")
        self.delivery_date_edit.setMinimumDate(QDate(1900, 1, 1))
        self.delivery_date_edit.setDate(self.delivery_date_edit.minimumDate())

        # NEW: insurance selection (populated by _load_patient_insurance)
        self.review_insurance_combo = QComboBox()
        self.review_insurance_combo.addItem("No insurance on file", None)
        self.review_insurance_combo.setEnabled(False)  # Read-only on review page

        self.billing_type_combo = QComboBox()
        self.billing_type_combo.addItems(
            ["Insurance", "Cash", "Medicare", "Medicaid", "Private", "Other"]
        )

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Optional internal notes / special instructions.")

        # On Hold checkbox
        self.on_hold_checkbox = QCheckBox("Place order On Hold")
        self.on_hold_checkbox.setToolTip("Check this to create the order in 'On Hold' status instead of 'Pending'")

        form.addRow("Delivery date:", self.delivery_date_edit)
        form.addRow("Insurance:", self.review_insurance_combo)
        form.addRow("Billing type:", self.billing_type_combo)
        form.addRow("Notes:", self.notes_edit)
        form.addRow("", self.on_hold_checkbox)

        layout.addLayout(form)

        # ---------------------- Document Attachment Section ----------------------
        attach_group = QGroupBox("Document Attachments")
        attach_group.setStyleSheet("""
            QGroupBox {
                font-weight: 600;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                margin-top: 12px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        attach_layout = QVBoxLayout(attach_group)
        attach_layout.setSpacing(8)

        # Info label
        attach_info = QLabel("Attach RX/CMN documents to this order (required unless overridden)")
        attach_info.setStyleSheet("color: #6b7280; font-size: 9pt;")
        attach_layout.addWidget(attach_info)

        # Attachment list
        self.attachment_list = QListWidget()
        self.attachment_list.setMaximumHeight(100)
        self.attachment_list.setAlternatingRowColors(True)
        self.attachment_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #d1d5db;
                border-radius: 3px;
                background-color: #fafafa;
            }
            QListWidget::item {
                padding: 4px;
            }
            QListWidget::item:alternate {
                background-color: #f5f5f5;
            }
        """)
        attach_layout.addWidget(self.attachment_list)

        # Track attachment paths
        self._attachment_paths: List[str] = []

        # Buttons row
        attach_btn_layout = QHBoxLayout()
        attach_btn_layout.setSpacing(8)

        self.attach_btn = QPushButton("📎 Attach Document...")
        self.attach_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        self.attach_btn.clicked.connect(self._attach_document)
        attach_btn_layout.addWidget(self.attach_btn)

        self.remove_attach_btn = QPushButton("Remove Selected")
        self.remove_attach_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
        """)
        self.remove_attach_btn.clicked.connect(self._remove_attachment)
        attach_btn_layout.addWidget(self.remove_attach_btn)

        attach_btn_layout.addStretch(1)
        attach_layout.addLayout(attach_btn_layout)

        # Skip attachment checkbox (override)
        self.skip_attachment_checkbox = QCheckBox("Skip document attachment (create order without documents)")
        self.skip_attachment_checkbox.setToolTip(
            "Check this to create the order without attaching any documents.\n"
            "Warning: Orders without documentation may be incomplete."
        )
        self.skip_attachment_checkbox.setStyleSheet("color: #dc2626; font-weight: 500; margin-top: 5px;")
        attach_layout.addWidget(self.skip_attachment_checkbox)

        layout.addWidget(attach_group)
        # ---------------------- End Document Attachment Section ----------------------

        layout.addStretch(1)

        self.stack.addWidget(page)

    # ------------------------- navigation -------------------------

    def go_back(self) -> None:
        index = self.stack.currentIndex()
        if index > 0:
            self.stack.setCurrentIndex(index - 1)
        self._update_buttons()

    def go_next(self) -> None:
        if not self._validate_current_page():
            return

        index = self.stack.currentIndex()
        if index < self.stack.count() - 1:
            self.stack.setCurrentIndex(index + 1)
        self._update_buttons()
        
        # Set focus when entering RX page (page 1)
        if self.stack.currentIndex() == 1:
            # Use QTimer to defer focus after page transition completes
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.prescriber_name_edit.setFocus())
        
        # Auto-open inventory search when entering Items page (page 2) for the first time
        if self.stack.currentIndex() == 2 and self._items_page_first_visit:
            self._items_page_first_visit = False
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, self.search_inventory_item)
        
        # Update review page when entering it (page 3)
        if self.stack.currentIndex() == 3:
            self._sync_review_page()

    def get_prescriber_name(self) -> str:
        """
        Return the currently selected prescriber name, regardless of whether
        the UI uses a search line-edit or a combo box.
        """
        name = ""

        # Prefer the search line-edit if present
        try:
            if hasattr(self, "prescriber_name_edit") and self.prescriber_name_edit is not None:
                txt = self.prescriber_name_edit.text()
                if txt:
                    name = txt.strip()
        except Exception:
            name = ""

        # Fallback to the combo box if we still don't have a name
        if not name:
            try:
                if hasattr(self, "prescriber_combo") and self.prescriber_combo is not None:
                    txt = self.prescriber_combo.currentText()
                    if txt:
                        name = txt.strip()
            except Exception:
                pass

        return name
    
    def _sync_review_page(self) -> None:
        """
        Sync the review page widgets with data from earlier pages.
        Called when navigating to the review page (step 4).
        """
        # Sync insurance combo: copy from patient page to review page
        if hasattr(self, "insurance_combo") and hasattr(self, "review_insurance_combo"):
            self.review_insurance_combo.clear()
            
            # Copy all items from patient page combo to review combo
            for i in range(self.insurance_combo.count()):
                text = self.insurance_combo.itemText(i)
                data = self.insurance_combo.itemData(i)
                self.review_insurance_combo.addItem(text, data)
            
            # Set to same index as patient page
            self.review_insurance_combo.setCurrentIndex(self.insurance_combo.currentIndex())
    
    def _date_edit_text(self, edit: QDateEdit) -> str:
        try:
            selected = edit.date()
            minimum = edit.minimumDate()
            if not selected.isValid() or selected == minimum:
                return ""
            return selected.toString("MM/dd/yyyy")
        except Exception:
            return ""

    def _collect_insurance_snapshot(self) -> tuple[str, str, str, str, str]:
        insurance_name = ""
        insurance_kind = ""
        insurance_policy = ""
        insurance_member_id = ""
        insurance_group = ""

        if hasattr(self, "insurance_combo") and isinstance(self.insurance_combo, QComboBox):
            data = self.insurance_combo.currentData()
            if isinstance(data, dict):
                insurance_name = data.get("name", "") or ""
                insurance_kind = data.get("kind", "") or ""
                insurance_policy = data.get("policy_number", "") or ""
                insurance_member_id = data.get("member_id", "") or ""
                insurance_group = data.get("group_number", "") or ""
            else:
                txt = self.insurance_combo.currentText().strip()
                if txt and "no insurance on file" not in txt.lower():
                    insurance_name = txt

        return insurance_name, insurance_kind, insurance_policy, insurance_member_id, insurance_group

    def _build_result(self, items: Optional[List[OrderItem]] = None) -> OrderWizardResult:
        dx_values = [e.text().strip() for e in getattr(self, "dx_edits", [])]
        dx_values += [""] * (5 - len(dx_values))

        delivery_text = ""
        if hasattr(self, "delivery_date_edit"):
            delivery_text = self._date_edit_text(self.delivery_date_edit)

        insurance_name, insurance_kind, insurance_policy, insurance_member_id, insurance_group = self._collect_insurance_snapshot()

        rx_date_2 = ""
        if (
            hasattr(self, "prescriber2_checkbox")
            and self.prescriber2_checkbox.isChecked()
            and hasattr(self, "rx_date_2_edit")
        ):
            rx_date_2 = self._date_edit_text(self.rx_date_2_edit)

        return OrderWizardResult(
            patient_name=self.patient_name_edit.text().strip() if hasattr(self, "patient_name_edit") else "",
            patient_dob=self.patient_dob_edit.text().strip() if hasattr(self, "patient_dob_edit") else "",
            patient_phone=self.patient_phone_edit.text().strip() if hasattr(self, "patient_phone_edit") else "",
            order_date=self._date_edit_text(self.order_date_edit) if hasattr(self, "order_date_edit") else "",
            rx_date=self._date_edit_text(self.rx_date_edit) if hasattr(self, "rx_date_edit") else "",
            rx_origin=self.rx_origin_combo.currentText().strip() if hasattr(self, "rx_origin_combo") else "",
            prescriber_name=self.prescriber_name_edit.text().strip() if hasattr(self, "prescriber_name_edit") else "",
            prescriber_npi=self.prescriber_npi_edit.text().strip() if hasattr(self, "prescriber_npi_edit") else "",
            prescriber_phone=self.prescriber_phone_edit.text().strip() if hasattr(self, "prescriber_phone_edit") else "",
            prescriber_fax=self.prescriber_fax_edit.text().strip() if hasattr(self, "prescriber_fax_edit") else "",
            rx_date_2=rx_date_2,
            prescriber_name_2=self.prescriber_name_2_edit.text().strip() if (
                hasattr(self, "prescriber2_checkbox") and self.prescriber2_checkbox.isChecked()
            ) else "",
            prescriber_npi_2=self.prescriber_npi_2_edit.text().strip() if (
                hasattr(self, "prescriber2_checkbox") and self.prescriber2_checkbox.isChecked()
            ) else "",
            prescriber_phone_2=self.prescriber_phone_2_edit.text().strip() if (
                hasattr(self, "prescriber2_checkbox") and self.prescriber2_checkbox.isChecked()
            ) else "",
            prescriber_fax_2=self.prescriber_fax_2_edit.text().strip() if (
                hasattr(self, "prescriber2_checkbox") and self.prescriber2_checkbox.isChecked()
            ) else "",
            items=items if items is not None else self._collect_items(),
            icd_code_1=dx_values[0],
            icd_code_2=dx_values[1],
            icd_code_3=dx_values[2],
            icd_code_4=dx_values[3],
            icd_code_5=dx_values[4],
            doctor_directions=self.doctor_directions_edit.toPlainText().strip()
            if hasattr(self, "doctor_directions_edit")
            else "",
            delivery_date=delivery_text,
            billing_type=self.billing_type_combo.currentText() if hasattr(self, "billing_type_combo") else "",
            notes=self.notes_edit.toPlainText().strip() if hasattr(self, "notes_edit") else "",
            patient_id=self.patient_id,
            insurance_name=insurance_name,
            insurance_kind=insurance_kind,
            insurance_policy_number=insurance_policy,
            insurance_member_id=insurance_member_id,
            insurance_group_number=insurance_group,
            refill_group_id=self._refill_group_id,
            on_hold=self.on_hold_checkbox.isChecked() if hasattr(self, "on_hold_checkbox") else False,
            attachment_paths=self._attachment_paths.copy() if hasattr(self, "_attachment_paths") else [],
            resume_order_id=self.resume_order_id,
        )

    def save_for_later(self) -> None:
        reply = QMessageBox.question(
            self,
            "Save For Later",
            "Save this unfinished order as Incomplete so it can be finished later?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from dmelogic.db.orders import (
                create_incomplete_order_from_wizard_result,
                update_incomplete_order_from_wizard_result,
            )

            self._result = self._build_result(items=self._collect_items(allow_incomplete=True))
            if self.resume_order_id:
                order_id = update_incomplete_order_from_wizard_result(
                    self.resume_order_id,
                    self._result,
                    complete=False,
                    folder_path=self.folder_path,
                )
            else:
                order_id = create_incomplete_order_from_wizard_result(
                    self._result,
                    folder_path=self.folder_path,
                )
            self.order_saved_for_later.emit(order_id)
            QMessageBox.information(
                self,
                "Saved For Later",
                f"Order ORD-{order_id:03d} was saved as Incomplete.",
            )
            self.done(QDialog.DialogCode.Rejected)
        except Exception as e:
            print(f"Error saving incomplete order: {e}")
            QMessageBox.critical(
                self,
                "Save For Later",
                f"Could not save this unfinished order:\n{e}",
            )

    def finish(self) -> None:
        # Validate last page too
        if not self._validate_current_page():
            return

        # Require a real patient selection before finishing
        if not getattr(self, "patient_id", 0):
            QMessageBox.warning(
                self,
                "Patient Required",
                "Please use 'Search patients…' or 'Add new patient…' on Step 1 to select a real patient before creating an order.",
            )
            # Jump back to Patient step to make it obvious
            try:
                self.stack.setCurrentIndex(0)
                self._update_buttons()
            except Exception:
                pass
            return

        items = self._collect_items()
        if not items:
            QMessageBox.warning(self, "Order", "Add at least one item to the order.")
            return

        # Validate document attachment requirement
        has_attachments = bool(self._attachment_paths)
        skip_attachments = self.skip_attachment_checkbox.isChecked()
        
        if not has_attachments and not skip_attachments:
            QMessageBox.warning(
                self,
                "Documents Required",
                "Please attach at least one document (RX/CMN) to this order.\n\n"
                "If you need to create the order without documents, check the\n"
                "'Skip document attachment' checkbox."
            )
            return

        # Show confirmation dialog with items preview
        preview_text = "<h3>Order Items:</h3><ul>"
        for item in items:
            preview_text += f"<li><b>{item.hcpcs}</b> - {item.description}<br>"
            preview_text += f"Qty: {item.quantity}, Refills: {item.refills}, Days: {item.days_supply}</li>"
        preview_text += "</ul><br><b>Confirm and create this order?</b>"
        
        confirm = QMessageBox()
        confirm.setWindowTitle("Confirm Order")
        confirm.setTextFormat(Qt.TextFormat.RichText)
        confirm.setText(preview_text)
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QMessageBox.StandardButton.Yes)
        
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        self._result = self._build_result(items=items)
        self.accept()

    # ---------------------- Document Attachment Helpers ----------------------
    def _attach_document(self) -> None:
        """Open file dialog to select documents to attach."""
        from pathlib import Path
        
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach Documents",
            "",
            "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.doc *.docx);;All Files (*.*)"
        )
        
        if not file_paths:
            return
        
        for file_path in file_paths:
            if file_path not in self._attachment_paths:
                self._attachment_paths.append(file_path)
                # Show just filename in list
                display_name = Path(file_path).name
                self.attachment_list.addItem(f"📄 {display_name}")
        
        # Update visual feedback
        self._update_attachment_status()
    
    def _remove_attachment(self) -> None:
        """Remove selected attachment from the list."""
        current_row = self.attachment_list.currentRow()
        if current_row >= 0:
            self.attachment_list.takeItem(current_row)
            del self._attachment_paths[current_row]
            self._update_attachment_status()
    
    def _update_attachment_status(self) -> None:
        """Update visual cues based on attachment state."""
        has_attachments = bool(self._attachment_paths)
        
        # Update list background color based on state
        if has_attachments:
            self.attachment_list.setStyleSheet("""
                QListWidget {
                    border: 1px solid #22c55e;
                    border-radius: 3px;
                    background-color: #f0fdf4;
                }
                QListWidget::item {
                    padding: 4px;
                }
            """)
        else:
            self.attachment_list.setStyleSheet("""
                QListWidget {
                    border: 1px solid #d1d5db;
                    border-radius: 3px;
                    background-color: #fafafa;
                }
                QListWidget::item {
                    padding: 4px;
                }
                QListWidget::item:alternate {
                    background-color: #f5f5f5;
                }
            """)
    # ---------------------- End Document Attachment Helpers ----------------------

    def _update_buttons(self) -> None:
        index = self.stack.currentIndex()
        total = self.stack.count()

        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(index < total - 1)
        self.finish_button.setVisible(index == total - 1)

        # Update title
        titles = ["Patient", "RX / Prescriber", "Items", "Review & Finish"]
        self.title_label.setText(f"Step {index + 1} of {total} — {titles[index]}")

         # Highlight current step in the sidebar, if present
        if hasattr(self, "_step_labels"):
            for i, lbl in enumerate(self._step_labels):
                lbl.setProperty("active", i == index)
                # Refresh style so QSS reacts to the property change
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)

        # Update patient info headers on all pages
        patient_name = self.patient_name_edit.text().strip()
        patient_dob = self.patient_dob_edit.text().strip()
        
        if patient_name and patient_dob:
            info_text = f"Patient: {patient_name}  |  DOB: {patient_dob}"
            
            # Update RX page header
            if hasattr(self, 'rx_patient_info_label'):
                self.rx_patient_info_label.setText(info_text)
            
            # Update Items page header
            if hasattr(self, 'items_patient_info_label'):
                self.items_patient_info_label.setText(info_text)
            
            # Update Review page header
            if hasattr(self, 'review_patient_info_label'):
                self.review_patient_info_label.setText(info_text)

    # ------------------------- validation & collect -------------------------

    def _validate_current_page(self) -> bool:
        index = self.stack.currentIndex()

        # --- Step 1: Patient ---
        if index == 0:
            if not self.patient_name_edit.text().strip():
                QMessageBox.warning(self, "Order", "Enter patient name.")
                return False
            return True

        # --- Step 2: RX / Prescriber ---
        if index == 1:
            _agent = is_current_user_agent()

            # 1) RX Date is mandatory (skip for agents)
            if not _agent and hasattr(self, "rx_date_edit"):
                rx_date = self.rx_date_edit.date()
                min_date = self.rx_date_edit.minimumDate()
                if not rx_date.isValid() or rx_date == min_date:
                    QMessageBox.warning(
                        self,
                        "Order",
                        "Enter a valid RX Date.",
                    )
                    return False
            
            # 2) Prescriber is mandatory (skip for agents)
            if not _agent:
                prescriber_name = (
                    self.prescriber_name_edit.text().strip()
                    if hasattr(self, "prescriber_name_edit")
                    else ""
                )
                if not prescriber_name:
                    QMessageBox.warning(
                        self,
                        "Order",
                        "Select or enter a prescriber before continuing.",
                    )
                    return False

                prescriber_phone = self.prescriber_phone_edit.text().strip() if hasattr(self, "prescriber_phone_edit") else ""
                prescriber_fax = self.prescriber_fax_edit.text().strip() if hasattr(self, "prescriber_fax_edit") else ""
                if not prescriber_phone and not prescriber_fax:
                    QMessageBox.warning(
                        self,
                        "Order",
                        "Enter at least one prescriber contact method for this order (phone or fax).",
                    )
                    return False

            # 3) At least one ICD-10 is mandatory (skip for agents)
            if not _agent:
                icd_widgets = []

                # Check for dx_edits (the actual attribute name used in the wizard)
                if hasattr(self, "dx_edits"):
                    icd_widgets.extend(
                        w for w in getattr(self, "dx_edits") or [] if w is not None
                    )

                # Fallback: check for icd_edits or individual attribute names
                if not icd_widgets and hasattr(self, "icd_edits"):
                    icd_widgets.extend(
                        w for w in getattr(self, "icd_edits") or [] if w is not None
                    )

                possible_icd_attrs = [
                    "icd_code_1",
                    "icd_code_2",
                    "icd_code_3",
                    "icd_code_4",
                    "icd_code_5",
                    "icd10_1_edit",
                    "icd10_2_edit",
                    "icd10_3_edit",
                    "icd10_4_edit",
                    "icd10_5_edit",
                ]
                for attr in possible_icd_attrs:
                    if hasattr(self, attr):
                        w = getattr(self, attr)
                        if w is not None and w not in icd_widgets:
                            icd_widgets.append(w)

                # Enforce ICD-10 requirement - at least one must be filled
                has_icd = False
                if icd_widgets:
                    has_icd = any(
                        getattr(w, "text", lambda: "")().strip() for w in icd_widgets
                    )
                
                if not has_icd:
                    QMessageBox.warning(
                        self,
                        "Order",
                        "Enter at least one ICD-10 diagnosis code.",
                    )
                    return False

            return True

        # --- Step 3: Items ---
        if index == 2:
            # Validate that items have required fields filled
            validation_error = self._validate_items()
            if validation_error:
                QMessageBox.warning(self, "Order", validation_error)
                return False
            
            # Agents can proceed with placeholder items or even no items
            if not is_current_user_agent() and not self._collect_items():
                QMessageBox.warning(self, "Order", "Add at least one item.")
                return False
            return True

        # --- Step 4: Review ---
        return True

    def _validate_items(self) -> Optional[str]:
        """
        Validate that all items have required fields filled.
        Returns error message string if validation fails, None if valid.
        """
        _agent = is_current_user_agent()

        for row in range(self.items_table.rowCount()):
            hcpcs_item = self.items_table.item(row, 0)
            desc_item = self.items_table.item(row, 1)
            qty_widget = self.items_table.cellWidget(row, 2)
            
            hcpcs = hcpcs_item.text().strip() if hcpcs_item else ""
            desc = desc_item.text().strip() if desc_item else ""
            qty = qty_widget.value() if qty_widget else 0
            
            # Skip completely empty rows
            if not hcpcs and not desc:
                continue

            # Agents may use placeholder items ([TBD]) — only require a description
            if _agent and hcpcs == "[TBD]":
                if not desc:
                    return f"Item {row + 1}: Enter a description for the placeholder item."
                continue
            
            # If row has HCPCS or description, validate quantity
            if qty == 0:
                return f"Item {row + 1}: Quantity must be greater than 0."
        
        return None

    def _collect_items(self, allow_incomplete: bool = False) -> List[OrderItem]:
        items: List[OrderItem] = []
        
        for row in range(self.items_table.rowCount()):
            hcpcs_item = self.items_table.item(row, 0)
            desc_item = self.items_table.item(row, 1)
            qty_widget = self.items_table.cellWidget(row, 2)
            refills_widget = self.items_table.cellWidget(row, 3)
            days_widget = self.items_table.cellWidget(row, 4)
            directions_item = self.items_table.item(row, 5)
            
            hcpcs = hcpcs_item.text().strip() if hcpcs_item else ""
            desc = desc_item.text().strip() if desc_item else ""
            qty = qty_widget.value() if qty_widget else 0
            refills = refills_widget.value() if refills_widget else 0
            days = days_widget.value() if days_widget else 30
            directions = directions_item.text().strip() if directions_item else ""
            
            # Rental checkbox
            chk_widget = self.items_table.cellWidget(row, 6)
            is_rental = False
            if chk_widget:
                # The checkbox is wrapped in a QWidget container
                chk = chk_widget.findChild(QCheckBox)
                if chk:
                    is_rental = chk.isChecked()
            
            # Modifiers
            def _mod(col: int) -> str:
                w = self.items_table.cellWidget(row, col)
                return w.text().strip().upper() if isinstance(w, QLineEdit) else ""

            mod1 = _mod(7)
            mod2 = _mod(8)
            mod3 = _mod(9)
            mod4 = _mod(10)
            
            # Skip empty items
            if not hcpcs and not desc:
                continue
            
            # Skip items with qty = 0 unless saving an unfinished draft.
            if qty == 0 and not allow_incomplete:
                continue
            
            items.append(
                OrderItem(
                    hcpcs=hcpcs,
                    description=desc,
                    quantity=qty,
                    refills=refills,
                    days_supply=days,
                    directions=directions,
                    is_rental=is_rental,
                    modifier1=mod1,
                    modifier2=mod2,
                    modifier3=mod3,
                    modifier4=mod4,
                )
            )
        
        return items
