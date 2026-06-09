"""
Pending approval operations for agent-created orders.

Orders created by AI agents are stored with order_status = 'Pending Approval'
and agent_created = 1. They must be reviewed and approved by a human user
before they appear in the main orders list.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List, Optional

from .base import get_connection
from .models import OrderStatus
from .patients import create_or_get_patient
from dmelogic.config import debug_log


def fetch_pending_approval_orders(
    folder_path: Optional[str] = None,
) -> List[sqlite3.Row]:
    """
    Fetch all orders that are awaiting human approval (agent-created).

    Returns rows sorted by created_date DESC so newest agent orders appear first.
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT o.*, GROUP_CONCAT(oi.hcpcs_code, ', ') AS hcpcs_summary,
                       GROUP_CONCAT(oi.description, ', ') AS items_summary,
                       COUNT(oi.id) AS item_count
                FROM orders o
                LEFT JOIN order_items oi ON oi.order_id = o.id
                WHERE o.order_status = ?
                  AND o.agent_created = 1
                GROUP BY o.id
                ORDER BY o.created_date DESC, o.id DESC
                """,
                (OrderStatus.PENDING_APPROVAL.value,),
            )
            return cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_pending_approval_orders: {e}")
        return []


def count_pending_approvals(folder_path: Optional[str] = None) -> int:
    """Return the number of orders awaiting approval."""
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM orders WHERE order_status = ? AND agent_created = 1",
                (OrderStatus.PENDING_APPROVAL.value,),
            )
            row = cur.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in count_pending_approvals: {e}")
        return 0


def approve_order(
    order_id: int,
    approved_by: str = "User",
    folder_path: Optional[str] = None,
) -> bool:
    """
    Approve an agent-created order — moves it from 'Pending Approval' to 'Pending'
    so it appears in the normal orders list and can proceed through the workflow.

    Also ensures the patient is saved to the patients.db table and links
    the patient_id on the order.

    Args:
        order_id: The order primary key.
        approved_by: Username/identifier of the approver.
        folder_path: Optional DB folder path override.

    Returns:
        True if the order was approved, False if not found or already processed.
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        try:
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # First, fetch the order to get patient info
            cur.execute(
                """
                SELECT patient_last_name, patient_first_name, patient_dob,
                       patient_phone, patient_address, primary_insurance,
                       primary_insurance_id, patient_id
                FROM orders
                WHERE id = ?
                  AND order_status = ?
                  AND agent_created = 1
                """,
                (order_id, OrderStatus.PENDING_APPROVAL.value)
            )
            order_row = cur.fetchone()
            if not order_row:
                debug_log(f"Order {order_id} not found or not pending approval")
                return False
            
            (patient_last, patient_first, patient_dob, patient_phone,
             patient_address, primary_insurance, primary_insurance_id,
             existing_patient_id) = order_row
            
            # Create or find patient in patients.db
            patient_id = existing_patient_id
            if patient_last and patient_first:
                patient_id = create_or_get_patient(
                    last_name=patient_last,
                    first_name=patient_first,
                    dob=patient_dob,
                    phone=patient_phone,
                    address=patient_address,
                    primary_insurance=primary_insurance,
                    primary_insurance_id=primary_insurance_id,
                    folder_path=folder_path,
                )
                if patient_id:
                    debug_log(f"Linked order {order_id} to patient_id {patient_id}")
            
            # Now approve the order and link patient_id
            cur.execute(
                """
                UPDATE orders
                SET order_status = ?,
                    agent_approved_by = ?,
                    agent_approved_at = ?,
                    patient_id = COALESCE(?, patient_id),
                    updated_date = ?
                WHERE id = ?
                  AND order_status = ?
                  AND agent_created = 1
                """,
                (
                    OrderStatus.PENDING.value,
                    approved_by,
                    now,
                    patient_id,
                    now,
                    order_id,
                    OrderStatus.PENDING_APPROVAL.value,
                ),
            )
            conn.commit()
            changed = cur.rowcount > 0
            if changed:
                debug_log(f"Order {order_id} approved by {approved_by}")
                try:
                    from dmelogic.security.audit import audit_log
                    audit_log("approve_agent_order", "order", order_id,
                              f"Approved by {approved_by}, patient_id={patient_id}")
                except Exception:
                    pass
                try:
                    from dmelogic.db.audit import record
                    from dmelogic.paths import db_dir
                    from dmelogic.security.auth import get_session
                    _session = get_session()
                    record(
                        db_dir() / "audit.db",
                        user_id=_session.user_id if _session else None,
                        username=approved_by,
                        action="order.approve",
                        target_type="order",
                        target_id=str(order_id),
                        details={"approved_by": approved_by, "patient_id": patient_id},
                    )
                except Exception:
                    pass
            return changed
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in approve_order({order_id}): {e}")
        return False


def reject_order(
    order_id: int,
    rejected_by: str = "User",
    reason: str = "",
    folder_path: Optional[str] = None,
) -> bool:
    """
    Reject an agent-created order — marks it as Cancelled.

    Args:
        order_id: The order primary key.
        rejected_by: Username/identifier of the rejector.
        reason: Optional rejection reason (stored in notes).
        folder_path: Optional DB folder path override.

    Returns:
        True if the order was rejected, False if not found or already processed.
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        try:
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Append rejection reason to notes
            note_suffix = ""
            if reason:
                note_suffix = f"\n[Agent order rejected by {rejected_by}: {reason}]"
            else:
                note_suffix = f"\n[Agent order rejected by {rejected_by}]"

            cur.execute(
                """
                UPDATE orders
                SET order_status = ?,
                    agent_approved_by = ?,
                    agent_approved_at = ?,
                    notes = COALESCE(notes, '') || ?,
                    updated_date = ?
                WHERE id = ?
                  AND order_status = ?
                  AND agent_created = 1
                """,
                (
                    OrderStatus.CANCELLED.value,
                    rejected_by,
                    now,
                    note_suffix,
                    now,
                    order_id,
                    OrderStatus.PENDING_APPROVAL.value,
                ),
            )
            conn.commit()
            changed = cur.rowcount > 0
            if changed:
                debug_log(f"Order {order_id} rejected by {rejected_by}: {reason}")
                try:
                    from dmelogic.security.audit import audit_log
                    audit_log("reject_agent_order", "order", order_id,
                              f"Rejected by {rejected_by}: {reason}")
                except Exception:
                    pass
                try:
                    from dmelogic.db.audit import record
                    from dmelogic.paths import db_dir
                    from dmelogic.security.auth import get_session
                    _session = get_session()
                    record(
                        db_dir() / "audit.db",
                        user_id=_session.user_id if _session else None,
                        username=rejected_by,
                        action="order.reject",
                        target_type="order",
                        target_id=str(order_id),
                        details={"rejected_by": rejected_by, "reason": reason},
                    )
                except Exception:
                    pass
            return changed
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in reject_order({order_id}): {e}")
        return False


def mark_order_agent_created(
    order_id: int,
    folder_path: Optional[str] = None,
) -> bool:
    """
    Mark an existing order as agent-created and set its status to Pending Approval.
    Useful when an agent creates an order through the normal create_order pathway.
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        try:
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                """
                UPDATE orders
                SET agent_created = 1,
                    order_status = ?,
                    updated_date = ?
                WHERE id = ?
                """,
                (OrderStatus.PENDING_APPROVAL.value, now, order_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in mark_order_agent_created({order_id}): {e}")
        return False
