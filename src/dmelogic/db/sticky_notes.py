"""Sticky Notes data layer (notes.db).

Supports CRUD plus linking to patients, orders, or prescribers.
Always use get_connection to honor folder_path and PyInstaller paths.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any

from dmelogic.db.base import get_connection, row_to_dict, rows_to_dicts
from dmelogic.config import debug_log

NOTE_DB_FILE = "notes.db"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_notes_db(folder_path: Optional[str] = None) -> None:
    """Ensure schema exists for notes and links."""
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sticky_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#FFF7A8',
            pinned INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sticky_note_links (
            note_id INTEGER NOT NULL,
            entity_type TEXT NOT NULL CHECK(entity_type IN ('patient','order','prescriber')),
            entity_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(note_id, entity_type, entity_id),
            FOREIGN KEY(note_id) REFERENCES sticky_notes(id) ON DELETE CASCADE
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sticky_notes_updated ON sticky_notes(updated_at);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_links_entity ON sticky_note_links(entity_type, entity_id);")
    conn.commit()
    try:
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        debug_log(f"notes.db initialized at {db_path}")
    except Exception:
        pass


def create_note(
    title: str,
    body: str,
    color: str = "#FFF7A8",
    pinned: bool = False,
    folder_path: Optional[str] = None,
) -> int:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    ts = _now()
    cur = conn.execute(
        """
        INSERT INTO sticky_notes (title, body, color, pinned, archived, created_at, updated_at)
        VALUES (?, ?, ?, ?, 0, ?, ?)
        """,
        (title or "", body or "", color or "#FFF7A8", 1 if pinned else 0, ts, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_note(
    note_id: int,
    title: str,
    body: str,
    color: str,
    pinned: bool,
    archived: bool,
    folder_path: Optional[str] = None,
) -> None:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.execute(
        """
        UPDATE sticky_notes
        SET title = ?, body = ?, color = ?, pinned = ?, archived = ?, updated_at = ?
        WHERE id = ?
        """,
        (title or "", body or "", color or "#FFF7A8", 1 if pinned else 0, 1 if archived else 0, _now(), note_id),
    )
    conn.commit()


def archive_note(note_id: int, archived: bool = True, folder_path: Optional[str] = None) -> None:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.execute(
        "UPDATE sticky_notes SET archived = ?, updated_at = ? WHERE id = ?",
        (1 if archived else 0, _now(), note_id),
    )
    conn.commit()


def delete_note(note_id: int, folder_path: Optional[str] = None) -> None:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.execute("DELETE FROM sticky_notes WHERE id = ?", (note_id,))
    conn.commit()


def get_note(note_id: int, folder_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a note by ID, including its links."""
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM sticky_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return None
    note = row_to_dict(row)
    note["links"] = get_note_links(note_id, folder_path=folder_path)
    return note


def set_note_links(note_id: int, links: List[Tuple[str, int]], folder_path: Optional[str] = None) -> None:
    """Replace all links for a note in one transaction."""
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    ts = _now()
    with conn:
        conn.execute("DELETE FROM sticky_note_links WHERE note_id = ?", (note_id,))
        for entity_type, entity_id in links or []:
            conn.execute(
                """
                INSERT OR IGNORE INTO sticky_note_links (note_id, entity_type, entity_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (note_id, entity_type, int(entity_id), ts),
            )


def get_note_links(note_id: int, folder_path: Optional[str] = None) -> List[Tuple[str, int]]:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    rows = conn.execute(
        "SELECT entity_type, entity_id FROM sticky_note_links WHERE note_id = ? ORDER BY entity_type, entity_id",
        (note_id,),
    ).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def list_notes(
    include_archived: bool = False,
    search: Optional[str] = None,
    folder_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    params: list = []
    where: list[str] = []
    if not include_archived:
        where.append("archived = 0")
    if search:
        where.append("(title LIKE ? OR body LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"""
        SELECT n.*, (
            SELECT COUNT(*) FROM sticky_note_links l WHERE l.note_id = n.id
        ) AS links_count
        FROM sticky_notes n
        {where_sql}
        ORDER BY n.pinned DESC, datetime(n.updated_at) DESC
        """,
        params,
    ).fetchall()
    return rows_to_dicts(rows)


def list_unlinked_notes(include_archived: bool = False, folder_path: Optional[str] = None) -> List[Dict[str, Any]]:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT n.*,
            (SELECT COUNT(*) FROM sticky_note_links l WHERE l.note_id = n.id) AS links_count
        FROM sticky_notes n
        WHERE (
            SELECT COUNT(*) FROM sticky_note_links l WHERE l.note_id = n.id
        ) = 0
        AND (? OR n.archived = 0)
        ORDER BY n.pinned DESC, datetime(n.updated_at) DESC
        """,
        (1 if include_archived else 0,),
    ).fetchall()
    return rows_to_dicts(rows)


def list_notes_for_entity(
    entity_type: str,
    entity_id: int,
    include_archived: bool = False,
    folder_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT n.*,
            (SELECT COUNT(*) FROM sticky_note_links l2 WHERE l2.note_id = n.id) AS links_count
        FROM sticky_notes n
        JOIN sticky_note_links l ON l.note_id = n.id
        WHERE l.entity_type = ? AND l.entity_id = ? AND (? OR n.archived = 0)
        ORDER BY n.pinned DESC, datetime(n.updated_at) DESC
        """,
        (entity_type, int(entity_id), 1 if include_archived else 0),
    ).fetchall()
    return rows_to_dicts(rows)


def get_note_with_links(note_id: int, folder_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    note = get_note(note_id, folder_path=folder_path)
    if not note:
        return None
    note["links"] = get_note_links(note_id, folder_path=folder_path)
    return note


def reorder_notes(note_ids: List[int], folder_path: Optional[str] = None) -> None:
    """Persist new note order after drag-drop. Updates updated_at to maintain order."""
    init_notes_db(folder_path)
    conn = get_connection(NOTE_DB_FILE, folder_path=folder_path)
    # For now, just touch the notes in order so newest comes first
    # The list view orders by updated_at DESC, so we touch them in reverse
    ts_base = datetime.now()
    with conn:
        for idx, note_id in enumerate(reversed(note_ids)):
            # Each note gets a slightly later timestamp
            ts = (ts_base.isoformat(timespec="milliseconds") + f".{idx:04d}")
            conn.execute(
                "UPDATE sticky_notes SET updated_at = ? WHERE id = ?",
                (ts, note_id),
            )
