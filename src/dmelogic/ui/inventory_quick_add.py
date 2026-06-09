"""
Inline Inventory Quick-Add Widget
==================================
Embeddable widget that sits above the inventory table, allowing
rapid item entry without opening a dialog. Fields auto-complete
from existing data.

Feature #1: Inline Inventory Quick-Add

Integration:
    In setup_inventory_tab() in main_window.py, add between the toolbar and table:

        from dmelogic.ui.inventory_quick_add import InlineQuickAdd
        self.quick_add = InlineQuickAdd(
            folder_path=getattr(self, 'folder_path', None),
            parent=self
        )
        self.quick_add.item_added.connect(self.load_inventory)  # your reload method
        layout.addWidget(self.quick_add)
"""

from __future__ import annotations

import sqlite3
from typing import Optional
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QComboBox,
    QPushButton, QLabel, QFrame, QMessageBox, QCompleter,
)
from PyQt6.QtCore import Qt, pyqtSignal, QStringListModel
from PyQt6.QtGui import QFont, QDoubleValidator, QIntValidator

from dmelogic.db.base import get_connection


class InlineQuickAdd(QFrame):
    """
    Collapsible inline row for rapid inventory item entry.

    Emits:
        item_added: Signal emitted after a successful insert (no args).

    The widget starts collapsed. Click "➕ Quick Add" to expand a
    single-row form with HCPCS, description, category, cost, price,
    stock, and reorder level fields. Fields auto-complete from
    existing inventory data.
    """

    item_added = pyqtSignal()

    def __init__(self, folder_path: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.main_window = parent
        self._expanded = False

        self.setObjectName("QuickAddFrame")
        self._setup_ui()
        self._load_completions()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Toggle button row
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)

        self.toggle_btn = QPushButton("➕ Quick Add Item")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #10B981;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 600;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #059669; }
        """)
        self.toggle_btn.clicked.connect(self._toggle)
        toggle_row.addWidget(self.toggle_btn)
        toggle_row.addStretch()

        self.main_layout.addLayout(toggle_row)

        # Expandable form
        self.form_frame = QFrame()
        self.form_frame.setObjectName("QuickAddForm")
        self.form_frame.setStyleSheet("""
            QFrame#QuickAddForm {
                background-color: #F0FDF4;
                border: 1px solid #BBF7D0;
                border-radius: 8px;
                padding: 10px;
                margin-top: 4px;
            }
        """)
        self.form_frame.setVisible(False)

        form_layout = QVBoxLayout(self.form_frame)
        form_layout.setContentsMargins(12, 10, 12, 10)
        form_layout.setSpacing(8)

        # Row 1: HCPCS, Description, Category
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self.hcpcs_edit = self._make_field("HCPCS Code", 120)
        self.desc_edit = self._make_field("Description", 260)
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.setMinimumWidth(140)
        self.category_combo.setPlaceholderText("Category")
        self.category_combo.setStyleSheet(self._input_style())

        row1.addWidget(self._labeled("HCPCS", self.hcpcs_edit))
        row1.addWidget(self._labeled("Description", self.desc_edit), 1)
        row1.addWidget(self._labeled("Category", self.category_combo))

        form_layout.addLayout(row1)

        # Row 2: Cost, Bill Amt, Stock, Reorder, Brand, Item #, Actions
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self.cost_edit = self._make_field("0.00", 80)
        self.cost_edit.setValidator(QDoubleValidator(0, 999999, 2))
        self.bill_edit = self._make_field("0.00", 80)
        self.bill_edit.setValidator(QDoubleValidator(0, 999999, 2))
        self.stock_edit = self._make_field("0", 60)
        self.stock_edit.setValidator(QIntValidator(0, 999999))
        self.reorder_edit = self._make_field("0", 60)
        self.reorder_edit.setValidator(QIntValidator(0, 999999))
        self.brand_edit = self._make_field("Brand", 120)
        self.item_number_edit = self._make_field("Item #", 100)

        row2.addWidget(self._labeled("Cost", self.cost_edit))
        row2.addWidget(self._labeled("Bill Amt", self.bill_edit))
        row2.addWidget(self._labeled("Stock", self.stock_edit))
        row2.addWidget(self._labeled("Reorder Lvl", self.reorder_edit))
        row2.addWidget(self._labeled("Brand", self.brand_edit))
        row2.addWidget(self._labeled("Item #", self.item_number_edit))
        row2.addStretch()

        # Save / Cancel
        save_btn = QPushButton("✅ Save")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                border-radius: 6px;
                padding: 6px 18px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #047857; }
        """)
        save_btn.clicked.connect(self._save_item)

        cancel_btn = QPushButton("✖ Cancel")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #E5E7EB;
                color: #374151;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #D1D5DB; }
        """)
        cancel_btn.clicked.connect(self._collapse)

        row2.addWidget(save_btn)
        row2.addWidget(cancel_btn)

        form_layout.addLayout(row2)
        self.main_layout.addWidget(self.form_frame)

    def _make_field(self, placeholder: str, width: int) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(width)
        edit.setStyleSheet(self._input_style())
        return edit

    def _labeled(self, label_text: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet("color: #6B7280;")
        layout.addWidget(lbl)
        layout.addWidget(widget)
        return container

    @staticmethod
    def _input_style() -> str:
        return """
            QLineEdit, QComboBox {
                background: #FFFFFF;
                border: 1px solid #D1D5DB;
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 11px;
                color: #111827;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #10B981;
            }
        """

    # ------------------------------------------------------------------ Toggle

    def _toggle(self):
        self._expanded = not self._expanded
        self.form_frame.setVisible(self._expanded)
        self.toggle_btn.setText("➖ Close Quick Add" if self._expanded else "➕ Quick Add Item")
        if self._expanded:
            self.hcpcs_edit.setFocus()

    def _collapse(self):
        self._expanded = False
        self.form_frame.setVisible(False)
        self.toggle_btn.setText("➕ Quick Add Item")
        self._clear_fields()

    def _clear_fields(self):
        for field in [self.hcpcs_edit, self.desc_edit, self.cost_edit,
                      self.bill_edit, self.stock_edit, self.reorder_edit,
                      self.brand_edit, self.item_number_edit]:
            field.clear()
        self.category_combo.setCurrentText("")

    # ------------------------------------------------------------------ Autocomplete

    def _load_completions(self):
        """Load distinct values from inventory for auto-complete."""
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Categories
            cur.execute("SELECT DISTINCT category FROM inventory WHERE category IS NOT NULL ORDER BY category")
            categories = [row["category"] for row in cur.fetchall()]
            self.category_combo.addItems(categories)

            # Brands
            cur.execute("SELECT DISTINCT brand FROM inventory WHERE brand IS NOT NULL AND brand != '' ORDER BY brand")
            brands = [row["brand"] for row in cur.fetchall()]
            self._set_completer(self.brand_edit, brands)

            # HCPCS codes
            cur.execute("SELECT DISTINCT hcpcs_code FROM inventory WHERE hcpcs_code IS NOT NULL ORDER BY hcpcs_code")
            hcpcs_list = [row["hcpcs_code"] for row in cur.fetchall()]
            self._set_completer(self.hcpcs_edit, hcpcs_list)

            # Descriptions
            cur.execute("SELECT DISTINCT description FROM inventory WHERE description IS NOT NULL ORDER BY description LIMIT 200")
            descs = [row["description"] for row in cur.fetchall()]
            self._set_completer(self.desc_edit, descs)

            conn.close()
        except Exception as e:
            print(f"Quick-add completion load error: {e}")

    def _set_completer(self, edit: QLineEdit, items: list):
        model = QStringListModel(items)
        completer = QCompleter()
        completer.setModel(model)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(8)
        edit.setCompleter(completer)

    # ------------------------------------------------------------------ Save

    def _save_item(self):
        hcpcs = self.hcpcs_edit.text().strip()
        desc = self.desc_edit.text().strip()

        if not hcpcs and not desc:
            QMessageBox.warning(self, "Quick Add", "Please enter at least an HCPCS code or description.")
            return

        category = self.category_combo.currentText().strip()
        brand = self.brand_edit.text().strip()
        item_number = self.item_number_edit.text().strip()

        try:
            cost = float(self.cost_edit.text() or 0)
        except ValueError:
            cost = 0.0
        try:
            bill = float(self.bill_edit.text() or 0)
        except ValueError:
            bill = 0.0
        try:
            stock = int(self.stock_edit.text() or 0)
        except ValueError:
            stock = 0
        try:
            reorder = int(self.reorder_edit.text() or 0)
        except ValueError:
            reorder = 0

        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO inventory (
                    hcpcs_code, description, category, cost, retail_price,
                    brand, stock_quantity, reorder_level, item_number,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (hcpcs, desc, category, cost, bill, brand, stock, reorder, item_number))
            conn.commit()
            conn.close()

            self._clear_fields()
            self.hcpcs_edit.setFocus()

            # Refresh completions with new data
            self._load_completions()

            # Emit signal so parent can reload the table
            self.item_added.emit()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add item:\n{e}")
