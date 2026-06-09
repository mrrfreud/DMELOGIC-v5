"""
Order domain models.

Represents orders, order items, and related business logic for DME orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, TYPE_CHECKING
from dmelogic.config import debug_log

if TYPE_CHECKING:
    from dmelogic.db.models import BillingType


class OrderStatus(Enum):
    """Order status enumeration with business meaning."""
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
    
    @property
    def is_active(self) -> bool:
        """Check if order is in an active state."""
        return self not in (OrderStatus.CANCELLED, OrderStatus.DELIVERED)
    
    @property
    def can_be_edited(self) -> bool:
        """Check if order can be modified."""
        return self in (OrderStatus.PENDING, OrderStatus.ON_HOLD)
    
    @property
    def can_be_cancelled(self) -> bool:
        """Check if order can be cancelled."""
        return self.is_active


@dataclass
class OrderItem:
    """
    Single line item in a DME order.
    
    Represents a product/service being ordered with quantities,
    pricing, refill tracking, and authorization details.
    
    Business Rules:
    - Refills must be >= 0
    - Day supply must be > 0
    - Quantity must be > 0
    - Next refill date calculated from last_filled + day_supply
    """
    # Identity
    id: Optional[int] = None  # rowid in order_items table
    order_id: Optional[int] = None
    
    # Product identification
    hcpcs_code: str = ""
    description: str = ""
    item_number: str = ""
    
    # Quantities
    quantity: int = 1
    day_supply: int = 30
    refills: int = 0
    
    # Pricing
    cost_ea: Optional[Decimal] = None  # Unit cost
    total: Optional[Decimal] = None    # Line total
    
    # Authorization
    pa_number: str = ""  # Prior authorization
    
    # DME billing specifics
    is_rental: bool = False
    modifier1: Optional[str] = None
    modifier2: Optional[str] = None
    modifier3: Optional[str] = None
    modifier4: Optional[str] = None
    
    # Instructions
    directions: str = ""
    
    # Display ordering (for drag-and-drop reorder in ePACES helper)
    sort_order: int = 0
    
    # Item-level prescriber (for orders with multiple prescribers)
    prescriber_id: Optional[int] = None  # FK to prescribers table
    prescriber_name: Optional[str] = None  # Snapshot at time of order
    prescriber_npi: Optional[str] = None  # Snapshot at time of order
    
    # Refill tracking
    last_filled_date: Optional[date] = None
    
    # Legacy fields
    rx_no: str = ""  # Legacy RX number (now use rx_date in Order)
    
    def __post_init__(self):
        """Validate and normalize data after initialization."""
        # Ensure quantities are valid
        if self.quantity < 0:
            debug_log(f"OrderItem {self.hcpcs_code}: quantity < 0, setting to 1")
            self.quantity = 1
        
        if self.day_supply <= 0:
            debug_log(f"OrderItem {self.hcpcs_code}: day_supply <= 0, setting to 30")
            self.day_supply = 30
        
        if self.refills < 0:
            debug_log(f"OrderItem {self.hcpcs_code}: refills < 0, setting to 0")
            self.refills = 0
        
        # Convert pricing to Decimal if needed
        if self.cost_ea is not None and not isinstance(self.cost_ea, Decimal):
            try:
                self.cost_ea = Decimal(str(self.cost_ea))
            except Exception as e:
                debug_log(f"OrderItem {self.hcpcs_code}: Invalid cost_ea: {e}")
                self.cost_ea = None
        
        if self.total is not None and not isinstance(self.total, Decimal):
            try:
                self.total = Decimal(str(self.total))
            except Exception as e:
                debug_log(f"OrderItem {self.hcpcs_code}: Invalid total: {e}")
                self.total = None
    
    @property
    def next_refill_date(self) -> Optional[date]:
        """Calculate when this item is due for refill."""
        if self.last_filled_date and self.day_supply > 0:
            from datetime import timedelta
            return self.last_filled_date + timedelta(days=self.day_supply)
        return None
    
    @property
    def has_refills_remaining(self) -> bool:
        """Check if item has refills left."""
        return self.refills > 0
    
    @property
    def is_refillable(self) -> bool:
        """Check if item is eligible for refill."""
        if not self.has_refills_remaining:
            return False
        
        next_refill = self.next_refill_date
        if next_refill is None:
            return True  # No last filled date, assume refillable
        
        return date.today() >= next_refill
    
    def calculate_total(self) -> Optional[Decimal]:
        """Calculate line total from cost × quantity."""
        if self.cost_ea is None:
            return None
        return self.cost_ea * Decimal(str(self.quantity))
    
    def apply_pricing(self, unit_cost: Decimal) -> None:
        """Apply pricing and calculate total."""
        self.cost_ea = unit_cost
        self.total = self.calculate_total()
    
    @property
    def modifiers(self) -> List[str]:
        """Get list of non-empty modifiers."""
        return [
            m for m in (self.modifier1, self.modifier2, self.modifier3, self.modifier4)
            if m
        ]
    
    def use_refill(self, fill_date: Optional[date] = None) -> None:
        """
        Decrement refill count and update last filled date.
        
        Business Rule: Refill can only be used if refills > 0.
        """
        if self.refills <= 0:
            raise ValueError(f"No refills remaining for {self.hcpcs_code}")
        
        self.refills -= 1
        self.last_filled_date = fill_date or date.today()
        debug_log(f"OrderItem {self.id}: Refill used, {self.refills} remaining")
    
    def validate(self) -> List[str]:
        """
        Validate business rules for this order item.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        if not self.hcpcs_code or not self.hcpcs_code.strip():
            errors.append("HCPCS code is required")
        
        if self.quantity <= 0:
            errors.append("Quantity must be greater than 0")
        
        if self.day_supply <= 0:
            errors.append("Day supply must be greater than 0")
        
        if self.refills < 0:
            errors.append("Refills cannot be negative")
        
        # Business rule: Certain HCPCS codes require PA
        if self.hcpcs_code.startswith("E") and not self.pa_number:
            errors.append(f"Prior authorization required for {self.hcpcs_code}")
        
        return errors


@dataclass
class Order:
    """
    DME Order aggregate root.
    
    Represents a complete order with header information and line items.
    Enforces business rules and manages order lifecycle.
    
    Aggregate Pattern:
    - Order is the aggregate root
    - OrderItems are part of the aggregate
    - All item operations go through Order methods
    """
    # Identity
    id: Optional[int] = None
    
    # Dates
    rx_date: Optional[date] = None
    rx_date_2: Optional[date] = None  # Second RX date for additional prescriptions
    order_date: Optional[date] = None
    delivery_date: Optional[date] = None
    pickup_date: Optional[date] = None
    
    # Patient information
    patient_name: str = ""
    patient_first_name: str = ""
    patient_last_name: str = ""
    patient_dob: Optional[date] = None
    patient_phone: str = ""
    patient_address: str = ""
    
    # Prescriber information
    prescriber_name: str = ""
    prescriber_npi: str = ""
    prescriber_name_2: str = ""  # Second prescriber
    prescriber_npi_2: str = ""
    
    # Insurance information
    primary_insurance: str = ""
    primary_insurance_id: str = ""
    secondary_insurance: str = ""
    secondary_insurance_id: str = ""
    
    # Order management
    order_status: OrderStatus = OrderStatus.PENDING
    billing_selection: str = "Insurance"
    billing_type: Optional["BillingType"] = None  # Set by domain layer, not legacy DB
    # Hold scheduling
    hold_until_date: Optional[date] = None
    hold_resume_status: Optional[OrderStatus] = None
    hold_note: str = ""
    hold_reminder_sent: bool = False
    hold_set_at: Optional[datetime] = None
    
    # Clinical information
    icd_code_1: str = ""
    icd_code_2: str = ""
    icd_code_3: str = ""
    icd_code_4: str = ""
    icd_code_5: str = ""
    
    # Notes and directions
    notes: str = ""
    doctor_directions: str = ""
    epaces_alert: str = ""  # Popup note shown when EPACES helper opens
    
    # Order items (aggregate)
    items: List[OrderItem] = field(default_factory=list)
    
    # Legacy/tracking fields
    rx_no: str = ""  # Legacy field
    tracking_number: str = ""
    is_pickup: bool = False  # True if order is pickup, False if delivery
    
    # Billing tracking
    billed: bool = False
    paid: bool = False
    paid_date: Optional[date] = None
    
    def __post_init__(self):
        """Validate and normalize data after initialization."""
        # Ensure status is OrderStatus enum
        if isinstance(self.order_status, str):
            try:
                self.order_status = OrderStatus(self.order_status)
            except ValueError:
                debug_log(f"Invalid order status: {self.order_status}, defaulting to PENDING")
                self.order_status = OrderStatus.PENDING

        if self.hold_resume_status and isinstance(self.hold_resume_status, str):
            try:
                self.hold_resume_status = OrderStatus(self.hold_resume_status)
            except ValueError:
                self.hold_resume_status = None
        
        # Parse patient name if needed
        if not self.patient_first_name and not self.patient_last_name and self.patient_name:
            self._parse_patient_name()
        
        # Set default dates
        if self.order_date is None:
            self.order_date = date.today()
        if self.rx_date is None:
            self.rx_date = self.order_date
    
    def _parse_patient_name(self) -> None:
        """Parse 'LAST, FIRST' format into separate fields."""
        if "," in self.patient_name:
            parts = [p.strip() for p in self.patient_name.split(",", 1)]
            self.patient_last_name = parts[0]
            if len(parts) > 1:
                self.patient_first_name = parts[1]
        else:
            # No comma, treat as last name
            self.patient_last_name = self.patient_name.strip()
    
    @property
    def full_patient_name(self) -> str:
        """Get full patient name in 'LAST, FIRST' format."""
        if self.patient_first_name:
            return f"{self.patient_last_name}, {self.patient_first_name}"
        return self.patient_last_name
    
    @property
    def icd_codes(self) -> List[str]:
        """Get list of non-empty ICD-10 codes."""
        codes = [
            self.icd_code_1,
            self.icd_code_2,
            self.icd_code_3,
            self.icd_code_4,
            self.icd_code_5,
        ]
        return [c.strip() for c in codes if c and c.strip()]
    
    @property
    def order_total(self) -> Optional[Decimal]:
        """Calculate total order value from all items."""
        if not self.items:
            return None
        
        total = Decimal("0")
        for item in self.items:
            if item.total:
                total += item.total
        
        return total if total > Decimal("0") else None
    
    @property
    def item_count(self) -> int:
        """Get number of line items."""
        return len(self.items)
    
    @property
    def can_be_edited(self) -> bool:
        """Check if order can be modified based on status."""
        return self.order_status.can_be_edited
    
    @property
    def can_be_cancelled(self) -> bool:
        """Check if order can be cancelled based on status."""
        return self.order_status.can_be_cancelled
    
    @property
    def has_refillable_items(self) -> bool:
        """Check if any items are eligible for refill."""
        return any(item.is_refillable for item in self.items)
    
    def add_item(self, item: OrderItem) -> None:
        """
        Add an item to the order.
        
        Business Rule: Item must be valid before adding.
        """
        errors = item.validate()
        if errors:
            raise ValueError(f"Invalid order item: {'; '.join(errors)}")
        
        item.order_id = self.id
        self.items.append(item)
    
    def remove_item(self, item_id: int) -> bool:
        """
        Remove an item from the order by ID.
        
        Returns:
            True if item was removed, False if not found
        """
        original_count = len(self.items)
        self.items = [item for item in self.items if item.id != item_id]
        return len(self.items) < original_count
    
    def change_status(self, new_status: OrderStatus, reason: str = "") -> None:
        """
        Change order status with business rule validation.
        
        Business Rules:
        - Can't change from CANCELLED or DELIVERED
        - Status transitions must be valid
        """
        if not self.can_be_edited and new_status != OrderStatus.CANCELLED:
            raise ValueError(
                f"Cannot change status from {self.order_status.value} to {new_status.value}"
            )
        
        old_status = self.order_status
        self.order_status = new_status
        debug_log(f"Order {self.id}: Status changed {old_status.value} → {new_status.value}. Reason: {reason}")
    
    def validate(self) -> List[str]:
        """
        Validate business rules for this order.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Patient validation
        if not self.patient_last_name or not self.patient_last_name.strip():
            errors.append("Patient last name is required")
        
        # Prescriber validation
        if not self.prescriber_name or not self.prescriber_name.strip():
            errors.append("Prescriber name is required")
        
        if not self.prescriber_npi or not self.prescriber_npi.strip():
            errors.append("Prescriber NPI is required")
        elif len(self.prescriber_npi.strip()) != 10:
            errors.append("Prescriber NPI must be 10 digits")
        
        # Insurance validation (if billing to insurance)
        if self.billing_selection == "Insurance":
            if not self.primary_insurance or not self.primary_insurance.strip():
                errors.append("Primary insurance is required when billing to insurance")
        
        # Item validation
        if not self.items:
            errors.append("Order must have at least one item")
        
        for idx, item in enumerate(self.items, 1):
            item_errors = item.validate()
            for error in item_errors:
                errors.append(f"Item {idx} ({item.hcpcs_code}): {error}")
        
        # Business rule: At least one ICD-10 code required
        if not self.icd_codes:
            errors.append("At least one ICD-10 code is required")
        
        return errors
    
    def to_dict(self) -> dict:
        """Convert order to dictionary (for JSON serialization, etc.)."""
        return {
            "id": self.id,
            "rx_date": self.rx_date.isoformat() if self.rx_date else None,
            "order_date": self.order_date.isoformat() if self.order_date else None,
            "delivery_date": self.delivery_date.isoformat() if self.delivery_date else None,
            "patient_name": self.full_patient_name,
            "patient_dob": self.patient_dob.isoformat() if self.patient_dob else None,
            "patient_phone": self.patient_phone,
            "prescriber_name": self.prescriber_name,
            "prescriber_npi": self.prescriber_npi,
            "primary_insurance": self.primary_insurance,
            "order_status": self.order_status.value,
            "billing_selection": self.billing_selection,
            "icd_codes": self.icd_codes,
            "notes": self.notes,
            "item_count": self.item_count,
            "order_total": str(self.order_total) if self.order_total else None,
        }
