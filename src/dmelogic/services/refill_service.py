"""
refill_service.py — Business logic for processing refill orders.

This service orchestrates the creation of new orders from refillable items,
using the UnitOfWork pattern to ensure transactional consistency across
multiple database operations.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from dmelogic.db.base import UnitOfWork, ensure_writes_allowed
from dmelogic.db import orders
from dmelogic.config import debug_log


def process_refill(
    order_id: int,
    folder_path: Optional[str] = None,
    force: bool = False,
):
    """
    Compatibility wrapper for single-order refill processing.

    Delegates to the top-level refill service that manages refill chains.
    """
    from dmelogic.refill_service import process_refill as _process_single

    return _process_single(order_id, folder_path=folder_path, force=force)


def process_refills(
    selected_item_ids: Iterable[int],
    refill_fill_date: Optional[str] = None,
    folder_path: Optional[str] = None,
) -> int:
    """
    Process refill orders for selected order items.

    For each selected order_item rowid:
      1. Create a new order for that patient/prescriber
      2. Decrement refills and update last_filled_date on the source item
      3. Optionally reserve inventory and create billing records (future)

    Args:
        selected_item_ids: Iterable of order_item rowids to process
        refill_fill_date: 'YYYY-MM-DD' date for the refill, defaults to today
        folder_path: Optional database folder path

    Returns:
        Count of successfully processed refills

    Raises:
        WritesBlockedError: If backup is in progress

    Business Rules:
        - Each refill creates a new order in 'Pending' status
        - Source item refills are decremented by 1
        - Source item last_filled_date is updated to prevent premature re-refill
        - All operations for a single refill are atomic (UnitOfWork)
        - If any refill fails, it's logged and skipped (doesn't abort others)

    Example:
        >>> from dmelogic.services.refill_service import process_refills
        >>> selected_ids = [42, 57, 89]  # order_item rowids from UI
        >>> count = process_refills(selected_ids, refill_fill_date='2025-12-10')
        >>> print(f"Processed {count} refills")
    """
    # Check if writes are allowed (not blocked by backup)
    ensure_writes_allowed()
    
    if refill_fill_date is None:
        refill_fill_date = date.today().strftime("%Y-%m-%d")

    processed_count = 0

    # Process each refill in its own UnitOfWork for isolation
    # If one fails, others can still succeed
    for item_id in selected_item_ids:
        try:
            with UnitOfWork(folder_path=folder_path) as uow:
                orders_conn = uow.connection("orders.db")

                # 1) Load original item + its order header
                src = orders.fetch_order_item_with_header(
                    order_item_rowid=item_id,
                    conn=orders_conn,
                )

                if not src:
                    debug_log(f"Refill skipped: order_item {item_id} not found")
                    continue

                # 2) Create a new order for this refill
                new_order_id = orders.create_refill_order_from_source(
                    src,
                    fill_date=refill_fill_date,
                    conn=orders_conn,
                )

                # 3) Decrement refills and update last_filled_date on source item
                orders.mark_refill_used(
                    order_item_rowid=item_id,
                    new_last_filled_date=refill_fill_date,
                    conn=orders_conn,
                )

                # 4) Future: Reserve inventory
                # inventory_conn = uow.connection("inventory.db")
                # inventory.reserve_item_for_refill(
                #     hcpcs_code=src["hcpcs_code"],
                #     quantity=src["qty"],
                #     conn=inventory_conn,
                # )

                processed_count += 1
                debug_log(
                    f"Refill processed: item {item_id} -> new order {new_order_id}"
                )

            # UnitOfWork commits on successful exit
        except Exception as e:
            debug_log(f"Failed to process refill for item {item_id}: {e}")
            # Continue processing other items

    return processed_count


def process_refills_grouped(
    selected_item_ids: Iterable[int],
    refill_fill_date: Optional[str] = None,
    folder_path: Optional[str] = None,
) -> dict:
    """
    Process refills with intelligent grouping by patient/prescriber.

    Instead of creating one order per item, this groups multiple items
    for the same patient/prescriber into a single order.

    Args:
        selected_item_ids: Iterable of order_item rowids to process
        refill_fill_date: 'YYYY-MM-DD' date for the refill, defaults to today
        folder_path: Optional database folder path

    Returns:
        Dict with 'orders_created' count and 'items_processed' count

    Business Rules:
        - Groups items by (patient_name, patient_dob, prescriber_npi)
        - Creates one order per group with multiple items
        - More efficient than one order per item
        - All items in a group are atomic (same UnitOfWork)

    Note:
        This is a more advanced version. Start with process_refills() first.
    """
    if refill_fill_date is None:
        refill_fill_date = date.today().strftime("%Y-%m-%d")

    # Group items by patient/prescriber
    from collections import defaultdict

    groups = defaultdict(list)

    with UnitOfWork(folder_path=folder_path) as uow:
        orders_conn = uow.connection("orders.db")

        # Phase 1: Fetch all items and group them
        for item_id in selected_item_ids:
            try:
                src = orders.fetch_order_item_with_header(
                    order_item_rowid=item_id,
                    conn=orders_conn,
                )
                if not src:
                    continue

                # Group key: patient + prescriber
                key = (
                    src.get("patient_name", ""),
                    src.get("patient_dob", ""),
                    src.get("prescriber_npi", ""),
                )
                groups[key].append((item_id, src))
            except Exception as e:
                debug_log(f"Failed to fetch item {item_id}: {e}")

        # Phase 2: Create orders for each group
        orders_created = 0
        items_processed = 0

        for group_key, items in groups.items():
            try:
                # Create one order for this group (use first item as template)
                first_item_id, first_src = items[0]
                new_order_id = orders.create_refill_order_from_source(
                    first_src,
                    fill_date=refill_fill_date,
                    conn=orders_conn,
                )

                # Mark all items in this group as used
                for item_id, src in items:
                    orders.mark_refill_used(
                        order_item_rowid=item_id,
                        new_last_filled_date=refill_fill_date,
                        conn=orders_conn,
                    )
                    items_processed += 1

                orders_created += 1
                debug_log(
                    f"Grouped refill: order {new_order_id} with {len(items)} items"
                )

            except Exception as e:
                debug_log(f"Failed to process group {group_key}: {e}")

    return {
        "orders_created": orders_created,
        "items_processed": items_processed,
    }
