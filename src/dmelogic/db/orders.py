from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import get_connection
from .models import OrderInput, OrderItemInput, Order, OrderItem, OrderStatus, BillingType
from .converters import safe_int, row_to_order, row_to_order_item
from dmelogic.config import debug_log

# Audit logging - import lazily to avoid circular imports
def _audit_action(action: str, resource_id: Any, details: str = None):
    """Helper to log audit action - handles import lazily"""
    try:
        from dmelogic.security.audit import audit_log
        audit_log(action, "order", resource_id, details)
    except Exception:
        pass  # Don't break operations if audit fails

if TYPE_CHECKING:
    from dmelogic.ui.order_wizard import OrderWizardResult, OrderItem as WizardOrderItem  # type: ignore


def wizard_item_to_input(w_item: "WizardOrderItem") -> OrderItemInput:
    """
    Map a wizard UI OrderItem to the domain OrderItemInput,
    including rentals + up to 4 modifiers.
    """
    # No free-text parsing; wizard now provides 4 explicit fields
    return OrderItemInput(
        hcpcs=w_item.hcpcs,
        description=w_item.description,
        quantity=w_item.quantity,
        refills=w_item.refills,
        days_supply=w_item.days_supply,
        directions=w_item.directions or None,
        is_rental=w_item.is_rental,
        modifier1=w_item.modifier1 or None,
        modifier2=w_item.modifier2 or None,
        modifier3=w_item.modifier3 or None,
        modifier4=w_item.modifier4 or None,
    )


def find_refill_eligible_orders_for_patient(
    patient_id: int,
    folder_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """
    Return other orders for this patient that are refill-eligible / active.
    
    Used to:
      - Remind at order creation time (wizard) to link related orders
      - Remind at refill time (Order Editor)
    
    Returns orders where:
      - patient_id matches
      - status in ('Pending', 'Active', 'Shipped')
      - has items with refills > 0
    
    Returns:
        List of dicts with order info for display in linking dialog
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection("orders.db", folder_path=folder_path)
    
    conn.row_factory = sqlite3.Row
    
    try:
        cur = conn.cursor()
        
        # Query orders that are active/pending/shipped and have refillable items
        cur.execute(
            """
            SELECT DISTINCT
                o.id,
                o.parent_order_id,
                o.refill_number,
                o.order_date,
                o.prescriber_name,
                o.order_status,
                COALESCE(o.refill_group_id, 0) AS refill_group_id,
                -- Calculate total refills remaining from items
                (SELECT SUM(CAST(COALESCE(oi.refills, '0') AS INTEGER))
                 FROM order_items oi 
                 WHERE oi.order_id = o.id) AS total_refills
            FROM orders o
            WHERE o.patient_id = ?
              AND o.order_status IN ('Pending', 'Active', 'Shipped', 'Unbilled', 'Billed')
              AND COALESCE(o.is_locked, 0) = 0
              AND COALESCE(o.refill_completed, 0) = 0
              AND EXISTS (
                  SELECT 1 FROM order_items oi
                  WHERE oi.order_id = o.id
                  AND CAST(COALESCE(oi.refills, '0') AS INTEGER) > 0
              )
            ORDER BY o.order_date DESC, o.id DESC
            """,
            (patient_id,),
        )
        rows = cur.fetchall()
        
        results: List[Dict[str, Any]] = []
        for row in rows:
            oid = row["id"]
            base_oid = row["parent_order_id"]
            refill_num = row["refill_number"] or 0
            
            # Option 1: always display the continuous order ID, with refill context appended.
            if base_oid and refill_num > 0:
                display_number = f"ORD-{oid:03d} (Refill of ORD-{int(base_oid):03d} R{int(refill_num)})"
            else:
                display_number = f"ORD-{oid:03d}"
            
            # Calculate refill due date (order_date + days_supply from items)
            refill_due_date = None
            try:
                cur.execute(
                    """
                    SELECT MAX(CAST(COALESCE(day_supply, '30') AS INTEGER)) as max_days
                    FROM order_items WHERE order_id = ?
                    """,
                    (oid,),
                )
                days_row = cur.fetchone()
                if days_row and days_row["max_days"]:
                    from datetime import datetime, timedelta
                    order_dt = datetime.strptime(row["order_date"], "%Y-%m-%d") if row["order_date"] else None
                    if order_dt:
                        due_dt = order_dt + timedelta(days=int(days_row["max_days"]))
                        refill_due_date = due_dt.strftime("%m/%d/%Y")
            except Exception:
                pass
            
            results.append({
                "order_id": oid,
                "display_number": display_number,
                "base_order_id": base_oid,
                "refill_number": refill_num,
                "order_date": row["order_date"],
                "refill_due_date": refill_due_date,
                "refills_remaining": row["total_refills"] or 0,
                "prescriber_name": row["prescriber_name"] or "Unknown",
                "status": row["order_status"],
                "refill_group_id": row["refill_group_id"] if row["refill_group_id"] else None,
            })
        
        return results
    
    except Exception as e:
        debug_log(f"Error in find_refill_eligible_orders_for_patient: {e}")
        return []
    finally:
        if owns_conn and conn is not None:
            conn.close()


def get_orders_for_patient(
    patient_id: int,
    folder_path: Optional[str] = None,
    fallback_last_name: Optional[str] = None,
    fallback_first_name: Optional[str] = None,
    fallback_dob: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return ALL orders for a patient, using patient_id as the primary lookup
    with fallback to name + DOB matching for legacy orders.
    
    This is the single, shared function for loading a patient's order history
    in BOTH the Patient Details dialog and the Patient Profile view.
    
    Uses the same logic as the main Orders screen but filtered by patient.
    
    Args:
        patient_id: Patient ID to look up
        folder_path: Optional database folder path
        fallback_last_name: Last name for fallback matching (legacy orders)
        fallback_first_name: First name for fallback matching (legacy orders)
        fallback_dob: DOB string for fallback matching (legacy orders)
    
    Returns:
        List of dicts with order details (id, order_number, rx_date, order_date,
        status, item_count, items_with_refills, max_refills, max_day_supply,
        refill_status, refill_due_date)
    """
    conn = get_connection("orders.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    
    try:
        cur = conn.cursor()
        orders: List[sqlite3.Row] = []
        order_date_sort_expr = """
                CASE
                    WHEN order_date GLOB '____-__-__*' THEN substr(order_date, 1, 10)
                    WHEN order_date GLOB '__/__/____*' THEN substr(order_date, 7, 4) || '-' || substr(order_date, 1, 2) || '-' || substr(order_date, 4, 2)
                    WHEN order_date GLOB '__-__-____*' THEN substr(order_date, 7, 4) || '-' || substr(order_date, 1, 2) || '-' || substr(order_date, 4, 2)
                    ELSE '0000-00-00'
                END
        """
        
        # Primary: Query by patient_id.
        # Also include legacy name/DOB matches so historical orders that were not
        # linked cleanly to patient_id still show up in patient details.
        seen_order_ids = set()

        def fetch_matching_orders(where_clause: str, params: tuple) -> List[sqlite3.Row]:
            cur.execute(
                f"""
                  SELECT id, rx_date, order_date, order_status,
                      parent_order_id, refill_number, tracking_number,
                       (SELECT COUNT(*) FROM order_items WHERE order_id = orders.id) as item_count,
                       (SELECT COUNT(*) FROM order_items WHERE order_id = orders.id AND CAST(COALESCE(refills, '0') AS INTEGER) > 0) as items_with_refills,
                       (SELECT MAX(CAST(COALESCE(refills, '0') AS INTEGER)) FROM order_items WHERE order_id = orders.id) as max_refills,
                       (SELECT MAX(CAST(COALESCE(day_supply, '0') AS INTEGER)) FROM order_items WHERE order_id = orders.id) as max_day_supply
                FROM orders
                WHERE {where_clause}
                ORDER BY {order_date_sort_expr} DESC, id DESC
                """,
                params,
            )
            return cur.fetchall()

        if patient_id:
            for row in fetch_matching_orders("patient_id = ?", (patient_id,)):
                orders.append(row)
                seen_order_ids.add(int(row["id"]))

        if fallback_last_name and fallback_first_name:
            norm_dob = (fallback_dob or "").replace("/", "").replace("-", "").replace(".", "").replace(" ", "").strip()
            name_where = "UPPER(patient_last_name) = UPPER(?) AND UPPER(patient_first_name) = UPPER(?)"
            name_params = [fallback_last_name, fallback_first_name]
            if norm_dob:
                name_where += " AND REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(TRIM(patient_dob),''), '/', ''), '-', ''), '.', ''), ' ', ''), ':', '') = ?"
                name_params.append(norm_dob)

            if patient_id:
                debug_log(f"Merging patient_id={patient_id} orders with legacy name+DOB matches")
            else:
                debug_log(f"No orders by patient_id={patient_id}; using name+DOB match")

            for row in fetch_matching_orders(name_where, tuple(name_params)):
                row_id = int(row["id"])
                if row_id in seen_order_ids:
                    continue
                orders.append(row)
                seen_order_ids.add(row_id)
        
        # Process results
        results: List[Dict[str, Any]] = []
        for row in orders:
            order_id = row["id"]
            item_count = row["item_count"] or 0
            items_with_refills = row["items_with_refills"] or 0
            max_refills = row["max_refills"] or 0
            max_day_supply = row["max_day_supply"] or 0
            parent_id = row["parent_order_id"]
            refill_num = row["refill_number"] or 0
            
            # Option 1: always display the continuous order ID, with refill context appended.
            if parent_id and refill_num > 0:
                order_number = f"ORD-{order_id:03d} (Refill of ORD-{int(parent_id):03d} R{int(refill_num)})"
            else:
                order_number = f"ORD-{order_id:03d}"
            
            # Determine refill status text
            if max_refills == 0:
                refill_status = "No refills left"
            elif items_with_refills == item_count:
                refill_status = "All items have refills"
            else:
                refill_status = "Some items have refills"
            
            # Calculate refill due date
            refill_due_date = ""
            refill_color = None
            order_date = row["order_date"]
            if order_date and max_day_supply > 0:
                try:
                    from datetime import datetime, timedelta
                    order_date_str = str(order_date)
                    order_dt = None
                    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y"]:
                        try:
                            order_dt = datetime.strptime(order_date_str.split(" ")[0], fmt)
                            break
                        except ValueError:
                            continue
                    
                    if order_dt:
                        due_dt = order_dt + timedelta(days=max_day_supply)
                        refill_due_date = due_dt.strftime("%m/%d/%Y")
                        
                        # Traffic light logic for color coding
                        today = datetime.now()
                        days_until_due = (due_dt - today).days
                        
                        if max_refills == 0:
                            refill_color = "red"  # No refills
                        elif days_until_due <= 0:
                            refill_color = "green"  # Due or overdue
                        elif days_until_due <= 5:
                            refill_color = "yellow"  # Within 5 days
                        else:
                            refill_color = "red"  # Not due yet
                except Exception:
                    pass
            
            results.append({
                "id": order_id,
                "order_number": order_number,
                "rx_date": row["rx_date"],
                "order_date": order_date,
                "status": row["order_status"] or "Pending",
                "tracking_number": (row["tracking_number"] or "").strip() or None,
                "item_count": item_count,
                "items_with_refills": items_with_refills,
                "max_refills": max_refills,
                "max_day_supply": max_day_supply,
                "refill_status": refill_status,
                "refill_due_date": refill_due_date,
                "refill_color": refill_color,
                "parent_order_id": parent_id,
                "refill_number": refill_num,
            })
        
        debug_log(f"get_orders_for_patient({patient_id}): found {len(results)} orders")
        return results
    
    except Exception as e:
        debug_log(f"Error in get_orders_for_patient: {e}")
        return []
    finally:
        conn.close()


def find_orders_with_missing_patient(folder_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Diagnostic helper: Find orders that have NULL patient_id or whose
    patient_id doesn't match any existing patient in the patients table.
    
    Use this to identify orders that aren't linked to a valid patient
    (explaining why they don't show up in patient order history).
    
    Returns:
        List of dicts with order info: id, order_number, patient_id,
        patient_name_at_order_time, possible_match
    """
    conn = get_connection("orders.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    
    try:
        cur = conn.cursor()
        
        # Find orders with NULL patient_id or patient_id not in patients table
        # Note: patients are in a separate database file
        cur.execute(
            """
            SELECT 
                o.id,
                o.patient_id,
                o.patient_last_name,
                o.patient_first_name,
                o.patient_dob,
                o.order_date,
                o.order_status
            FROM orders o
            WHERE o.patient_id IS NULL
               OR o.patient_id = 0
               OR o.patient_id = ''
            ORDER BY o.id
            """
        )
        rows = cur.fetchall()
        
        results: List[Dict[str, Any]] = []
        for row in rows:
            order_id = row["id"]
            patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
            
            results.append({
                "id": order_id,
                "order_number": f"ORD-{order_id:03d}",
                "patient_id": row["patient_id"],
                "patient_name_at_order_time": patient_name,
                "patient_dob": row["patient_dob"],
                "order_date": row["order_date"],
                "status": row["order_status"],
            })
        
        debug_log(f"find_orders_with_missing_patient: found {len(results)} unlinked orders")
        return results
    
    except Exception as e:
        debug_log(f"Error in find_orders_with_missing_patient: {e}")
        return []
    finally:
        conn.close()


def fetch_all_orders(folder_path: Optional[str] = None) -> List[sqlite3.Row]:
    """
    Return all orders ordered by created_date DESC.

    Uses the existing 'orders' table; assumes schema is already created
    by the existing setup_orders_database logic.
    
    Note: Excludes agent-created orders with 'Pending Approval' status.
    Those are fetched separately via fetch_pending_approval_orders().
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT *
                FROM orders
                WHERE COALESCE(order_status, '') != 'Pending Approval'
                ORDER BY created_date DESC, id DESC
                """
            )
            return cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_all_orders: {e}")
        return []


def fetch_order_by_id(order_id: int, folder_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Fetch a single order row by primary key id."""
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
            return cur.fetchone()
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_order_by_id({order_id}): {e}")
        return None


def search_orders(search_term: str, folder_path: Optional[str] = None) -> List[dict]:
    """
    Search orders by order number or patient name.
    Returns list of dicts with order info.
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            like = f"%{search_term}%"
            # Try to parse as order ID
            try:
                order_id = int(search_term)
                cur.execute(
                    """
                    SELECT id, patient_id, patient_name, patient_first_name, patient_last_name,
                           order_status, order_date, created_date
                    FROM orders
                    WHERE id = ?
                    ORDER BY created_date DESC
                    LIMIT 50
                    """,
                    (order_id,)
                )
            except ValueError:
                cur.execute(
                    """
                    SELECT id, patient_id, patient_name, patient_first_name, patient_last_name,
                           order_status, order_date, created_date
                    FROM orders
                    WHERE patient_name LIKE ? 
                       OR patient_first_name LIKE ?
                       OR patient_last_name LIKE ?
                       OR CAST(id AS TEXT) LIKE ?
                    ORDER BY created_date DESC
                    LIMIT 50
                    """,
                    (like, like, like, like)
                )
            results = []
            for row in cur.fetchall():
                d = dict(row)
                # Normalize patient_name
                if not d.get('patient_name'):
                    first = d.get('patient_first_name', '')
                    last = d.get('patient_last_name', '')
                    d['patient_name'] = f"{first} {last}".strip()
                # Normalize status field
                d['status'] = d.get('order_status', 'Unknown')
                results.append(d)
            return results
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in search_orders: {e}")
        return []


def get_order(order_id: int, folder_path: Optional[str] = None) -> Optional[dict]:
    """Get order by ID as dict."""
    row = fetch_order_by_id(order_id, folder_path)
    return dict(row) if row else None


def fetch_order_with_items(
    order_id: int,
    folder_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[Order]:
    """
    Load a full Order aggregate (header + items) as a domain model.
    
    This is the single authoritative way to hydrate a complete Order
    for any view layer (State Portal, HCFA-1500, delivery tickets, etc.).
    
    Architecture:
    - Uses row_to_order / row_to_order_item converters
    - Returns typed Order domain model with items list populated
    - If conn is provided, it is reused (no close/commit)
    - If conn is None, opens its own connection and closes it
    
    Args:
        order_id: Order ID to fetch
        folder_path: Optional database folder path
        conn: Optional connection to reuse (for transactions)
    
    Returns:
        Order domain model with items populated, or None if not found
    
    Example:
        >>> order = fetch_order_with_items(123, folder_path=data_path)
        >>> if order:
        ...     print(f"Order {order.id} has {len(order.items)} items")
        ...     for item in order.items:
        ...         print(f"  {item.hcpcs_code}: ${item.unit_price}")
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection("orders.db", folder_path=folder_path)
    
    # Always ensure row_factory is set (even if conn was provided)
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        # --- Fetch order header ---
        cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        header_row = cur.fetchone()
        if not header_row:
            return None

        # Convert row to Order domain model
        order = row_to_order(header_row)

        # --- Fetch order items ---
        # Try sort_order column first; fall back to id if column doesn't exist yet
        try:
            cur.execute(
                "SELECT * FROM order_items WHERE order_id = ? ORDER BY COALESCE(sort_order, id) ASC, id ASC",
                (order_id,),
            )
        except sqlite3.OperationalError:
            cur.execute(
                "SELECT * FROM order_items WHERE order_id = ? ORDER BY id ASC",
                (order_id,),
            )
        item_rows = cur.fetchall()
        
        # Convert rows to OrderItem domain models
        items = [row_to_order_item(r) for r in item_rows]

        # Attach items to order
        # Order.items is defined as list[OrderItem] in models.py
        order.items = items

        return order

    except Exception as e:
        debug_log(f"DB Error in fetch_order_with_items({order_id}): {e}")
        return None
    finally:
        if owns_conn and conn is not None:
            conn.close()


def update_order_fields(order_id: int, fields: dict, folder_path: Optional[str] = None) -> None:
    """
    Update specified fields on an order.
    
    Args:
        order_id: Order ID to update
        fields: Dictionary of field names to values (e.g., {"doctor_directions": "...", "notes": "..."})
        folder_path: Optional database folder path
    """
    if not fields:
        return
    
    # Allowed fields to prevent SQL injection
    allowed_fields = {
        "doctor_directions", "notes", "patient_address", "tracking_number",
        "special_instructions", "rx_date", "rx_date_2", "order_date", "delivery_date", "pickup_date",
        "icd_code_1", "icd_code_2", "icd_code_3", "icd_code_4", "icd_code_5",
        "prescriber_phone", "prescriber_fax", "epaces_alert",
        "prescriber_name_2", "prescriber_npi_2", "patient_id"
    }
    update_fields = {k: v for k, v in fields.items() if k in allowed_fields}
    
    if not update_fields:
        return
    
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        try:
            cur = conn.cursor()
            set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
            values = list(update_fields.values()) + [order_id]
            cur.execute(
                f"UPDATE orders SET {set_clause}, updated_date = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            conn.commit()
            debug_log(f"Updated order {order_id} fields: {list(update_fields.keys())}")
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"Error updating order {order_id} fields: {e}")
        raise


def update_order_item(item_id: int, fields: dict, folder_path: Optional[str] = None) -> None:
    """
    Update specified fields on an order item.
    
    Args:
        item_id: Order item ID to update
        fields: Dictionary of field names to values (e.g., {"qty": 5, "modifier1": "NU"})
        folder_path: Optional database folder path
    """
    if not fields:
        debug_log(f"[UPDATE_ITEM] No fields provided for item {item_id}")
        return
    
    # Allowed fields to prevent SQL injection
    allowed_fields = {
        "qty", "refills", "day_supply", "cost_ea", "total",
        "modifier1", "modifier2", "modifier3", "modifier4",
        "directions", "pa_number", "rental_month", "is_rental",
        "prescriber_id", "prescriber_name", "prescriber_npi"
    }
    update_fields = {k: v for k, v in fields.items() if k in allowed_fields}
    
    if not update_fields:
        debug_log(f"[UPDATE_ITEM] No allowed fields for item {item_id}, fields={fields}")
        return
    
    debug_log(f"[UPDATE_ITEM] item_id={item_id}, update_fields={update_fields}, folder_path={folder_path}")
    
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        debug_log(f"[UPDATE_ITEM] Got connection to orders.db")
        try:
            cur = conn.cursor()
            
            # Ensure prescriber columns exist (migration may not have run)
            for col in ["prescriber_id", "prescriber_name", "prescriber_npi"]:
                if col in update_fields:
                    try:
                        cur.execute(f"ALTER TABLE order_items ADD COLUMN {col} TEXT")
                        conn.commit()
                    except Exception:
                        pass  # Column already exists
            
            set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
            values = list(update_fields.values()) + [item_id]
            sql = f"UPDATE order_items SET {set_clause} WHERE id = ?"
            debug_log(f"[UPDATE_ITEM] SQL: {sql}, values={values}")
            cur.execute(sql, values)
            rows_affected = cur.rowcount
            debug_log(f"[UPDATE_ITEM] Rows affected before commit: {rows_affected}")
            conn.commit()
            debug_log(f"Updated order item {item_id} fields: {list(update_fields.keys())}, rows_affected={rows_affected}")
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"Error updating order item {item_id} fields: {e}")
        raise


def recompute_refill_due_date(order_id: int, folder_path: Optional[str] = None) -> Optional[str]:
    """Recalculate and persist the order-level refill_due_date based on items.

    Logic: take the order_date if present, otherwise rx_date; add the max day_supply
    across order_items. Stores the result as MM/DD/YYYY in orders.refill_due_date.
    Returns the computed date string, or None if not computable.
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()

            # Fetch base dates and max day supply in one round trip
            cur.execute(
                """
                SELECT order_date, rx_date,
                       (SELECT MAX(CAST(COALESCE(day_supply, '0') AS INTEGER))
                        FROM order_items
                        WHERE order_id = orders.id) AS max_day_supply
                FROM orders
                WHERE id = ?
                """,
                (order_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            max_days = int(row["max_day_supply"] or 0)
            if max_days <= 0:
                cur.execute(
                    "UPDATE orders SET refill_due_date = NULL WHERE id = ?",
                    (order_id,),
                )
                conn.commit()
                return None

            base_date_raw = row["order_date"] or row["rx_date"]
            if not base_date_raw:
                cur.execute(
                    "UPDATE orders SET refill_due_date = NULL WHERE id = ?",
                    (order_id,),
                )
                conn.commit()
                return None

            base_dt = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
                try:
                    base_dt = datetime.strptime(str(base_date_raw).split(" ")[0], fmt)
                    break
                except ValueError:
                    continue

            if not base_dt:
                cur.execute(
                    "UPDATE orders SET refill_due_date = NULL WHERE id = ?",
                    (order_id,),
                )
                conn.commit()
                return None

            due_dt = base_dt + timedelta(days=max_days)
            due_str = due_dt.strftime("%m/%d/%Y")
            cur.execute(
                "UPDATE orders SET refill_due_date = ? WHERE id = ?",
                (due_str, order_id),
            )
            conn.commit()
            return due_str
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"Error recomputing refill_due_date for order {order_id}: {e}")
        return None


def add_order_item(
    order_id: int,
    fields: dict,
    folder_path: Optional[str] = None,
) -> int:
    """Insert a new order item and return its rowid."""
    allowed_fields = {
        "hcpcs_code",
        "description",
        "item_number",
        "refills",
        "day_supply",
        "qty",
        "cost_ea",
        "total",
        "pa_number",
        "directions",
        "is_rental",
        "modifier1",
        "modifier2",
        "modifier3",
        "modifier4",
        "prescriber_id",
        "prescriber_name",
        "prescriber_npi",
    }
    clean_fields = {k: v for k, v in fields.items() if k in allowed_fields}

    if not clean_fields:
        return -1

    conn = get_connection("orders.db", folder_path=folder_path)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO order_items (
                order_id,
                rx_no,
                hcpcs_code,
                description,
                item_number,
                refills,
                day_supply,
                qty,
                cost_ea,
                total,
                pa_number,
                directions,
                last_filled_date,
                is_rental,
                modifier1,
                modifier2,
                modifier3,
                modifier4,
                prescriber_id,
                prescriber_name,
                prescriber_npi
            ) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                clean_fields.get("hcpcs_code", ""),
                clean_fields.get("description", ""),
                clean_fields.get("item_number", ""),
                str(clean_fields.get("refills", "0")),
                str(clean_fields.get("day_supply", "0")),
                str(clean_fields.get("qty", "0")),
                str(clean_fields.get("cost_ea", "")),
                str(clean_fields.get("total", "")),
                clean_fields.get("pa_number", ""),
                clean_fields.get("directions", ""),
                date.today().strftime("%Y-%m-%d"),
                1 if clean_fields.get("is_rental") else 0,
                clean_fields.get("modifier1"),
                clean_fields.get("modifier2"),
                clean_fields.get("modifier3"),
                clean_fields.get("modifier4"),
                clean_fields.get("prescriber_id"),
                clean_fields.get("prescriber_name"),
                clean_fields.get("prescriber_npi"),
            ),
        )

        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def delete_order_item(item_id: int, folder_path: Optional[str] = None) -> None:
    """Delete a single order item."""
    conn = get_connection("orders.db", folder_path=folder_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM order_items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()


def update_order_status(order_id: int, status: str, folder_path: Optional[str] = None) -> None:
    """
    Update the order_status for an order.
    
    NOTE: This is a low-level function that does NOT validate workflow transitions.
    For validated status updates with business rules, use:
        from .order_workflow import update_order_status_validated
    
    Args:
        order_id: Order ID to update
        status: New status string (not validated)
        folder_path: Optional database folder path
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        try:
            cur = conn.cursor()
            
            # Update status
            cur.execute(
                "UPDATE orders SET order_status = ?, updated_date = CURRENT_TIMESTAMP WHERE id = ?",
                (status, order_id),
            )
            
            # If status is BILLED, also update the billed field
            if status == "Billed":
                cur.execute(
                    "UPDATE orders SET billed = 1 WHERE id = ?",
                    (order_id,),
                )
                debug_log(f"Marked order {order_id} as billed")
            
            # If status is PAID, also update the paid field and paid_date
            if status == "Paid":
                cur.execute(
                    "UPDATE orders SET paid = 1, paid_date = DATE('now') WHERE id = ?",
                    (order_id,),
                )
                debug_log(f"Marked order {order_id} as paid")
            
            conn.commit()
            debug_log(f"Updated order {order_id} status to {status}")
            
            # Audit log the status change
            _audit_action("update", order_id, f"Status changed to: {status}")
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in update_order_status({order_id}, {status}): {e}")
        raise


def set_order_hold(
    order_id: int,
    hold_until_date: str,
    resume_status: str,
    note: str = "",
    folder_path: Optional[str] = None,
) -> None:
    """Persist hold metadata for an order already set to On Hold."""
    conn = get_connection("orders.db", folder_path=folder_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE orders
               SET hold_until_date = ?,
                   hold_resume_status = ?,
                   hold_note = ?,
                   hold_reminder_sent = 0,
                   hold_set_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (hold_until_date, resume_status, note, order_id),
        )
        conn.commit()
    finally:
        conn.close()


def clear_order_hold(
    order_id: int,
    folder_path: Optional[str] = None,
    mark_reminder_sent: bool = False,
) -> None:
    """Clear hold metadata after releasing an order from hold."""
    conn = get_connection("orders.db", folder_path=folder_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE orders
               SET hold_until_date = NULL,
                   hold_resume_status = NULL,
                   hold_note = NULL,
                   hold_set_at = NULL,
                   hold_reminder_sent = CASE WHEN ? THEN 1 ELSE hold_reminder_sent END
             WHERE id = ?
            """,
            (1 if mark_reminder_sent else 0, order_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_due_hold_orders(folder_path: Optional[str] = None) -> List[dict]:
    """Return holded orders whose release date is due (today or earlier)."""
    conn = get_connection("orders.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, patient_first_name, patient_last_name,
                   hold_until_date, hold_resume_status, hold_note, order_status
              FROM orders
             WHERE order_status = 'On Hold'
               AND hold_until_date IS NOT NULL
               AND date(hold_until_date) <= date('now')
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _split_patient_name(full_name: str) -> tuple[str, str]:
    """
    Split 'LAST, FIRST' into (last, first). Fall back reasonably if the
    format is different.
    """
    full_name = (full_name or "").strip()
    if not full_name:
        return "", ""

    if "," in full_name:
        last, first = full_name.split(",", 1)
        return last.strip(), first.strip()

    parts = full_name.split()
    if len(parts) >= 2:
        first = parts[0]
        last = " ".join(parts[1:])
        return last.strip(), first.strip()

    # Single token – put it in last name
    return full_name, ""


def create_order(order_input: OrderInput, folder_path: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Create a new order using domain DTO (decoupled from UI types).
    
    Validates business rules before insert and uses safe conversions.
    Returns the new order ID.
    
    Raises ValueError if validation fails.
    """
    # Validate business rules
    validation_errors = order_input.validate()
    if validation_errors:
        error_msg = "Order validation failed:\n" + "\n".join(f"  - {e}" for e in validation_errors)
        debug_log(f"Order validation failed: {validation_errors}")
        raise ValueError(error_msg)
    
    close_conn = conn is None
    if conn is None:
        conn = get_connection("orders.db", folder_path=folder_path)
    
    try:
        cur = conn.cursor()
        
        # Use dates from input or default to today
        rx_date_str = order_input.rx_date or date.today().strftime("%Y-%m-%d")
        order_date_str = order_input.order_date or date.today().strftime("%Y-%m-%d")
        
        # Agent-created orders get 'Pending Approval' status instead of whatever was requested
        effective_status = order_input.order_status
        agent_flag = 0
        if order_input.agent_created:
            effective_status = OrderStatus.PENDING_APPROVAL.value
            agent_flag = 1
            # For agent orders the rx_date may be unreadable — use a
            # sentinel so the column is never NULL (schema requires NOT NULL).
            if not order_input.rx_date or not order_input.rx_date.strip():
                rx_date_str = "[NEEDS REVIEW]"
            # Ensure patient names are never NULL (schema requires NOT NULL)
            if not order_input.patient_last_name or not order_input.patient_last_name.strip():
                order_input.patient_last_name = "[Unknown]"
            if not order_input.patient_first_name or not order_input.patient_first_name.strip():
                order_input.patient_first_name = "[Unknown]"
            # Prescriber may also be blank — schema requires NOT NULL
            if not order_input.prescriber_name or not order_input.prescriber_name.strip():
                order_input.prescriber_name = "[Unknown]"
            if not order_input.prescriber_npi or not order_input.prescriber_npi.strip():
                order_input.prescriber_npi = "[Unknown]"
        
        # Prepare ICD codes (support both list and individual fields)
        icd_codes = order_input.icd_codes or []
        icd_1 = order_input.icd_code_1 or (icd_codes[0] if len(icd_codes) > 0 else None)
        icd_2 = order_input.icd_code_2 or (icd_codes[1] if len(icd_codes) > 1 else None)
        icd_3 = order_input.icd_code_3 or (icd_codes[2] if len(icd_codes) > 2 else None)
        icd_4 = order_input.icd_code_4 or (icd_codes[3] if len(icd_codes) > 3 else None)
        icd_5 = order_input.icd_code_5 or (icd_codes[4] if len(icd_codes) > 4 else None)
        
        # Insert order header
        debug_log("[create_order] inserting order without prescriber_id column")
        cur.execute(
            """
            INSERT INTO orders (
                rx_date,
                order_date,
                patient_id,
                patient_last_name,
                patient_first_name,
                patient_dob,
                patient_phone,
                patient_address,
                patient_name,
                prescriber_name,
                prescriber_npi,
                rx_date_2,
                prescriber_name_2,
                prescriber_npi_2,
                primary_insurance,
                primary_insurance_id,
                billing_selection,
                order_status,
                delivery_date,
                icd_code_1,
                icd_code_2,
                icd_code_3,
                icd_code_4,
                icd_code_5,
                doctor_directions,
                notes,
                parent_order_id,
                refill_number,
                agent_created,
                rx_origin
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rx_date_str,
                order_date_str,
                order_input.patient_id,
                order_input.patient_last_name,
                order_input.patient_first_name,
                order_input.patient_dob or None,
                order_input.patient_phone or None,
                order_input.patient_address or None,
                order_input.patient_full_name,
                order_input.prescriber_name or None,
                order_input.prescriber_npi or None,
                order_input.rx_date_2 or None,
                order_input.prescriber_name_2 or None,
                order_input.prescriber_npi_2 or None,
                order_input.primary_insurance or None,
                order_input.primary_insurance_id or None,
                order_input.billing_type,
                effective_status,
                order_input.delivery_date or None,
                icd_1,
                icd_2,
                icd_3,
                icd_4,
                icd_5,
                order_input.doctor_directions or None,
                order_input.notes or None,
                order_input.parent_order_id,
                order_input.refill_number,
                agent_flag,
                getattr(order_input, "rx_origin", None) or None,
            ),
        )
        
        order_id = int(cur.lastrowid)
        
        # Insert order items with safe conversions
        for item in order_input.items:
            hcpcs = (item.hcpcs or "").strip()
            desc = (item.description or "").strip()
            
            # Skip empty lines
            if not hcpcs and not desc:
                continue
            
            # Agent placeholder items: ensure at least a description exists
            if not hcpcs and agent_flag:
                hcpcs = "[TBD]"
            if not desc and agent_flag:
                desc = "[Placeholder — needs review]" if item.is_placeholder else "[No description]"
            
            # Use safe_int for all numeric conversions
            qty = safe_int(item.quantity, default=1, field_name="quantity")
            refills = safe_int(item.refills, default=0, field_name="refills")
            days = safe_int(item.days_supply, default=30, field_name="days_supply")
            
            # Calculate cost and total if provided
            cost_ea_str = ""
            total_str = ""
            if item.cost_ea:
                cost_ea_str = f"{float(item.cost_ea):.2f}"
                total_str = f"{float(item.cost_ea * qty):.2f}"
            
            # Normalize modifiers
            mods = item.normalized_modifiers()
            
            cur.execute(
                """
                INSERT INTO order_items (
                    order_id,
                    rx_no,
                    hcpcs_code,
                    description,
                    item_number,
                    refills,
                    day_supply,
                    qty,
                    cost_ea,
                    total,
                    directions,
                    last_filled_date,
                    is_rental,
                    modifier1,
                    modifier2,
                    modifier3,
                    modifier4
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    "",  # rx_no is legacy
                    hcpcs,
                    desc,
                    item.item_number or "",
                    str(refills),
                    str(days),
                    str(qty),
                    cost_ea_str,
                    total_str,
                    item.directions or "",
                    order_date_str,
                    1 if item.is_rental else 0,
                    mods[0],
                    mods[1],
                    mods[2],
                    mods[3],
                ),
            )
        
        if close_conn:
            conn.commit()
        debug_log(f"Created order {order_id} for patient {order_input.patient_full_name}")
        
        # Audit log the order creation
        _audit_action("create", order_id, f"Patient: {order_input.patient_full_name}")
        
        return order_id
    
    except Exception as e:
        debug_log(f"DB Error in create_order: {e}")
        raise
    finally:
        if close_conn:
            conn.close()


def create_order_from_wizard_result(
    result: "OrderWizardResult",
    folder_path: Optional[str] = None,
) -> int:
    """
    Persist a new order created from the OrderWizard into orders.db.

    - Writes to the existing 'orders' table (same schema as legacy).
    - Writes to 'order_items' with minimal data (HCPCS, description, qty, refills, days).
    - Returns the new orders.id primary key.
    """
    # Resolve orders.db via the shared base helper
    conn = get_connection("orders.db", folder_path=folder_path)
    try:
        cur = conn.cursor()

        # Get dates from wizard result
        rx_date_str = result.rx_date if hasattr(result, 'rx_date') else date.today().strftime("%Y-%m-%d")
        order_date_str = result.order_date if hasattr(result, 'order_date') else date.today().strftime("%Y-%m-%d")

        patient_name = (result.patient_name or "").strip()
        patient_last, patient_first = _split_patient_name(patient_name)
        patient_dob = (result.patient_dob or "").strip()
        patient_phone = (result.patient_phone or "").strip()

        prescriber_name = (result.prescriber_name or "").strip()
        prescriber_npi = (result.prescriber_npi or "").strip()

        billing_selection = (result.billing_type or "").strip()
        delivery_date = (result.delivery_date or "").strip()
        notes = (result.notes or "").strip()

        # Insurance snapshot from wizard
        insurance_name = (result.insurance_name or "").strip()
        insurance_kind = (result.insurance_kind or "").strip()
        insurance_member_id = (result.insurance_member_id or "").strip()
        insurance_policy = (result.insurance_policy_number or "").strip()

        # Map kind ("Primary"/"Secondary") to appropriate field
        primary_insurance = insurance_name if insurance_kind == "Primary" else ""
        primary_insurance_id = insurance_member_id if insurance_kind == "Primary" else ""
        secondary_insurance = insurance_name if insurance_kind == "Secondary" else ""
        secondary_insurance_id = insurance_member_id if insurance_kind == "Secondary" else ""

        # Extract patient_id if provided
        patient_id = getattr(result, "patient_id", 0) or 0
        
        # Extract refill_group_id if provided (for linking related orders)
        refill_group_id = getattr(result, "refill_group_id", None)
        
        # Extract rx_origin if provided
        rx_origin = getattr(result, "rx_origin", "") or ""
        
        # Extract on_hold flag if provided
        on_hold = getattr(result, "on_hold", False)
        initial_status = "On Hold" if on_hold else "Unbilled"

        # Basic header insert – we only fill a subset of columns; the rest stay NULL/default.
        cur.execute(
            """
            INSERT INTO orders (
                patient_id,
                rx_date,
                order_date,
                patient_last_name,
                patient_first_name,
                patient_dob,
                patient_phone,
                patient_name,
                prescriber_name,
                prescriber_npi,
                billing_selection,
                order_status,
                delivery_date,
                notes,
                primary_insurance,
                primary_insurance_id,
                secondary_insurance,
                secondary_insurance_id,
                parent_order_id,
                refill_number,
                is_locked,
                refill_group_id,
                rx_origin
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_id or None,
                rx_date_str,
                order_date_str,
                patient_last,
                patient_first,
                patient_dob or None,
                patient_phone or None,
                patient_name or None,
                prescriber_name or None,
                prescriber_npi or None,
                billing_selection or None,
                initial_status,                # "Unbilled" or "On Hold"
                delivery_date or None,
                notes or None,
                primary_insurance or None,
                primary_insurance_id or None,
                secondary_insurance or None,
                secondary_insurance_id or None,
                None,                          # parent_order_id
                0,                             # refill_number
                0,                             # is_locked
                refill_group_id,               # refill_group_id for linking
                rx_origin or None,             # rx_origin
            ),
        )

        order_id = int(cur.lastrowid)
        
        # If no refill_group_id was set, use this order's ID as its own group root
        if refill_group_id is None:
            cur.execute(
                "UPDATE orders SET refill_group_id = ? WHERE id = ?",
                (order_id, order_id),
            )

        # ---- Insert items ----
        # Use the extended order_items schema (compatible with legacy).
        # Columns: order_id, rx_no, hcpcs_code, description,
        #          item_number, refills, day_supply, qty,
        #          cost_ea, total, pa_number, directions, last_filled_date
        today_item_str = order_date_str
        rx_number = ""  # rx_no field is legacy, we now use rx_date in orders table

        for item in result.items:
            # Convert wizard item to domain input (includes rental + modifiers)
            item_input = wizard_item_to_input(item)
            
            hcpcs = (item_input.hcpcs or "").strip()
            desc = (item_input.description or "").strip()
            
            # Use safe_int for robust conversion
            qty = safe_int(item_input.quantity, default=1, field_name=f"item.quantity ({hcpcs})")
            refills = safe_int(item_input.refills, default=0, field_name=f"item.refills ({hcpcs})")
            days = safe_int(item_input.days_supply, default=30, field_name=f"item.days_supply ({hcpcs})")

            # Skip completely empty lines
            if not hcpcs and not desc:
                continue

            cur.execute(
                """
                INSERT INTO order_items (
                    order_id,
                    rx_no,
                    hcpcs_code,
                    description,
                    item_number,
                    refills,
                    day_supply,
                    qty,
                    cost_ea,
                    total,
                    pa_number,
                    directions,
                    last_filled_date,
                    is_rental,
                    modifier1,
                    modifier2,
                    modifier3,
                    modifier4
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    rx_number or "",    # RX# shared across items
                    hcpcs,
                    desc,
                    "",                 # item_number – can be linked to inventory later
                    str(refills),
                    str(days),
                    str(qty),
                    "",                 # cost_ea – optional, left blank
                    "",                 # total – optional, left blank
                    "",                 # pa_number
                    item_input.directions or "",  # directions from wizard
                    today_item_str,
                    1 if item_input.is_rental else 0,  # is_rental from wizard
                    item_input.modifier1,
                    item_input.modifier2,
                    item_input.modifier3,
                    item_input.modifier4,
                ),
            )

        conn.commit()
        return order_id

    finally:
        conn.close()


def create_order_from_wizard_result_uow(
    result: "OrderWizardResult",
    conn: Optional[sqlite3.Connection] = None,
    folder_path: Optional[str] = None,
) -> int:
    """
    UnitOfWork-aware version of create_order_from_wizard_result.
    
    If conn is provided: Uses injected connection (UoW manages commit/rollback)
    If conn is None: Creates own connection and commits (standalone operation)
    
    This enables both patterns:
    - Service layer with UoW: passes conn from UoW
    - Direct repository call: passes None, function owns lifecycle
    
    Args:
        result: OrderWizardResult from wizard
        conn: Optional injected connection from UoW
        folder_path: Database folder path
        
    Returns:
        New order ID
    """
    owns_connection = conn is None
    if owns_connection:
        conn = get_connection("orders.db", folder_path=folder_path)
    
    try:
        cur = conn.cursor()

        # Get dates from wizard result
        rx_date_str = result.rx_date if hasattr(result, 'rx_date') else date.today().strftime("%Y-%m-%d")
        order_date_str = result.order_date if hasattr(result, 'order_date') else date.today().strftime("%Y-%m-%d")

        patient_name = (result.patient_name or "").strip()
        patient_last, patient_first = _split_patient_name(patient_name)
        patient_dob = (result.patient_dob or "").strip()
        patient_phone = (result.patient_phone or "").strip()

        prescriber_name = (result.prescriber_name or "").strip()
        prescriber_npi = (result.prescriber_npi or "").strip()

        billing_selection = (result.billing_type or "").strip()
        delivery_date = (result.delivery_date or "").strip()
        notes = (result.notes or "").strip()
        
        # Extract on_hold flag if provided
        on_hold = getattr(result, "on_hold", False)
        initial_status = "On Hold" if on_hold else "Unbilled"

        # Basic header insert
        cur.execute(
            """
            INSERT INTO orders (
                rx_date,
                order_date,
                patient_last_name,
                patient_first_name,
                patient_dob,
                patient_phone,
                patient_name,
                prescriber_name,
                prescriber_npi,
                billing_selection,
                order_status,
                delivery_date,
                notes,
                parent_order_id,
                refill_number,
                is_locked
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rx_date_str,
                order_date_str,
                patient_last,
                patient_first,
                patient_dob or None,
                patient_phone or None,
                patient_name or None,
                prescriber_name or None,
                prescriber_npi or None,
                billing_selection or None,
                initial_status,                # "Unbilled" or "On Hold"
                delivery_date or None,
                notes or None,
                None,  # parent_order_id
                0,     # refill_number
                0,     # is_locked
            ),
        )

        order_id = int(cur.lastrowid)

        # Insert items
        today_item_str = order_date_str
        rx_number = ""

        for item in result.items:
            hcpcs = (item.hcpcs or "").strip()
            desc = (item.description or "").strip()
            
            qty = safe_int(item.quantity, default=1, field_name=f"item.quantity ({hcpcs})")
            refills = safe_int(item.refills, default=0, field_name=f"item.refills ({hcpcs})")
            days = safe_int(item.days_supply, default=30, field_name=f"item.days_supply ({hcpcs})")

            if not hcpcs and not desc:
                continue

            cur.execute(
                """
                INSERT INTO order_items (
                    order_id,
                    rx_no,
                    hcpcs_code,
                    description,
                    item_number,
                    refills,
                    day_supply,
                    qty,
                    cost_ea,
                    total,
                    pa_number,
                    directions,
                    last_filled_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    rx_number or "",
                    hcpcs,
                    desc,
                    "",
                    str(refills),
                    str(days),
                    str(qty),
                    "",
                    "",
                    "",
                    "",
                    today_item_str,
                ),
            )

        # Only commit if we own the connection
        if owns_connection:
            conn.commit()
            
        return order_id

    finally:
        if owns_connection and conn:
            conn.close()


# ============================================================================
# REFILL TRACKING HELPERS
# ============================================================================


def fetch_order_item_with_header(
    order_item_rowid: int,
    conn: Optional[sqlite3.Connection] = None,
    folder_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch an order_item row along with its parent order header info.

    Args:
        order_item_rowid: The ROWID of the order_items row
        conn: Optional existing connection (for UnitOfWork usage)
        folder_path: Optional database folder path (if conn not provided)

    Returns:
        Dict with combined order + order_item fields, or None if not found.
    """
    close_conn = False
    if conn is None:
        conn = get_connection("orders.db", folder_path=folder_path)
        close_conn = True

    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                oi.rowid AS order_item_rowid,
                oi.order_id,
                oi.rx_no,
                oi.hcpcs_code,
                oi.description,
                oi.item_number,
                oi.refills,
                oi.day_supply,
                oi.qty,
                oi.cost_ea,
                oi.total,
                oi.pa_number,
                oi.directions,
                oi.last_filled_date,
                oi.prescriber_id AS item_prescriber_id,
                oi.prescriber_name AS item_prescriber_name,
                oi.prescriber_npi AS item_prescriber_npi,
                o.order_date,
                o.rx_date,
                o.rx_date_2,
                o.patient_last_name,
                o.patient_first_name,
                o.patient_name,
                o.patient_dob,
                o.patient_phone,
                o.patient_address,
                o.prescriber_name,
                o.prescriber_npi,
                o.prescriber_name_2,
                o.prescriber_npi_2,
                o.patient_id,
                o.primary_insurance,
                o.primary_insurance_id,
                o.secondary_insurance,
                o.secondary_insurance_id,
                o.order_status,
                o.parent_order_id,
                o.refill_number
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.id
            WHERE oi.rowid = ?
            """,
            (order_item_rowid,),
        )
        row = cur.fetchone()
        if not row:
            return None

        # Convert to dict for easier manipulation
        return dict(row)
    finally:
        if close_conn:
            conn.close()


def create_refill_order_from_source(
    src: Dict[str, Any],
    fill_date: str,
    conn: Optional[sqlite3.Connection] = None,
    folder_path: Optional[str] = None,
) -> int:
    """
    Create a new order for a refill based on the source order_item data.

    Args:
        src: Dict containing order + order_item fields from fetch_order_item_with_header
        fill_date: 'YYYY-MM-DD' - the date this refill is being filled
        conn: Optional existing connection (for UnitOfWork usage)
        folder_path: Optional database folder path (if conn not provided)

    Returns:
        The new order ID created for this refill.

    Business Logic:
        - Creates new order with same patient/prescriber info
        - Marks as 'Pending' status
        - Creates single order_item with decremented refills
        - Sets last_filled_date to fill_date on new item
    """
    close_conn = False
    if conn is None:
        conn = get_connection("orders.db", folder_path=folder_path)
        close_conn = True

    try:
        cur = conn.cursor()

        # Compute parent_order_id and refill_number for proper chain tracking
        # The base order is either the parent of the source, or the source itself
        src_parent_id = src.get("parent_order_id") or 0
        src_order_id = src.get("order_id")
        base_order_id = src_parent_id if src_parent_id else src_order_id
        
        # Get max refill number in chain and increment
        cur.execute(
            "SELECT MAX(refill_number) FROM orders WHERE parent_order_id = ? OR id = ?",
            (base_order_id, base_order_id),
        )
        (max_refill,) = cur.fetchone()
        next_refill_number = (int(max_refill) if max_refill is not None else 0) + 1

        # 1. Create new order header
        cur.execute(
            """
            INSERT INTO orders (
                order_date,
                patient_last_name,
                patient_first_name,
                patient_name,
                patient_dob,
                patient_phone,
                patient_address,
                patient_city,
                patient_state,
                patient_zip,
                prescriber_name,
                prescriber_npi,
                prescriber_phone,
                prescriber_name_2,
                prescriber_npi_2,
                rx_date_2,
                diagnosis_code,
                primary_insurance,
                secondary_insurance,
                order_status,
                created_date,
                parent_order_id,
                refill_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill_date,
                src.get("patient_last_name", ""),
                src.get("patient_first_name", ""),
                src.get("patient_name", ""),
                src.get("patient_dob", ""),
                src.get("patient_phone", ""),
                src.get("patient_address", ""),
                src.get("patient_city", ""),
                src.get("patient_state", ""),
                src.get("patient_zip", ""),
                src.get("prescriber_name", ""),
                src.get("prescriber_npi", ""),
                src.get("prescriber_phone", ""),
                src.get("prescriber_name_2", ""),
                src.get("prescriber_npi_2", ""),
                src.get("rx_date_2", ""),
                src.get("diagnosis_code", ""),
                src.get("primary_insurance", ""),
                src.get("secondary_insurance", ""),
                "Unbilled",
                fill_date,  # created_date
                base_order_id,  # parent_order_id - points to original order
                next_refill_number,  # refill_number - increments for each refill
            ),
        )
        new_order_id = cur.lastrowid

        # Force all status/billing flags to the fresh Unbilled state so we never inherit
        # workflow progress from the source order, even if legacy columns exist.
        cur.execute(
            """
            UPDATE orders
               SET order_status = ?,
                   billed = 0,
                   paid = 0,
                   paid_date = NULL,
                   updated_date = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            ("Unbilled", new_order_id),
        )
        try:
            cur.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                ("Unbilled", new_order_id),
            )
        except sqlite3.OperationalError:
            # Legacy databases may not have the old 'status' column anymore.
            pass

        # 2. Create new order_item with decremented refills
        current_refills = safe_int(src.get("refills", "0"))
        remaining_refills = max(0, current_refills - 1)

        cur.execute(
            """
            INSERT INTO order_items (
                order_id,
                rx_no,
                hcpcs_code,
                description,
                item_number,
                refills,
                day_supply,
                qty,
                cost_ea,
                total,
                pa_number,
                directions,
                last_filled_date,
                prescriber_id,
                prescriber_name,
                prescriber_npi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_order_id,
                src.get("rx_no", ""),
                src.get("hcpcs_code", ""),
                src.get("description", ""),
                src.get("item_number", ""),
                str(remaining_refills),
                src.get("day_supply", ""),
                src.get("qty", ""),
                src.get("cost_ea", ""),
                src.get("total", ""),
                src.get("pa_number", ""),
                src.get("directions", ""),
                fill_date,  # last_filled_date set to today
                src.get("item_prescriber_id", ""),
                src.get("item_prescriber_name", ""),
                src.get("item_prescriber_npi", ""),
            ),
        )

        if close_conn:
            conn.commit()

        return new_order_id

    finally:
        if close_conn:
            conn.close()


def mark_refill_used(
    order_item_rowid: int,
    new_last_filled_date: str,
    conn: Optional[sqlite3.Connection] = None,
    folder_path: Optional[str] = None,
) -> None:
    """
    Decrement refills_remaining and update last_filled_date on the source order_item.

    Args:
        order_item_rowid: The ROWID of the order_items row to update
        new_last_filled_date: 'YYYY-MM-DD' - the date this refill was filled
        conn: Optional existing connection (for UnitOfWork usage)
        folder_path: Optional database folder path (if conn not provided)

    Business Logic:
        - Decrements refills by 1 (capped at 0)
        - Updates last_filled_date to the fill date
        - This prevents the item from appearing in future refill queries
          until the next refill is due
    """
    close_conn = False
    if conn is None:
        conn = get_connection("orders.db", folder_path=folder_path)
        close_conn = True

    try:
        cur = conn.cursor()

        # Decrement refills and update last_filled_date
        cur.execute(
            """
            UPDATE order_items
            SET
                refills = CAST(MAX(0, CAST(refills AS INTEGER) - 1) AS TEXT),
                last_filled_date = ?
            WHERE rowid = ?
            """,
            (new_last_filled_date, order_item_rowid),
        )

        if close_conn:
            conn.commit()

    finally:
        if close_conn:
            conn.close()


# ============================================================================
# Refill Processing Helpers
# ============================================================================

def get_max_refill_number(
    base_order_id: int,
    folder_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[int]:
    """
    Get the maximum refill_number for orders in a refill chain.
    
    Args:
        base_order_id: The base order ID (first order in chain)
        folder_path: Database folder path
        conn: Optional existing connection
        
    Returns:
        Maximum refill_number found, or None if no refills exist
    """
    close_conn = conn is None
    if conn is None:
        conn = get_connection("orders.db", folder_path)
    
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(refill_number) FROM orders WHERE parent_order_id = ? OR id = ?",
            (base_order_id, base_order_id),
        )
        (val,) = cur.fetchone()
        return int(val) if val is not None else None
    finally:
        if close_conn:
            conn.close()


def set_order_locked(
    order_id: int,
    locked: bool,
    folder_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None
) -> None:
    """
    Lock or unlock an order to prevent/allow refill processing.
    
    Args:
        order_id: Order to lock/unlock
        locked: True to lock, False to unlock
        folder_path: Database folder path
        conn: Optional existing connection
    """
    close_conn = conn is None
    if conn is None:
        conn = get_connection("orders.db", folder_path)
    
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE orders SET is_locked = ? WHERE id = ?",
            (1 if locked else 0, order_id),
        )
        if close_conn:
            conn.commit()
    finally:
        if close_conn:
            conn.close()


def set_refill_completed(
    order_id: int,
    completed: bool,
    folder_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None
) -> None:
    """
    Mark an order as refill-completed (has been processed as a refill source).
    
    This is separate from is_locked - refill_completed indicates this order
    was processed and shows "REFILLED" in the UI. The new refill order
    should NOT have refill_completed=1.
    
    Args:
        order_id: Order to mark
        completed: True to mark as completed, False to clear
        folder_path: Database folder path
        conn: Optional existing connection
    """
    from datetime import date
    
    close_conn = conn is None
    if conn is None:
        conn = get_connection("orders.db", folder_path)
    
    try:
        cur = conn.cursor()
        if completed:
            cur.execute(
                "UPDATE orders SET refill_completed = 1, refill_completed_at = ? WHERE id = ?",
                (date.today().isoformat(), order_id),
            )
        else:
            cur.execute(
                "UPDATE orders SET refill_completed = 0, refill_completed_at = NULL WHERE id = ?",
                (order_id,),
            )
        if close_conn:
            conn.commit()
    finally:
        if close_conn:
            conn.close()


def delete_order(
    order_id: int,
    *,
    folder_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """
    Delete an order and its items.

    Used for reversing refills; callers must ensure this is safe.
    """
    close_conn = conn is None
    if conn is None:
        conn = get_connection("orders.db", folder_path)

    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        cur.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        if close_conn:
            conn.commit()
        
        # Audit log the deletion
        _audit_action("delete", order_id, "Order deleted")
    finally:
        if close_conn:
            conn.close()


# ============================================================================
# Order Domain Model Fetch
# ============================================================================

def _parse_date(value: Any) -> Optional[date]:
    """
    Safe conversion to date object.
    
    Handles:
    - None/empty → None
    - date object → date
    - "YYYY-MM-DD" → date
    - "MM/DD/YYYY" → date
    - Invalid → None
    """
    if value is None or value == "":
        return None
    
    if isinstance(value, date):
        return value
    
    try:
        # Try ISO format first (YYYY-MM-DD)
        if "-" in str(value):
            return datetime.strptime(str(value), "%Y-%m-%d").date()
        # Try US format (MM/DD/YYYY)
        elif "/" in str(value):
            return datetime.strptime(str(value), "%m/%d/%Y").date()
    except (ValueError, AttributeError):
        pass
    
    return None


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    """
    Safe conversion to Decimal. Returns None if conversion fails or value is empty.
    
    Handles:
    - None/empty string → None
    - TEXT numeric string → Decimal("12.34")
    - Invalid → None
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_order_status(value: Any) -> OrderStatus:
    """
    Safe conversion to OrderStatus enum with fallback to PENDING.
    
    Handles:
    - Valid status string → OrderStatus enum
    - Invalid/None → OrderStatus.PENDING
    """
    if value is None:
        return OrderStatus.PENDING
    
    try:
        return OrderStatus(value)
    except (ValueError, KeyError):
        # Fallback for unknown statuses
        return OrderStatus.PENDING


def _safe_billing_type(value: Any) -> BillingType:
    """
    Safe conversion to BillingType enum with fallback to INSURANCE.
    
    Handles:
    - Valid billing string → BillingType enum (case-insensitive)
    - Invalid/None → BillingType.INSURANCE
    """
    if value is None or value == "":
        return BillingType.INSURANCE
    
    # Case-insensitive lookup
    value_str = str(value).strip().upper()
    for bt in BillingType:
        if bt.value.upper() == value_str:
            return bt
    
    return BillingType.INSURANCE


def _rowset_to_order_domain(order_row: sqlite3.Row, item_rows: list[sqlite3.Row]) -> Order:
    """
    Transform legacy DB rows into rich Order domain model.
    
    This is where we map from the denormalized legacy schema into
    the clean domain model with snapshot fields and FK references.
    
    Args:
        order_row: Row from orders table
        item_rows: Rows from order_items table
        
    Returns:
        Complete Order domain object
    """
    # Helper to safely get column value
    def get_col(row, col_name, default=""):
        try:
            return row[col_name] if col_name in row.keys() else default
        except (KeyError, IndexError):
            return default
    
    # Patient snapshot - compose from legacy fields
    patient_last = (get_col(order_row, "patient_last_name") or "").strip()
    patient_first = (get_col(order_row, "patient_first_name") or "").strip()
    patient_snapshot = f"{patient_last}, {patient_first}".strip(", ") if (patient_last or patient_first) else None
    
    # Patient address snapshot
    addr_raw = get_col(order_row, "patient_address") or ""
    
    # Patient DOB
    patient_dob = _parse_date(get_col(order_row, "patient_dob"))
    
    # Prescriber snapshot
    prescriber_name = (get_col(order_row, "prescriber_name") or "").strip() or None
    prescriber_npi = (get_col(order_row, "prescriber_npi") or "").strip() or None
    
    # Insurance snapshot - with fallback to patient's current insurance for older orders
    insurance_name = (get_col(order_row, "primary_insurance") or "").strip() or None
    insurance_id = (get_col(order_row, "primary_insurance_id") or "").strip() or None
    
    # If insurance is missing (old orders), try to load from patient record
    patient_id = get_col(order_row, "patient_id")
    if not insurance_name and patient_id:
        try:
            from .patients import fetch_patient_by_id
            patient_row = fetch_patient_by_id(patient_id)
            if patient_row:
                insurance_name = (get_col(patient_row, "primary_insurance") or "").strip() or None
                insurance_id = (get_col(patient_row, "policy_number") or "").strip() or None
        except Exception:
            pass  # Ignore errors, keep insurance as None
    
    # Status and billing type (safe enum conversion)
    status_str = (get_col(order_row, "order_status", "Pending") or "Pending").strip()
    order_status = _safe_order_status(status_str)
    
    billing_str = (get_col(order_row, "billing_selection") or "").strip()
    billing_type = _safe_billing_type(billing_str)
    
    # ICD codes into list
    icd_codes = []
    for col in ("icd_code_1", "icd_code_2", "icd_code_3", "icd_code_4", "icd_code_5"):
        v = (get_col(order_row, col) or "").strip()
        if v:
            icd_codes.append(v)
    
    # Dates
    rx_date = _parse_date(get_col(order_row, "rx_date"))
    order_date = _parse_date(get_col(order_row, "order_date"))
    delivery_date = _parse_date(get_col(order_row, "delivery_date"))
    pickup_date = _parse_date(get_col(order_row, "pickup_date"))
    paid_date = _parse_date(get_col(order_row, "paid_date"))
    created_date = _parse_date(get_col(order_row, "created_date"))
    updated_date = _parse_date(get_col(order_row, "updated_date"))
    
    # Doctor directions and notes
    doctor_directions = (get_col(order_row, "doctor_directions") or "").strip() or None
    notes = (get_col(order_row, "notes") or "").strip() or None
    
    # Map items
    items: list[OrderItem] = []
    for r in item_rows:
        # Parse last_filled_date
        last_filled = _parse_date(get_col(r, "last_filled_date"))
        
        items.append(
            OrderItem(
                id=r["id"],
                order_id=r["order_id"],
                inventory_item_id=None,  # Legacy doesn't have this FK yet
                hcpcs_code=(get_col(r, "hcpcs_code") or "").strip(),
                description=(get_col(r, "description") or "").strip(),
                item_number=(get_col(r, "item_number") or "").strip() or None,
                rx_no=(get_col(r, "rx_no") or "").strip() or None,
                quantity=safe_int(get_col(r, "qty"), default=1),
                refills=safe_int(get_col(r, "refills"), default=0),
                days_supply=safe_int(get_col(r, "day_supply"), default=30),
                cost_ea=_decimal_or_none(get_col(r, "cost_ea")),
                total_cost=_decimal_or_none(get_col(r, "total")),
                modifier1=(get_col(r, "modifier1") or "").strip() or None,
                modifier2=(get_col(r, "modifier2") or "").strip() or None,
                modifier3=(get_col(r, "modifier3") or "").strip() or None,
                modifier4=(get_col(r, "modifier4") or "").strip() or None,
                pa_number=(get_col(r, "pa_number") or "").strip() or None,
                directions=(get_col(r, "directions") or "").strip() or None,
                last_filled_date=last_filled,
                rental_month=safe_int(get_col(r, "rental_month"), default=0),
                is_rental=bool(safe_int(get_col(r, "is_rental"), default=0)),
            )
        )
    
    # Build complete Order domain object
    return Order(
        id=order_row["id"],
        
        # FKs (not in legacy schema yet, will add later)
        patient_id=get_col(order_row, "patient_id", None),  # If column exists
        prescriber_id=None,  # Legacy doesn't have this
        insurance_id=None,   # Legacy doesn't have this
        
        # Snapshot fields (what was valid at order time)
        patient_name_at_order_time=patient_snapshot,
        patient_dob_at_order_time=patient_dob,
        patient_address_at_order_time=addr_raw or None,
        prescriber_name_at_order_time=prescriber_name,
        prescriber_npi_at_order_time=prescriber_npi,
        insurance_name_at_order_time=insurance_name,
        insurance_id_at_order_time=insurance_id,
        
        # Dates
        rx_date=rx_date,
        order_date=order_date,
        delivery_date=delivery_date,
        pickup_date=pickup_date,
        created_date=created_date,
        updated_date=updated_date,
        
        # Status and billing
        order_status=order_status,
        billing_type=billing_type,
        
        # Refill tracking
        parent_order_id=get_col(order_row, "parent_order_id", None),
        refill_number=safe_int(get_col(order_row, "refill_number"), default=0),
        is_locked=bool(safe_int(get_col(order_row, "is_locked"), default=0)),
        refill_completed=bool(safe_int(get_col(order_row, "refill_completed"), default=0)),
        refill_completed_at=_parse_date(get_col(order_row, "refill_completed_at", None)),
        
        # Tracking and fulfillment
        tracking_number=(get_col(order_row, "tracking_number") or "").strip() or None,
        is_pickup=bool(safe_int(get_col(order_row, "is_pickup"), default=0)),
        billed=bool(safe_int(get_col(order_row, "billed"), default=0)),
        paid=bool(safe_int(get_col(order_row, "paid"), default=0)),
        paid_date=paid_date,
        
        # Clinical
        icd_codes=icd_codes,
        doctor_directions=doctor_directions,
        
        # Items and notes
        items=items,
        notes=notes,
    )


# Note: fetch_order_with_items is defined earlier in this file (line 81)
# with support for optional conn parameter for transactions


