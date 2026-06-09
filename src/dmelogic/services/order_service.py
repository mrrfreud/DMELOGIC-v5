"""
order_service.py — Business logic for order creation and management.

This service layer provides high-level order operations that coordinate
multiple repository calls and enforce business rules using UnitOfWork.

All service functions follow the UnitOfWork pattern:
- Open UoW for multi-DB coordination
- Call repositories with injected connections
- Auto-commit on success, auto-rollback on exception
- Provide transactional guarantees within practical SQLite limits
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional
import sqlite3

from dmelogic.db.base import UnitOfWork, ensure_writes_allowed
from dmelogic.db.orders import create_order_from_wizard_result_uow
from dmelogic.db.patients import fetch_patient_insurance
from dmelogic.db.inventory import fetch_latest_item_by_hcpcs
from dmelogic.config import debug_log

if TYPE_CHECKING:
    from dmelogic.ui.order_wizard import OrderWizardResult


def create_order_with_enrichment(
    result: "OrderWizardResult",
    folder_path: Optional[str] = None,
) -> int:
    """
    Create an order from wizard result with enriched data using UnitOfWork.
    
    This service function coordinates multiple databases:
    1. Reads from patients.db (insurance enrichment)
    2. Reads from inventory.db (pricing enrichment)
    3. Writes to orders.db (order creation + item updates)
    
    Uses UnitOfWork to ensure:
    - All operations use same folder_path context
    - Atomic commit/rollback across the workflow
    - Proper connection lifecycle management
    
    Args:
        result: OrderWizardResult from the wizard
        folder_path: Optional database folder path
        
    Returns:
        The created order ID
        
    Raises:
        WritesBlockedError: If backup is in progress
        
    Business Logic:
        - Pulls insurance from patients.db if not provided
        - Looks up inventory costs and item numbers
        - Calculates line totals from price × quantity
        - Updates order items with enriched data
        - All DB access through repositories with injected connections
    """
    # Check if writes are allowed (not blocked by backup)
    ensure_writes_allowed()
    
    try:
        with UnitOfWork(folder_path=folder_path) as uow:
            # --- Split patient name "LAST, FIRST" ---
            last_name = ""
            first_name = ""
            if result.patient_name:
                parts = [p.strip() for p in result.patient_name.split(",")]
                if parts:
                    last_name = parts[0]
                    if len(parts) > 1:
                        first_name = parts[1]

            # --- Enrich with insurance info from patients.db (best effort) ---
            primary_insurance = ""
            primary_insurance_id = ""
            patient_address = ""
            
            # Use insurance from wizard result if available
            if result.insurance_name:
                primary_insurance = result.insurance_name
                primary_insurance_id = result.insurance_policy_number or ""
            elif last_name or first_name:
                # Fallback: try to pull from patients.db using repository
                try:
                    insurance_data = fetch_patient_insurance(
                        last_name=last_name,
                        first_name=first_name,
                        dob=None,
                        folder_path=folder_path
                    )
                    if insurance_data:
                        if not primary_insurance:
                            primary_insurance = insurance_data.get("primary_insurance") or ""
                            primary_insurance_id = insurance_data.get("primary_insurance_id") or ""
                        addr_parts = [
                            insurance_data.get("address") or "", 
                            insurance_data.get("city") or "", 
                            insurance_data.get("state") or "", 
                            insurance_data.get("zip_code") or ""
                        ]
                        patient_address = ", ".join([p for p in addr_parts if p.strip()])
                except Exception as e:
                    debug_log(f"Could not read insurance from patients.db: {e}")

            # --- Create order using repository function with UoW connection ---
            # First, enhance the result object with enriched data
            if not hasattr(result, 'rx_date'):
                result.rx_date = datetime.now().strftime("%m/%d/%Y")
            if not hasattr(result, 'order_date'):
                result.order_date = datetime.now().strftime("%m/%d/%Y")
            
            # Get orders.db connection from UoW
            orders_conn = uow.connection("orders.db")
            order_id = create_order_from_wizard_result_uow(result, conn=orders_conn)
            
            # --- Enrich order items with inventory data ---
            cur = orders_conn.cursor()
            
            for item in result.items:
                hcpcs = (item.hcpcs or "").strip()
                if not hcpcs:
                    continue
                    
                item_number = ""
                cost_ea = ""
                line_total = ""
                
                # Pull from inventory using repository
                try:
                    inv_row = fetch_latest_item_by_hcpcs(hcpcs_code=hcpcs, folder_path=folder_path)
                    if inv_row:
                        item_number = inv_row.get("item_number") or ""
                        # Bill amount each
                        price = float(inv_row.get("retail_price") or 0)
                        cost_ea = f"{price:.2f}" if price else ""
                        
                        if price and item.quantity:
                            lt = price * float(item.quantity)
                            line_total = f"{lt:.2f}"
                except Exception as e:
                    debug_log(f"Inventory lookup failed for {hcpcs}: {e}")
                
                # Update the order_item with enriched data
                if item_number or cost_ea or line_total:
                    cur.execute(
                        """
                        UPDATE order_items
                        SET item_number = ?,
                            cost_ea = ?,
                            total = ?
                        WHERE order_id = ? AND hcpcs_code = ?
                        """,
                        (item_number, cost_ea, line_total, order_id, hcpcs)
                    )
            
            # Update order header with insurance if we got it
            if primary_insurance or primary_insurance_id or patient_address:
                cur.execute(
                    """
                    UPDATE orders
                    SET primary_insurance = ?,
                        primary_insurance_id = ?,
                        patient_address = ?
                    WHERE id = ?
                    """,
                    (primary_insurance, primary_insurance_id, patient_address, order_id)
                )
            
            # UoW will auto-commit on successful exit
            return order_id
        
    except Exception as e:
        debug_log(f"Failed to create order with enrichment: {e}")
        raise


def delete_order_with_audit(
    order_id: int,
    reason: str,
    deleted_by: str,
    folder_path: Optional[str] = None,
) -> None:
    """
    Delete an order and log the action to audit trail.
    
    Uses UnitOfWork to ensure atomic operation:
    1. Delete from order_items table
    2. Delete from orders table
    3. Insert audit log entry (future: audit.db)
    
    Args:
        order_id: The order ID to delete
        reason: Reason for deletion
        deleted_by: User/system identifier
        folder_path: Optional database folder path
        
    Raises:
        WritesBlockedError: If backup is in progress
        
    Business Logic:
        - Cascading delete (items first, then header)
        - Audit trail for compliance
        - Atomic operation via UoW
    """
    # Check if writes are allowed (not blocked by backup)
    ensure_writes_allowed()
    
    try:
        with UnitOfWork(folder_path=folder_path) as uow:
            orders_conn = uow.connection("orders.db")
            cur = orders_conn.cursor()
            
            # Verify order exists
            cur.execute("SELECT id FROM orders WHERE id = ?", (order_id,))
            if not cur.fetchone():
                raise ValueError(f"Order {order_id} not found")
            
            # Delete order items first (FK constraint)
            cur.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
            items_deleted = cur.rowcount
            
            # Delete order header
            cur.execute("DELETE FROM orders WHERE id = ?", (order_id,))
            
            debug_log(
                f"Deleted order {order_id}: {items_deleted} items removed. "
                f"Reason: {reason}, By: {deleted_by}"
            )
            
            # Future: Insert to audit.db when available
            # audit_conn = uow.connection("audit.db")
            # audit_conn.execute(
            #     "INSERT INTO audit_log (action, entity_type, entity_id, reason, user, timestamp) "
            #     "VALUES (?, ?, ?, ?, ?, ?)",
            #     ("DELETE", "order", order_id, reason, deleted_by, datetime.now())
            # )
            
    except Exception as e:
        debug_log(f"Failed to delete order {order_id}: {e}")
        raise
