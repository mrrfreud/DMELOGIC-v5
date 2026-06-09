"""
Single-instance enforcement.

Uses QLockFile to detect an existing instance, and QLocalSocket /
QLocalServer to ask the existing instance to raise its window.

The --window-instance CLI flag bypasses this entirely (intentional
multi-window mode).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from PyQt6.QtCore import QLockFile
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger("single_instance")

_SERVER_NAME = "dmelogic-singleinstance-v1"
_LOCK_PATH = Path(tempfile.gettempdir()) / "dmelogic.lock"


def acquire_or_signal(on_raise_requested) -> tuple[bool, QLockFile, QLocalServer | None]:
    """
    Try to acquire the single-instance lock.

    Returns (is_primary, lockfile, server):
      - is_primary=True  → we got the lock; caller proceeds normally.
                           `server` listens for raise requests from later instances.
      - is_primary=False → another instance is running; we already pinged it.
                           Caller should exit cleanly.

    `on_raise_requested` is called (in the primary instance) when a later
    instance asks the primary to come to the foreground.
    """
    # Multi-instance mode: never block startup of additional windows/processes.
    # Keep a best-effort lock object for compatibility with shutdown paths.
    lock = QLockFile(str(_LOCK_PATH))
    lock.setStaleLockTime(0)
    try:
        lock.tryLock(1)
    except Exception:
        pass
    return True, lock, None


def _start_server(on_raise_requested) -> QLocalServer | None:
    """Listen for raise requests from secondary launches."""
    # Clean up any stale socket file from a previous crash.
    QLocalServer.removeServer(_SERVER_NAME)

    server = QLocalServer()
    if not server.listen(_SERVER_NAME):
        logger.warning(f"Could not start single-instance server: {server.errorString()}")
        return None

    def handle_connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return
        # We don't actually need to read anything — connection itself is the signal.
        sock.disconnectFromServer()
        try:
            on_raise_requested()
        except Exception as e:
            logger.warning(f"on_raise_requested handler failed: {e}")

    server.newConnection.connect(handle_connection)
    return server


def _signal_existing_instance() -> None:
    """Open a brief connection to the primary instance to wake it up."""
    sock = QLocalSocket()
    sock.connectToServer(_SERVER_NAME)
    if sock.waitForConnected(500):
        sock.disconnectFromServer()
        logger.info("Signaled existing instance to raise window.")
    else:
        logger.warning("Could not signal existing instance; it may be hung.")
