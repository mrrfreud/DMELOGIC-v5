"""
PDF Tool Dialog — Merge / Split PDFs
=====================================
PyQt6 dialog with two tabs:
  1. Merge / Combine – add multiple PDFs, reorder, merge into one
  2. Split / Extract – view page thumbnails, select pages to extract
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QImage, QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QPushButton, QListWidget, QListWidgetItem, QLabel,
    QFileDialog, QMessageBox, QScrollArea, QGridLayout,
    QCheckBox, QFrame, QSizePolicy, QApplication,
)


class PDFToolDialog(QDialog):
    """PDF merge / split tool dialog."""

    def __init__(self, parent=None, initial_file: str = ""):
        super().__init__(parent)
        self.setWindowTitle("PDF Tool — Merge / Split")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.resize(750, 600)

        self._setup_ui()

        if initial_file and os.path.isfile(initial_file):
            self.tabs.setCurrentWidget(self._extract_tab)
            self._load_extract_file(initial_file)

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.tabs = QTabWidget()
        self._merge_tab = self._build_merge_tab()
        self._extract_tab = self._build_extract_tab()
        self.tabs.addTab(self._merge_tab, "  Merge / Combine  ")
        self.tabs.addTab(self._extract_tab, "  Split / Extract  ")

        layout.addWidget(self.tabs)

    # ---- Merge tab ----

    def _build_merge_tab(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        # Top buttons
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("+ Add Files")
        self._btn_add.clicked.connect(self._add_files)
        self._btn_clear = QPushButton("Clear List")
        self._btn_clear.clicked.connect(self._clear_files)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_clear)
        btn_row.addStretch()
        vbox.addLayout(btn_row)

        # File list
        self._file_list = QListWidget()
        self._file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._file_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        vbox.addWidget(self._file_list, 1)

        # Bottom buttons
        bottom = QHBoxLayout()
        self._btn_up = QPushButton("▲ Move Up")
        self._btn_up.clicked.connect(self._move_up)
        self._btn_down = QPushButton("▼ Move Down")
        self._btn_down.clicked.connect(self._move_down)
        self._btn_remove = QPushButton("✕ Remove")
        self._btn_remove.clicked.connect(self._remove_selected)
        self._btn_merge = QPushButton("MERGE PDFs")
        self._btn_merge.setStyleSheet(
            "QPushButton { background-color: #0078D4; color: white; "
            "font-weight: bold; padding: 8px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #106EBE; }"
        )
        self._btn_merge.clicked.connect(self._merge_pdfs)

        bottom.addWidget(self._btn_up)
        bottom.addWidget(self._btn_down)
        bottom.addWidget(self._btn_remove)
        bottom.addStretch()
        bottom.addWidget(self._btn_merge)
        vbox.addLayout(bottom)

        self._merge_paths: List[str] = []
        return tab

    # ---- Extract tab ----

    def _build_extract_tab(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        # Step 1: File selection
        lbl1 = QLabel("<b>Step 1:</b> Select a PDF")
        vbox.addWidget(lbl1)

        file_row = QHBoxLayout()
        self._lbl_file = QLabel("No file selected")
        self._lbl_file.setStyleSheet(
            "background: white; border: 1px solid #ccc; padding: 4px;"
        )
        self._lbl_file.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_extract)
        file_row.addWidget(self._lbl_file, 1)
        file_row.addWidget(btn_browse)
        vbox.addLayout(file_row)

        # Step 2: Page thumbnails
        lbl2 = QLabel("<b>Step 2:</b> Click pages to select them")
        vbox.addWidget(lbl2)

        # Select-all / Deselect-all row
        sel_row = QHBoxLayout()
        btn_sel_all = QPushButton("Select All")
        btn_sel_all.clicked.connect(self._select_all_pages)
        btn_desel = QPushButton("Deselect All")
        btn_desel.clicked.connect(self._deselect_all_pages)
        self._lbl_count = QLabel("")
        sel_row.addWidget(btn_sel_all)
        sel_row.addWidget(btn_desel)
        sel_row.addStretch()
        sel_row.addWidget(self._lbl_count)
        vbox.addLayout(sel_row)

        # Scroll area for thumbnails
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { background: #f5f5f5; }")
        self._thumb_container = QWidget()
        self._thumb_layout = QGridLayout(self._thumb_container)
        self._thumb_layout.setSpacing(8)
        self._scroll.setWidget(self._thumb_container)
        vbox.addWidget(self._scroll, 1)

        # Extract button
        self._btn_extract = QPushButton("EXTRACT SELECTED PAGES")
        self._btn_extract.setEnabled(False)
        self._btn_extract.setStyleSheet(
            "QPushButton { background-color: #0078D4; color: white; "
            "font-weight: bold; padding: 10px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #106EBE; }"
            "QPushButton:disabled { background-color: #999; }"
        )
        self._btn_extract.clicked.connect(self._extract_pages)
        vbox.addWidget(self._btn_extract)

        self._extract_path: str = ""
        self._page_checks: List[QCheckBox] = []
        self._page_frames: List[QFrame] = []
        self._thumb_pixmaps: list = []  # prevent GC
        return tab

    # ================================================================== Merge logic

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs", "", "PDF Files (*.pdf)"
        )
        for f in files:
            self._merge_paths.append(f)
            item = QListWidgetItem(os.path.basename(f))
            item.setToolTip(f)
            self._file_list.addItem(item)

    def _clear_files(self):
        self._merge_paths.clear()
        self._file_list.clear()

    def _remove_selected(self):
        row = self._file_list.currentRow()
        if row >= 0:
            self._merge_paths.pop(row)
            self._file_list.takeItem(row)

    def _move_up(self):
        row = self._file_list.currentRow()
        if row > 0:
            item = self._file_list.takeItem(row)
            path = self._merge_paths.pop(row)
            self._file_list.insertItem(row - 1, item)
            self._merge_paths.insert(row - 1, path)
            self._file_list.setCurrentRow(row - 1)

    def _move_down(self):
        row = self._file_list.currentRow()
        if 0 <= row < self._file_list.count() - 1:
            item = self._file_list.takeItem(row)
            path = self._merge_paths.pop(row)
            self._file_list.insertItem(row + 1, item)
            self._merge_paths.insert(row + 1, path)
            self._file_list.setCurrentRow(row + 1)

    def _merge_pdfs(self):
        if not self._merge_paths:
            QMessageBox.warning(self, "Warning", "Add at least one PDF file first.")
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Merged PDF", "", "PDF Files (*.pdf)"
        )
        if not save_path:
            return

        try:
            from pypdf import PdfWriter
            writer = PdfWriter()
            for pdf_path in self._merge_paths:
                writer.append(pdf_path)
            with open(save_path, "wb") as out:
                writer.write(out)
            QMessageBox.information(
                self, "Success",
                f"Merged {len(self._merge_paths)} PDFs successfully!\n{save_path}"
            )
            self._clear_files()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Merge failed:\n{e}")

    # ================================================================== Extract logic

    def _browse_extract(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF", "", "PDF Files (*.pdf)"
        )
        if path:
            self._load_extract_file(path)

    def _load_extract_file(self, path: str):
        self._extract_path = path
        self._lbl_file.setText(os.path.basename(path))
        self._lbl_file.setToolTip(path)
        self._load_thumbnails()

    def _load_thumbnails(self):
        """Render page thumbnails and create selectable cards."""
        # Clear existing
        for i in reversed(range(self._thumb_layout.count())):
            w = self._thumb_layout.itemAt(i).widget()
            if w:
                w.setParent(None)
        self._page_checks.clear()
        self._page_frames.clear()
        self._thumb_pixmaps.clear()
        self._lbl_count.setText("")

        try:
            import fitz  # PyMuPDF
        except ImportError:
            QMessageBox.critical(
                self, "Error",
                "PyMuPDF is required for page thumbnails.\n"
                "Install with: pip install PyMuPDF"
            )
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            doc = fitz.open(self._extract_path)
            total = len(doc)
            cols = 4

            for idx in range(total):
                page = doc[idx]
                # Render at ~150px wide
                zoom = 150 / page.rect.width if page.rect.width else 1.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)

                # Convert to QPixmap
                qimg = QImage(
                    pix.samples, pix.width, pix.height,
                    pix.stride, QImage.Format.Format_RGB888
                )
                pixmap = QPixmap.fromImage(qimg)
                self._thumb_pixmaps.append(pixmap)

                # Build card
                card = QFrame()
                card.setFrameShape(QFrame.Shape.Box)
                card.setLineWidth(2)
                card.setStyleSheet(
                    "QFrame { background: white; border: 2px solid #ccc; border-radius: 4px; }"
                )
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(6, 6, 6, 6)
                card_layout.setSpacing(4)

                cb = QCheckBox(f"Page {idx + 1}")
                cb.stateChanged.connect(lambda state, c=card, i=idx: self._on_page_toggled(c, i))
                card_layout.addWidget(cb, 0, Qt.AlignmentFlag.AlignCenter)

                img_lbl = QLabel()
                img_lbl.setPixmap(pixmap)
                img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                img_lbl.mousePressEvent = lambda e, chk=cb: chk.setChecked(not chk.isChecked())
                card_layout.addWidget(img_lbl)

                row, col = divmod(idx, cols)
                self._thumb_layout.addWidget(card, row, col)
                self._page_checks.append(cb)
                self._page_frames.append(card)

            doc.close()
            self._btn_extract.setEnabled(total > 0)
            self._lbl_count.setText(f"{total} pages")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load PDF:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _on_page_toggled(self, card: QFrame, idx: int):
        """Update card border when selection changes."""
        checked = self._page_checks[idx].isChecked()
        if checked:
            card.setStyleSheet(
                "QFrame { background: #E3F2FD; border: 2px solid #0078D4; border-radius: 4px; }"
            )
        else:
            card.setStyleSheet(
                "QFrame { background: white; border: 2px solid #ccc; border-radius: 4px; }"
            )
        # Update count
        sel = sum(1 for cb in self._page_checks if cb.isChecked())
        total = len(self._page_checks)
        self._lbl_count.setText(f"{sel}/{total} selected" if sel else f"{total} pages")

    def _select_all_pages(self):
        for cb in self._page_checks:
            cb.setChecked(True)

    def _deselect_all_pages(self):
        for cb in self._page_checks:
            cb.setChecked(False)

    def _extract_pages(self):
        selected = [i for i, cb in enumerate(self._page_checks) if cb.isChecked()]
        if not selected:
            QMessageBox.warning(self, "Warning", "Select at least one page.")
            return

        # Default filename
        base = Path(self._extract_path).stem
        default_name = f"{base}_pages.pdf"

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Extracted Pages", default_name, "PDF Files (*.pdf)"
        )
        if not save_path:
            return

        try:
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(self._extract_path)
            writer = PdfWriter()
            for idx in selected:
                writer.add_page(reader.pages[idx])
            with open(save_path, "wb") as out:
                writer.write(out)
            QMessageBox.information(
                self, "Success",
                f"Extracted {len(selected)} page(s) successfully!\n{save_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Extract failed:\n{e}")
