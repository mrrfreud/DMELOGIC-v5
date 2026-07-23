"""
fax_contact_locations.py
========================
CRUD for the locations attached to a fax contact.

A contact (a row in ``prescribers``) may have many locations — a prescriber
who practices at four offices has four, each with its own facility name,
address, phone and fax. Exactly one is flagged ``is_primary``.

The contact's flat columns (``practice_name``, ``address_line1`` … ``phone``,
``fax``) are kept as a mirror of the primary location so the older code that
reads a prescriber's single address/fax keeps working untouched. Every write
here that can change which location is primary calls ``sync_primary_to_contact``.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from .base import get_connection
from dmelogic.config import debug_log


LOCATION_FIELDS = (
    "facility_name", "address_line1", "address_line2", "city", "state",
    "zip_code", "phone", "fax", "status", "notes",
)


def _conn(folder_path: Optional[str] = None) -> sqlite3.Connection:
    conn = get_connection("prescribers.db", folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_locations(contact_id: int, folder_path: Optional[str] = None) -> List[sqlite3.Row]:
    """All locations for a contact, primary first."""
    try:
        conn = _conn(folder_path)
        try:
            return conn.execute(
                """SELECT * FROM fax_contact_locations
                    WHERE contact_id = ?
                    ORDER BY is_primary DESC,
                             COALESCE(facility_name,'') COLLATE NOCASE ASC,
                             id ASC""",
                (int(contact_id),),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_locations: {e}")
        return []


def get_primary_location(contact_id: int, folder_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """The contact's primary location (falls back to any location)."""
    rows = fetch_locations(contact_id, folder_path=folder_path)
    for r in rows:
        if r["is_primary"]:
            return r
    return rows[0] if rows else None


def add_location(contact_id: int, values: dict, make_primary: bool = False,
                 folder_path: Optional[str] = None) -> Optional[int]:
    """Insert a location. Returns the new id."""
    try:
        conn = _conn(folder_path)
        try:
            cols = [f for f in LOCATION_FIELDS if f in values]
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO fax_contact_locations (contact_id, {', '.join(cols)}) "
                f"VALUES (?, {placeholders})",
                (int(contact_id), *[values.get(c) for c in cols]),
            )
            new_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            # First location for a contact is automatically primary.
            has_primary = conn.execute(
                "SELECT COUNT(*) FROM fax_contact_locations WHERE contact_id = ? AND is_primary = 1",
                (int(contact_id),),
            ).fetchone()[0]
            conn.commit()
            if make_primary or not has_primary:
                set_primary_location(contact_id, new_id, folder_path=folder_path)
            return new_id
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in add_location: {e}")
        return None


def update_location(location_id: int, values: dict, folder_path: Optional[str] = None) -> bool:
    """Update a location's fields; re-syncs the mirror if it is the primary."""
    cols = [f for f in LOCATION_FIELDS if f in values]
    if not cols:
        return False
    try:
        conn = _conn(folder_path)
        try:
            row = conn.execute(
                "SELECT contact_id, is_primary FROM fax_contact_locations WHERE id = ?",
                (int(location_id),),
            ).fetchone()
            conn.execute(
                f"UPDATE fax_contact_locations SET {', '.join(f'{c} = ?' for c in cols)}, "
                f"updated_date = CURRENT_TIMESTAMP WHERE id = ?",
                (*[values.get(c) for c in cols], int(location_id)),
            )
            conn.commit()
        finally:
            conn.close()
        if row and row["is_primary"]:
            sync_primary_to_contact(row["contact_id"], folder_path=folder_path)
        return True
    except Exception as e:
        debug_log(f"DB Error in update_location: {e}")
        return False


def delete_location(location_id: int, folder_path: Optional[str] = None) -> bool:
    """
    Delete a location. Refuses to remove a contact's last remaining location;
    if the primary is deleted, another location is promoted.
    """
    try:
        conn = _conn(folder_path)
        try:
            row = conn.execute(
                "SELECT contact_id, is_primary FROM fax_contact_locations WHERE id = ?",
                (int(location_id),),
            ).fetchone()
            if not row:
                return False
            contact_id = row["contact_id"]
            remaining = conn.execute(
                "SELECT COUNT(*) FROM fax_contact_locations WHERE contact_id = ?",
                (contact_id,),
            ).fetchone()[0]
            if remaining <= 1:
                return False  # never leave a contact with no location
            conn.execute("DELETE FROM fax_contact_locations WHERE id = ?", (int(location_id),))
            conn.commit()
            promote = None
            if row["is_primary"]:
                nxt = conn.execute(
                    "SELECT id FROM fax_contact_locations WHERE contact_id = ? ORDER BY id ASC LIMIT 1",
                    (contact_id,),
                ).fetchone()
                promote = nxt["id"] if nxt else None
        finally:
            conn.close()
        if promote:
            set_primary_location(contact_id, promote, folder_path=folder_path)
        return True
    except Exception as e:
        debug_log(f"DB Error in delete_location: {e}")
        return False


def set_primary_location(contact_id: int, location_id: int,
                         folder_path: Optional[str] = None) -> bool:
    """Make one location primary (clearing the others) and re-sync the mirror."""
    try:
        conn = _conn(folder_path)
        try:
            conn.execute(
                "UPDATE fax_contact_locations SET is_primary = 0 WHERE contact_id = ?",
                (int(contact_id),),
            )
            conn.execute(
                "UPDATE fax_contact_locations SET is_primary = 1, updated_date = CURRENT_TIMESTAMP "
                "WHERE id = ? AND contact_id = ?",
                (int(location_id), int(contact_id)),
            )
            conn.commit()
        finally:
            conn.close()
        sync_primary_to_contact(contact_id, folder_path=folder_path)
        return True
    except Exception as e:
        debug_log(f"DB Error in set_primary_location: {e}")
        return False


def sync_primary_to_contact(contact_id: int, folder_path: Optional[str] = None) -> bool:
    """
    Copy the primary location onto the contact's flat columns.

    This is what keeps every existing prescriber lookup (order creation, the
    agent, the API, refill faxes) working without changes.
    """
    try:
        primary = get_primary_location(contact_id, folder_path=folder_path)
        if not primary:
            return False
        conn = _conn(folder_path)
        try:
            conn.execute(
                """UPDATE prescribers
                      SET practice_name = ?, address_line1 = ?, address_line2 = ?,
                          city = ?, state = ?, zip_code = ?, phone = ?, fax = ?,
                          updated_date = CURRENT_TIMESTAMP
                    WHERE id = ?""",
                (
                    primary["facility_name"], primary["address_line1"], primary["address_line2"],
                    primary["city"], primary["state"], primary["zip_code"],
                    primary["phone"], primary["fax"], int(contact_id),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in sync_primary_to_contact: {e}")
        return False


def count_locations(contact_id: int, folder_path: Optional[str] = None) -> int:
    """How many locations a contact has (shown in the Prescribers table)."""
    try:
        conn = _conn(folder_path)
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM fax_contact_locations WHERE contact_id = ?",
                (int(contact_id),),
            ).fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def fetch_contacts_by_category(category: Optional[str] = None,
                               folder_path: Optional[str] = None) -> List[sqlite3.Row]:
    """
    Contacts filtered by category ("DME", "INS_MLTC", …). None returns all.
    Used by the contact manager and (in phase 3) the fax recipient picker.
    """
    try:
        conn = _conn(folder_path)
        try:
            if category:
                return conn.execute(
                    """SELECT * FROM prescribers WHERE COALESCE(category,'PRESCRIBER') = ?
                        ORDER BY COALESCE(NULLIF(TRIM(display_name),''),
                                          last_name) COLLATE NOCASE ASC""",
                    (category,),
                ).fetchall()
            return conn.execute(
                """SELECT * FROM prescribers
                    ORDER BY COALESCE(NULLIF(TRIM(display_name),''),
                                      last_name) COLLATE NOCASE ASC"""
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in fetch_contacts_by_category: {e}")
        return []


CONTACT_FIELDS = (
    "display_name", "category", "default_cover_message", "notes", "status",
    "contact_person", "contact_position", "contact_phone", "contact_extension",
)


def get_contact(contact_id: int, folder_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Fetch a single contact row."""
    try:
        conn = _conn(folder_path)
        try:
            return conn.execute(
                "SELECT * FROM prescribers WHERE id = ?", (int(contact_id),)
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in get_contact: {e}")
        return None


def update_contact(contact_id: int, values: dict, folder_path: Optional[str] = None) -> bool:
    """
    Update a contact's own fields (name, category, named person, cover message).
    Location/address data is handled separately by the location functions.
    """
    cols = [f for f in CONTACT_FIELDS if f in values]
    if not cols:
        return False
    try:
        conn = _conn(folder_path)
        try:
            conn.execute(
                f"UPDATE prescribers SET {', '.join(f'{c} = ?' for c in cols)}, "
                f"updated_date = CURRENT_TIMESTAMP WHERE id = ?",
                (*[values.get(c) for c in cols], int(contact_id)),
            )
            # Keep last_name aligned with display_name for organizations so the
            # existing name-ordered lists and searches still find them.
            if "display_name" in values:
                from dmelogic.fax_contacts import is_organization
                cat = values.get("category")
                if cat is None:
                    row = conn.execute(
                        "SELECT category FROM prescribers WHERE id = ?", (int(contact_id),)
                    ).fetchone()
                    cat = row["category"] if row else None
                if is_organization(cat):
                    conn.execute(
                        "UPDATE prescribers SET last_name = ?, first_name = '' WHERE id = ?",
                        ((values.get("display_name") or "").strip(), int(contact_id)),
                    )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in update_contact: {e}")
        return False


def create_organization_contact(display_name: str, category: str,
                                default_cover_message: Optional[str] = None,
                                folder_path: Optional[str] = None) -> Optional[int]:
    """
    Create an organization contact (another DME, an Ins/MLTC). These have no
    person fields — just a name, a category and their locations.
    """
    try:
        from dmelogic.fax_contacts import normalize_category, default_cover_message as _default_msg
        cat = normalize_category(category)
        msg = default_cover_message if default_cover_message is not None else _default_msg(cat)
        conn = _conn(folder_path)
        try:
            conn.execute(
                """INSERT INTO prescribers
                       (last_name, first_name, display_name, category,
                        default_cover_message, status, created_date, updated_date)
                   VALUES (?, '', ?, ?, ?, 'Active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                ((display_name or "").strip(), (display_name or "").strip(), cat, msg),
            )
            new_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.commit()
            return new_id
        finally:
            conn.close()
    except Exception as e:
        debug_log(f"DB Error in create_organization_contact: {e}")
        return None
