"""Sticky Note Editor dialog."""
from __future__ import annotations

from typing import Optional, List, Tuple
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QCheckBox,
    QPushButton, QComboBox, QListWidget, QListWidgetItem, QMessageBox, QInputDialog
)
from PyQt6.QtCore import Qt

from dmelogic.db import sticky_notes as notes_db

COLORS = [
    "#FFF7A8",
    "#FFECB3",
    "#C8E6C9",
    "#BBDEFB",
    "#FFE0B2",
]


class StickyNoteEditorDialog(QDialog):
    """Dialog to edit a sticky note and its links."""

    def __init__(self, note_id: int, folder_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.note_id = note_id
        self.folder_path = folder_path
        self.setWindowTitle("Sticky Note")
        self.resize(500, 500)

        self.note = notes_db.get_note_with_links(note_id, folder_path=folder_path)
        if not self.note:
            QMessageBox.warning(self, "Not found", "Note no longer exists.")
            self.reject()
            return

        self._setup_ui()
        self._populate()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Title
        layout.addWidget(QLabel("Title"))
        self.title_edit = QLineEdit()
        layout.addWidget(self.title_edit)

        # Body
        layout.addWidget(QLabel("Body"))
        self.body_edit = QTextEdit()
        self.body_edit.setMinimumHeight(180)
        layout.addWidget(self.body_edit)

        # Row: pinned, archived, color
        row = QHBoxLayout()
        self.pinned_cb = QCheckBox("Pinned")
        row.addWidget(self.pinned_cb)
        self.archived_cb = QCheckBox("Archived")
        row.addWidget(self.archived_cb)
        row.addStretch()
        row.addWidget(QLabel("Color"))
        self.color_combo = QComboBox()
        for c in COLORS:
            self.color_combo.addItem(c, c)
        row.addWidget(self.color_combo)
        layout.addLayout(row)

        # Links list
        layout.addWidget(QLabel("Links"))
        self.links_list = QListWidget()
        layout.addWidget(self.links_list)

        link_row = QHBoxLayout()
        self.add_link_btn = QPushButton("Add Link…")
        self.add_link_btn.clicked.connect(self._add_link)
        link_row.addWidget(self.add_link_btn)
        self.remove_link_btn = QPushButton("Remove Selected")
        self.remove_link_btn.clicked.connect(self._remove_link)
        link_row.addWidget(self.remove_link_btn)
        link_row.addStretch()
        layout.addLayout(link_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _populate(self) -> None:
        self.title_edit.setText(self.note.get("title") or "")
        self.body_edit.setPlainText(self.note.get("body") or "")
        self.pinned_cb.setChecked(bool(self.note.get("pinned")))
        self.archived_cb.setChecked(bool(self.note.get("archived")))
        color = self.note.get("color") or COLORS[0]
        idx = max(0, self.color_combo.findData(color))
        self.color_combo.setCurrentIndex(idx)

        self.links_list.clear()
        for entity_type, entity_id in self.note.get("links", []):
            self._add_link_item(entity_type, entity_id)

    def _add_link_item(self, entity_type: str, entity_id: int):
        text = f"{entity_type} #{entity_id}"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, (entity_type, int(entity_id)))
        self.links_list.addItem(item)

    def _add_link(self):
        entity_types = ["patient", "order", "prescriber"]
        etype, ok = QInputDialog.getItem(self, "Entity Type", "Choose type:", entity_types, 0, False)
        if not ok:
            return
        entity_id, ok = QInputDialog.getInt(self, "Entity ID", "Enter ID:", 1, 1)
        if not ok:
            return
        self._add_link_item(etype, int(entity_id))

    def _remove_link(self):
        row = self.links_list.currentRow()
        if row >= 0:
            self.links_list.takeItem(row)

    def _collect_links(self) -> List[Tuple[str, int]]:
        links: List[Tuple[str, int]] = []
        for i in range(self.links_list.count()):
            item = self.links_list.item(i)
            etype, eid = item.data(Qt.ItemDataRole.UserRole)
            links.append((etype, int(eid)))
        return links

    def _save(self):
        title = self.title_edit.text()
        body = self.body_edit.toPlainText()
        color = self.color_combo.currentData()
        pinned = self.pinned_cb.isChecked()
        archived = self.archived_cb.isChecked()

        notes_db.update_note(
            self.note_id,
            title=title,
            body=body,
            color=color,
            pinned=pinned,
            archived=archived,
            folder_path=self.folder_path,
        )
        notes_db.set_note_links(self.note_id, self._collect_links(), folder_path=self.folder_path)
        self.accept()
