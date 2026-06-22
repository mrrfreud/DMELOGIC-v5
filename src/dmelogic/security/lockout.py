"""
Failed-login lockout.

Tracks failed attempts per username and locks accounts after N failures
within M minutes. Lockout duration is configurable via Config.

Lockout state is SQLite-backed so it survives a process restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dmelogic.db.connection import open_db

logger = logging.getLogger("lockout")


@dataclass
class LockoutStatus:
    locked: bool
    locked_until: datetime | None = None
    remaining_attempts: int = 0


def _normalize_username(username: str) -> str:
    return (username or "").strip().casefold()


def init_lockout_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT    NOT NULL,
                ts        TEXT    NOT NULL,
                success   INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts_user_ts ON login_attempts(username, ts)")


def record_attempt(db_path: Path, username: str, success: bool) -> None:
    try:
        normalized = _normalize_username(username)
        if not normalized:
            return
        with open_db(db_path) as conn:
            conn.execute(
                "INSERT INTO login_attempts (username, ts, success) VALUES (?, ?, ?)",
                (
                    normalized,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    1 if success else 0,
                ),
            )
            # Clear failed attempts on success to give the user a fresh slate.
            if success:
                conn.execute(
                    "DELETE FROM login_attempts WHERE (username = ? OR LOWER(username) = ?) AND success = 0",
                    (normalized, normalized),
                )
    except Exception as e:
        logger.warning(f"record_attempt failed for {username}: {e}")


def clear_attempts(db_path: Path, username: str | None = None) -> None:
    """Clear lockout attempt history for one user or for all users."""
    try:
        with open_db(db_path) as conn:
            if username is None:
                conn.execute("DELETE FROM login_attempts")
                return

            normalized = _normalize_username(username)
            if not normalized:
                return

            conn.execute(
                "DELETE FROM login_attempts WHERE username = ? OR LOWER(username) = ?",
                (normalized, normalized),
            )
    except Exception as e:
        logger.warning(f"clear_attempts failed for {username}: {e}")


def check_lockout(
    db_path: Path,
    username: str,
    *,
    max_attempts: int,
    window_minutes: int,
    lockout_minutes: int,
) -> LockoutStatus:
    """
    Return whether `username` is currently locked out.

    Lockout = >= max_attempts failed attempts within window_minutes.
    The lockout itself lasts lockout_minutes from the most recent failure.
    """
    try:
        normalized = _normalize_username(username)
        if not normalized:
            return LockoutStatus(locked=False, remaining_attempts=max_attempts)

        cutoff_window = (
            datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        ).isoformat().replace("+00:00", "Z")
        with open_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT ts FROM login_attempts
                WHERE (username = ? OR LOWER(username) = ?) AND success = 0 AND ts >= ?
                ORDER BY ts DESC
                """,
                (normalized, normalized, cutoff_window),
            ).fetchall()

        failures = len(rows)
        remaining = max(0, max_attempts - failures)

        if failures >= max_attempts:
            most_recent = datetime.fromisoformat(rows[0]["ts"].replace("Z", "+00:00"))
            unlock_at = most_recent + timedelta(minutes=lockout_minutes)
            if datetime.now(timezone.utc) < unlock_at:
                return LockoutStatus(locked=True, locked_until=unlock_at, remaining_attempts=0)

        return LockoutStatus(locked=False, remaining_attempts=remaining)
    except Exception as e:
        logger.warning(f"check_lockout failed for {username}: {e}")
        # Fail open — don't lock people out because the lockout system itself broke.
        return LockoutStatus(locked=False, remaining_attempts=max_attempts)
