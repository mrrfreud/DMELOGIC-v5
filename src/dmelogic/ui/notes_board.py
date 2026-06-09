from __future__ import annotations

from typing import Optional, Dict, Any, List

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QCheckBox,
    QLabel,
    QApplication,
)
from PyQt6.QtCore import Qt, QTimer

from dmelogic.db import sticky_notes as notes_db
from dmelogic.ui.sticky_note_widget import StickyNoteWindow, DEFAULT_COLORS

# Module-level storage to keep windows alive
_global_board_windows: Dict[int, StickyNoteWindow] = {}


class NotesBoardDialog(QDialog):
    """Board for creating and opening floating sticky notes."""

    def __init__(self, folder_path: Optional[str], parent=None):
        super().__init__(parent)
        self.folder_path = folder_path
        self._open_windows: Dict[int, StickyNoteWindow] = {}

        self.setWindowTitle("📝 Sticky Notes")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setMinimumSize(520, 420)

        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Title
        title = QLabel("Sticky Notes Board")
        title.setStyleSheet("font-size: 16pt; font-weight: bold; color: #000;")
        root.addWidget(title)

        # Top row - buttons
        top = QHBoxLayout()
        btn_new = QPushButton("New Note")
        btn_new.setStyleSheet("font-size: 11pt; padding: 6px 12px; color: #000;")
        btn_new.clicked.connect(self._new_note)
        top.addWidget(btn_new)

        btn_open = QPushButton("Open")
        btn_open.setStyleSheet("font-size: 11pt; padding: 6px 12px; color: #000;")
        btn_open.clicked.connect(self._open_selected)
        top.addWidget(btn_open)

        btn_delete = QPushButton("Delete")
        btn_delete.setStyleSheet("font-size: 11pt; padding: 6px 12px; color: #000;")
        btn_delete.setToolTip("Delete selected note")
        btn_delete.clicked.connect(self._delete_selected)
        top.addWidget(btn_delete)

        top.addStretch(1)

        self.show_archived = QCheckBox("Show Archived")
        self.show_archived.toggled.connect(self._reload)
        top.addWidget(self.show_archived)

        root.addLayout(top)

        # Notes list with colored indicators
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.model().rowsMoved.connect(self._on_rows_moved)
        self.list.itemDoubleClicked.connect(lambda _: self._open_selected())
        self.list.setStyleSheet("""
            QListWidget {
                font-size: 11pt;
                border: 1px solid #ccc;
                border-radius: 6px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #e3f2fd;
            }
        """)
        root.addWidget(self.list, 1)

        # Info label
        info = QLabel("Double-click a note to open it as a floating sticky • Drag to reorder")
        info.setStyleSheet("color: #666; font-size: 9pt;")
        root.addWidget(info)

    def _reload(self) -> None:
        self.list.clear()
        include_arch = self.show_archived.isChecked()
        notes = notes_db.list_notes(include_archived=include_arch, folder_path=self.folder_path)
        for n in notes:
            title = n.get("title") or "(untitled)"
            color = n.get("color", DEFAULT_COLORS[0])
            pinned = "[Pinned] " if n.get("pinned") else ""
            archived = "[Archived] " if n.get("archived") else ""
            item = QListWidgetItem(f"{pinned}{archived}{title}")
            item.setData(256, n["id"])
            item.setData(257, n)  # Store full note data
            # Color indicator via background tint
            item.setBackground(self._tinted_color(color))
            self.list.addItem(item)

    def _tinted_color(self, hex_color: str):
        from PyQt6.QtGui import QColor
        c = QColor(hex_color)
        c.setAlpha(80)
        return c

    def _on_rows_moved(self, parent, start, end, destination, row):
        """Persist the new order after drag-drop reorder."""
        ids_in_order = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item:
                ids_in_order.append(int(item.data(256)))
        # Update sort_order for each note
        notes_db.reorder_notes(ids_in_order, folder_path=self.folder_path)

    def _next_color(self) -> str:
        try:
            total = len(notes_db.list_notes(include_archived=True, folder_path=self.folder_path))
            return DEFAULT_COLORS[total % len(DEFAULT_COLORS)]
        except Exception:
            return DEFAULT_COLORS[0]

    def _new_note(self) -> None:
        note_id = notes_db.create_note(
            title="",
            body="",
            color=self._next_color(),
            pinned=False,
            folder_path=self.folder_path,
        )
        self._reload()
        self._open_note_id(note_id)

    def _open_selected(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        note_id = int(item.data(256))
        self._open_note_id(note_id)

    def _delete_selected(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        note_id = int(item.data(256))

        # Close any open window first
        if note_id in self._open_windows:
            try:
                self._open_windows[note_id].close()
            except Exception:
                pass
            self._open_windows.pop(note_id, None)

        notes_db.delete_note(note_id, folder_path=self.folder_path)
        self._reload()

    def _open_note_id(self, note_id: int) -> None:
        global _global_board_windows
        
        # Check if window still exists and is visible
        try:
            if note_id in _global_board_windows:
                w = _global_board_windows[note_id]
                if w is not None and w.isVisible():
                    w.raise_()
                    w.activateWindow()
                    return
                else:
                    _global_board_windows.pop(note_id, None)
        except RuntimeError:
            _global_board_windows.pop(note_id, None)

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
        
        # Store in global dict AND local dict
        _global_board_windows[note_id] = w
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
                window.setWindowFlags(
                    (window.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
                    | Qt.WindowType.Window
                )
                window.show()
        except RuntimeError:
            pass
