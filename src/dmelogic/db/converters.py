"""
Type conversion utilities for database operations.

Provides safe conversion functions with logging for data quality issues.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from ..config import debug_log
from .models import (
    Patient, PatientAddress, PatientInsurance,
    Prescriber, InventoryItem, Order, OrderItem
)


# ============================================================================
# Safe primitive conversions
# ============================================================================

def safe_int(value: Any, default: int = 0, field_name: str = "value") -> int:
    """
    Safely convert value to int with logging on failure.
    
    Handles common edge cases:
    - None -> default
    - Empty string -> default
    - "2x", "1.5", etc. -> default with warning
    """
    if value is None or value == "":
        return default
    
    try:
        # Handle string representations
        if isinstance(value, str):
            # Strip whitespace and common suffixes
            value = value.strip().rstrip("xX")
        
        # Try conversion
        if isinstance(value, float):
            return int(value)
        return int(value)
    except (TypeError, ValueError) as e:
        debug_log(f"⚠️ Bad int for '{field_name}': '{value}' (error: {e}), using default {default}")
        return default


def safe_decimal(value: Any, default: Decimal = Decimal("0.00"), field_name: str = "value") -> Decimal:
    """
    Safely convert value to Decimal with logging on failure.
    
    Handles common edge cases:
    - None -> default
    - Empty string -> default
    - "$123.45" -> 123.45
    - "N/A" -> default with warning
    """
    if value is None or value == "":
        return default
    
    try:
        # Handle string representations
        if isinstance(value, str):
            # Strip whitespace, currency symbols, commas
            value = value.strip().lstrip("$").replace(",", "")
        
        return Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as e:
        debug_log(f"⚠️ Bad decimal for '{field_name}': '{value}' (error: {e}), using default {default}")
        return default


def safe_date(value: Any, field_name: str = "date") -> Optional[date]:
    """
    Safely parse date from various formats.
    
    Supports:
    - ISO format: YYYY-MM-DD
    - US format: MM/DD/YYYY
    - Already a date object
    """
    if value is None or value == "":
        return None
    
    if isinstance(value, date):
        return value
    
    if isinstance(value, datetime):
        return value.date()
    
    if not isinstance(value, str):
        debug_log(f"⚠️ Unexpected date type for '{field_name}': {type(value)}")
        return None
    
    value = value.strip()
    if not value:
        return None
    
    # Try common formats
    formats = [
        "%Y-%m-%d",      # ISO: 2024-12-05
        "%m/%d/%Y",      # US: 12/05/2024
        "%d/%m/%Y",      # EU: 05/12/2024
        "%Y/%m/%d",      # Alt ISO: 2024/12/05
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    
    debug_log(f"⚠️ Could not parse date for '{field_name}': '{value}'")
    return None


def safe_datetime(value: Any, field_name: str = "datetime") -> Optional[datetime]:
    """Safely parse datetime from string or return existing datetime."""
    if value is None or value == "":
        return None
    
    if isinstance(value, datetime):
        return value
    
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    
    if not isinstance(value, str):
        return None
    
    value = value.strip()
    if not value:
        return None
    
    # Try common formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    
    debug_log(f"⚠️ Could not parse datetime for '{field_name}': '{value}'")
    return None


# ============================================================================
# Row to Model conversions
# ============================================================================

def safe_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """Safely get a value from a sqlite3.Row, returning default if key doesn't exist."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def row_to_patient(row: sqlite3.Row) -> Patient:
    """Convert database row to Patient model."""
    # Parse address
    address = PatientAddress(
        address=safe_get(row, "address"),
        city=safe_get(row, "city"),
        state=safe_get(row, "state"),
        zip_code=safe_get(row, "zip_code")
    )
    
    # Parse insurance
    insurance = PatientInsurance(
        primary_insurance=safe_get(row, "primary_insurance"),
        policy_number=safe_get(row, "policy_number"),
        group_number=safe_get(row, "group_number"),
        secondary_insurance=safe_get(row, "secondary_insurance"),
        secondary_insurance_id=safe_get(row, "secondary_insurance_id"),
        primary_insurance_id=safe_get(row, "policy_number")  # Alias: policy_number IS primary_insurance_id
    )
    
    return Patient(
        id=row["id"],
        first_name=row["first_name"] or "",
        last_name=row["last_name"] or "",
        dob=safe_date(safe_get(row, "dob"), "patient.dob"),
        phone=safe_get(row, "phone"),
        address=address,
        insurance=insurance,
        email=safe_get(row, "email"),
        emergency_contact=safe_get(row, "emergency_contact"),
        notes=safe_get(row, "notes"),
        created_date=safe_datetime(safe_get(row, "created_date")),
        updated_date=safe_datetime(safe_get(row, "updated_date"))
    )


def row_to_prescriber(row: sqlite3.Row) -> Prescriber:
    """Convert database row to Prescriber model."""
    return Prescriber(
        id=row["id"],
        first_name=safe_get(row, "first_name") or "",
        last_name=safe_get(row, "last_name") or "",
        npi_number=safe_get(row, "npi_number") or safe_get(row, "npi"),
        title=safe_get(row, "title"),
        phone=safe_get(row, "phone"),
        fax=safe_get(row, "fax"),
        specialty=safe_get(row, "specialty"),
        address=safe_get(row, "address"),
        city=safe_get(row, "city"),
        state=safe_get(row, "state"),
        zip_code=safe_get(row, "zip_code"),
        status=safe_get(row, "status", "Active"),
        notes=safe_get(row, "notes"),
        created_date=safe_datetime(safe_get(row, "created_date")),
        updated_date=safe_datetime(safe_get(row, "updated_date"))
    )


def row_to_inventory_item(row: sqlite3.Row) -> InventoryItem:
    """Convert database row to InventoryItem model."""
    return InventoryItem(
        item_id=row["item_id"],
        item_number=safe_get(row, "item_number") or "",
        hcpcs_code=safe_get(row, "hcpcs_code") or "",
        description=safe_get(row, "description") or "",
        category=safe_get(row, "category"),
        retail_price=safe_decimal(safe_get(row, "retail_price"), field_name="retail_price"),
        cost=safe_decimal(safe_get(row, "cost"), field_name="cost"),
        quantity_on_hand=safe_int(safe_get(row, "quantity_on_hand"), field_name="quantity_on_hand"),
        reorder_point=safe_int(safe_get(row, "reorder_point"), field_name="reorder_point"),
        manufacturer=safe_get(row, "manufacturer"),
        supplier=safe_get(row, "supplier"),
        rental_item=bool(safe_get(row, "rental_item", False)),
        rental_rate_monthly=safe_decimal(safe_get(row, "rental_rate_monthly")) if safe_get(row, "rental_rate_monthly") else None,
        notes=safe_get(row, "notes"),
        created_date=safe_datetime(safe_get(row, "created_date")),
        updated_date=safe_datetime(safe_get(row, "updated_date"))
    )


def row_to_order(row: sqlite3.Row) -> Order:
    """Convert database row to Order model."""
    from .models import OrderStatus, BillingType
    
    # Convert string status to enum
    status_str = safe_get(row, "order_status", "Pending")
    try:
        order_status = OrderStatus(status_str) if status_str else OrderStatus.PENDING
    except ValueError:
        debug_log(f"⚠️ Invalid order_status '{status_str}', using PENDING")
        order_status = OrderStatus.PENDING
    
    # Convert string billing type to enum
    billing_str = safe_get(row, "billing_selection") or safe_get(row, "billing_type", "Insurance")
    try:
        billing_type = BillingType(billing_str) if billing_str else BillingType.INSURANCE
    except ValueError:
        debug_log(f"⚠️ Invalid billing_type '{billing_str}', using INSURANCE")
        billing_type = BillingType.INSURANCE

    resume_status_raw = safe_get(row, "hold_resume_status")
    hold_resume_status = None
    if resume_status_raw:
        try:
            hold_resume_status = OrderStatus(resume_status_raw)
        except ValueError:
            hold_resume_status = None
    
    # Build icd_codes list from individual columns
    icd_codes = []
    for i in range(1, 6):
        code = safe_get(row, f"icd_code_{i}")
        if code and str(code).strip():
            icd_codes.append(str(code).strip())
    
    result = Order(
        id=row["id"],
        patient_id=safe_get(row, "patient_id"),  # FK for live patient lookup
        rx_date=safe_date(safe_get(row, "rx_date"), "rx_date"),
        rx_date_2=safe_get(row, "rx_date_2"),  # Secondary RX date as string
        order_date=safe_date(safe_get(row, "order_date"), "order_date"),
        patient_last_name=safe_get(row, "patient_last_name"),
        patient_first_name=safe_get(row, "patient_first_name"),
        patient_dob=safe_date(safe_get(row, "patient_dob"), "patient_dob"),
        patient_phone=safe_get(row, "patient_phone"),
        patient_address=safe_get(row, "patient_address"),
        prescriber_name=safe_get(row, "prescriber_name"),
        prescriber_npi=safe_get(row, "prescriber_npi"),
        prescriber_phone=safe_get(row, "prescriber_phone"),
        prescriber_fax=safe_get(row, "prescriber_fax"),
        prescriber_name_2=safe_get(row, "prescriber_name_2"),  # Secondary prescriber
        prescriber_npi_2=safe_get(row, "prescriber_npi_2"),
        billing_selection=billing_str,
        billing_type=billing_type,
        order_status=order_status,
        delivery_date=safe_get(row, "delivery_date"),
        pickup_date=safe_date(safe_get(row, "pickup_date"), "pickup_date"),
        primary_insurance=safe_get(row, "primary_insurance"),
        primary_insurance_id=safe_get(row, "primary_insurance_id"),
        secondary_insurance=safe_get(row, "secondary_insurance"),
        secondary_insurance_id=safe_get(row, "secondary_insurance_id"),
        tracking_number=safe_get(row, "tracking_number"),
        is_pickup=bool(safe_get(row, "is_pickup", 0)),
        billed=bool(safe_get(row, "billed", 0)),
        paid=bool(safe_get(row, "paid", 0)),
        paid_date=safe_date(safe_get(row, "paid_date"), "paid_date"),
        hold_until_date=safe_date(safe_get(row, "hold_until_date"), "hold_until_date"),
        hold_resume_status=hold_resume_status,
        hold_note=safe_get(row, "hold_note") or "",
        hold_reminder_sent=bool(safe_get(row, "hold_reminder_sent", 0)),
        hold_set_at=safe_datetime(safe_get(row, "hold_set_at")),
        notes=safe_get(row, "notes"),
        special_instructions=safe_get(row, "special_instructions"),
        epaces_alert=safe_get(row, "epaces_alert"),
        icd_codes=icd_codes,
        doctor_directions=safe_get(row, "doctor_directions"),
        parent_order_id=safe_get(row, "parent_order_id"),
        refill_number=safe_int(safe_get(row, "refill_number", 0)),
        is_locked=bool(safe_get(row, "is_locked", 0)),
        refill_completed=bool(safe_get(row, "refill_completed", 0)),
        refill_completed_at=safe_date(safe_get(row, "refill_completed_at"), "refill_completed_at"),
        created_date=safe_datetime(safe_get(row, "created_date")),
        updated_date=safe_datetime(safe_get(row, "updated_date"))
    )
    
    return result


def row_to_order_item(row: sqlite3.Row) -> OrderItem:
    """Convert database row to OrderItem model (dmelogic.db.models.OrderItem)."""
    return OrderItem(
        id=row["id"],
        order_id=row["order_id"],
        inventory_item_id=safe_get(row, "inventory_item_id"),
        hcpcs_code=safe_get(row, "hcpcs_code") or "",
        description=safe_get(row, "description") or "",
        item_number=safe_get(row, "item_number") or "",
        quantity=safe_int(safe_get(row, "qty") or safe_get(row, "quantity"), 1, "quantity"),
        refills=safe_int(safe_get(row, "refills"), 0, "refills"),
        days_supply=safe_int(safe_get(row, "day_supply") or safe_get(row, "days_supply"), 30, "days_supply"),
        cost_ea=safe_decimal(safe_get(row, "cost_ea")) if safe_get(row, "cost_ea") else None,
        total_cost=safe_decimal(safe_get(row, "total")) if safe_get(row, "total") else None,
        # DME billing specifics
        is_rental=bool(safe_get(row, "is_rental", 0)),
        modifier1=safe_get(row, "modifier1"),
        modifier2=safe_get(row, "modifier2"),
        modifier3=safe_get(row, "modifier3"),
        modifier4=safe_get(row, "modifier4"),
        pa_number=safe_get(row, "pa_number") or "",
        directions=safe_get(row, "directions"),
        sort_order=safe_int(safe_get(row, "sort_order"), 0, "sort_order"),
        last_filled_date=safe_date(safe_get(row, "last_filled_date"), "last_filled_date"),
        rental_month=safe_int(safe_get(row, "rental_month"), 0, "rental_month"),
        # Item-level prescriber (for multi-prescriber orders)
        prescriber_id=safe_get(row, "prescriber_id"),
        prescriber_name=safe_get(row, "prescriber_name"),
        prescriber_npi=safe_get(row, "prescriber_npi"),
    )
