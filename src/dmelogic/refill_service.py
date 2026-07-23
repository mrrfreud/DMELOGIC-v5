"""
Refill processing service for DMELogic.

Business Rules:
- Eligibility: RX date ≤ 365 days ago, order not locked, ≥1 item with refills > 0
- New refill order: Same patient/prescriber/insurance/ICD-10, order_date=today, rx_date unchanged
- Items: Copy items where refills > 0, decrement refills by 1
- Refill numbering: parent_order_id = base_order_id, refill_number increments
- Original order: Mark as locked after processing
- Out-of-refill items: Add note to new order
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Tuple, List

from dmelogic.db import orders as orders_repo
from dmelogic.db.base import UnitOfWork
from dmelogic.db.models import Order, OrderItemInput, OrderInput, OrderStatus
from dmelogic.config import debug_log
from dmelogic.place_of_service import place_of_service_code


REFILL_MAX_AGE_DAYS = 365


class RefillError(Exception):
    """Raised when refill processing fails validation."""
    pass


def _compute_base_and_next_refill_number(
    order: Order,
    folder_path: Optional[str]
) -> Tuple[int, int]:
    """
    Determine base_order_id and the next refill_number.
    
    - If order.parent_order_id is set, base = parent_order_id
    - Else base = order.id
    - next_refill_number = max(refill_number) + 1 for all orders with same base
    
    Args:
        order: Source order to refill
        folder_path: Database folder path
        
    Returns:
        Tuple of (base_order_id, next_refill_number)
    """
    base_order_id = order.parent_order_id or order.id
    max_refill = orders_repo.get_max_refill_number(base_order_id, folder_path=folder_path)
    next_refill = (max_refill or 0) + 1
    return base_order_id, next_refill


def process_refill(
    order_id: int,
    *,
    folder_path: Optional[str] = None,
    force: bool = False,
) -> Order:
    """
    Create a new refill order based on an existing order.
    
    - Validates age, locking, and item refills
    - New order has today's order_date, same rx_date
    - Items with refills <= 0 are skipped; others get refills-1
    - Original order is locked
    - Returns the newly created Order domain object
    
    Args:
        order_id: ID of the order to refill
        folder_path: Database folder path
        
    Returns:
        Newly created refill Order
        
    Raises:
        RefillError: On validation failures
    """
    with UnitOfWork(folder_path=folder_path) as uow:
        conn_orders = uow.connection("orders.db")

        # 1) Load source order as domain object
        src = orders_repo.fetch_order_with_items(order_id, folder_path=folder_path, conn=conn_orders)
        if src is None:
            raise RefillError(f"Order {order_id} not found")

        # 2) Basic validations
        if src.refill_completed:
            # Check if the successor refill still exists — if not, auto-clear the flag
            # For refill orders, successors have parent_order_id = base AND refill_number = src + 1
            cur = conn_orders.cursor()
            src_refill_no = int(getattr(src, "refill_number", 0) or 0)
            base_order_id = src.parent_order_id or order_id
            
            # Check for successor order (next refill in the chain)
            cur.execute(
                """SELECT COUNT(*) FROM orders 
                   WHERE (parent_order_id = ? OR id = ?) AND refill_number = ?""",
                (base_order_id, base_order_id, src_refill_no + 1),
            )
            successor_count = cur.fetchone()[0]
            
            if successor_count == 0:
                # Stale flag — the refill was deleted; clear it and proceed
                cur.execute(
                    "UPDATE orders SET refill_completed = 0, refill_completed_at = NULL, is_locked = 0 WHERE id = ?",
                    (order_id,),
                )
                conn_orders.commit()
                src.refill_completed = False
                src.is_locked = False
                debug_log(f"Auto-cleared stale refill_completed/is_locked on order {order_id} (no successor refill exists)")
            else:
                raise RefillError("This order has already been refilled for this cycle.")
        
        if src.is_locked:
            # Check if the successor refill still exists — if not, auto-unlock
            cur = conn_orders.cursor()
            src_refill_no = int(getattr(src, "refill_number", 0) or 0)
            base_order_id = src.parent_order_id or order_id
            
            cur.execute(
                """SELECT COUNT(*) FROM orders 
                   WHERE (parent_order_id = ? OR id = ?) AND refill_number = ?""",
                (base_order_id, base_order_id, src_refill_no + 1),
            )
            successor_count = cur.fetchone()[0]
            
            if successor_count == 0:
                cur.execute(
                    "UPDATE orders SET is_locked = 0 WHERE id = ?",
                    (order_id,),
                )
                conn_orders.commit()
                src.is_locked = False
                debug_log(f"Auto-cleared stale is_locked on order {order_id} (no successor refill exists)")
            else:
                raise RefillError("This order is locked and cannot be refilled.")

        if src.rx_date is None:
            raise RefillError("RX date is missing; cannot process refill.")

        if (not force) and (date.today() - src.rx_date) > timedelta(days=REFILL_MAX_AGE_DAYS):
            raise RefillError("Prescription is older than 365 days; new RX required.")

        refillable_items = []
        out_of_refill_msgs: List[str] = []

        for item in src.items:
            if item.refills is None or item.refills <= 0:
                # out of refill, add a message but do not include in new order
                if item.description:
                    out_of_refill_msgs.append(
                        f"{item.description} is out of refill, please contact provider for a new prescription."
                    )
                continue

            qty = int(item.quantity or 0)
            if qty <= 0:
                if item.description:
                    out_of_refill_msgs.append(
                        f"{item.description} has invalid quantity ({qty}) and was skipped for refill processing."
                    )
                continue

            # copy item, but decrease refills by 1
            refillable_items.append(
                OrderItemInput(
                    hcpcs=item.hcpcs_code,
                    description=item.description or "",
                    quantity=qty,
                    refills=item.refills - 1,
                    days_supply=item.days_supply,
                    directions=item.directions or "",
                    item_number=item.item_number or None if hasattr(item, "item_number") else None,
                    cost_ea=item.cost_ea if isinstance(item.cost_ea, Decimal) else None,
                    # modifiers / rentals if present
                    is_rental=getattr(item, "is_rental", False),
                    modifier1=getattr(item, "modifier1", None),
                    modifier2=getattr(item, "modifier2", None),
                    modifier3=getattr(item, "modifier3", None),
                    modifier4=getattr(item, "modifier4", None),
                )
            )

        if not refillable_items:
            raise RefillError("All items are out of refills; no refill order created.")

        # Note: Max-Units / incompatible-item rules are NOT enforced here.
        # A refill always creates the new, current-dated order by cloning the
        # source; the source order (already filled/billed) must never be edited.
        # Any quantity/combination correction is flagged by the UI on the NEW
        # order so it can be fixed there before billing (see order_rules
        # .run_refill_with_override / evaluate_order_object).

        # 3) Compute base order + next refill number
        base_order_id, next_refill = _compute_base_and_next_refill_number(src, folder_path)

        # Enforce "one refill per cycle" using chain position rather than is_locked.
        # In this design, all refills point to base_order_id (not the immediate predecessor),
        # so the authoritative truth is: the *latest* refill_number in the chain is the only
        # order that can be processed next.
        max_refill_in_chain = (next_refill - 1)
        src_refill_no = int(getattr(src, "refill_number", 0) or 0)
        if src_refill_no < max_refill_in_chain:
            raise RefillError("This order has already been refilled for this cycle.")

        # 4) Compose notes with any out-of-refill messages
        notes = src.notes or ""
        if out_of_refill_msgs:
            extra = "\n".join(out_of_refill_msgs)
            notes = (notes + "\n\n" if notes else "") + extra

        # 5) Build OrderInput for new order
        new_status = OrderStatus.UNBILLED.value

        order_input = OrderInput(
            patient_last_name=src.patient_last_name or "",
            patient_first_name=src.patient_first_name or "",
            patient_dob=str(src.patient_dob) if src.patient_dob else None,
            patient_phone=src.patient_phone,
            patient_address=src.patient_address,
            patient_id=src.patient_id,
            prescriber_id=src.prescriber_id,
            prescriber_name=src.prescriber_name,
            prescriber_npi=src.prescriber_npi,
            insurance_id=src.insurance_id,
            primary_insurance=src.primary_insurance,
            primary_insurance_id=src.primary_insurance_id,
            rx_date=str(src.rx_date) if src.rx_date else None,
            order_date=str(date.today()),
            order_status=new_status,
            billing_type=src.billing_type.value if hasattr(src.billing_type, 'value') else str(src.billing_type),
            icd_codes=list(src.icd_codes),
            notes=notes,
            items=refillable_items,
            parent_order_id=base_order_id,
            refill_number=next_refill,
            place_of_service=place_of_service_code(getattr(src, "place_of_service", None)),
        )

        # 6) Persist new order (refill_completed=0 is the default, ensuring it shows due date not REFILLED)
        new_order_id = orders_repo.create_order(order_input, folder_path=folder_path, conn=conn_orders)

        # 7) Mark source order as refill-completed (shows REFILLED) and lock it
        orders_repo.set_refill_completed(order_id, completed=True, folder_path=folder_path, conn=conn_orders)
        orders_repo.set_order_locked(order_id, locked=True, folder_path=folder_path, conn=conn_orders)

    # UnitOfWork committed - fetch the new order outside the transaction
    new_order = orders_repo.fetch_order_with_items(new_order_id, folder_path=folder_path)
    return new_order


def reverse_refill(
    refill_order_id: int,
    *,
    folder_path: Optional[str] = None,
) -> Order:
    """
    Reverse a previously created refill.

    - Finds the immediate predecessor order (refill_number - 1 in the chain)
    - Restores 1 refill to each matching predecessor item (by HCPCS/description/item_number)
    - Unlocks the predecessor order and clears its refill_completed flag
    - Deletes the refill order and its items
    - Returns the restored predecessor order
    """
    with UnitOfWork(folder_path=folder_path) as uow:
        conn_orders = uow.connection("orders.db")

        # Load the refill order
        refill = orders_repo.fetch_order_with_items(
            refill_order_id,
            folder_path=folder_path,
            conn=conn_orders,
        )
        if refill is None:
            raise RefillError(f"Refill order {refill_order_id} not found")

        if not refill.parent_order_id:
            raise RefillError(f"Order {refill_order_id} is not a refill (no parent_order_id)")

        base_order_id = refill.parent_order_id
        refill_num = int(getattr(refill, "refill_number", 0) or 0)
        
        # Find the immediate predecessor order:
        # - If refill_number is 1, predecessor is the base order
        # - Otherwise, predecessor is the order with refill_number = refill_number - 1
        cur = conn_orders.cursor()
        if refill_num <= 1:
            predecessor_id = base_order_id
        else:
            # Find the order with refill_number = refill_num - 1 in the same chain
            cur.execute(
                """SELECT id FROM orders 
                   WHERE (parent_order_id = ? OR id = ?) AND refill_number = ?""",
                (base_order_id, base_order_id, refill_num - 1),
            )
            row = cur.fetchone()
            if row:
                predecessor_id = row[0]
            else:
                # Fallback to base order if predecessor not found
                predecessor_id = base_order_id
                debug_log(f"Predecessor with refill_number={refill_num - 1} not found, falling back to base order {base_order_id}")

        predecessor = orders_repo.fetch_order_with_items(
            predecessor_id,
            folder_path=folder_path,
            conn=conn_orders,
        )
        if predecessor is None:
            raise RefillError(f"Predecessor order {predecessor_id} not found")

        # Build lookup for predecessor items
        predecessor_lookup = {}
        for item in predecessor.items:
            key = (
                (item.hcpcs_code or "").strip().upper(),
                (item.description or "").strip().upper(),
                (item.item_number or "").strip().upper(),
            )
            predecessor_lookup[key] = item.id

        restored = 0
        for child in refill.items:
            key = (
                (child.hcpcs_code or "").strip().upper(),
                (child.description or "").strip().upper(),
                (child.item_number or "").strip().upper(),
            )
            predecessor_item_id = predecessor_lookup.get(key)
            if predecessor_item_id:
                cur.execute(
                    "UPDATE order_items SET refills = refills + 1 WHERE id = ?",
                    (predecessor_item_id,),
                )
                restored += 1

        debug_log(
            f"Reversing refill order {refill_order_id}: restored refills on {restored} predecessor items and unlocking predecessor {predecessor_id}"
        )

        # Unlock predecessor and clear refill_completed flag
        orders_repo.set_refill_completed(predecessor_id, completed=False, folder_path=folder_path, conn=conn_orders)
        orders_repo.set_order_locked(predecessor_id, locked=False, folder_path=folder_path, conn=conn_orders)

        # Delete the refill order and its items
        orders_repo.delete_order(refill_order_id, folder_path=folder_path, conn=conn_orders)

    # Return the refreshed predecessor order after commit
    return orders_repo.fetch_order_with_items(predecessor_id, folder_path=folder_path)
