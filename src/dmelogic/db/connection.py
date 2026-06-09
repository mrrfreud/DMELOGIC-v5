"""
Centralized SQLite connection management.

Every connection from here gets:
  - WAL journal mode (multi-reader, single-writer concurrency)
  - foreign_keys = ON (SQLite ships with this OFF)
  - Row factory for dict-style access
  - 30-second busy timeout (waits instead of immediately failing on lock)

Use as a context manager:

    from dmelogic.db.connection import open_db
    with open_db("orders.db") as conn:
        rows = conn.execute("SELECT ...").fetchall()
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("db")

_PRAGMAS_APPLIED: set[str] = set()  # track DBs we've already initialized


def _apply_pragmas(conn: sqlite3.Connection, db_path: str) -> None:
    """Apply session/file-level pragmas. Only journal_mode persists to file."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")

    # journal_mode is persistent — only set it once per file.
    if db_path not in _PRAGMAS_APPLIED:
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode and mode[0].lower() != "wal":
                logger.warning(f"WAL mode not enabled for {db_path}: got {mode[0]}")
            _PRAGMAS_APPLIED.add(db_path)
        except sqlite3.Error as e:
            logger.warning(f"Could not enable WAL on {db_path}: {e}")


@contextmanager
def open_db(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """
    Open a SQLite connection with project-standard settings.
    Commits on clean exit, rolls back on exception, always closes.
    """
    db_path = str(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _apply_pragmas(conn, db_path)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
