from __future__ import annotations

import sqlite3
from typing import Optional

from dmelogic.config import debug_log
from dmelogic.db.base import resolve_db_path


def _build_address(addr: Optional[str], city: Optional[str], state: Optional[str], zipc: Optional[str]) -> Optional[str]:
    """Return a single-line address or None if all components are empty."""
    addr = (addr or "").strip()
    city = (city or "").strip()
    state = (state or "").strip()
    zipc = (zipc or "").strip()

    if not any([addr, city, state, zipc]):
        return None

    city_state = ", ".join([p for p in [city, state] if p])
    tail = (f"{city_state} {zipc}".strip()).strip()
    parts = [p for p in [addr, tail] if p]
    return ", ".join(parts).strip() if parts else None


def get_patient_full_address(
    patient_db_path: str,
    patient_id: Optional[int] = None,
    last: Optional[str] = None,
    first: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve a patient's address from patients.db, preferring patient_id.

    Args:
        patient_db_path: Path to patients.db (use resolve_db_path("patients.db", folder_path=...)).
        patient_id: Optional patient id for direct lookup.
        last: Optional last name for fallback lookup.
        first: Optional first name for fallback lookup.
    """
    db_path = patient_db_path or resolve_db_path("patients.db")
    if not db_path:
        return None

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # 1) Best: by patient_id
        if patient_id is not None:
            cur.execute(
                "SELECT address, city, state, zip FROM patients WHERE id=? LIMIT 1",
                (int(patient_id),),
            )
            row = cur.fetchone()
            if row:
                conn.close()
                return _build_address(row[0], row[1], row[2], row[3])

        # 2) Fallback: by name
        if last and first:
            cur.execute(
                """
                SELECT address, city, state, zip
                FROM patients
                WHERE UPPER(last_name)=? AND UPPER(first_name)=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (last.strip().upper(), first.strip().upper()),
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return _build_address(row[0], row[1], row[2], row[3])

        conn.close()
        return None
    except Exception as exc:  # noqa: BLE001
        debug_log(f"patient_address lookup failed: {exc}")
        return None


def backfill_order_addresses(orders_db: str, patients_db: str) -> int:
    """Update orders.patient_address when blank/placeholder using patients.db lookup.

    Returns count of updated orders.
    """
    if not orders_db or not patients_db:
        return 0

    updated = 0
    orders_path = resolve_db_path(orders_db) if orders_db.endswith(".db") else orders_db
    patients_path = resolve_db_path(patients_db) if patients_db.endswith(".db") else patients_db

    conn_orders = sqlite3.connect(orders_path)
    cur_orders = conn_orders.cursor()
    try:
        cur_orders.execute(
            """
            SELECT id, patient_id, patient_last_name, patient_first_name, patient_address
            FROM orders
            """
        )
        rows = cur_orders.fetchall()

        for oid, pid, last, first, addr in rows:
            addr_norm = (addr or "").strip().upper()
            if addr_norm in ("", "N/A", "NA", "NONE"):
                full_addr = get_patient_full_address(patients_path, pid, last, first)
                if full_addr:
                    cur_orders.execute(
                        "UPDATE orders SET patient_address=? WHERE id=?",
                        (full_addr, oid),
                    )
                    updated += 1
        conn_orders.commit()
    except Exception as exc:  # noqa: BLE001
        debug_log(f"backfill_order_addresses failed: {exc}")
    finally:
        conn_orders.close()

    return updated
