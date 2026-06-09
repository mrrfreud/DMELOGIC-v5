"""
Order Templates Database Layer
===============================
CRUD operations for order templates (pre-configured item bundles).

Feature #2: Order Templates (DB)

Tables:
    order_templates:
        id INTEGER PRIMARY KEY
        name TEXT NOT NULL
        description TEXT
        billing_type TEXT
        created_at TEXT
        updated_at TEXT

    order_template_items:
        id INTEGER PRIMARY KEY
        template_id INTEGER
        hcpcs TEXT
        description TEXT
        quantity INTEGER DEFAULT 1
        refills INTEGER DEFAULT 0
        days_supply INTEGER DEFAULT 0
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

from dmelogic.db.base import get_connection


# ═══════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TemplateItem:
    hcpcs: str = ""
    description: str = ""
    quantity: int = 1
    refills: int = 0
    days_supply: int = 0
    id: int = 0
    template_id: int = 0


@dataclass
class OrderTemplate:
    name: str = ""
    description: str = ""
    billing_type: str = "Insurance"
    items: List[TemplateItem] = field(default_factory=list)
    id: int = 0
    created_at: str = ""
    updated_at: str = ""


# ═══════════════════════════════════════════════════════════════════
# Schema initialization
# ═══════════════════════════════════════════════════════════════════


def init_templates_db(folder_path: Optional[str] = None) -> None:
    """Create the templates tables if they don't exist."""
    conn = get_connection("orders.db", folder_path=folder_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            billing_type TEXT DEFAULT 'Insurance',
            created_at TEXT,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_template_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            hcpcs TEXT DEFAULT '',
            description TEXT DEFAULT '',
            quantity INTEGER DEFAULT 1,
            refills INTEGER DEFAULT 0,
            days_supply INTEGER DEFAULT 0,
            FOREIGN KEY (template_id) REFERENCES order_templates(id)
                ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════════


def get_all_templates(folder_path: Optional[str] = None) -> List[OrderTemplate]:
    """Return all templates (without items)."""
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM order_templates ORDER BY name")
        templates = []
        for row in cur.fetchall():
            templates.append(OrderTemplate(
                id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                billing_type=row["billing_type"] or "Insurance",
                created_at=row["created_at"] or "",
                updated_at=row["updated_at"] or "",
            ))
        conn.close()
        return templates
    except Exception as e:
        print(f"get_all_templates error: {e}")
        return []


def get_template_with_items(
    template_id: int,
    folder_path: Optional[str] = None,
) -> Optional[OrderTemplate]:
    """Return a single template with its items loaded."""
    try:
        conn = get_connection("orders.db", folder_path=folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM order_templates WHERE id = ?", (template_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None

        template = OrderTemplate(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            billing_type=row["billing_type"] or "Insurance",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

        cur.execute(
            "SELECT * FROM order_template_items WHERE template_id = ? ORDER BY id",
            (template_id,),
        )
        for item_row in cur.fetchall():
            template.items.append(TemplateItem(
                id=item_row["id"],
                template_id=item_row["template_id"],
                hcpcs=item_row["hcpcs"] or "",
                description=item_row["description"] or "",
                quantity=item_row["quantity"] or 1,
                refills=item_row["refills"] or 0,
                days_supply=item_row["days_supply"] or 0,
            ))

        conn.close()
        return template
    except Exception as e:
        print(f"get_template_with_items error: {e}")
        return None


def save_template(
    template: OrderTemplate,
    folder_path: Optional[str] = None,
) -> int:
    """
    Insert or update a template and its items.
    Returns the template ID.
    """
    conn = get_connection("orders.db", folder_path=folder_path)
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if template.id:
            # Update existing
            cur.execute("""
                UPDATE order_templates
                SET name = ?, description = ?, billing_type = ?, updated_at = ?
                WHERE id = ?
            """, (template.name, template.description, template.billing_type, now, template.id))
            tid = template.id

            # Remove old items and re-insert
            cur.execute("DELETE FROM order_template_items WHERE template_id = ?", (tid,))
        else:
            # Insert new
            cur.execute("""
                INSERT INTO order_templates (name, description, billing_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (template.name, template.description, template.billing_type, now, now))
            tid = cur.lastrowid

        # Insert items
        for item in template.items:
            cur.execute("""
                INSERT INTO order_template_items
                    (template_id, hcpcs, description, quantity, refills, days_supply)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (tid, item.hcpcs, item.description, item.quantity, item.refills, item.days_supply))

        conn.commit()
        return tid
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def delete_template(
    template_id: int,
    folder_path: Optional[str] = None,
) -> None:
    """Delete a template and its items."""
    conn = get_connection("orders.db", folder_path=folder_path)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM order_template_items WHERE template_id = ?", (template_id,))
        cur.execute("DELETE FROM order_templates WHERE id = ?", (template_id,))
        conn.commit()
    finally:
        conn.close()
