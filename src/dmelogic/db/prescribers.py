from __future__ import annotations

import sqlite3
from typing import List, Optional

from .base import get_connection
from .models import Prescriber
from .converters import row_to_prescriber
from dmelogic.config import debug_log


def fetch_all_prescribers(folder_path: Optional[str] = None) -> List[sqlite3.Row]:
    """
    Return all prescribers ordered by last_name, first_name.
    Returns sqlite3.Row objects (dict-like, subscriptable).
    """
    try:
        conn = get_connection("prescribers.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT *
                FROM prescribers
                ORDER BY last_name COLLATE NOCASE ASC,
                         first_name COLLATE NOCASE ASC
                """
            )
            rows = cur.fetchall()
            return rows
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_all_prescribers: {e}")
        return []


def fetch_prescriber_by_npi(npi: str, folder_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """
    Find a prescriber by their NPI, if present.
    Returns sqlite3.Row object (dict-like, subscriptable).
    """
    conn = get_connection("prescribers.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM prescribers WHERE npi_number = ?", (npi,))
        row = cur.fetchone()
        return row
    finally:
        conn.close()


def fetch_active_prescribers(folder_path: Optional[str] = None) -> List[sqlite3.Row]:
    """
    Return all active prescribers ordered by last_name, first_name.
    Used by prescriber search dialog.
    Returns sqlite3.Row objects (dict-like, subscriptable).
    """
    conn = get_connection("prescribers.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, first_name, last_name, title, npi_number, phone, specialty
            FROM prescribers
            WHERE status = 'Active'
            ORDER BY last_name COLLATE NOCASE ASC,
                     first_name COLLATE NOCASE ASC
            """
        )
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()


def search_prescribers(search_term: str, folder_path: Optional[str] = None) -> List[dict]:
    """
    Search prescribers by name or NPI.
    Supports 'last,first', 'last first', or single-term searches.
    Returns list of dicts with prescriber info.
    """
    try:
        conn = get_connection("prescribers.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            # Split on comma or whitespace for "last,first" / "last first" input
            parts = [p.strip() for p in search_term.replace(',', ' ').split() if p.strip()]
            if len(parts) >= 2:
                # Two-part search: match last AND first (either order)
                like1 = f"%{parts[0]}%"
                like2 = f"%{parts[1]}%"
                cur.execute(
                    """
                    SELECT id, first_name, last_name, title, npi_number, phone, specialty
                    FROM prescribers
                    WHERE (last_name LIKE ? AND first_name LIKE ?)
                       OR (last_name LIKE ? AND first_name LIKE ?)
                    ORDER BY last_name, first_name
                    LIMIT 50
                    """,
                    (like1, like2, like2, like1)
                )
            else:
                term = parts[0] if parts else search_term.strip()
                like = f"%{term}%"
                cur.execute(
                    """
                    SELECT id, first_name, last_name, title, npi_number, phone, specialty
                    FROM prescribers
                    WHERE first_name LIKE ? OR last_name LIKE ?
                       OR (first_name || ' ' || last_name) LIKE ?
                       OR npi_number LIKE ?
                    ORDER BY last_name, first_name
                    LIMIT 50
                    """,
                    (like, like, like, like)
                )
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["npi"] = d.get("npi_number") or ""
                results.append(d)
            return results
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in search_prescribers: {e}")
        return []


def get_prescriber(prescriber_id: int, folder_path: Optional[str] = None) -> Optional[dict]:
    """Get prescriber by ID as dict."""
    try:
        conn = get_connection("prescribers.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM prescribers WHERE id = ?", (prescriber_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in get_prescriber: {e}")
        return None
