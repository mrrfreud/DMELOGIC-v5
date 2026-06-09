"""
Draggable button bar widget that allows reordering buttons via drag-and-drop.
Persists order to settings.
"""
from __future__ import annotations

from typing import List, Optional, Callable
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QApplication, QSizePolicy, QMenu
)
from PyQt6.QtCore import Qt, QMimeData, QPoint
from PyQt6.QtGui import QDrag, QPixmap, QPainter, QColor, QAction


class DraggableButton(QPushButton):
    """A QPushButton that can be dragged to reorder."""

    def __init__(self, text: str, key: str, parent=None):
        super().__init__(text, parent)
        self.key = key  # unique identifier for saving order
        self._drag_start_pos: Optional[QPoint] = None
        self._is_drop_target = False
        self.setAcceptDrops(True)  # Accept drops for proper event propagation

    def set_drop_highlight(self, highlight: bool) -> None:
        """Set visual highlight when this button is a drop target."""
        self._is_drop_target = highlight
        if highlight:
            self.setStyleSheet(self.styleSheet() + """
                QPushButton { border: 2px dashed #0078D4 !important; }
            """)
        else:
            # Remove the highlight border by refreshing the style
            style = self.styleSheet().replace(
                "QPushButton { border: 2px dashed #0078D4 !important; }", ""
            )
            self.setStyleSheet(style)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._drag_start_pos is None:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        # Start drag
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(self.key)
        drag.setMimeData(mime)

        # Create pixmap of button for drag visual
        pixmap = QPixmap(self.size())
        self.render(pixmap)
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())

        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        """Forward drag enter to parent bar."""
        if event.mimeData().hasText() and self.parent():
            self.parent().dragEnterEvent(event)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Forward drag move to parent bar."""
        if self.parent():
            # Convert position to parent coordinates
            parent_pos = self.mapToParent(event.position().toPoint())
            self.parent()._update_drop_highlight(self.parent()._get_drop_index(parent_pos))
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        """Forward drag leave to parent bar."""
        if self.parent():
            self.parent()._clear_drop_highlights()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        """Forward drop to parent bar."""
        if self.parent():
            # Convert position to parent coordinates
            parent_pos = self.mapToParent(event.position().toPoint())
            # Create a modified event isn't possible, so we manually handle it
            dragged_key = event.mimeData().text()
            if dragged_key in self.parent()._buttons:
                self.parent()._clear_drop_highlights()
                insert_idx = self.parent()._get_drop_index(parent_pos)
                
                # Remove from old position
                old_idx = self.parent()._order.index(dragged_key)
                self.parent()._order.pop(old_idx)
                
                # Adjust insert index if needed
                if old_idx < insert_idx:
                    insert_idx -= 1
                
                # Insert at new position
                self.parent()._order.insert(insert_idx, dragged_key)
                
                self.parent()._rebuild_layout()
                event.acceptProposedAction()
                
                # Notify save callback
                if self.parent()._save_callback:
                    self.parent()._save_callback(self.parent()._order)
                return
            event.ignore()


class DraggableButtonBar(QWidget):
    """
    A horizontal bar of buttons that can be reordered via drag-drop.
    
    Usage:
        bar = DraggableButtonBar(save_callback=my_save_func)
        bar.add_button("new_order", "➕ New Order", on_click=self.new_order)
        bar.add_button("edit_order", "✏️ Edit Order", on_click=self.edit_order)
        bar.set_order(["edit_order", "new_order"])  # load saved order
    """

    def __init__(self, save_callback: Optional[Callable[[List[str]], None]] = None, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        
        self._buttons: dict[str, DraggableButton] = {}
        self._order: List[str] = []
        self._default_order: List[str] = []  # Store original order
        self._save_callback = save_callback
        self._drop_indicator_idx: int = -1

    def add_button(self, key: str, text: str, tooltip: str = "", on_click: Optional[Callable] = None) -> DraggableButton:
        """Add a draggable button to the bar."""
        btn = DraggableButton(text, key, self)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if tooltip:
            btn.setToolTip(tooltip)
        if on_click:
            btn.clicked.connect(on_click)
        
        self._buttons[key] = btn
        self._order.append(key)
        self._default_order.append(key)  # Track original order
        self._layout.addWidget(btn)
        return btn

    def get_button(self, key: str) -> Optional[DraggableButton]:
        """Get button reference by key."""
        return self._buttons.get(key)

    def set_order(self, order: List[str]) -> None:
        """Set the button order from a saved list of keys."""
        # Validate keys
        valid_order = [k for k in order if k in self._buttons]
        # Add any missing keys at the end
        for k in self._order:
            if k not in valid_order:
                valid_order.append(k)
        self._order = valid_order
        self._rebuild_layout()

    def get_order(self) -> List[str]:
        """Get current button order as list of keys."""
        return list(self._order)

    def reset_to_default(self) -> None:
        """Reset button order to original default."""
        self._order = list(self._default_order)
        self._rebuild_layout()
        if self._save_callback:
            self._save_callback(self._order)

    def contextMenuEvent(self, event):
        """Show context menu with reset option on right-click."""
        menu = QMenu(self)
        reset_action = QAction("🔄 Reset Button Order", self)
        reset_action.triggered.connect(self.reset_to_default)
        menu.addAction(reset_action)
        menu.exec(event.globalPos())

    def _rebuild_layout(self) -> None:
        """Rebuild layout in current order."""
        # Remove all widgets from layout
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        
        # Re-add in order
        for key in self._order:
            if key in self._buttons:
                self._layout.addWidget(self._buttons[key])

    def _get_drop_index(self, pos: QPoint) -> int:
        """Calculate insertion index based on mouse position."""
        for i, key in enumerate(self._order):
            btn = self._buttons[key]
            btn_center = btn.pos().x() + btn.width() // 2
            if pos.x() < btn_center:
                return i
        return len(self._order)

    def _clear_drop_highlights(self) -> None:
        """Clear all drop highlight indicators."""
        for btn in self._buttons.values():
            btn.set_drop_highlight(False)
        self._drop_indicator_idx = -1

    def _update_drop_highlight(self, insert_idx: int) -> None:
        """Update visual drop indicator."""
        if insert_idx == self._drop_indicator_idx:
            return
        
        self._clear_drop_highlights()
        self._drop_indicator_idx = insert_idx
        
        # Highlight the button at or after insertion point
        if 0 <= insert_idx < len(self._order):
            key = self._order[insert_idx]
            self._buttons[key].set_drop_highlight(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            key = event.mimeData().text()
            if key in self._buttons:
                event.acceptProposedAction()

    def dragMoveEvent(self, event):
        pos = event.position().toPoint()
        insert_idx = self._get_drop_index(pos)
        self._update_drop_highlight(insert_idx)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._clear_drop_highlights()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._clear_drop_highlights()
        
        dragged_key = event.mimeData().text()
        if dragged_key not in self._buttons:
            return

        drop_pos = event.position().toPoint()
        insert_idx = self._get_drop_index(drop_pos)

        # Remove from old position
        old_idx = self._order.index(dragged_key)
        self._order.pop(old_idx)
        
        # Adjust insert index if needed
        if old_idx < insert_idx:
            insert_idx -= 1
        
        # Insert at new position
        self._order.insert(insert_idx, dragged_key)
        
        self._rebuild_layout()
        event.acceptProposedAction()

        # Notify save callback
        if self._save_callback:
            self._save_callback(self._order)
