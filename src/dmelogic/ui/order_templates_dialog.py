"""
Order Templates Dialog
======================
UI for managing and applying order templates (pre-configured bundles).

Feature #2: Order Templates (UI)

Integration:
    Add a "📋 Templates" button to the Orders tab toolbar:
        from dmelogic.ui.order_templates_dialog import OrderTemplatesDialog
        
        btn_templates = QPushButton("📋 Templates")
        btn_templates.clicked.connect(lambda: OrderTemplatesDialog(
            folder_path=getattr(self, 'folder_path', None),
            parent=self
        ).exec())
    
    To apply a template to the Order Wizard, connect:
        dialog.template_applied.connect(self.open_new_order_wizard_with_template)
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QLineEdit, QTextEdit, QComboBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QWidget,
    QMessageBox, QFrame, QSplitter,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from dmelogic.db.order_templates import (
    init_templates_db, get_all_templates, get_template_with_items,
    save_template, delete_template,
    OrderTemplate, TemplateItem,
)


class OrderTemplatesDialog(QDialog):
    """
    Manage order templates: create, edit, delete, and apply.

    Signals:
        template_applied(OrderTemplate): Emitted when user clicks "Apply"
            with a fully loaded template including items.
    """

    template_applied = pyqtSignal(object)  # OrderTemplate

    def __init__(self, folder_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.folder_path = folder_path

        self.setWindowTitle("📋 Order Templates")
        self.setMinimumSize(800, 500)
        self.resize(900, 560)

        init_templates_db(folder_path)
        self._setup_ui()
        self._load_templates()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Left: template list
        left_panel = QVBoxLayout()
        left_panel.setSpacing(8)

        lbl = QLabel("Templates")
        lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        left_panel.addWidget(lbl)

        self.template_list = QListWidget()
        self.template_list.setMinimumWidth(220)
        self.template_list.currentRowChanged.connect(self._on_template_selected)
        self.template_list.setStyleSheet("""
            QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #E5E7EB; }
            QListWidget::item:selected { background-color: #DBEAFE; color: #1D4ED8; }
        """)
        left_panel.addWidget(self.template_list, 1)

        # Template list buttons
        list_btns = QHBoxLayout()
        self.btn_new = QPushButton("➕ New")
        self.btn_new.setStyleSheet("background: #10B981; color: white; border-radius: 4px; padding: 5px 12px; font-weight: 600;")
        self.btn_new.clicked.connect(self._new_template)
        list_btns.addWidget(self.btn_new)

        self.btn_delete = QPushButton("🗑️ Delete")
        self.btn_delete.setStyleSheet("background: #EF4444; color: white; border-radius: 4px; padding: 5px 12px; font-weight: 600;")
        self.btn_delete.clicked.connect(self._delete_template)
        list_btns.addWidget(self.btn_delete)
        list_btns.addStretch()

        left_panel.addLayout(list_btns)
        layout.addLayout(left_panel)

        # Right: template editor
        right_panel = QVBoxLayout()
        right_panel.setSpacing(10)

        # Template info
        info_frame = QFrame()
        info_frame.setStyleSheet("QFrame { background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 8px; padding: 10px; }")
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(8)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Template name (e.g. 'Wheelchair Setup')")
        row1.addWidget(self.name_edit, 1)
        row1.addWidget(QLabel("Billing:"))
        self.billing_combo = QComboBox()
        self.billing_combo.addItems(["Insurance", "Cash", "Rental", "Medicare", "Medicaid"])
        row1.addWidget(self.billing_combo)
        info_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Description:"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Optional description")
        row2.addWidget(self.desc_edit, 1)
        info_layout.addLayout(row2)

        right_panel.addWidget(info_frame)

        # Items table
        items_label = QLabel("Template Items")
        items_label.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        right_panel.addWidget(items_label)

        self.items_table = QTableWidget()
        self.items_table.setColumnCount(5)
        self.items_table.setHorizontalHeaderLabels(["HCPCS", "Description", "Qty", "Refills", "Days Supply"])
        self.items_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.items_table.horizontalHeader().resizeSection(0, 120)
        self.items_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.items_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.items_table.horizontalHeader().resizeSection(2, 60)
        self.items_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.items_table.horizontalHeader().resizeSection(3, 60)
        self.items_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.items_table.horizontalHeader().resizeSection(4, 90)
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setAlternatingRowColors(True)
        right_panel.addWidget(self.items_table, 1)

        # Item action buttons
        item_btns = QHBoxLayout()
        btn_add_row = QPushButton("+ Add Item")
        btn_add_row.setStyleSheet("background: #3B82F6; color: white; border-radius: 4px; padding: 4px 12px;")
        btn_add_row.clicked.connect(self._add_item_row)
        item_btns.addWidget(btn_add_row)

        btn_remove_row = QPushButton("- Remove Item")
        btn_remove_row.setStyleSheet("background: #6B7280; color: white; border-radius: 4px; padding: 4px 12px;")
        btn_remove_row.clicked.connect(self._remove_item_row)
        item_btns.addWidget(btn_remove_row)
        item_btns.addStretch()

        btn_save = QPushButton("💾 Save Template")
        btn_save.setStyleSheet("background: #059669; color: white; border-radius: 4px; padding: 6px 18px; font-weight: 600;")
        btn_save.clicked.connect(self._save_current)
        item_btns.addWidget(btn_save)

        btn_apply = QPushButton("🚀 Apply to New Order")
        btn_apply.setStyleSheet("background: #4F46E5; color: white; border-radius: 4px; padding: 6px 18px; font-weight: 600;")
        btn_apply.clicked.connect(self._apply_template)
        item_btns.addWidget(btn_apply)

        right_panel.addLayout(item_btns)
        layout.addLayout(right_panel, 1)

    # ------------------------------------------------------------------ Load

    def _load_templates(self):
        self.template_list.clear()
        templates = get_all_templates(self.folder_path)
        for t in templates:
            item = QListWidgetItem(f"{t.name}\n{t.description}" if t.description else t.name)
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            self.template_list.addItem(item)

    def _on_template_selected(self, row: int):
        if row < 0:
            return
        item = self.template_list.item(row)
        if not item:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        template = get_template_with_items(tid, self.folder_path)
        if not template:
            return
        self._populate_editor(template)

    def _populate_editor(self, template: OrderTemplate):
        self.name_edit.setText(template.name)
        self.desc_edit.setText(template.description)
        idx = self.billing_combo.findText(template.billing_type)
        if idx >= 0:
            self.billing_combo.setCurrentIndex(idx)

        self.items_table.setRowCount(0)
        for ti in template.items:
            self._add_item_row(ti)

        # Store current template ID
        self._current_template_id = template.id

    # ------------------------------------------------------------------ Edit

    def _new_template(self):
        self._current_template_id = 0
        self.name_edit.clear()
        self.desc_edit.clear()
        self.billing_combo.setCurrentIndex(0)
        self.items_table.setRowCount(0)
        self._add_item_row()
        self.name_edit.setFocus()

    def _add_item_row(self, item: Optional[TemplateItem] = None):
        row = self.items_table.rowCount()
        self.items_table.insertRow(row)
        self.items_table.setItem(row, 0, QTableWidgetItem(item.hcpcs if item else ""))
        self.items_table.setItem(row, 1, QTableWidgetItem(item.description if item else ""))
        self.items_table.setItem(row, 2, QTableWidgetItem(str(item.quantity) if item else "1"))
        self.items_table.setItem(row, 3, QTableWidgetItem(str(item.refills) if item else "0"))
        self.items_table.setItem(row, 4, QTableWidgetItem(str(item.days_supply) if item else "0"))

    def _remove_item_row(self):
        row = self.items_table.currentRow()
        if row >= 0:
            self.items_table.removeRow(row)

    def _get_items_from_table(self) -> list:
        items = []
        for row in range(self.items_table.rowCount()):
            hcpcs = (self.items_table.item(row, 0) or QTableWidgetItem()).text().strip()
            desc = (self.items_table.item(row, 1) or QTableWidgetItem()).text().strip()
            if not hcpcs and not desc:
                continue
            try:
                qty = int((self.items_table.item(row, 2) or QTableWidgetItem("1")).text() or 1)
            except ValueError:
                qty = 1
            try:
                refills = int((self.items_table.item(row, 3) or QTableWidgetItem("0")).text() or 0)
            except ValueError:
                refills = 0
            try:
                days = int((self.items_table.item(row, 4) or QTableWidgetItem("0")).text() or 0)
            except ValueError:
                days = 0

            items.append(TemplateItem(hcpcs=hcpcs, description=desc, quantity=qty, refills=refills, days_supply=days))
        return items

    # ------------------------------------------------------------------ Save / Delete

    def _save_current(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Templates", "Please enter a template name.")
            return

        items = self._get_items_from_table()
        if not items:
            QMessageBox.warning(self, "Templates", "Please add at least one item.")
            return

        template = OrderTemplate(
            id=getattr(self, "_current_template_id", 0),
            name=name,
            description=self.desc_edit.text().strip(),
            billing_type=self.billing_combo.currentText(),
            items=items,
        )
        save_template(template, self.folder_path)
        self._load_templates()
        QMessageBox.information(self, "Templates", f"Template '{name}' saved.")

    def _delete_template(self):
        item = self.template_list.currentItem()
        if not item:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        name = self.name_edit.text() or "this template"
        if QMessageBox.question(
            self, "Delete Template",
            f"Delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            delete_template(tid, self.folder_path)
            self._load_templates()
            self.name_edit.clear()
            self.desc_edit.clear()
            self.items_table.setRowCount(0)

    # ------------------------------------------------------------------ Apply

    def _apply_template(self):
        """Load the full template and emit signal for the Order Wizard."""
        tid = getattr(self, "_current_template_id", 0)
        if not tid:
            QMessageBox.warning(self, "Templates", "Please select a template first.")
            return
        template = get_template_with_items(tid, self.folder_path)
        if template:
            self.template_applied.emit(template)
            self.accept()
