"""
Unbilled orders reminder.

Refactored from the inline version in app.py:
  - Uses the central DB connection helper (not raw sqlite3.connect).
  - Uses OrderStatus enum (not string literals).
  - Includes a "Don't show again today" checkbox honored across the day.
  - Optionally refreshes every N minutes so reminders surface mid-session.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCheckBox, QMessageBox

from dmelogic.core.constants import OrderStatus
from dmelogic.db.connection import open_db
from dmelogic.ui.prefs import (
    is_unbilled_reminder_snoozed,
    snooze_unbilled_reminder_today,
)

logger = logging.getLogger("reminders")


def schedule_unbilled_reminder(
    win,
    orders_db: Path,
    *,
    refresh_interval_minutes: int = 30,
    honor_snooze: bool = True,
) -> QTimer | None:
    """
    Show the reminder once at startup, then optionally repeat on a timer.

    Returns the QTimer (so caller can stop it on logout). None if no
    periodic refresh was scheduled.
    """
    # Initial check, delayed so the main window is fully painted.
    QTimer.singleShot(500, lambda: _show_reminder_if_needed(win, orders_db, honor_snooze))

    if refresh_interval_minutes <= 0:
        return None

    timer = QTimer(win)
    timer.setInterval(refresh_interval_minutes * 60 * 1000)
    timer.timeout.connect(lambda: _show_reminder_if_needed(win, orders_db, honor_snooze))
    timer.start()
    return timer


def _show_reminder_if_needed(win, orders_db: Path, honor_snooze: bool) -> None:
    if honor_snooze and is_unbilled_reminder_snoozed():
        logger.info("Unbilled reminder snoozed for today; skipping.")
        return
    if not orders_db.exists():
        return
    try:
        unbilled, due_holds = _fetch_attention_orders(orders_db)
    except Exception as e:
        logger.warning(f"Could not fetch attention orders: {e}")
        return

    if not unbilled and not due_holds:
        return

    _show_dialog(win, unbilled, due_holds)


def _fetch_attention_orders(orders_db: Path) -> tuple[list, list]:
    """Returns (unbilled_rows, on_hold_due_rows)."""
    with open_db(orders_db) as conn:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_status_date
            ON orders (order_status, order_date)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_status_hold
            ON orders (order_status, hold_until_date)
        """)

        unbilled = conn.execute(
            """
            SELECT id, patient_name, order_status, order_date, delivery_date
            FROM orders
            WHERE order_status = ?
            ORDER BY order_date ASC
            LIMIT 20
            """,
            (OrderStatus.UNBILLED.value,),
        ).fetchall()

        due_holds = conn.execute(
            """
            SELECT id, patient_name, hold_until_date, hold_resume_status
            FROM orders
            WHERE order_status = ?
              AND hold_until_date IS NOT NULL
              AND date(hold_until_date) <= date('now')
            """,
            (OrderStatus.ON_HOLD.value,),
        ).fetchall()

    return list(unbilled), list(due_holds)


def _show_dialog(win, unbilled: list, due_holds: list) -> None:
    messages = []

    if unbilled:
        lines = []
        for row in unbilled[:10]:
            order_num = f"ORD-{row['id']:03d}"
            patient = row['patient_name'] or "Unknown"
            lines.append(f"  • {order_num}: {patient} ({row['order_status']})")
        more = f"\n  ... and {len(unbilled) - 10} more" if len(unbilled) > 10 else ""
        messages.append(
            f"⚠️ UNBILLED ORDERS ({len(unbilled)}):\n" + "\n".join(lines) + more
        )

    if due_holds:
        lines = []
        for row in due_holds[:5]:
            order_num = f"ORD-{row['id']:03d}"
            patient = row['patient_name'] or "Unknown"
            resume = row['hold_resume_status'] or "Pending"
            lines.append(f"  • {order_num}: {patient} → {resume}")
        messages.append(
            f"⏰ HOLDS DUE FOR RELEASE ({len(due_holds)}):\n" + "\n".join(lines)
        )

    msg_box = QMessageBox(win)
    msg_box.setWindowTitle("\U0001f4cb Orders Requiring Attention")
    msg_box.setIcon(QMessageBox.Icon.Warning)
    msg_box.setText("The following orders need your attention:")
    msg_box.setDetailedText("\n\n".join(messages))
    msg_box.setInformativeText(
        f"{len(unbilled)} unbilled orders, {len(due_holds)} holds due"
    )
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)

    snooze_cb = QCheckBox("Don't show again today")
    msg_box.setCheckBox(snooze_cb)

    msg_box.exec()

    if snooze_cb.isChecked():
        snooze_unbilled_reminder_today()
        logger.info("Unbilled reminder snoozed until tomorrow.")

    logger.info(f"Shown unbilled reminder: {len(unbilled)} unbilled, {len(due_holds)} holds due")
