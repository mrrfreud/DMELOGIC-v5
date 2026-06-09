"""
Inventory Search Dialog - for selecting items from inventory.db
"""

from __future__ import annotations

from typing import List, Dict, Any

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QLabel,
    QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor

# Use the centralized inventory access helpers
from dmelogic.db.inventory import fetch_all_inventory


class InventorySearchDialog(QDialog):
    """Dialog for searching and selecting inventory items."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search Inventory Items")
        self.setModal(False)  # Non-modal so it can be minimized
        
        # Enable window controls: minimize, maximize, resize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        self.resize(900, 600)

        self.all_items: List[Dict[str, Any]] = []
        self.selected_item: Dict[str, Any] | None = None

        self._setup_ui()
        self.load_all_items()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Search bar
        search_layout = QHBoxLayout()
        search_layout.setSpacing(6)

        search_layout.addWidget(QLabel("Search:"))

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Type to search by HCPCS, description, category, or brand..."
        )
        self.search_edit.textChanged.connect(self.on_search)
        search_layout.addWidget(self.search_edit, 1)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.search_edit.clear)
        search_layout.addWidget(clear_btn)

        layout.addLayout(search_layout)

        # Results tree
        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabels(
            ["HCPCS", "Description", "Category", "Brand", "Cost", "Bill"]
        )
        self.results_tree.setAlternatingRowColors(True)
        self.results_tree.setSortingEnabled(True)
        self.results_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.results_tree.setSelectionMode(
            QTreeWidget.SelectionMode.SingleSelection
        )
        self.results_tree.itemDoubleClicked.connect(self.on_select)

        header = self.results_tree.header()
        header.resizeSection(0, 120)  # HCPCS
        header.resizeSection(1, 350)  # Description
        header.resizeSection(2, 150)  # Category
        header.resizeSection(3, 120)  # Brand
        header.resizeSection(4, 90)   # Cost
        header.resizeSection(5, 90)   # Bill

        layout.addWidget(self.results_tree, 1)

        # Count label
        self.results_label = QLabel("0 items")
        self.results_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.results_label)

        # Buttons
        btn_layout = QHBoxLayout()
        
        # Add New Item button on the left
        add_new_btn = QPushButton("+ Add New Item")
        add_new_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        add_new_btn.clicked.connect(self.on_add_new_item)
        btn_layout.addWidget(add_new_btn)
        
        btn_layout.addStretch()

        self.select_btn = QPushButton("Select")
        self.select_btn.setDefault(True)
        self.select_btn.clicked.connect(self.on_select)
        btn_layout.addWidget(self.select_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------ data

    def load_all_items(self) -> None:
        """Load all inventory items from DB."""
        try:
            rows = fetch_all_inventory()  # sqlite3.Row list
            self.all_items = [dict(r) for r in rows]
            self.display_items(self.all_items)
        except Exception as e:
            self.all_items = []
            QMessageBox.warning(
                self,
                "Inventory Error",
                f"Failed to load inventory items:\n{e}",
            )

    def display_items(self, items: List[Dict[str, Any]]) -> None:
        """Populate the tree with the given list of dicts."""
        self.results_tree.clear()

        text_color = QBrush(QColor("#000000"))

        for data in items:
            # Defensive: support different capitalizations / column names
            hcpcs = (
                data.get("hcpcs_code")
                or data.get("HCPCS")
                or data.get("item_code")
                or ""
            )
            desc = data.get("description") or data.get("DESCRIPTION") or ""
            category = data.get("category") or data.get("CATEGORY") or ""
            brand = data.get("brand") or data.get("BRAND") or ""
            cost = data.get("cost") or data.get("COST") or ""
            # Bill amount is stored as retail_price in inventory table
            bill = (
                data.get("retail_price")
                or data.get("RETAIL_PRICE")
                or data.get("bill_amount")
                or data.get("BILL_AMOUNT")
                or ""
            )

            item = QTreeWidgetItem(
                [
                    str(hcpcs),
                    str(desc),
                    str(category),
                    str(brand),
                    f"{cost}",
                    f"{bill}",
                ]
            )

            # Stash full row data on the item
            item.setData(0, Qt.ItemDataRole.UserRole, data)

            for col in range(item.columnCount()):
                item.setForeground(col, text_color)

            self.results_tree.addTopLevelItem(item)

        count = len(items)
        self.results_label.setText(
            f"{count} item{'s' if count != 1 else ''}"
        )

    # ------------------------------------------------------------------ search

    def on_search(self, text: str) -> None:
        """Filter items by the search text."""
        term = text.strip().upper()
        if not term:
            self.display_items(self.all_items)
            return

        # Split term into parts for AND-style matching
        parts = [p for p in term.replace(",", " ").split() if p]

        filtered: List[Dict[str, Any]] = []
        for data in self.all_items:
            hcpcs = (
                str(
                    data.get("hcpcs_code")
                    or data.get("HCPCS")
                    or data.get("item_code")
                    or ""
                ).upper()
            )
            desc = str(data.get("description") or "").upper()
            category = str(data.get("category") or "").upper()
            brand = str(data.get("brand") or "").upper()

            haystack = " ".join([hcpcs, desc, category, brand])

            if all(p in haystack for p in parts):
                filtered.append(data)

        self.display_items(filtered)

    # ------------------------------------------------------------------ selection

    def on_select(self) -> None:
        """Handle Select button / double-click."""
        selected = self.results_tree.selectedItems()
        if not selected:
            QMessageBox.warning(
                self,
                "No Selection",
                "Please select an item from the list.",
            )
            return

        item = selected[0]
        self.selected_item = item.data(0, Qt.ItemDataRole.UserRole)
        self.accept()

    def get_selected_item(self) -> Dict[str, Any] | None:
        """Return dict with the selected inventory row (or None)."""
        return self.selected_item

    def set_initial_query(self, text: str) -> None:
        """Seed search box and trigger search (optional)."""
        if text:
            self.search_edit.setText(text)
    
    def on_add_new_item(self) -> None:
        """Open a dialog to add a new inventory item, then refresh the list."""
        try:
            # Import the dialog class
            from dmelogic.legacy import InventoryItemDialog
            
            # Create and show the dialog
            dialog = InventoryItemDialog(self)
            dialog.setWindowTitle("Add New Inventory Item")
            
            # Connect to refresh when accepted
            def on_item_added():
                item_data = dialog.get_item_data()
                if not item_data:
                    return
                
                try:
                    # Get the database file path from parent
                    if hasattr(self.parent(), 'inventory_database_file'):
                        db_file = self.parent().inventory_database_file
                    else:
                        import os
                        db_file = os.path.join(
                            os.path.expandvars(r"%PROGRAMDATA%"),
                            "DMELogic",
                            "Data",
                            "inventory.db"
                        )
                    
                    # Save to database
                    import sqlite3
                    conn = sqlite3.connect(db_file)
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        INSERT INTO inventory (
                            hcpcs_code, description, category, cost, retail_price,
                            brand, stock_quantity, reorder_level, supplier, notes,
                            created_at, item_number
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
                    """, (
                        item_data.get('hcpcs_code', ''),
                        item_data.get('description', ''),
                        item_data.get('category', ''),
                        item_data.get('cost', 0),
                        item_data.get('bill_amount', 0),  # This goes into retail_price column
                        item_data.get('brand', ''),
                        item_data.get('stock_quantity', 0),
                        item_data.get('reorder_level', 0),
                        item_data.get('source', ''),
                        item_data.get('notes', ''),
                        item_data.get('item_number', '')
                    ))
                    
                    conn.commit()
                    conn.close()
                    
                    # Reload all items to include the new one
                    self.load_all_items()
                    
                    # Set search to the new item's HCPCS so it's easy to find
                    hcpcs = item_data.get('hcpcs_code', '')
                    if hcpcs:
                        self.search_edit.setText(hcpcs)
                    
                    # Show success message
                    QMessageBox.information(
                        self,
                        "Item Added",
                        f"Successfully added: {hcpcs} - {item_data.get('description', '')}"
                    )
                    
                except Exception as e:
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"Failed to add inventory item: {e}"
                    )
            
            dialog.accepted.connect(on_item_added)
            
            # Show modally so user completes the add before continuing
            dialog.exec()
            
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"Could not open add inventory dialog: {e}"
            )
