"""
stat_card.py — a modern KPI card with a prominent value and an optional
embedded mini-chart (sparkline / donut / bar / icon).

Matches the reference dashboard look: label + big number on the left, a small
data visualization on the right, in a clean white card. Charts are rendered
with matplotlib to a crisp pixmap (no live canvas lifecycle to manage).

    card = StatCard("Total Orders")
    card.set_value(485)
    card.set_sparkline([12, 15, 14, 20, 22, 28, 31])

Exposes ``value_label`` / ``title_label`` so existing code that sets text on
them keeps working.
"""

from __future__ import annotations

import io
from typing import Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

# Brand palette (kept in sync with ui/theme_modern).
ACCENT = "#2563eb"
ACCENT_FILL = "#bfdbfe"
SLATE = "#0f172a"
MUTED = "#64748b"
AMBER = "#f59e0b"
GREEN = "#16a34a"
RED = "#dc2626"

_CHART_W, _CHART_H = 132, 64   # px


def _render_figure_to_pixmap(fig, width: int, height: int) -> QPixmap:
    """Render a matplotlib figure to a transparent QPixmap."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, dpi=100,
                bbox_inches="tight", pad_inches=0.02)
    import matplotlib.pyplot as plt
    plt.close(fig)
    buf.seek(0)
    pix = QPixmap()
    pix.loadFromData(buf.getvalue(), "PNG")
    if not pix.isNull():
        pix = pix.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return pix


def _new_fig(w_px: int = _CHART_W, h_px: int = _CHART_H):
    import matplotlib
    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=100)
    return fig


class StatCard(QFrame):
    """A KPI card: title + big value on the left, optional mini-chart right."""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(96)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "StatCard { background-color: #ffffff; border: 1px solid #e2e8f0;"
            " border-radius: 12px; }"
            " StatCard:hover { border: 1px solid #2563eb; }"
            " QLabel { border: none; background: transparent; }"
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 14, 14, 14)
        root.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {MUTED}; font-size: 12px; font-weight: 600;")
        self.value_label = QLabel("0")
        self.value_label.setStyleSheet(
            f"color: {SLATE}; font-size: 30px; font-weight: 800;")
        left.addWidget(self.title_label)
        left.addWidget(self.value_label)
        left.addStretch(1)
        root.addLayout(left, 1)

        self.chart_label = QLabel()
        self.chart_label.setFixedSize(_CHART_W, _CHART_H)
        self.chart_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.chart_label, 0,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    # ── value ───────────────────────────────────────────────────────────
    def set_value(self, value) -> None:
        self.value_label.setText(str(value))

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    # ── charts ──────────────────────────────────────────────────────────
    def _set_chart(self, pix: QPixmap) -> None:
        if pix and not pix.isNull():
            self.chart_label.setPixmap(pix)

    def set_sparkline(self, values: Sequence[float], color: str = ACCENT) -> None:
        """An area sparkline (e.g. orders over time)."""
        vals = [float(v) for v in values] or [0.0]
        try:
            fig = _new_fig()
            ax = fig.add_axes([0, 0, 1, 1])
            x = list(range(len(vals)))
            ax.plot(x, vals, color=color, linewidth=2)
            ax.fill_between(x, vals, min(vals), color=color, alpha=0.18)
            ax.set_axis_off()
            ax.margins(x=0.02, y=0.15)
            self._set_chart(_render_figure_to_pixmap(fig, _CHART_W, _CHART_H))
        except Exception:
            pass

    def set_donut(self, segments: Sequence[float],
                  colors: Optional[Sequence[str]] = None) -> None:
        """A small donut (e.g. status breakdown)."""
        vals = [max(0.0, float(v)) for v in segments]
        if sum(vals) <= 0:
            vals = [1.0]
            colors = ["#e2e8f0"]
        colors = list(colors) if colors else [ACCENT, AMBER, GREEN, RED, MUTED]
        try:
            fig = _new_fig(_CHART_H, _CHART_H)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.pie(vals, colors=colors[:len(vals)], startangle=90,
                   wedgeprops=dict(width=0.42, edgecolor="white"))
            ax.set_aspect("equal")
            self._set_chart(_render_figure_to_pixmap(fig, _CHART_H, _CHART_H))
        except Exception:
            pass

    def set_bars(self, values: Sequence[float], color: str = ACCENT) -> None:
        """A small bar chart (e.g. refills due by day)."""
        vals = [float(v) for v in values] or [0.0]
        try:
            fig = _new_fig()
            ax = fig.add_axes([0, 0, 1, 1])
            ax.bar(range(len(vals)), vals, color=color, width=0.6)
            ax.set_axis_off()
            ax.margins(x=0.05, y=0.15)
            self._set_chart(_render_figure_to_pixmap(fig, _CHART_W, _CHART_H))
        except Exception:
            pass

    def set_icon(self, emoji: str = "⚠️", color: str = AMBER) -> None:
        """A simple status glyph instead of a chart."""
        self.chart_label.setPixmap(QPixmap())
        self.chart_label.setText(emoji)
        self.chart_label.setStyleSheet(f"font-size: 30px; color: {color};")

    def set_accent_value(self, color: str = ACCENT) -> None:
        self.value_label.setStyleSheet(
            f"color: {color}; font-size: 30px; font-weight: 800;")
