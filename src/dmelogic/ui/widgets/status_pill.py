"""
status_pill.py — paint status values as rounded colored pills in a QTableView.

    table.setItemDelegateForColumn(status_col, StatusPillDelegate(table))

Maps common DME statuses to a semantic color (green/amber/red/blue) and draws a
soft-tinted rounded chip with colored text, matching the reference design.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import QStyledItemDelegate

# text color, soft background
_GREEN = ("#15803d", "#dcfce7")
_AMBER = ("#b45309", "#fef3c7")
_RED = ("#b91c1c", "#fee2e2")
_BLUE = ("#1d4ed8", "#dbeafe")
_SLATE = ("#475569", "#f1f5f9")

_MAP = {
    # green — money in / fulfilled
    "paid": _GREEN, "approved": _GREEN, "billed": _GREEN, "shipped": _GREEN,
    "delivered": _GREEN, "completed": _GREEN, "complete": _GREEN, "active": _GREEN,
    "filled": _GREEN, "ready": _GREEN,
    # amber — in flight / waiting
    "pending": _AMBER, "in-progress": _AMBER, "in progress": _AMBER,
    "processing": _AMBER, "awaiting payment": _AMBER, "awaiting": _AMBER,
    "on hold": _AMBER, "hold": _AMBER, "new": _AMBER, "missing info": _AMBER,
    # red — problem
    "overdue": _RED, "denied": _RED, "rejected": _RED, "cancelled": _RED,
    "canceled": _RED, "void": _RED, "error": _RED, "missing insurance": _RED,
    # blue — informational
    "submitted": _BLUE, "sent": _BLUE, "ordered": _BLUE,
}


def status_color(value: str):
    """Return (text_color, bg_color) for a status string."""
    return _MAP.get((value or "").strip().lower(), _SLATE)


class StatusPillDelegate(QStyledItemDelegate):
    """Draws the cell's text as a colored status pill."""

    def paint(self, painter: QPainter, option, index) -> None:
        text = (index.data() or "").strip()
        if not text:
            super().paint(painter, option, index)
            return

        # Selection background first.
        if option.state & option.state.__class__.State_Selected:
            painter.fillRect(option.rect, QColor("#dbeafe"))

        fg, bg = status_color(text)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        font = QFont(option.font)
        font.setPointSizeF(max(8.0, option.font.pointSizeF()))
        font.setBold(True)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(text)
        pad_h, pad_v = 10, 4
        pill_w = tw + pad_h * 2
        pill_h = metrics.height() + pad_v * 2

        r = option.rect
        x = r.x() + 8
        y = r.y() + (r.height() - pill_h) / 2

        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, pill_w, pill_h), pill_h / 2, pill_h / 2)
        painter.fillPath(path, QColor(bg))

        painter.setPen(QColor(fg))
        painter.drawText(QRectF(x, y, pill_w, pill_h),
                         Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
