"""
Domain models for DMELogic - typed representations of database entities.

These models provide type safety, business logic encapsulation, and cleaner
UI/repository separation compared to raw sqlite3.Row objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from enum import Enum


# ============================================================================
# Enums for DME-specific business rules
# ============================================================================

class OrderStatus(str, Enum):
    """
    Valid order statuses with DME workflow semantics.
    
    Union of legacy values and new workflow states used by the refactored UI.
    """
    PENDING = "Pending"
    PENDING_APPROVAL = "Pending Approval"
    VERIFIED = "Verified"
    SUBMITTED = "Submitted"
    APPROVED = "Approved"
    DELIVERED = "Delivered"
    SHIPPED = "Shipped"
    PICKED_UP = "Picked Up"
    CANCELLED = "Cancelled"
    ON_HOLD = "On Hold"
    UNBILLED = "Unbilled"
    BILLED = "Billed"
    PAID = "Paid"
    DOCS_NEEDED = "Docs Needed"
    READY = "Ready"
    DENIED = "Denied"
    CLOSED = "Closed"


class BillingType(str, Enum):
    """Billing selection types."""
    INSURANCE = "Insurance"
    CASH = "Cash"
    RENTAL = "Rental"
    MEDICARE = "Medicare"
    MEDICAID = "Medicaid"


class InventoryCategory(str, Enum):
    """Common DME inventory categories."""
    WHEELCHAIR = "Wheelchair"
    WALKER = "Walker"
    HOSPITAL_BED = "Hospital Bed"
    OXYGEN = "Oxygen"
    CPAP_BIPAP = "CPAP/BiPAP"
    DIABETIC_SUPPLIES = "Diabetic Supplies"
    ORTHOTIC = "Orthotic"
    PROSTHETIC = "Prosthetic"
    OTHER = "Other"


# ============================================================================
# Patient Models
# ============================================================================

@dataclass
class PatientInsurance:
    """Patient insurance information."""
    primary_insurance: Optional[str] = None
    policy_number: Optional[str] = None
    group_number: Optional[str] = None
    secondary_insurance: Optional[str] = None
    secondary_insurance_id: Optional[str] = None
    primary_insurance_id: Optional[str] = None

    # Address snapshot so order creation can build a full address
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None


@dataclass
class PatientAddress:
    """Patient address components."""
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    
    def format_full(self) -> str:
        """Return formatted full address string."""
        parts = [
            self.address or "",
            self.city or "",
            self.state or "",
            self.zip_code or ""
        ]
        return ", ".join([p for p in parts if p.strip()])


@dataclass
class Patient:
    """Patient domain model."""
    id: int
    first_name: str
    last_name: str
    dob: Optional[date] = None
    phone: Optional[str] = None
    address: Optional[PatientAddress] = None
    insurance: Optional[PatientInsurance] = None
    email: Optional[str] = None
    emergency_contact: Optional[str] = None
    notes: Optional[str] = None
    created_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    
    @property
    def full_name(self) -> str:
        """Return full name in 'LAST, FIRST' format."""
        return f"{self.last_name.upper()}, {self.first_name.upper()}"
    
    @property
    def age(self) -> Optional[int]:
        """Calculate age from DOB."""
        if not self.dob:
            return None
        today = date.today()
        return today.year - self.dob.year - (
            (today.month, today.day) < (self.dob.month, self.dob.day)
        )


# ============================================================================
# Prescriber Models
# ============================================================================

@dataclass
class Prescriber:
    """Prescriber/physician domain model."""
    id: int
    first_name: str
    last_name: str
    npi_number: Optional[str] = None
    title: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    specialty: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    status: str = "Active"
    notes: Optional[str] = None
    created_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    
    @property
    def full_name(self) -> str:
        """Return full name with title."""
        if self.title:
            return f"{self.title} {self.first_name} {self.last_name}"
        return f"{self.first_name} {self.last_name}"
    
    @property
    def display_name(self) -> str:
        """Return name in 'LAST, FIRST' format for order forms."""
        return f"{self.last_name.upper()}, {self.first_name.upper()}"


# ============================================================================
# Inventory Models
# ============================================================================

@dataclass
class InventoryItem:
    """Inventory item domain model with DME-specific business logic."""
    item_id: int
    item_number: str
    hcpcs_code: str
    description: str
    category: Optional[str] = None
    retail_price: Decimal = Decimal("0.00")
    cost: Decimal = Decimal("0.00")
    quantity_on_hand: int = 0
    reorder_point: int = 0
    manufacturer: Optional[str] = None
    supplier: Optional[str] = None
    rental_item: bool = False
    rental_rate_monthly: Optional[Decimal] = None
    notes: Optional[str] = None
    created_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    
    @property
    def is_low_stock(self) -> bool:
        """Check if item is below reorder point."""
        return self.quantity_on_hand <= self.reorder_point
    
    @property
    def margin(self) -> Decimal:
        """Calculate profit margin."""
        if self.retail_price <= 0:
            return Decimal("0.00")
        return ((self.retail_price - self.cost) / self.retail_price) * 100
    
    def calculate_line_total(self, quantity: int) -> Decimal:
        """Calculate total for given quantity."""
        return self.retail_price * Decimal(str(quantity))


@dataclass
class OrderItemInput:
    """Input DTO for creating order items - decoupled from UI types."""
    hcpcs: str = ""
    description: str = ""
    quantity: int = 1
    refills: int = 0
    days_supply: int = 30
    directions: Optional[str] = None
    item_number: Optional[str] = None
    cost_ea: Optional[Decimal] = None
    
    # DME billing specifics
    is_rental: bool = False
    modifier1: Optional[str] = None
    modifier2: Optional[str] = None
    modifier3: Optional[str] = None
    modifier4: Optional[str] = None
    
    # Agent placeholder flag — True when the agent could not fully identify the item
    is_placeholder: bool = False
    
    def normalized_modifiers(
        self,
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Normalize and clean modifier codes."""
        mods = [self.modifier1, self.modifier2, self.modifier3, self.modifier4]
        cleaned: list[Optional[str]] = []
        for m in mods:
            if not m:
                cleaned.append(None)
                continue
            m2 = m.strip().upper()
            # Treat literal "NONE" string as no modifier
            if m2 in ("", "NONE", "NULL", "N/A"):
                cleaned.append(None)
            else:
                cleaned.append(m2)
        return tuple((cleaned + [None] * 4)[:4])  # type: ignore[return-value]
    
    def validate(self, agent_context: bool = False) -> list[str]:
        """Validate order item business rules.
        
        When agent_context=True, relaxes rules to allow placeholder items
        where the agent couldn't fully identify the product.
        """
        errors = []
        
        if agent_context:
            # Agent items only need *something* — either HCPCS or description
            has_hcpcs = bool(self.hcpcs and self.hcpcs.strip())
            has_desc = bool(self.description and self.description.strip())
            if not has_hcpcs and not has_desc:
                errors.append("Item must have at least a HCPCS code or description")
        else:
            if not self.hcpcs or not self.hcpcs.strip():
                errors.append("HCPCS code is required")
        
        if self.quantity <= 0:
            errors.append(f"Invalid quantity: {self.quantity}")
        
        if self.refills < 0:
            errors.append(f"Invalid refills: {self.refills}")
        
        if self.days_supply <= 0:
            errors.append(f"Invalid days supply: {self.days_supply}")
        
        # Validate modifiers
        for m in self.normalized_modifiers():
            if m and len(m) > 2:
                errors.append(f"Invalid modifier '{m}' – must be 2 chars")
        
        return errors


@dataclass
class OrderInput:
    """
    Input DTO for creating orders - decoupled from UI types.
    Pure domain model with no PyQt dependencies.
    """
    patient_last_name: str
    patient_first_name: str
    patient_dob: Optional[str] = None
    patient_phone: Optional[str] = None
    patient_address: Optional[str] = None
    
    # FK support for refill processing
    patient_id: Optional[int] = None
    prescriber_id: Optional[int] = None
    insurance_id: Optional[int] = None
    
    prescriber_name: Optional[str] = None
    prescriber_npi: Optional[str] = None
    
    # Second prescriber (for orders with multiple RXs from different doctors)
    rx_date_2: Optional[str] = None
    prescriber_name_2: Optional[str] = None
    prescriber_npi_2: Optional[str] = None
    
    rx_date: Optional[str] = None
    order_date: Optional[str] = None
    delivery_date: Optional[str] = None
    
    # How the Rx arrived — "Fax", "Phone", "eRx", "Walk-in", etc.
    rx_origin: Optional[str] = None
    
    billing_type: str = BillingType.INSURANCE.value
    order_status: str = OrderStatus.PENDING.value
    
    primary_insurance: Optional[str] = None
    primary_insurance_id: Optional[str] = None
    
    # ICD-10 diagnosis codes (support both list and individual fields)
    icd_codes: list[str] = field(default_factory=list)
    icd_code_1: Optional[str] = None
    icd_code_2: Optional[str] = None
    icd_code_3: Optional[str] = None
    icd_code_4: Optional[str] = None
    icd_code_5: Optional[str] = None
    
    doctor_directions: Optional[str] = None
    notes: Optional[str] = None
    special_instructions: Optional[str] = None  # Delivery notes for driver
    
    items: list[OrderItemInput] = field(default_factory=list)
    
    # Refill tracking
    parent_order_id: Optional[int] = None
    refill_number: int = 0
    
    # Agent-created order — requires approval before entering the active workflow
    agent_created: bool = False
    
    # Validation overrides
    skip_icd_validation: bool = False  # Allow creating order without ICD-10 (pended)
    
    @property
    def patient_full_name(self) -> str:
        """Return formatted patient name."""
        return f"{self.patient_last_name}, {self.patient_first_name}"
    
    def validate(self) -> list[str]:
        """
        Validate order business rules before persistence.
        Returns list of validation errors (empty if valid).
        
        When agent_created=True most guardrails are bypassed:
        - Patient name can be blank or partial
        - ICD-10 codes are not required
        - Items may be placeholders (no HCPCS)
        - Rx date / insurance can be missing
        """
        errors = []
        
        if self.agent_created:
            # ----------------------------------------------------------
            # Agent context: very lenient — only require *something* to
            # identify the order and at least one (possibly placeholder) item.
            # ----------------------------------------------------------
            has_patient = bool(
                (self.patient_last_name and self.patient_last_name.strip())
                or (self.patient_first_name and self.patient_first_name.strip())
                or self.patient_id
            )
            if not has_patient:
                errors.append("At least a partial patient name or patient ID is required")
            
            if not self.items:
                errors.append("Order must contain at least one item (placeholders are OK)")
            
            # Validate each item with agent_context=True (relaxed)
            for idx, item in enumerate(self.items, 1):
                item_errors = item.validate(agent_context=True)
                for err in item_errors:
                    errors.append(f"Item {idx}: {err}")
            
            return errors
        
        # ==============================================================
        # Normal (non-agent) validation — full guardrails
        # ==============================================================
        
        # Required fields
        if not self.patient_last_name or not self.patient_last_name.strip():
            errors.append("Patient last name is required")
        
        if not self.patient_first_name or not self.patient_first_name.strip():
            errors.append("Patient first name is required")
        
        # DME-specific: at least one diagnosis code required for insurance
        if self.billing_type == BillingType.INSURANCE.value and not self.skip_icd_validation:
            # Check both list and individual fields
            has_icd = bool(self.icd_codes) or any([
                self.icd_code_1,
                self.icd_code_2,
                self.icd_code_3,
                self.icd_code_4,
                self.icd_code_5,
            ])
            if not has_icd:
                errors.append("At least one ICD-10 diagnosis code required for insurance billing")
        
        # Must have at least one item
        if not self.items:
            errors.append("Order must contain at least one item")
        
        # Validate each item
        for idx, item in enumerate(self.items, 1):
            item_errors = item.validate()
            for err in item_errors:
                errors.append(f"Item {idx}: {err}")
        
        return errors


@dataclass
class OrderItem:
    """
    Order item domain model - represents a single line item on an order.
    
    Used by: fetch_order_with_items, state portal view, 1500 claim logic,
    order editor, billing workflows, rental tracking.
    """
    id: int
    order_id: int
    inventory_item_id: Optional[int] = None  # FK to inventory (when added)
    
    # Item identification
    hcpcs_code: str = ""
    description: str = ""
    item_number: Optional[str] = None
    rx_no: Optional[str] = None
    
    # Quantities
    quantity: int = 1
    refills: int = 0
    days_supply: int = 30
    
    # Pricing (Decimal for precision)
    cost_ea: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    
    # Billing modifiers (up to 4 for HCFA-1500 and State Portal)
    modifier1: Optional[str] = None
    modifier2: Optional[str] = None
    modifier3: Optional[str] = None
    modifier4: Optional[str] = None
    
    # Additional info
    pa_number: Optional[str] = None
    directions: Optional[str] = None
    sort_order: int = 0  # Display order for drag-and-drop reordering
    last_filled_date: Optional[date] = None
    rental_month: int = 0  # Tracks which rental month (1-13+) for K modifiers
    
    # Item-level prescriber (for orders with multiple prescribers)
    prescriber_id: Optional[int] = None  # FK to prescribers table
    prescriber_name: Optional[str] = None  # Snapshot at time of order
    prescriber_npi: Optional[str] = None  # Snapshot at time of order
    
    # DME rental flag (stored in DB, drives billing logic)
    is_rental: bool = False
    
    @property
    def has_refills_remaining(self) -> bool:
        """Check if item has refills available."""
        return self.refills > 0
    
    @property
    def formatted_total(self) -> str:
        """Return formatted dollar amount for total."""
        if self.total_cost is None:
            return "$0.00"
        return f"${self.total_cost:.2f}"
    
    @property
    def all_modifiers(self) -> list[str]:
        """Get all non-empty modifiers as a list."""
        return [m for m in [self.modifier1, self.modifier2, self.modifier3, self.modifier4] if m]
    
    @property
    def modifiers(self) -> list[str]:
        """Alias for all_modifiers for API consistency."""
        return self.all_modifiers
    
    def get_rental_k_modifier(self) -> Optional[str]:
        """
        Get the appropriate K modifier based on rental month.
        
        Rental modifier progression:
        - Month 1: KH (Initial claim, first month rental)
        - Months 2-3: KI (Second and third rental months)
        - Months 4-13: KJ (Fourth through thirteenth rental months)
        - Month 14+: None (capped rental period)
        
        Returns:
            K modifier string or None if not applicable
        """
        if not self.is_rental or self.rental_month <= 0:
            return None
        
        if self.rental_month == 1:
            return 'KH'
        elif 2 <= self.rental_month <= 3:
            return 'KI'
        elif 4 <= self.rental_month <= 13:
            return 'KJ'
        else:
            # Beyond 13 months - typically ownership transfers
            return None


@dataclass
class Order:
    """
    Complete order domain model with snapshot fields and FK references.
    
    This is the foundational object for:
    - State portal mapping (box-by-box HCFA-1500)
    - Order editor UI
    - Billing/claims workflows
    - Audit views
    - Rental tracking
    - Print/document generation
    
    Key Design:
    - FKs (patient_id, prescriber_id, insurance_id) allow live lookups
    - Snapshot fields (*_at_order_time) preserve what was valid at order time
    - This supports compliance, audits, and historical accuracy
    
    Usage:
        order = fetch_order_with_items(order_id)
        # Now you have a typed, complete Order object
        # with all patient, prescriber, insurance, and item data
    """
    id: int
    
    # Foreign key references (live lookups)
    patient_id: Optional[int] = None
    prescriber_id: Optional[int] = None
    insurance_id: Optional[int] = None

    # Snapshot and legacy header fields (stored on the order row)
    patient_name_at_order_time: Optional[str] = None
    patient_dob_at_order_time: Optional[date] = None
    patient_address_at_order_time: Optional[str] = None
    prescriber_name_at_order_time: Optional[str] = None
    prescriber_npi_at_order_time: Optional[str] = None
    insurance_name_at_order_time: Optional[str] = None
    insurance_id_at_order_time: Optional[str] = None
    # Legacy flat fields (still present in orders table for UI bindings)
    patient_last_name: Optional[str] = None
    patient_first_name: Optional[str] = None
    patient_dob: Optional[date] = None
    patient_phone: Optional[str] = None
    patient_address: Optional[str] = None
    prescriber_name: Optional[str] = None
    prescriber_npi: Optional[str] = None
    prescriber_phone: Optional[str] = None
    prescriber_fax: Optional[str] = None
    prescriber_name_2: Optional[str] = None  # Secondary prescriber for multi-RX orders
    prescriber_npi_2: Optional[str] = None
    primary_insurance: Optional[str] = None
    primary_insurance_id: Optional[str] = None
    secondary_insurance: Optional[str] = None
    secondary_insurance_id: Optional[str] = None

    # Order dates
    rx_date: Optional[date] = None
    rx_date_2: Optional[str] = None  # Secondary RX date as string
    order_date: Optional[date] = None
    delivery_date: Optional[date] = None
    pickup_date: Optional[date] = None
    paid_date: Optional[date] = None

    # Status and billing
    order_status: OrderStatus = OrderStatus.PENDING
    billing_type: BillingType = BillingType.INSURANCE
    billing_selection: Optional[str] = None  # legacy string column
    hold_until_date: Optional[date] = None
    hold_resume_status: Optional[OrderStatus] = None
    hold_note: Optional[str] = None
    hold_reminder_sent: bool = False
    hold_set_at: Optional[datetime] = None

    # Refill tracking
    parent_order_id: Optional[int] = None
    refill_number: int = 0
    is_locked: bool = False
    refill_completed: bool = False  # True when this order has been processed as a refill source
    refill_completed_at: Optional[date] = None  # When the refill was processed

    # Fulfillment / tracking
    tracking_number: Optional[str] = None
    is_pickup: bool = False
    billed: bool = False
    paid: bool = False

    # Clinical information
    icd_codes: list[str] = field(default_factory=list)
    doctor_directions: Optional[str] = None

    # Items list
    items: list["OrderItem"] = field(default_factory=list)

    # Additional fields
    notes: Optional[str] = None
    special_instructions: Optional[str] = None  # Delivery notes for driver
    epaces_alert: Optional[str] = None  # Popup alert shown when EPACES helper opens
    created_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    
    # Lazy-loaded FK resolution methods
    def get_current_patient(self, folder_path: Optional[str] = None) -> Optional["Patient"]:
        """Fetch current patient record via FK (live lookup)."""
        if not self.patient_id:
            return None
        from .patients import fetch_patient_by_id
        return fetch_patient_by_id(self.patient_id, folder_path=folder_path)
    
    def get_current_prescriber(self, folder_path: Optional[str] = None) -> Optional["Prescriber"]:
        """Fetch current prescriber record via FK (live lookup)."""
        if not self.prescriber_id:
            return None
        from .prescribers import fetch_prescriber_by_id
        return fetch_prescriber_by_id(self.prescriber_id, folder_path=folder_path)
    
    @property
    def patient_full_name(self) -> str:
        """Return patient name in 'LAST, FIRST' format."""
        # First try the snapshot field
        if self.patient_name_at_order_time:
            return self.patient_name_at_order_time
        # Fall back to legacy flat fields
        if self.patient_last_name or self.patient_first_name:
            last = self.patient_last_name or ""
            first = self.patient_first_name or ""
            return f"{last}, {first}".strip(", ")
        return ""
    
    @property
    def has_items(self) -> bool:
        """Check if order has any items."""
        return len(self.items) > 0
    
    @property
    def total_items_count(self) -> int:
        """Return total quantity of all items."""
        return sum(item.quantity for item in self.items)
    
    @property
    def order_total(self) -> Decimal:
        """Calculate total order amount from all items."""
        return sum((item.total_cost or Decimal("0")) for item in self.items)
    
    @property
    def has_diagnosis_codes(self) -> bool:
        """Check if order has at least one diagnosis code."""
        return len(self.icd_codes) > 0
    
    @property
    def formatted_order_total(self) -> str:
        """Return formatted dollar amount for order total."""
        return f"${self.order_total:.2f}"


