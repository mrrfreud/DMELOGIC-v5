"""
Order workflow and business rules for DME operations.

This module enforces status transitions and business rules to ensure
data integrity and compliance with DME billing requirements.
"""

from typing import Optional, Set
from .models import OrderStatus
from .base import get_connection  # exposed for tests that inject a mock


# ============================================================================
# Status Transition Rules
# ============================================================================

# Define allowed status transitions (state machine)
STATUS_TRANSITIONS: dict[OrderStatus, Set[OrderStatus]] = {
    # Saved-for-later orders that are intentionally unfinished.
    OrderStatus.INCOMPLETE: {
        OrderStatus.PENDING,
        OrderStatus.DOCS_NEEDED,
        OrderStatus.READY,
        OrderStatus.UNBILLED,
        OrderStatus.ON_HOLD,
        OrderStatus.CANCELLED,
    },

    # Agent-created orders awaiting human approval
    OrderStatus.PENDING_APPROVAL: {
        OrderStatus.PENDING,      # Approve → move to normal Pending
        OrderStatus.CANCELLED,    # Reject → cancel
    },

    # New orders can be pushed forward or skipped straight to billing when needed.
    OrderStatus.PENDING: {
        OrderStatus.DOCS_NEEDED,
        OrderStatus.READY,
        OrderStatus.SUBMITTED,
        OrderStatus.APPROVED,
        OrderStatus.DELIVERED,
        OrderStatus.SHIPPED,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.DOCS_NEEDED: {
        OrderStatus.READY,
        OrderStatus.PENDING,
        OrderStatus.UNBILLED,
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.READY: {
        OrderStatus.SUBMITTED,
        OrderStatus.APPROVED,
        OrderStatus.DELIVERED,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,  # allow direct billing when delivery already confirmed offline
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.SUBMITTED: {
        OrderStatus.APPROVED,
        OrderStatus.DELIVERED,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.APPROVED: {
        OrderStatus.DELIVERED,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.DELIVERED: {
        OrderStatus.SHIPPED,
        OrderStatus.PICKED_UP,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.PAID,       # Cash/direct payment
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.SHIPPED: {
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.PAID,       # Cash/direct payment
        OrderStatus.PICKED_UP,
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.PICKED_UP: {
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.PAID,       # Cash/direct payment
        OrderStatus.DENIED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.UNBILLED: {
        OrderStatus.BILLED,
        OrderStatus.PAID,      # Manual payment marking (cash, etc.)
        OrderStatus.DENIED,
        OrderStatus.ON_HOLD,
        OrderStatus.CANCELLED,
    },

    OrderStatus.BILLED: {
        OrderStatus.PENDING,
        OrderStatus.DOCS_NEEDED,
        OrderStatus.READY,
        OrderStatus.SUBMITTED,
        OrderStatus.APPROVED,
        OrderStatus.DELIVERED,
        OrderStatus.SHIPPED,
        OrderStatus.PICKED_UP,
        OrderStatus.UNBILLED,
        OrderStatus.PAID,      # Payment received
        OrderStatus.DENIED,
        OrderStatus.CLOSED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.DENIED: {
        OrderStatus.BILLED,      # Re-submit with corrections
        OrderStatus.CANCELLED,   # Give up
        OrderStatus.ON_HOLD,
    },

    OrderStatus.PAID: {
        # Allow corrections/reversals - paid orders can be moved to any status
        OrderStatus.PENDING,
        OrderStatus.DOCS_NEEDED,
        OrderStatus.READY,
        OrderStatus.SUBMITTED,
        OrderStatus.APPROVED,
        OrderStatus.DELIVERED,
        OrderStatus.SHIPPED,
        OrderStatus.PICKED_UP,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.DENIED,
        OrderStatus.CLOSED,
        OrderStatus.CANCELLED,
        OrderStatus.ON_HOLD,
    },

    OrderStatus.ON_HOLD: {
        # Can return to previous state
        OrderStatus.PENDING,
        OrderStatus.DOCS_NEEDED,
        OrderStatus.READY,
        OrderStatus.SUBMITTED,
        OrderStatus.APPROVED,
        OrderStatus.DELIVERED,
        OrderStatus.SHIPPED,
        OrderStatus.PICKED_UP,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.CANCELLED,
    },

    OrderStatus.CLOSED: {
        # Terminal state - no transitions
    },

    OrderStatus.CANCELLED: {
        # Allow reactivation of cancelled orders
        OrderStatus.PENDING,
        OrderStatus.DOCS_NEEDED,
        OrderStatus.READY,
        OrderStatus.SUBMITTED,
        OrderStatus.APPROVED,
        OrderStatus.DELIVERED,
        OrderStatus.SHIPPED,
        OrderStatus.PICKED_UP,
        OrderStatus.UNBILLED,
        OrderStatus.BILLED,
        OrderStatus.ON_HOLD,
    },
}


def can_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """
    Check if status transition is allowed.
    
    Args:
        from_status: Current order status
        to_status: Desired new status
    
    Returns:
        True if transition is allowed by business rules
    """
    # Per requirement: any status can change to any other status, regardless of
    # the current status. The STATUS_TRANSITIONS map is retained only for
    # reference/ordering, not enforcement.
    return True


def validate_transition(from_status: OrderStatus, to_status: OrderStatus) -> Optional[str]:
    """
    Validate status transition and return error message if invalid.
    
    Args:
        from_status: Current order status
        to_status: Desired new status
    
    Returns:
        None if valid, error message string if invalid
    """
    if can_transition(from_status, to_status):
        return None
    
    allowed_next = STATUS_TRANSITIONS.get(from_status, set())
    if allowed_next:
        allowed_str = ", ".join(s.value for s in allowed_next)
        return (
            f"Invalid transition: {from_status.value} → {to_status.value}. "
            f"Allowed next states: {allowed_str}"
        )
    else:
        return (
            f"Cannot transition from terminal state {from_status.value}. "
            f"Order is closed."
        )


def get_allowed_next_statuses(current_status: OrderStatus) -> Set[OrderStatus]:
    """
    Get all statuses that can be transitioned to from current status.
    
    Args:
        current_status: Current order status
    
    Returns:
        Set of allowed next statuses
    """
    # Any status is reachable from any status — return every status except the
    # current one. (PENDING_APPROVAL is an internal agent-intake state and is
    # not offered as a manual choice.)
    return {s for s in OrderStatus
            if s != current_status and s != OrderStatus.PENDING_APPROVAL}


def get_status_description(status: OrderStatus) -> str:
    """
    Get human-readable description of status.
    
    Args:
        status: Order status
    
    Returns:
        Description string explaining what the status means
    """
    descriptions = {
        OrderStatus.INCOMPLETE: "Order was saved before creation was finished",
        OrderStatus.PENDING: "Order received, awaiting initial processing",
        OrderStatus.DOCS_NEEDED: "Missing required documentation (CMN, POD, prescription, etc.)",
        OrderStatus.READY: "All documentation complete, approved for delivery/billing",
        OrderStatus.DELIVERED: "Equipment delivered to patient, POD signed",
        OrderStatus.BILLED: "Claim submitted to insurance/state portal",
        OrderStatus.DENIED: "Claim denied by payer, review required",
        OrderStatus.PAID: "Payment received from payer",
        OrderStatus.CLOSED: "Order complete, archived",
        OrderStatus.CANCELLED: "Order cancelled by provider or patient",
        OrderStatus.ON_HOLD: "Temporarily paused, awaiting action or information",
    }
    return descriptions.get(status, status.value)


def is_terminal_status(status: OrderStatus) -> bool:
    """
    Check if status is terminal (no further transitions).
    
    Args:
        status: Order status to check
    
    Returns:
        True if status is terminal (Closed only - Cancelled can be reactivated)
    """
    return status in {OrderStatus.CLOSED}


def requires_documentation(status: OrderStatus) -> bool:
    """
    Check if transitioning to this status requires complete documentation.
    
    Args:
        status: Target status
    
    Returns:
        True if all docs must be present
    """
    return status in {
        OrderStatus.READY,
        OrderStatus.DELIVERED,
        OrderStatus.BILLED,
    }


# ============================================================================
# Order Validation Rules
# ============================================================================

def validate_order_for_billing(order_data: dict) -> list[str]:
    """
    Validate that order has all required fields for billing.
    
    Args:
        order_data: Order dictionary with fields
    
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    # Required for insurance billing
    if not order_data.get("patient_id") and not order_data.get("patient_name_at_order_time"):
        errors.append("Patient information required for billing")
    
    if not order_data.get("prescriber_id") and not order_data.get("prescriber_name_at_order_time"):
        errors.append("Prescriber information required for billing")
    
    if not order_data.get("prescriber_npi_at_order_time"):
        errors.append("Prescriber NPI required for billing")
    
    if not order_data.get("rx_date"):
        errors.append("Prescription date required for billing")
    
    # Must have at least one ICD-10 code for insurance
    has_icd = any(
        order_data.get(f"icd_code_{i}")
        for i in range(1, 6)
    )
    if not has_icd:
        errors.append("At least one ICD-10 diagnosis code required for billing")
    
    # Must have items
    # (This would check order_items in a real implementation)
    
    return errors


def validate_order_for_delivery(order_data: dict) -> list[str]:
    """
    Validate that order has all required fields for delivery.
    
    Args:
        order_data: Order dictionary with fields
    
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    if not order_data.get("patient_address_at_order_time"):
        errors.append("Patient address required for delivery")
    
    if not order_data.get("delivery_date"):
        errors.append("Delivery date must be scheduled")
    
    return errors


# ============================================================================
# Status Update with Validation
# ============================================================================

def update_order_status_validated(
    order_id: int,
    new_status: OrderStatus | str,
    note: str = "",
    folder_path: Optional[str] = None,
    current_status: Optional[OrderStatus | str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Update order status with workflow validation.

    Args:
        order_id: Order ID to update
        new_status: Desired new status (enum or string)
        note: Optional status note (ignored here, kept for compatibility)
        folder_path: Optional database folder path
        current_status: Optional current status override; when not provided, read from DB

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    # Normalize target status
    try:
        to_status = new_status if isinstance(new_status, OrderStatus) else OrderStatus(str(new_status))
    except ValueError:
        return False, f"Invalid new status: {new_status}"

    owns_conn = False
    conn = None
    try:
        if current_status is None:
            conn = get_connection("orders.db", folder_path=folder_path)
            owns_conn = True
            cur = conn.cursor()
            cur.execute("SELECT order_status FROM orders WHERE id = ?", (order_id,))
            row = cur.fetchone()
            if not row:
                return False, f"Order {order_id} not found"
            current_status_val = row[0]
        else:
            current_status_val = current_status

        try:
            from_status = current_status_val if isinstance(current_status_val, OrderStatus) else OrderStatus(str(current_status_val))
        except ValueError:
            return False, f"Invalid current status: {current_status_val}"

        # Validate transition
        error = validate_transition(from_status, to_status)
        if error:
            return False, error

        # If valid, perform the update using the injected connection (for tests)
        if conn is None:
            conn = get_connection("orders.db", folder_path=folder_path)
            owns_conn = True

        cur = conn.cursor()
        cur.execute(
            "UPDATE orders SET order_status = ?, updated_date = CURRENT_TIMESTAMP WHERE id = ?",
            (to_status.value, order_id),
        )
        if to_status.value == "Billed":
            cur.execute("UPDATE orders SET billed = 1 WHERE id = ?", (order_id,))
        if to_status.value == "Paid":
            cur.execute("UPDATE orders SET paid = 1, paid_date = DATE('now') WHERE id = ?", (order_id,))

        # Clear hold metadata when leaving the On Hold state
        if to_status != OrderStatus.ON_HOLD:
            cur.execute(
                """
                UPDATE orders
                   SET hold_until_date = NULL,
                       hold_resume_status = NULL,
                       hold_note = NULL,
                       hold_reminder_sent = 0,
                       hold_set_at = NULL
                 WHERE id = ?
                """,
                (order_id,),
            )
        conn.commit()
        return True, None
    except Exception as e:
        return False, f"Database error: {str(e)}"
    finally:
        if owns_conn and conn:
            conn.close()


def update_order_status_with_hold(
    order_id: int,
    current_status: OrderStatus | str,
    new_status: OrderStatus | str,
    folder_path: Optional[str] = None,
    hold_until_date: Optional[str] = None,
    hold_resume_status: Optional[str] = None,
    hold_note: str = "",
) -> tuple[bool, Optional[str]]:
    """Update status and persist hold metadata in one shared workflow step."""
    success, error_msg = update_order_status_validated(
        order_id=order_id,
        current_status=current_status,
        new_status=new_status,
        folder_path=folder_path,
    )
    if not success:
        return False, error_msg

    try:
        target = new_status if isinstance(new_status, OrderStatus) else OrderStatus(str(new_status))
    except ValueError:
        return True, None

    if target == OrderStatus.ON_HOLD and hold_until_date:
        from .orders import set_order_hold

        set_order_hold(
            order_id=order_id,
            hold_until_date=hold_until_date,
            resume_status=hold_resume_status or OrderStatus.PENDING.value,
            note=hold_note,
            folder_path=folder_path,
        )

    return True, None


# ============================================================================
# High-Level Service Functions
# ============================================================================

def build_state_portal_json_for_order(
    order_id: int,
    folder_path: Optional[str] = None,
) -> dict:
    """
    High-level helper: load Order domain model + map to portal JSON.
    
    This demonstrates the clean architecture pattern:
    1. Repository layer: fetch_order_with_items() loads domain model
    2. View layer: StatePortalOrderView.from_order() transforms to view
    3. Serialization: to_portal_json() produces output
    
    Args:
        order_id: Order ID to export
        folder_path: Optional database folder path
    
    Returns:
        Dictionary ready for JSON serialization for state portal
    
    Raises:
        ValueError: If order not found
    
    Example:
        >>> json_data = build_state_portal_json_for_order(123)
        >>> import json
        >>> with open("portal_export.json", "w") as f:
        ...     json.dump(json_data, f, indent=2)
    """
    from .orders import fetch_order_with_items
    from .state_portal_view import StatePortalOrderView
    
    order = fetch_order_with_items(
        order_id=order_id,
        folder_path=folder_path,
    )
    if not order:
        raise ValueError(f"Order {order_id} not found")

    view = StatePortalOrderView.from_order(order, folder_path=folder_path)
    return view.to_portal_json()


def build_state_portal_csv_row_for_order(
    order_id: int,
    folder_path: Optional[str] = None,
) -> dict:
    """
    High-level helper: load Order domain model + map to portal CSV row.
    
    Args:
        order_id: Order ID to export
        folder_path: Optional database folder path
    
    Returns:
        Dictionary with CSV column names as keys
    
    Raises:
        ValueError: If order not found
    
    Example:
        >>> csv_row = build_state_portal_csv_row_for_order(123)
        >>> import csv
        >>> with open("portal_export.csv", "w", newline="") as f:
        ...     writer = csv.DictWriter(f, fieldnames=csv_row.keys())
        ...     writer.writeheader()
        ...     writer.writerow(csv_row)
    """
    from .orders import fetch_order_with_items
    from .state_portal_view import StatePortalOrderView
    
    order = fetch_order_with_items(
        order_id=order_id,
        folder_path=folder_path,
    )
    if not order:
        raise ValueError(f"Order {order_id} not found")

    view = StatePortalOrderView.from_order(order, folder_path=folder_path)
    return view.to_csv_row()
