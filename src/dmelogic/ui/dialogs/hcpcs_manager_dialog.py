"""
HCPCS Description Manager Dialog

Allows user to view, add, edit, and delete HCPCS to description mappings.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QLabel, QLineEdit,
    QMessageBox, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from dmelogic.utils.hcpcs_mapper import get_hcpcs_mapper


class HCPCSManagerDialog(QDialog):
    """Dialog for managing HCPCS to description mappings"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mapper = get_hcpcs_mapper()
        self.setWindowTitle("Manage HCPCS Descriptions")
        self.setModal(True)
        self.resize(800, 600)
        self._init_ui()
        self._load_mappings()
    
    def _init_ui(self):
        """Initialize the UI"""
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("HCPCS Code Description Mappings")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)
        
        # Description
        desc = QLabel(
            "These descriptions will be used in fax forms instead of inventory item descriptions.\n"
            "You can have multiple descriptions per HCPCS code."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["HCPCS Code", "Description"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        
        # Add new mapping section
        add_layout = QHBoxLayout()
        add_layout.addWidget(QLabel("HCPCS Code:"))
        self.hcpcs_input = QLineEdit()
        self.hcpcs_input.setPlaceholderText("e.g., T4524")
        self.hcpcs_input.setMaxLength(10)
        add_layout.addWidget(self.hcpcs_input)
        
        add_layout.addWidget(QLabel("Description:"))
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("e.g., ADULT BRIEFS/ PULL-UPS - EXTRA-LARGE")
        add_layout.addWidget(self.desc_input, 1)
        
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_mapping)
        add_layout.addWidget(add_btn)
        
        layout.addLayout(add_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_mapping)
        button_layout.addWidget(delete_btn)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
    
    def _load_mappings(self):
        """Load mappings into the table"""
        self.table.setRowCount(0)
        mappings = self.mapper.get_all_mappings()
        
        # Sort by HCPCS code
        sorted_codes = sorted(mappings.keys())
        
        for hcpcs_code in sorted_codes:
            descriptions = mappings[hcpcs_code]
            for desc in descriptions:
                row = self.table.rowCount()
                self.table.insertRow(row)
                
                hcpcs_item = QTableWidgetItem(hcpcs_code)
                hcpcs_item.setFlags(hcpcs_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 0, hcpcs_item)
                
                desc_item = QTableWidgetItem(desc)
                desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 1, desc_item)
    
    def _add_mapping(self):
        """Add a new HCPCS to description mapping"""
        hcpcs_code = self.hcpcs_input.text().strip().upper()
        description = self.desc_input.text().strip()
        
        if not hcpcs_code:
            QMessageBox.warning(self, "Input Error", "Please enter a HCPCS code.")
            return
        
        if not description:
            QMessageBox.warning(self, "Input Error", "Please enter a description.")
            return
        
        # Check if this exact mapping already exists
        existing_descriptions = self.mapper.get_all_descriptions(hcpcs_code)
        if description in existing_descriptions:
            QMessageBox.information(
                self,
                "Already Exists",
                f"This mapping already exists for {hcpcs_code}."
            )
            return
        
        # Add the mapping
        if self.mapper.add_mapping(hcpcs_code, description):
            self._load_mappings()
            self.hcpcs_input.clear()
            self.desc_input.clear()
            self.hcpcs_input.setFocus()
            QMessageBox.information(
                self,
                "Success",
                f"Added mapping:\n{hcpcs_code} → {description}"
            )
        else:
            QMessageBox.critical(
                self,
                "Error",
                "Failed to add mapping. Please try again."
            )
    
    def _delete_mapping(self):
        """Delete the selected mapping"""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "No Selection", "Please select a mapping to delete.")
            return
        
        hcpcs_code = self.table.item(current_row, 0).text()
        description = self.table.item(current_row, 1).text()
        
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete this mapping?\n\n{hcpcs_code} → {description}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.mapper.remove_mapping(hcpcs_code, description):
                self._load_mappings()
                QMessageBox.information(self, "Success", "Mapping deleted.")
            else:
                QMessageBox.critical(self, "Error", "Failed to delete mapping.")
