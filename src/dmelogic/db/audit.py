"""
Append-only audit log.

Records who did what, to which record, when. Designed for forensic
questions like "who voided ORD-2847?" — never for analytics.

Schema is intentionally minimal:
    (timestamp, user_id, username, action, target_type, target_id, details_json)

`details_json` is optional free-form context (old/new values, IP, etc.).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from dmelogic.db.connection import open_db

logger = logging.getLogger("audit")


def init_audit_db(audit_db_path: Path) -> None:
    """Create the audit table and indexes if they don't exist."""
    audit_db_path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(audit_db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT    NOT NULL,
                user_id       INTEGER,
                username      TEXT,
                action        TEXT    NOT NULL,
                target_type   TEXT,
                target_id     TEXT,
                details_json  TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id)")


def record(
    audit_db_path: Path,
    *,
    user_id: int | None,
    username: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Append one row to the audit log.

    Failures are logged but never raised — audit logging must never
    block a user action.
    """
    try:
        with open_db(audit_db_path) as conn:
            conn.execute(
                """
                INSERT INTO audit_log
                    (ts, user_id, username, action, target_type, target_id, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat() + "Z",
                    user_id,
                    username,
                    action,
                    target_type,
                    target_id,
                    json.dumps(details, default=str) if details else None,
                ),
            )
    except Exception as e:
        logger.warning(f"audit.record failed for action={action}: {e}")
