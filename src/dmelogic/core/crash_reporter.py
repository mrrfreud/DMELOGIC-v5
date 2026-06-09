"""
Global crash reporter.

Installs a sys.excepthook that:
  1. Writes a full traceback to crashes/crash_YYYY-MM-DD_HHMMSS.log
  2. Shows a friendly dialog with a "Copy details" button
  3. Lets the user choose to continue or quit

Without this, unhandled exceptions in PyQt event handlers either kill
the app silently (Windows) or scroll past in a console nobody is reading.
"""

from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger("crash")


def install_crash_reporter(
    crashes_dir: Path,
    show_dialog: bool = True,
    on_crash: Callable[[Path, str], None] | None = None,
) -> None:
    """
    Replace sys.excepthook with the crash reporter.

    Args:
        crashes_dir: where to write crash logs
        show_dialog: whether to pop up a Qt dialog. Set False for headless tests.
        on_crash: optional callback (crash_path, traceback_str) for telemetry hooks.
    """
    crashes_dir.mkdir(parents=True, exist_ok=True)

    def excepthook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt should fall through to default behavior.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        crash_path = crashes_dir / f"crash_{timestamp}.log"

        try:
            with crash_path.open("w", encoding="utf-8") as f:
                f.write(f"DMELogic crash report — {datetime.now().isoformat()}\n")
                f.write(f"Python: {sys.version}\n")
                f.write(f"Platform: {sys.platform}\n")
                f.write(f"Exception: {exc_type.__name__}: {exc_value}\n\n")
                f.write(tb_str)
        except Exception as write_err:
            logger.error(f"Could not write crash log: {write_err}")

        logger.critical(f"Unhandled exception ({exc_type.__name__}): {exc_value}\n{tb_str}")

        if on_crash is not None:
            try:
                on_crash(crash_path, tb_str)
            except Exception:
                pass  # never let telemetry break crash handling

        if show_dialog:
            _show_crash_dialog(exc_type, exc_value, tb_str, crash_path)

    sys.excepthook = excepthook


def _show_crash_dialog(exc_type, exc_value, tb_str: str, crash_path: Path) -> None:
    """Show a Qt dialog if a QApplication exists; otherwise print to stderr."""
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is None:
            print(f"[CRASH] {exc_type.__name__}: {exc_value}", file=sys.stderr)
            print(f"[CRASH] Details written to: {crash_path}", file=sys.stderr)
            return

        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("DMELogic — Unexpected Error")
        msg.setText("Something went wrong.")
        msg.setInformativeText(
            f"<b>{exc_type.__name__}</b>: {exc_value}<br><br>"
            f"A crash report was saved to:<br><code>{crash_path}</code><br><br>"
            "You can copy the details below and send them to support."
        )
        msg.setDetailedText(tb_str)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()
    except Exception as e:
        # Dialog itself crashed — at least don't loop.
        print(f"[CRASH] (and dialog failed: {e})", file=sys.stderr)
        print(tb_str, file=sys.stderr)
