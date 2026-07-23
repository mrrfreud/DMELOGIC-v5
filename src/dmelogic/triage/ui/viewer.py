"""
viewer.py — a lightweight document viewer for the triage screen.

Renders PDFs (via PyMuPDF) and common image formats to a scrollable pane,
with page navigation and fit-to-width. Deliberately minimal: this replaces the
*viewing* role of the legacy PDFViewer, while also supporting a simple manual
trim box for New Rx cleanup.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

logger = logging.getLogger("triage.viewer")

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"}
_MIN_ZOOM = 0.15
_MAX_ZOOM = 10.0
_ZOOM_STEP = 0.25


def _detect_document_bounds(arr):
    """Find the bounding box of the document/script within a grayscale array.

    The signal is the brightness of the image *border*: a phone photo of a
    script sits on a darker surround (desk/shadow), so the paper is the large
    BRIGHT region to crop to. A light border means the document already fills
    the view (a clean scan or PDF page) — there is nothing to trim, and we must
    NOT crop it down to the ink.

    Returns ``(box, reason)`` where box is ``(x0, y0, x1, y1)`` or ``None`` and
    reason is one of ``"ok"``, ``"nothing"`` (already fits), ``"nodetect"``.
    """
    import numpy as np

    h, w = arr.shape
    if h < 8 or w < 8:
        return None, "nodetect"

    # The paper is the bright region. Threshold relative to the paper's OWN
    # brightness (its 85th percentile) so that a gray/dark surround — a desk,
    # shadow, or the black corners in a phone capture — is excluded. A floor at
    # ~0.92 of the paper level cleanly separates white paper from a lighter-gray
    # desk; the absolute floor of 200 avoids "detecting" a dim, low-contrast
    # capture where paper and background are too similar.
    paper = float(np.percentile(arr, 85))
    floor = max(200.0, paper * 0.92)
    white = arr >= floor
    if white.mean() < 0.03:
        return None, "nodetect"

    # Rows/columns that carry enough paper (a ratio test, so specks and thin
    # bright streaks in the surround do not widen the box).
    row_ratio = white.mean(axis=1)
    col_ratio = white.mean(axis=0)
    rows = np.flatnonzero(row_ratio >= max(0.20, float(row_ratio.max()) * 0.30))
    cols = np.flatnonzero(col_ratio >= max(0.20, float(col_ratio.max()) * 0.30))
    if rows.size == 0 or cols.size == 0:
        return None, "nodetect"

    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None, "nodetect"

    # Paper already fills the frame → nothing worth trimming.
    if (x1 - x0) >= w * 0.97 and (y1 - y0) >= h * 0.97:
        return None, "nothing"
    return (x0, y0, x1, y1), "ok"


class _TrimCanvas(QLabel):
    selectionChanged = pyqtSignal(bool)
    panDelta = pyqtSignal(int, int)
    panActiveChanged = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._trim_enabled = False
        self._pan_enabled = False
        self._dragging = False
        self._start = QPoint()
        self._current = QPoint()
        self._panning = False
        self._pan_anchor = QPoint()

    def set_trim_enabled(self, enabled: bool) -> None:
        self._trim_enabled = bool(enabled)
        if not self._trim_enabled:
            self.clear_selection()
        self._sync_cursor()

    def set_pan_enabled(self, enabled: bool) -> None:
        self._pan_enabled = bool(enabled)
        if not self._pan_enabled and self._panning:
            self._panning = False
            self.panActiveChanged.emit(False)
        self._sync_cursor()

    def clear_selection(self) -> None:
        had_selection = self.has_selection()
        self._dragging = False
        self._start = QPoint()
        self._current = QPoint()
        if had_selection:
            self.selectionChanged.emit(False)
        self.update()

    def set_selection(self, rect: QRect) -> None:
        """Programmatically set the trim selection (used by Auto Trim)."""
        clamped = rect.intersected(self.pixmap_rect())
        if clamped.width() < 2 or clamped.height() < 2:
            self.clear_selection()
            return
        self._dragging = False
        self._start = clamped.topLeft()
        self._current = clamped.bottomRight()
        self.selectionChanged.emit(self.has_selection())
        self.update()

    def has_selection(self) -> bool:
        rect = self.selection_rect()
        return not rect.isNull() and rect.width() >= 2 and rect.height() >= 2

    def selection_rect(self) -> QRect:
        if self._start.isNull() and self._current.isNull():
            return QRect()
        return QRect(self._start, self._current).normalized()

    def pixmap_rect(self) -> QRect:
        pix = self.pixmap()
        if pix is None or pix.isNull():
            return QRect()
        size = pix.size()
        x = max(0, (self.width() - size.width()) // 2)
        y = max(0, (self.height() - size.height()) // 2)
        return QRect(QPoint(x, y), size)

    def mousePressEvent(self, event) -> None:
        if self._trim_enabled and event.button() == Qt.MouseButton.LeftButton:
            rect = self.pixmap_rect()
            point = event.position().toPoint()
            if rect.contains(point):
                self._dragging = True
                self._start = self._clamp_to_pixmap(point)
                self._current = self._start
                self.selectionChanged.emit(False)
                self.update()
                return
        if self._pan_enabled and event.button() == Qt.MouseButton.LeftButton:
            rect = self.pixmap_rect()
            point = event.position().toPoint()
            if rect.contains(point):
                self._panning = True
                self._pan_anchor = point
                self.panActiveChanged.emit(True)
                self._sync_cursor()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._current = self._clamp_to_pixmap(event.position().toPoint())
            self.selectionChanged.emit(self.has_selection())
            self.update()
            return
        if self._panning:
            point = event.position().toPoint()
            delta = point - self._pan_anchor
            self._pan_anchor = point
            self.panDelta.emit(delta.x(), delta.y())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._current = self._clamp_to_pixmap(event.position().toPoint())
            self.selectionChanged.emit(self.has_selection())
            self.update()
            return
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._panning = False
            self.panActiveChanged.emit(False)
            self._sync_cursor()
            return
        super().mouseReleaseEvent(event)

    def _sync_cursor(self) -> None:
        if self._trim_enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._panning:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._pan_enabled:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        rect = self.selection_rect()
        if rect.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor(37, 99, 235, 48))
        painter.setPen(QPen(QColor("#2563eb"), 2, Qt.PenStyle.DashLine))
        painter.drawRect(rect)

    def _clamp_to_pixmap(self, point: QPoint) -> QPoint:
        rect = self.pixmap_rect()
        if rect.isNull():
            return QPoint()
        return QPoint(
            max(rect.left(), min(point.x(), rect.right())),
            max(rect.top(), min(point.y(), rect.bottom())),
        )


class DocumentViewer(QWidget):
    """Displays a single PDF or image file with page navigation."""

    trimRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._path: Path | None = None
        self._pdf = None            # fitz.Document when a PDF is open
        self._image_pix = QPixmap()
        self._page = 0
        self._zoom = 1.0
        self._zoom_mode = "fit"    # fit | manual
        self._trim_mode = False
        self._source_size = QSize()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        bar = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Prev")
        self.next_btn = QPushButton("Next ▶")
        self.page_lbl = QLabel("—")
        self.open_browser_btn = QPushButton("🌐 Browser")
        self.trim_btn = QPushButton("✂ Trim")
        self.auto_trim_btn = QPushButton("🪄 Auto Trim")
        self.auto_trim_btn.setToolTip("Detect the document's edges and set the trim box automatically.")
        self.apply_trim_btn = QPushButton("Apply Trim")
        self.cancel_trim_btn = QPushButton("Cancel")
        self.zoom_out_btn = QPushButton("−")
        self.zoom_in_btn = QPushButton("+")
        for w in (
            self.prev_btn,
            self.next_btn,
            self.open_browser_btn,
            self.trim_btn,
            self.auto_trim_btn,
            self.apply_trim_btn,
            self.cancel_trim_btn,
        ):
            w.setFixedHeight(28)
        self.prev_btn.clicked.connect(self.prev_page)
        self.next_btn.clicked.connect(self.next_page)
        self.open_browser_btn.clicked.connect(self.open_in_browser)
        self.trim_btn.clicked.connect(self.enable_trim_mode)
        self.auto_trim_btn.clicked.connect(self.auto_trim)
        self.apply_trim_btn.clicked.connect(self.trimRequested.emit)
        self.cancel_trim_btn.clicked.connect(self.cancel_trim)
        self.zoom_in_btn.clicked.connect(lambda: self._set_zoom(self._zoom + _ZOOM_STEP, mode="manual"))
        self.zoom_out_btn.clicked.connect(lambda: self._set_zoom(self._zoom - _ZOOM_STEP, mode="manual"))
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.page_lbl)
        bar.addWidget(self.next_btn)
        bar.addWidget(self.open_browser_btn)
        bar.addWidget(self.trim_btn)
        bar.addWidget(self.auto_trim_btn)
        bar.addWidget(self.apply_trim_btn)
        bar.addWidget(self.cancel_trim_btn)
        bar.addStretch()
        bar.addWidget(self.zoom_out_btn)
        bar.addWidget(self.zoom_in_btn)
        root.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas = _TrimCanvas()
        self.canvas.setText("No document selected")
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setStyleSheet("color:#64748b; padding:40px;")
        self.canvas.selectionChanged.connect(self._update_controls)
        self.canvas.panDelta.connect(self._on_pan_delta)
        self.canvas.panActiveChanged.connect(self._update_controls)
        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll, 1)

        self._update_controls()

    def load(self, path: str | Path | None) -> None:
        self._close_pdf()
        self._page = 0
        self._path = Path(path) if path else None
        self._image_pix = QPixmap()
        self._source_size = QSize()
        self._zoom_mode = "fit"
        self.cancel_trim()

        if self._path is None or not self._path.exists():
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText(
                "No document selected" if self._path is None else f"File not found:\n{self._path}"
            )
            self._update_controls()
            return

        suffix = self._path.suffix.lower()
        if suffix == ".pdf":
            self._load_pdf()
        elif suffix in _IMAGE_SUFFIXES:
            pix = QPixmap(str(self._path))
            if pix.isNull():
                self.canvas.setPixmap(QPixmap())
                self.canvas.setText(f"Could not load image:\n{self._path.name}")
            else:
                self._image_pix = pix
                self._source_size = pix.size()
                self._fit_to_page()
        else:
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText(f"Cannot preview this file type:\n{self._path.name}")
        self._update_controls()

    def clear(self) -> None:
        self.load(None)

    def enable_trim_mode(self) -> None:
        if not self._can_trim_current():
            return
        self._trim_mode = True
        self.canvas.set_trim_enabled(True)
        self.canvas.clear_selection()
        self._update_controls()

    def cancel_trim(self) -> None:
        self._trim_mode = False
        self.canvas.set_trim_enabled(False)
        self.canvas.clear_selection()
        self._update_controls()

    def auto_trim(self) -> None:
        """Detect the document/page edges on the current page and set the trim box.

        Enters trim mode if needed, draws the detected box for review, and leaves
        it to the user to press Apply Trim (or adjust the box first).
        """
        if not self._can_trim_current():
            return
        if not self._trim_mode:
            self.enable_trim_mode()
        rect, reason = self._autodetect_pixmap_bbox()
        if rect is None:
            # Clear feedback so it never looks like the button did nothing.
            from PyQt6.QtWidgets import QMessageBox
            if reason == "nothing":
                text = ("This page already fits the document — there's nothing to "
                        "trim.\n\nAuto Trim only crops photos that have a "
                        "background/border around the paper.")
            else:
                text = ("Couldn't confidently detect the document's edges "
                        "(the background may be too light or too similar to the "
                        "paper).\n\nDrag a box manually, then press Apply Trim.")
            self.canvas.clear_selection()
            self._update_controls()
            QMessageBox.information(self, "Auto Trim", text)
            return
        self.canvas.set_selection(rect)
        self._update_controls()

    def _autodetect_pixmap_bbox(self):
        """Return ``(QRect|None, reason)`` for the detected document in canvas coords."""
        pix = self.canvas.pixmap()
        if pix is None or pix.isNull():
            return None, "nodetect"
        try:
            import numpy as np

            img = pix.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
            w, h = img.width(), img.height()
            if w < 8 or h < 8:
                return None, "nodetect"
            ptr = img.constBits()
            ptr.setsize(img.sizeInBytes())
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, img.bytesPerLine()))[:, :w]

            box, reason = _detect_document_bounds(arr)
            if box is None:
                return None, reason
            x0, y0, x1, y1 = box
            # Small padding so we never clip the page edge / border ink.
            pad = max(2, min(w, h) // 150)
            x0 = max(0, x0 - pad)
            y0 = max(0, y0 - pad)
            x1 = min(w, x1 + pad)
            y1 = min(h, y1 + pad)

            pr = self.canvas.pixmap_rect()
            return QRect(
                QPoint(pr.left() + x0, pr.top() + y0),
                QSize(x1 - x0, y1 - y0),
            ), "ok"
        except Exception as e:
            logger.warning("Auto Trim detection failed: %s", e)
            return None, "nodetect"

    def has_trim_selection(self) -> bool:
        return self._trim_mode and self.canvas.has_selection()

    def current_trim_box(self) -> tuple[float, float, float, float] | None:
        rect = self.canvas.selection_rect()
        pix_rect = self.canvas.pixmap_rect()
        if rect.isNull() or pix_rect.isNull() or self._source_size.isEmpty():
            return None
        scale_x = self._source_size.width() / pix_rect.width()
        scale_y = self._source_size.height() / pix_rect.height()
        return (
            (rect.left() - pix_rect.left()) * scale_x,
            (rect.top() - pix_rect.top()) * scale_y,
            (rect.right() - pix_rect.left() + 1) * scale_x,
            (rect.bottom() - pix_rect.top() + 1) * scale_y,
        )

    def current_page_index(self) -> int:
        return self._page

    def open_in_browser(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            import webbrowser

            uri = self._path.resolve().as_uri()
            if self._pdf is not None:
                uri = f"{uri}#page={self._page + 1}"
            webbrowser.open(uri, new=2)
        except Exception as e:
            logger.warning("Failed to open in browser %s: %s", self._path, e)

    def release(self) -> None:
        self._close_pdf()

    def _load_pdf(self) -> None:
        try:
            import fitz

            self._pdf = fitz.open(str(self._path))
            if self._pdf.page_count:
                page = self._pdf[self._page]
                self._source_size = QSize(int(round(page.rect.width)), int(round(page.rect.height)))
            self._fit_to_page()
        except Exception as e:
            logger.warning("Failed to open PDF %s: %s", self._path, e)
            self.canvas.setText(f"Could not open PDF:\n{e}")

    def _render_pdf_page(self) -> None:
        if not self._pdf:
            return
        try:
            import fitz

            page = self._pdf[self._page]
            matrix = fitz.Matrix(self._zoom, self._zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            self.canvas.setPixmap(QPixmap.fromImage(img.copy()))
            self.canvas.setText("")
            self._source_size = QSize(int(round(page.rect.width)), int(round(page.rect.height)))
            self.canvas.clear_selection()
        except Exception as e:
            logger.warning("Render failed: %s", e)
            self.canvas.setText(f"Render error:\n{e}")

    def _render_image(self) -> None:
        if self._image_pix.isNull():
            self.canvas.setText(f"Could not load image:\n{self._path.name}")
            return
        pix = self._image_pix
        self._source_size = pix.size()
        w = max(1, int(round(pix.width() * self._zoom)))
        h = max(1, int(round(pix.height() * self._zoom)))
        scaled = pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        self.canvas.setPixmap(scaled)
        self.canvas.setText("")
        self.canvas.clear_selection()

    def _close_pdf(self) -> None:
        if self._pdf is not None:
            try:
                self._pdf.close()
            except Exception:
                pass
            self._pdf = None

    def _page_count(self) -> int:
        if self._pdf is not None:
            return self._pdf.page_count
        return 1 if self._path and self._path.exists() else 0

    def _can_trim_current(self) -> bool:
        return bool(
            self._path
            and self._path.exists()
            and self._path.suffix.lower() in (_IMAGE_SUFFIXES | {".pdf"})
        )

    def next_page(self) -> None:
        if self._pdf and self._page < self._pdf.page_count - 1 and not self._trim_mode:
            self._page += 1
            if self._zoom_mode == "fit":
                page = self._pdf[self._page]
                self._source_size = QSize(int(round(page.rect.width)), int(round(page.rect.height)))
                self._fit_to_page()
            else:
                self._render_pdf_page()
            self._update_controls()

    def prev_page(self) -> None:
        if self._pdf and self._page > 0 and not self._trim_mode:
            self._page -= 1
            if self._zoom_mode == "fit":
                page = self._pdf[self._page]
                self._source_size = QSize(int(round(page.rect.width)), int(round(page.rect.height)))
                self._fit_to_page()
            else:
                self._render_pdf_page()
            self._update_controls()

    def _set_zoom(self, zoom: float, *, mode: str = "manual") -> None:
        if self._trim_mode:
            return
        self._zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, zoom))
        self._zoom_mode = mode
        if self._pdf is not None:
            self._render_pdf_page()
        elif self._path and self._path.suffix.lower() in _IMAGE_SUFFIXES:
            self._render_image()
        self._update_controls()

    def _fit_zoom_for_source(self) -> float:
        if self._source_size.isEmpty():
            return self._zoom
        vp = self.scroll.viewport().size()
        avail_w = max(1, vp.width() - 24)
        avail_h = max(1, vp.height() - 24)
        fit_w = avail_w / max(1, self._source_size.width())
        fit_h = avail_h / max(1, self._source_size.height())
        return max(_MIN_ZOOM, min(_MAX_ZOOM, min(fit_w, fit_h)))

    def _fit_to_page(self) -> None:
        if self._source_size.isEmpty():
            return
        self._set_zoom(self._fit_zoom_for_source(), mode="fit")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._trim_mode or self._zoom_mode != "fit" or self._source_size.isEmpty():
            return
        target_zoom = self._fit_zoom_for_source()
        if abs(target_zoom - self._zoom) > 0.01:
            self._set_zoom(target_zoom, mode="fit")

    def _update_controls(self, *_args) -> None:
        count = self._page_count()
        is_pdf = self._pdf is not None
        can_trim = self._can_trim_current()
        can_pan = self._can_pan_current()
        self.canvas.set_pan_enabled(can_pan and not self._trim_mode)
        self.prev_btn.setEnabled(is_pdf and self._page > 0 and not self._trim_mode)
        self.next_btn.setEnabled(is_pdf and self._page < count - 1 and not self._trim_mode)
        self.open_browser_btn.setEnabled(bool(self._path and self._path.exists()))
        self.trim_btn.setEnabled(can_trim and not self._trim_mode)
        has_pixmap = self.canvas.pixmap() is not None and not self.canvas.pixmap().isNull()
        self.auto_trim_btn.setEnabled(can_trim and has_pixmap)
        self.apply_trim_btn.setEnabled(can_trim and self.has_trim_selection())
        self.cancel_trim_btn.setEnabled(can_trim and self._trim_mode)
        self.zoom_out_btn.setEnabled(not self._trim_mode)
        self.zoom_in_btn.setEnabled(not self._trim_mode)
        self.zoom_in_btn.setText("+")
        self.zoom_out_btn.setText("-")
        if count == 0:
            self.page_lbl.setText("—")
        elif is_pdf:
            self.page_lbl.setText(
                f"Page {self._page + 1} / {count}  ·  {int(round(self._zoom * 100))}%"
                + ("  ·  drag to trim" if self._trim_mode else "")
            )
        else:
            self.page_lbl.setText(
                f"Image  ·  {int(round(self._zoom * 100))}%"
                + ("  ·  drag to trim" if self._trim_mode else "")
            )

    def _can_pan_current(self) -> bool:
        pix = self.canvas.pixmap()
        if pix is None or pix.isNull():
            return False
        vp = self.scroll.viewport().size()
        return pix.width() > vp.width() or pix.height() > vp.height()

    def _on_pan_delta(self, dx: int, dy: int) -> None:
        hbar = self.scroll.horizontalScrollBar()
        vbar = self.scroll.verticalScrollBar()
        hbar.setValue(hbar.value() - dx)
        vbar.setValue(vbar.value() - dy)
