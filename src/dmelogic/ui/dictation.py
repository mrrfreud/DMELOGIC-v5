"""
Lightweight dictation support for text fields.

Adds a small 🎤 button overlaid in the corner of a QTextEdit / QLineEdit that
triggers Windows' built-in speech dictation (the Win+H panel) into that field.

Why Win+H: it's built into Windows, free, supports punctuation, and on Windows
11 runs on-device — so patient information never leaves the PC. No extra
dependencies, no API cost.

Usage:
    from dmelogic.ui.dictation import enable_dictation
    enable_dictation(self.notes_text)
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QObject, QEvent, QTimer
from PyQt6.QtWidgets import QToolButton, QWidget


def _send_win_h() -> None:
    """Press Win+H to open Windows dictation for the focused control."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        KEYUP = 0x0002
        VK_LWIN, VK_H = 0x5B, 0x48
        user32.keybd_event(VK_LWIN, 0, 0, 0)        # Win down
        user32.keybd_event(VK_H, 0, 0, 0)           # H down
        user32.keybd_event(VK_H, 0, KEYUP, 0)       # H up
        user32.keybd_event(VK_LWIN, 0, KEYUP, 0)    # Win up
    except Exception:
        pass


class _CornerPositioner(QObject):
    """Keeps the mic button pinned to the bottom-right of its host field."""

    def __init__(self, host: QWidget, button: QToolButton, margin: int = 6):
        super().__init__(host)
        self._host = host
        self._btn = button
        self._margin = margin
        host.installEventFilter(self)
        self._reposition()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._reposition()
        return False

    def _reposition(self):
        b, h, m = self._btn, self._host, self._margin
        b.adjustSize()
        # Anchor to the bottom-LEFT corner. The left edge of a field is always
        # on screen, whereas the right edge can be clipped when the panel is
        # wider than the window (horizontal scroll) — which hid the button
        # unless the window was maximized.
        b.move(m, max(0, h.height() - b.height() - m))
        b.raise_()


def enable_dictation(widget: QWidget,
                     tooltip: str = "Dictate with Windows Speech (Win+H)") -> QWidget:
    """Overlay a 🎤 dictation button on ``widget`` and return ``widget``.

    No-op on non-Windows platforms. Safe to call once per field.
    """
    if os.name != "nt" or widget is None:
        return widget
    if widget.property("_dictation_enabled"):
        return widget

    # Scroll-area widgets (QTextEdit/QPlainTextEdit) paint their text on an
    # internal viewport that covers the frame, so a button parented to the frame
    # gets hidden behind it (and only flickers into view on a full repaint, e.g.
    # after maximizing). Overlay the button on the VIEWPORT instead so it stays
    # visible at any window size.
    host = widget.viewport() if hasattr(widget, "viewport") and callable(widget.viewport) else widget

    btn = QToolButton(host)
    btn.setText("🎤")
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    # Don't steal focus from the text field — Win+H types into the focused one.
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setStyleSheet(
        "QToolButton { border:1px solid #e2e8f0; border-radius:6px;"
        " background:#ffffff; padding:1px 4px; font-size:12px; }"
        "QToolButton:hover { background:#eff2f7; border-color:#cbd5e1; }"
    )

    def _go():
        try:
            widget.setFocus()
            # Let focus settle before opening the dictation panel.
            QTimer.singleShot(80, _send_win_h)
        except Exception:
            pass

    btn.clicked.connect(_go)
    positioner = _CornerPositioner(host, btn)
    btn.show()
    # Position once layout has settled (the field may still be 0-sized here).
    QTimer.singleShot(0, positioner._reposition)
    widget.setProperty("_dictation_enabled", True)
    return widget
