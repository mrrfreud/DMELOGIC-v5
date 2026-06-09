"""diagnostics.py — Small runtime diagnostics for support and troubleshooting."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .config import SETTINGS_FILE
from .paths import db_dir


def log_db_diagnostics() -> None:
    """Log which DB folder/file is actually being used (and basic order count)."""
    logger = logging.getLogger("diagnostics")

    try:
        settings_path = Path(SETTINGS_FILE)
        logger.info(f"SETTINGS_FILE: {settings_path} exists={settings_path.exists()}")
        logger.info(f"LOCALAPPDATA: {os.environ.get('LOCALAPPDATA')}")
        logger.info(f"USERNAME: {os.environ.get('USERNAME')}")
        logger.info(f"Frozen mode: {getattr(sys, 'frozen', False)}")
        logger.info(f"Executable: {sys.executable}")

        exe_dir = Path(sys.executable).resolve().parent
        data_path_txt = exe_dir / "data_path.txt"
        if data_path_txt.exists():
            try:
                logger.info(f"data_path.txt: {data_path_txt} -> '{data_path_txt.read_text(encoding='utf-8').strip()}'")
            except Exception:
                logger.info(f"data_path.txt: {data_path_txt} (read failed)")
        else:
            logger.info("data_path.txt: (not present)")

        folder = db_dir()
        orders_db = folder / "orders.db"

        logger.info(f"Resolved db_dir(): {folder}")
        if orders_db.exists():
            st = orders_db.stat()
            logger.info(
                f"Resolved orders.db: {orders_db} size={st.st_size} mtime={datetime.fromtimestamp(st.st_mtime).isoformat(sep=' ', timespec='seconds')}"
            )
            try:
                import sqlite3

                conn = sqlite3.connect(str(orders_db))
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
                if cur.fetchone():
                    cur.execute("SELECT COUNT(*) FROM orders")
                    count = int(cur.fetchone()[0])
                else:
                    count = -1
                conn.close()
                logger.info(f"orders table rowcount: {count}")
            except Exception as e:
                logger.info(f"orders table rowcount: (failed: {e})")
        else:
            logger.info(f"Resolved orders.db: {orders_db} exists=False")
    except Exception as e:
        logger.exception(f"Failed to log DB diagnostics: {e}")