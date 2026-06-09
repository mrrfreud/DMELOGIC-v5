"""
viewer.py — a lightweight document viewer for the triage screen.

Renders PDFs (via PyMuPDF) and common image formats to a scrollable pane,
with page navigation and fit-to-width. Deliberately minimal: this replaces the
*viewing* role of the legacy PDFViewer, nothing more.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

logger = logging.getLogger("triage.viewer")

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"}


class DocumentViewer(QWidget):
    """Displays a single PDF or image file with page navigation."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._path: Path | None = None
        self._pdf = None            # fitz.Document when a PDF is open
        self._page = 0
        self._zoom = 1.5

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Toolbar
        bar = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Prev")
        self.next_btn = QPushButton("Next ▶")
        self.page_lbl = QLabel("—")
        self.zoom_out_btn = QPushButton("−")
        self.zoom_in_btn = QPushButton("+")
        for w in (self.prev_btn, self.next_btn):
            w.setFixedHeight(28)
        self.prev_btn.clicked.connect(self.prev_page)
        self.next_btn.clicked.connect(self.next_page)
        self.zoom_in_btn.clicked.connect(lambda: self._set_zoom(self._zoom + 0.25))
        self.zoom_out_btn.clicked.connect(lambda: self._set_zoom(self._zoom - 0.25))
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.page_lbl)
        bar.addWidget(self.next_btn)
        bar.addStretch()
        bar.addWidget(self.zoom_out_btn)
        bar.addWidget(self.zoom_in_btn)
        root.addLayout(bar)

        # Scrollable page image
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas = QLabel("No document selected")
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setStyleSheet("color:#64748b; padding:40px;")
        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll, 1)

        self._update_controls()

    # ── public API ──────────────────────────────────────────────────────
    def load(self, path: str | Path | None) -> None:
        self._close_pdf()
        self._page = 0
        self._path = Path(path) if path else None

        if self._path is None or not self._path.exists():
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText("No document selected" if self._path is None
                                else f"File not found:\n{self._path}")
            self._update_controls()
            return

        suffix = self._path.suffix.lower()
        if suffix == ".pdf":
            self._load_pdf()
        elif suffix in _IMAGE_SUFFIXES:
            self._render_image()
        else:
            self.canvas.setPixmap(QPixmap())
            self.canvas.setText(f"Cannot preview this file type:\n{self._path.name}")
        self._update_controls()

    def clear(self) -> None:
        self.load(None)

    # ── PDF handling ────────────────────────────────────────────────────
    def _load_pdf(self) -> None:
        try:
            import fitz  # PyMuPDF
            self._pdf = fitz.open(str(self._path))
            self._render_pdf_page()
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
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888)
            self.canvas.setPixmap(QPixmap.fromImage(img.copy()))
            self.canvas.setText("")
        except Exception as e:
            logger.warning("Render failed: %s", e)
            self.canvas.setText(f"Render error:\n{e}")

    def _render_image(self) -> None:
        pix = QPixmap(str(self._path))
        if pix.isNull():
            self.canvas.setText(f"Could not load image:\n{self._path.name}")
            return
        scaled = pix.scaledToWidth(
            int(pix.width() * self._zoom),
            Qt.TransformationMode.SmoothTransformation,
        ) if self._zoom != 1.0 else pix
        self.canvas.setPixmap(scaled)
        self.canvas.setText("")

    def _close_pdf(self) -> None:
        if self._pdf is not None:
            try:
                self._pdf.close()
            except Exception:
                pass
            self._pdf = None

    # ── navigation ──────────────────────────────────────────────────────
    def _page_count(self) -> int:
        if self._pdf is not None:
            return self._pdf.page_count
        return 1 if self._path and self._path.exists() else 0

    def next_page(self) -> None:
        if self._pdf and self._page < self._pdf.page_count - 1:
            self._page += 1
            self._render_pdf_page()
            self._update_controls()

    def prev_page(self) -> None:
        if self._pdf and self._page > 0:
            self._page -= 1
            self._render_pdf_page()
            self._update_controls()

    def _set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.5, min(4.0, zoom))
        if self._pdf is not None:
            self._render_pdf_page()
        elif self._path and self._path.suffix.lower() in _IMAGE_SUFFIXES:
            self._render_image()

    def _update_controls(self) -> None:
        count = self._page_count()
        is_pdf = self._pdf is not None
        self.prev_btn.setEnabled(is_pdf and self._page > 0)
        self.next_btn.setEnabled(is_pdf and self._page < count - 1)
        if count == 0:
            self.page_lbl.setText("—")
        elif is_pdf:
            self.page_lbl.setText(f"Page {self._page + 1} / {count}")
        else:
            self.page_lbl.setText("Image")
