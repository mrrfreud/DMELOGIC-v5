"""
Idle-session lock.

Tracks user input (mouse, keyboard) on the QApplication. After N minutes
of inactivity, locks the window behind a re-auth dialog WITHOUT logging
out — unsaved state in tabs is preserved.

Important for shared pharmacy workstations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from PyQt6.QtCore import QEvent, QObject, QTimer

logger = logging.getLogger("idle_lock")


class IdleLockManager(QObject):
    """
    Install on a QApplication. Watches for input events; if none arrive
    within `timeout_minutes`, calls `lock_callback()` once.

    Caller's `lock_callback` is responsible for actually presenting the
    re-auth dialog and resetting `notify_activity()` on success.
    """

    # Event types that count as user activity.
    _ACTIVITY_EVENTS = {
        QEvent.Type.MouseMove,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.KeyPress,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
    }

    def __init__(self, app, timeout_minutes: int, lock_callback, parent=None):
        super().__init__(parent)
        self._app = app
        self._timeout = timedelta(minutes=timeout_minutes)
        self._lock_callback = lock_callback
        self._last_activity = datetime.utcnow()
        self._locked = False
        self._deferred_due_to_modal = False

        self._timer = QTimer(self)
        self._timer.setInterval(30_000)  # check every 30s
        self._timer.timeout.connect(self._check)
        self._timer.start()

        app.installEventFilter(self)
        logger.info(f"IdleLockManager started — timeout {timeout_minutes} min")

    def eventFilter(self, obj, event):
        if event.type() in self._ACTIVITY_EVENTS:
            self._last_activity = datetime.utcnow()
        return super().eventFilter(obj, event)

    def notify_activity(self) -> None:
        """Call from re-auth flow to reset the timer on successful unlock."""
        self._last_activity = datetime.utcnow()
        self._locked = False
        self._deferred_due_to_modal = False

    def _check(self) -> None:
        if self._locked:
            return
        if datetime.utcnow() - self._last_activity < self._timeout:
            return

        # Avoid opening the lock dialog while another modal dialog is active
        # (for example, Add New Patient), which can make the UI appear frozen.
        modal = None
        try:
            modal = self._app.activeModalWidget()
        except Exception:
            modal = None

        if modal is not None:
            try:
                if modal.isVisible():
                    if not self._deferred_due_to_modal:
                        title = ""
                        try:
                            title = (modal.windowTitle() or "").strip()
                        except Exception:
                            title = ""
                        if title:
                            logger.info(
                                "Idle timeout reached but modal dialog is open (%s); deferring lock.",
                                title,
                            )
                        else:
                            logger.info("Idle timeout reached but modal dialog is open; deferring lock.")
                    self._deferred_due_to_modal = True
                    return
            except Exception:
                pass

        self._deferred_due_to_modal = False
        self._locked = True
        logger.info("Idle timeout reached — locking session.")
        try:
            self._lock_callback()
        except Exception as e:
            logger.error(f"lock_callback failed: {e}")
            # If lock fails, don't get stuck — allow retry next tick.
            self._locked = False
