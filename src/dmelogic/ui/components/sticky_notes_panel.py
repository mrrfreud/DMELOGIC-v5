"""Reusable sticky notes panel for entity-linked notes."""
from __future__ import annotations

from typing import Optional, Dict, List
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QLabel, QApplication
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from dmelogic.db import sticky_notes as notes_db
from dmelogic.ui.sticky_note_widget import StickyNoteWindow, DEFAULT_COLORS

# Module-level storage to keep windows alive
_global_sticky_windows: Dict[int, StickyNoteWindow] = {}


class StickyNotesPanel(QWidget):
    """Embeddable panel for listing and editing sticky notes linked to an entity."""

    def __init__(
        self,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        folder_path: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.folder_path = folder_path
        self._open_windows: Dict[int, StickyNoteWindow] = {}

        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Header with context info
        if self.entity_type and self.entity_id is not None:
            header = QLabel(f"Sticky Notes for {self.entity_type.title()} #{self.entity_id}")
        else:
            header = QLabel("Unlinked Sticky Notes")
        header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #000;")
        layout.addWidget(header)

        btn_row = QHBoxLayout()
        self.new_btn = QPushButton("New Note")
        self.new_btn.setToolTip("Create a new sticky note linked to this entity")
        self.new_btn.setStyleSheet("color: #000;")
        self.new_btn.clicked.connect(self._create_note)
        btn_row.addWidget(self.new_btn)

        self.link_btn = QPushButton("Link Existing")
        self.link_btn.setToolTip("Link an unlinked note to this entity")
        self.link_btn.setStyleSheet("color: #000;")
        self.link_btn.clicked.connect(self._link_existing)
        btn_row.addWidget(self.link_btn)

        self.open_btn = QPushButton("Open")
        self.open_btn.setToolTip("Open selected note as floating sticky")
        self.open_btn.setStyleSheet("color: #000;")
        self.open_btn.clicked.connect(self._open_selected)
        btn_row.addWidget(self.open_btn)

        self.unlink_btn = QPushButton("Unlink")
        self.unlink_btn.setToolTip("Remove link from this entity (note remains)")
        self.unlink_btn.setStyleSheet("color: #000;")
        self.unlink_btn.clicked.connect(self._unlink_selected)
        btn_row.addWidget(self.unlink_btn)

        self.archive_btn = QPushButton("Archive")
        self.archive_btn.setToolTip("Archive this note")
        self.archive_btn.setStyleSheet("color: #000;")
        self.archive_btn.clicked.connect(self._archive_selected)
        btn_row.addWidget(self.archive_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setToolTip("Permanently delete this note")
        self.delete_btn.setStyleSheet("color: #c00; font-weight: bold;")
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.delete_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["", "Title", "Snippet", "Updated"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(100)  # Ensure table is visible
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table, 1)  # Give table stretch priority

        if not self.entity_type or self.entity_id is None:
            self.unlink_btn.setVisible(False)
            self.link_btn.setVisible(False)

    def refresh(self) -> None:
        """Reload notes for current context."""
        if self.entity_type and self.entity_id is not None:
            rows = notes_db.list_notes_for_entity(self.entity_type, int(self.entity_id), folder_path=self.folder_path)
        else:
            rows = notes_db.list_unlinked_notes(folder_path=self.folder_path)

        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            # Color + pinned indicator
            color = row.get("color", DEFAULT_COLORS[0])
            pinned = "P" if row.get("pinned") else ""
            ind_item = QTableWidgetItem(pinned)
            ind_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ind_item.setToolTip("Pinned" if pinned else "")
            ind_item.setData(Qt.ItemDataRole.UserRole, row["id"])
            ind_item.setData(Qt.ItemDataRole.UserRole + 1, row)  # Store full note
            bg = QColor(color)
            bg.setAlpha(100)
            ind_item.setBackground(bg)

            title_item = QTableWidgetItem(row.get("title") or "(untitled)")
            title_item.setBackground(bg)

            snippet = (row.get("body") or "")[:60].replace("\n", " ")
            snippet_item = QTableWidgetItem(snippet)
            snippet_item.setBackground(bg)

            updated = row.get("updated_at") or ""
            if len(updated) > 10:
                updated = updated[:10]  # Just date
            updated_item = QTableWidgetItem(updated)
            updated_item.setBackground(bg)

            self.table.setItem(i, 0, ind_item)
            self.table.setItem(i, 1, title_item)
            self.table.setItem(i, 2, snippet_item)
            self.table.setItem(i, 3, updated_item)

    # Helpers
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
        if self.entity_type and self.entity_id is not None:
            notes_db.set_note_links(note_id, [(self.entity_type, int(self.entity_id))], folder_path=self.folder_path)
        self._open_note_as_floating(note_id)
        self.refresh()

    def _open_selected(self):
        note_id = self._selected_note_id()
        if note_id is None:
            QMessageBox.information(self, "No selection", "Select a note first.")
            return
        self._open_note_as_floating(note_id)

    def _open_note_as_floating(self, note_id: int):
        """Open note as floating sticky window."""
        global _global_sticky_windows
        
        # Reuse existing window if still open
        try:
            if note_id in _global_sticky_windows:
                w = _global_sticky_windows[note_id]
                if w is not None and w.isVisible():
                    w.raise_()
                    w.activateWindow()
                    return
                else:
                    _global_sticky_windows.pop(note_id, None)
        except RuntimeError:
            # C++ object deleted
            _global_sticky_windows.pop(note_id, None)

        note = notes_db.get_note(note_id, folder_path=self.folder_path)
        if not note:
            return

        # Get the main window as parent to keep the window alive
        main_win = None
        app = QApplication.instance()
        if app:
            for widget in app.topLevelWidgets():
                if widget.objectName() == "MainWindow" or widget.__class__.__name__ == "MainWindow":
                    main_win = widget
                    break
        
        # Create window with main window as parent
        w = StickyNoteWindow(note, folder_path=self.folder_path, parent=main_win)
        # Make it a separate window that stays on top initially
        w.setWindowFlags(
            w.windowFlags() 
            | Qt.WindowType.Window 
            | Qt.WindowType.WindowStaysOnTopHint
        )
        
        # Store in global dict to keep alive
        _global_sticky_windows[note_id] = w
        self._open_windows[note_id] = w
        
        w.show()
        w.raise_()
        w.activateWindow()
        
        # Remove "stays on top" after a moment so it behaves normally
        QTimer.singleShot(100, lambda: self._remove_always_on_top(w))
    
    def _remove_always_on_top(self, window):
        """Remove the always-on-top flag after window is shown."""
        try:
            if window and window.isVisible():
                # Remove the stays on top hint but keep as separate window
                window.setWindowFlags(
                    (window.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
                    | Qt.WindowType.Window
                )
                window.show()  # Must show again after changing flags
        except RuntimeError:
            pass
    
    def _bring_to_front(self, window):
        """Bring window to front with proper activation."""
        try:
            if window and window.isVisible():
                window.raise_()
                window.activateWindow()
                window.setFocus()
        except RuntimeError:
            pass  # Window was deleted

    def _unlink_selected(self):
        if not (self.entity_type and self.entity_id is not None):
            return
        note_id = self._selected_note_id()
        if note_id is None:
            QMessageBox.information(self, "No selection", "Select a note first.")
            return
        links = notes_db.get_note_links(note_id, folder_path=self.folder_path)
        links = [(t, i) for (t, i) in links if not (t == self.entity_type and i == int(self.entity_id))]
        notes_db.set_note_links(note_id, links, folder_path=self.folder_path)
        self.refresh()

    def _archive_selected(self):
        note_id = self._selected_note_id()
        if note_id is None:
            QMessageBox.information(self, "No selection", "Select a note first.")
            return
        notes_db.archive_note(note_id, archived=True, folder_path=self.folder_path)
        self.refresh()

    def _delete_selected(self):
        """Permanently delete the selected note."""
        note_id = self._selected_note_id()
        if note_id is None:
            QMessageBox.information(self, "No selection", "Select a note first.")
            return
        
        # Get note details for confirmation
        row = self.table.currentRow()
        title_item = self.table.item(row, 1)
        title = title_item.text() if title_item else "(untitled)"
        
        reply = QMessageBox.warning(
            self,
            "Delete Note",
            f"Permanently delete note \"{title}\"?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        try:
            notes_db.delete_note(note_id, folder_path=self.folder_path)
            # Close floating window if open
            global _global_sticky_windows
            if note_id in _global_sticky_windows:
                try:
                    w = _global_sticky_windows.pop(note_id)
                    if w:
                        w.close()
                except RuntimeError:
                    pass
            if note_id in self._open_windows:
                self._open_windows.pop(note_id, None)
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete note: {e}")

    def _link_existing(self):
        """Link an existing unlinked note to this entity."""
        if not (self.entity_type and self.entity_id is not None):
            QMessageBox.information(self, "Linking", "Open from a patient/order/prescriber to link.")
            return
        unlinked = notes_db.list_unlinked_notes(folder_path=self.folder_path)
        if not unlinked:
            QMessageBox.information(self, "No unlinked notes", "Create a note first, then link it.")
            return
        titles = [f"#{r['id']} - {r.get('title') or '(untitled)'}" for r in unlinked]
        from PyQt6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(self, "Link Note", "Choose note to link:", titles, 0, False)
        if not ok:
            return
        idx = titles.index(choice)
        note_id = int(unlinked[idx]["id"])
        links = notes_db.get_note_links(note_id, folder_path=self.folder_path)
        links.append((self.entity_type, int(self.entity_id)))
        notes_db.set_note_links(note_id, links, folder_path=self.folder_path)
        self.refresh()

    def _link_existing(self):
        if not (self.entity_type and self.entity_id is not None):
            # No context: nothing to link
            QMessageBox.information(self, "Linking", "Open from a patient/order/prescriber to link.")
            return
        unlinked = notes_db.list_unlinked_notes(folder_path=self.folder_path)
        if not unlinked:
            QMessageBox.information(self, "No unlinked notes", "Create a note first, then link it.")
            return
        titles = [f"#{r['id']} - {r.get('title') or '(untitled)'}" for r in unlinked]
        from PyQt6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(self, "Link Note", "Choose note to link:", titles, 0, False)
        if not ok:
            return
        idx = titles.index(choice)
        note_id = int(unlinked[idx]["id"])
        links = notes_db.get_note_links(note_id, folder_path=self.folder_path)
        links.append((self.entity_type, int(self.entity_id)))
        notes_db.set_note_links(note_id, links, folder_path=self.folder_path)
        self.refresh()
