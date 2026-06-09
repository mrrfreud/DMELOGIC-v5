"""
refills.py — Repository functions for refill tracking.

This module handles queries for order items that are due for refills,
computing next_refill_due dates and filtering by date ranges.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional, TypedDict

from .base import get_connection
from dmelogic.config import debug_log


class RefillRow(TypedDict):
    """Type definition for a refill-due row returned by fetch_refills_due."""
    order_item_id: int
    order_id: int
    order_date: str
    patient_name: str
    patient_dob: str
    patient_phone: str
    hcpcs_code: str
    description: str
    refills_remaining: int
    day_supply: int
    last_filled_date: str
    next_refill_due: str
    days_until_due: int
    prescriber_name: str


def fetch_refills_due(
    start_date: str,
    end_date: str,
    today: str,
    folder_path: Optional[str] = None,
) -> List[RefillRow]:
    """
    Return refillable order items whose next refill due falls between
    [start_date, end_date] inclusive.

    Args:
        start_date: 'YYYY-MM-DD' - beginning of date range
        end_date: 'YYYY-MM-DD' - end of date range
        today: 'YYYY-MM-DD' - current date for computing days_until_due
        folder_path: Optional database folder path

    Returns:
        List of RefillRow dicts with order, patient, item, and computed refill info.

        Business Rules:
                - Only includes items with refills > 0 and day_supply > 0
                - Uses same base date rule as Orders tab refill due column:
                    due_date = COALESCE(order_date, rx_date) + day_supply days
                - Supports both ISO and MM/DD/YYYY date text in order/rx dates
                - Filters for next_refill_due between start_date and end_date
                - Sorted by next_refill_due, then patient name
    """
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()

            # Build optional safety filters only when the schema supports them.
            cur.execute("PRAGMA table_info(orders)")
            order_columns = {str(r[1]).lower() for r in cur.fetchall()}

            # Match Orders tab logic: base date is order_date first, then rx_date.
            # Accept YYYY-MM-DD* and MM/DD/YYYY* strings.
            base_date_expr = """
                COALESCE(
                    CASE
                        WHEN COALESCE(o.order_date, '') LIKE '____-__-__%' THEN substr(o.order_date, 1, 10)
                        WHEN COALESCE(o.order_date, '') LIKE '__/__/____%' THEN
                            substr(substr(o.order_date, 1, 10), 7, 4) || '-' ||
                            substr(substr(o.order_date, 1, 10), 1, 2) || '-' ||
                            substr(substr(o.order_date, 1, 10), 4, 2)
                        ELSE NULL
                    END,
                    CASE
                        WHEN COALESCE(o.rx_date, '') LIKE '____-__-__%' THEN substr(o.rx_date, 1, 10)
                        WHEN COALESCE(o.rx_date, '') LIKE '__/__/____%' THEN
                            substr(substr(o.rx_date, 1, 10), 7, 4) || '-' ||
                            substr(substr(o.rx_date, 1, 10), 1, 2) || '-' ||
                            substr(substr(o.rx_date, 1, 10), 4, 2)
                        ELSE NULL
                    END
                )
            """

            # We must repeat this expression in WHERE because SQLite does not
            # allow referencing SELECT aliases there.
            next_due_expr = """
                date(
                    {base_date_expr},
                    printf('+%d days', CAST(oi.day_supply AS INTEGER))
                )
            """.format(base_date_expr=base_date_expr)

            extra_filters: list[str] = []
            if "deleted_at" in order_columns:
                extra_filters.append("o.deleted_at IS NULL")
            if "refill_completed" in order_columns:
                # Source orders already used for refill creation must never reappear as due.
                extra_filters.append("COALESCE(o.refill_completed, 0) = 0")
            if "is_locked" in order_columns:
                extra_filters.append("COALESCE(o.is_locked, 0) = 0")
            if "parent_order_id" in order_columns:
                # Exclude root orders that already have refill children.
                extra_filters.append(
                    "o.id NOT IN (SELECT DISTINCT parent_order_id FROM orders WHERE parent_order_id IS NOT NULL)"
                )
            if "parent_order_id" in order_columns and "refill_number" in order_columns:
                newer_extra = ""
                if "deleted_at" in order_columns:
                    newer_extra = " AND newer.deleted_at IS NULL"

                # Keep only the newest order in each refill chain.
                extra_filters.append(
                    f"""
                    NOT EXISTS (
                        SELECT 1
                        FROM orders newer
                        WHERE COALESCE(newer.parent_order_id, newer.id) = COALESCE(o.parent_order_id, o.id)
                          AND (
                                COALESCE(newer.refill_number, 0) > COALESCE(o.refill_number, 0)
                                OR (
                                    COALESCE(newer.refill_number, 0) = COALESCE(o.refill_number, 0)
                                    AND newer.id > o.id
                                )
                              )
                          {newer_extra}
                    )
                    """.strip()
                )

            extra_where_sql = ""
            if extra_filters:
                extra_where_sql = "\n                AND " + "\n                AND ".join(extra_filters)

            sql = f"""
            SELECT
                oi.rowid AS order_item_id,
                o.id      AS order_id,
                o.order_date,
                COALESCE(o.patient_name,
                         TRIM(o.patient_last_name || ', ' || o.patient_first_name)) AS patient_name,
                o.patient_dob,
                o.patient_phone,
                oi.hcpcs_code,
                oi.description,
                CAST(oi.refills AS INTEGER) AS refills_remaining,
                CAST(oi.day_supply AS INTEGER) AS day_supply,
                oi.last_filled_date,
                {next_due_expr} AS next_refill_due,
                CAST(julianday({next_due_expr}) - julianday(?) AS INTEGER) AS days_until_due,
                o.prescriber_name
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.id
            WHERE
                CAST(oi.refills AS INTEGER) > 0
                AND CAST(oi.day_supply AS INTEGER) > 0
                AND {base_date_expr} IS NOT NULL
                AND {next_due_expr} BETWEEN ? AND ?
                {extra_where_sql}
            ORDER BY
                next_refill_due ASC,
                o.patient_last_name COLLATE NOCASE ASC,
                o.patient_first_name COLLATE NOCASE ASC
            """

            cur.execute(sql, (today, start_date, end_date))
            rows = cur.fetchall()

            result: List[RefillRow] = []
            for r in rows:
                result.append(
                    RefillRow(
                        order_item_id=r["order_item_id"],
                        order_id=r["order_id"],
                        order_date=r["order_date"] or "",
                        patient_name=r["patient_name"] or "",
                        patient_dob=r["patient_dob"] or "",
                        patient_phone=r["patient_phone"] or "",
                        hcpcs_code=r["hcpcs_code"] or "",
                        description=r["description"] or "",
                        refills_remaining=int(r["refills_remaining"] or 0),
                        day_supply=int(r["day_supply"] or 0),
                        last_filled_date=r["last_filled_date"] or "",
                        next_refill_due=r["next_refill_due"] or "",
                        days_until_due=int(r["days_until_due"] or 0),
                        prescriber_name=r["prescriber_name"] or "",
                    )
                )
            return result
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_refills_due: {e}")
        return []
