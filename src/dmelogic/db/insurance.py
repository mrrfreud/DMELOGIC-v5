"""
insurance.py — Database operations for insurance names.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from .base import get_connection
from dmelogic.config import debug_log


def fetch_all_insurance(folder_path: Optional[str] = None) -> List[sqlite3.Row]:
    """
    Return all insurance names ordered by usage count and name.
    """
    try:
        conn = get_connection("insurance_names.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT *
                FROM insurance_names
                ORDER BY usage_count DESC, name ASC
                """
            )
            return cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_all_insurance: {e}")
        return []


def fetch_insurance_by_id(ins_id: int, folder_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """
    Fetch a single insurance record by id.
    """
    conn = get_connection("insurance_names.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM insurance_names WHERE id = ?", (ins_id,))
        return cur.fetchone()
    finally:
        conn.close()
