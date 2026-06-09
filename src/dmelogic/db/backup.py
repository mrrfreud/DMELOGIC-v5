"""
Snapshot all *.db files before migrations run.

Uses sqlite3's online backup API so it works even on a DB that's open
elsewhere. Keeps the last N backup directories, oldest pruned first.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("backup")


def snapshot_databases(db_dir: Path, backups_root: Path, keep_last: int = 7) -> Path | None:
    """
    Copy every *.db file in db_dir into backups_root/<timestamp>/.
    Returns the snapshot directory, or None if nothing was backed up.
    """
    db_files = sorted(db_dir.glob("*.db"))
    if not db_files:
        logger.info("No databases to back up.")
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snapshot_dir = backups_root / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for src in db_files:
        dest = snapshot_dir / src.name
        try:
            # Online backup API — safe even if another connection has the DB open.
            with sqlite3.connect(str(src)) as src_conn, sqlite3.connect(str(dest)) as dst_conn:
                src_conn.backup(dst_conn)
            logger.info(f"Backed up {src.name} → {dest}")
        except sqlite3.Error as e:
            # Fall back to file copy. Less safe but better than nothing.
            logger.warning(f"sqlite3 backup failed for {src.name} ({e}); falling back to file copy.")
            try:
                shutil.copy2(src, dest)
            except OSError as copy_err:
                logger.error(f"Could not back up {src.name}: {copy_err}")

    _prune_old_snapshots(backups_root, keep_last)
    return snapshot_dir


def _prune_old_snapshots(backups_root: Path, keep_last: int) -> None:
    """Keep only the N most recent backup directories."""
    if keep_last <= 0:
        return
    snapshots = sorted(
        (p for p in backups_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in snapshots[keep_last:]:
        try:
            shutil.rmtree(old)
            logger.info(f"Pruned old backup: {old.name}")
        except OSError as e:
            logger.warning(f"Could not prune {old}: {e}")
