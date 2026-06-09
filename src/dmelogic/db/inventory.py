"""
inventory.py — Database operations for inventory items.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from .base import get_connection
from .models import InventoryItem
from .converters import row_to_inventory_item
from dmelogic.config import debug_log


def fetch_all_inventory(folder_path: Optional[str] = None) -> List[sqlite3.Row]:
    """
    Return all inventory items ordered by category, hcpcs_code, description.
    Returns sqlite3.Row objects (dict-like, subscriptable).
    """
    try:
        conn = get_connection("inventory.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT *
                FROM inventory
                ORDER BY
                    category COLLATE NOCASE ASC,
                    hcpcs_code COLLATE NOCASE ASC,
                    description COLLATE NOCASE ASC
                """
            )
            rows = cur.fetchall()
            return rows
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_all_inventory: {e}")
        return []


def fetch_item_by_code(item_code: str, folder_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """
    Fetch a single inventory item by its hcpcs_code.
    Returns sqlite3.Row object (dict-like, subscriptable).
    """
    conn = get_connection("inventory.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM inventory WHERE hcpcs_code = ?", (item_code,))
        row = cur.fetchone()
        return row
    finally:
        conn.close()


def fetch_latest_item_by_hcpcs(hcpcs_code: str, folder_path: Optional[str] = None) -> Optional[dict]:
    """
    Fetch the most recently updated inventory item matching the given HCPCS code.
    Used by order wizard to auto-fill item details.
    
    Handles hyphenated HCPCS codes like "A6530-SMLCTBG65" by matching:
    1. Exact match first
    2. Then prefix match (inventory hcpcs_code starts with the search code)
    
    Returns dict with item_number, retail_price, description for lightweight use.
    For full model, use fetch_item_by_code().
    """
    conn = get_connection("inventory.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        # First try exact match
        cur.execute(
            """
            SELECT item_number, retail_price, description
            FROM inventory
            WHERE UPPER(hcpcs_code) = UPPER(?)
            ORDER BY datetime(COALESCE(updated_date, created_date)) DESC, item_id DESC
            LIMIT 1
            """,
            (hcpcs_code,)
        )
        row = cur.fetchone()
        
        # If no exact match, try prefix match (for "A6530" matching "A6530-SMLCTBG65")
        if not row:
            cur.execute(
                """
                SELECT item_number, retail_price, description
                FROM inventory
                WHERE UPPER(hcpcs_code) LIKE UPPER(?) || '%'
                   OR UPPER(hcpcs_code) LIKE UPPER(?) || '-%'
                ORDER BY datetime(COALESCE(updated_date, created_date)) DESC, item_id DESC
                LIMIT 1
                """,
                (hcpcs_code, hcpcs_code)
            )
            row = cur.fetchone()
        
        if not row:
            return None
        
        return {
            "item_number": row["item_number"],
            "retail_price": row["retail_price"],
            "description": row["description"]
        }
    finally:
        conn.close()
