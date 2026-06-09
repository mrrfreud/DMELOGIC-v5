"""Global Sticky Notes Board."""
from __future__ import annotations

from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView
)
from PyQt6.QtCore import Qt

from dmelogic.db import sticky_notes as notes_db
from dmelogic.ui.dialogs.sticky_note_editor import StickyNoteEditorDialog


class StickyNotesBoard(QDialog):
    def __init__(self, folder_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.setWindowTitle("Sticky Notes Board")
        self.resize(800, 600)
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search notes...")
        self.search_edit.textChanged.connect(self.refresh)
        top_row.addWidget(self.search_edit, 2)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Unlinked", "Linked", "Archived"])
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        top_row.addWidget(self.filter_combo)

        self.new_btn = QPushButton("New Note")
        self.new_btn.clicked.connect(self._create_note)
        top_row.addWidget(self.new_btn)

        self.open_btn = QPushButton("Open/Edit")
        self.open_btn.clicked.connect(self._open_selected)
        top_row.addWidget(self.open_btn)

        self.archive_btn = QPushButton("Archive")
        self.archive_btn.clicked.connect(self._archive_selected)
        top_row.addWidget(self.archive_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete_selected)
        top_row.addWidget(self.delete_btn)

        top_row.addStretch()
        layout.addLayout(top_row)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Pinned", "Title", "Links", "Updated"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

    def refresh(self):
        search = self.search_edit.text().strip() or None
        filter_text = self.filter_combo.currentText()

        if filter_text == "Unlinked":
            rows = notes_db.list_unlinked_notes(include_archived=False, folder_path=self.folder_path)
        elif filter_text == "Archived":
            rows = notes_db.list_notes(include_archived=True, search=search, folder_path=self.folder_path)
            rows = [r for r in rows if r.get("archived")]
        else:
            rows = notes_db.list_notes(include_archived=False, search=search, folder_path=self.folder_path)
            if filter_text == "Linked":
                rows = [r for r in rows if (r.get("links_count", 0) or 0) > 0]

        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            id_item = QTableWidgetItem(str(row.get("id")))
            id_item.setData(Qt.ItemDataRole.UserRole, int(row.get("id")))
            pinned_item = QTableWidgetItem("📌" if row.get("pinned") else "")
            pinned_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            title_item = QTableWidgetItem(row.get("title") or "(untitled)")
            links_item = QTableWidgetItem(str(row.get("links_count", 0)))
            updated_item = QTableWidgetItem(row.get("updated_at") or "")

            self.table.setItem(i, 0, id_item)
            self.table.setItem(i, 1, pinned_item)
            self.table.setItem(i, 2, title_item)
            self.table.setItem(i, 3, links_item)
            self.table.setItem(i, 4, updated_item)

    def _selected_note_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))

    def _create_note(self):
        note_id = notes_db.create_note(title="", body="", folder_path=self.folder_path)
        self._open_editor(note_id)
        self.refresh()

    def _open_selected(self):
        note_id = self._selected_note_id()
        if note_id is None:
            QMessageBox.information(self, "No selection", "Select a note first.")
            return
        self._open_editor(note_id)
        self.refresh()

    def _open_editor(self, note_id: int):
        dlg = StickyNoteEditorDialog(note_id=note_id, folder_path=self.folder_path, parent=self)
        dlg.exec()

    def _archive_selected(self):
        note_id = self._selected_note_id()
        if note_id is None:
            return
        notes_db.archive_note(note_id, archived=True, folder_path=self.folder_path)
        self.refresh()

    def _delete_selected(self):
        note_id = self._selected_note_id()
        if note_id is None:
            return
        if QMessageBox.question(self, "Delete", "Delete this note?") != QMessageBox.StandardButton.Yes:
            return
        notes_db.delete_note(note_id, folder_path=self.folder_path)
        self.refresh()
