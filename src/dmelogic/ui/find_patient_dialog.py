"""
Find Patient Dialog - Advanced patient search with multiple criteria
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QApplication
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
import sqlite3
from datetime import datetime
import re
from typing import Optional


class FindPatientDialog(QDialog):
    """
    Dialog for searching patients by multiple criteria:
    - Name (first/last)
    - Date of Birth
    - Address
    - Telephone Number
    """
    
    def __init__(self, parent=None, folder_path: Optional[str] = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.selected_patient_id = None
        self.selected_patient_data = None
        
        self.setWindowTitle("🔍 Find Patient")
        self.setMinimumSize(900, 600)
        self.setModal(True)
        
        self._setup_ui()
        self._apply_styles()
        
    def _setup_ui(self):
        """Build the dialog UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # Search criteria group
        criteria_group = QGroupBox("Search Criteria")
        criteria_layout = QVBoxLayout(criteria_group)
        criteria_layout.setSpacing(10)
        
        # Name search
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("First or Last name...")
        self.name_input.returnPressed.connect(self._perform_search)
        name_row.addWidget(self.name_input, 1)
        criteria_layout.addLayout(name_row)
        
        # Date of Birth search
        dob_row = QHBoxLayout()
        dob_row.addWidget(QLabel("Date of Birth:"))
        self.dob_input = QLineEdit()
        self.dob_input.setPlaceholderText("YYYY-MM-DD or MM/DD/YYYY")
        self.dob_input.returnPressed.connect(self._perform_search)
        dob_row.addWidget(self.dob_input, 1)
        criteria_layout.addLayout(dob_row)
        
        # Address search
        address_row = QHBoxLayout()
        address_row.addWidget(QLabel("Address:"))
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Street, city, or zip code...")
        self.address_input.returnPressed.connect(self._perform_search)
        address_row.addWidget(self.address_input, 1)
        criteria_layout.addLayout(address_row)
        
        # Phone search
        phone_row = QHBoxLayout()
        phone_row.addWidget(QLabel("Phone:"))
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Phone number (any format)...")
        self.phone_input.returnPressed.connect(self._perform_search)
        phone_row.addWidget(self.phone_input, 1)
        criteria_layout.addLayout(phone_row)
        
        # Search buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        
        self.search_btn = QPushButton("🔍 Search")
        self.search_btn.clicked.connect(self._perform_search)
        btn_row.addWidget(self.search_btn)
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self._clear_search)
        btn_row.addWidget(self.clear_btn)
        
        criteria_layout.addLayout(btn_row)
        layout.addWidget(criteria_group)
        
        # Results label
        self.results_label = QLabel("Enter search criteria and click Search")
        self.results_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(self.results_label)
        
        # Results table
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(8)
        self.results_table.setHorizontalHeaderLabels([
            "ID", "Last Name", "First Name", "DOB", "Phone", 
            "Address", "City", "State"
        ])
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setSortingEnabled(True)
        self.results_table.doubleClicked.connect(self._on_row_double_clicked)
        
        # Auto-resize columns
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # ID
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Last Name
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # First Name
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # DOB
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Phone
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)  # Address
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)  # City
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)  # State
        
        layout.addWidget(self.results_table, 1)
        
        # Bottom buttons
        bottom_btns = QHBoxLayout()
        bottom_btns.addStretch()
        
        self.select_btn = QPushButton("Select Patient")
        self.select_btn.setEnabled(False)
        self.select_btn.clicked.connect(self._on_select)
        bottom_btns.addWidget(self.select_btn)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        bottom_btns.addWidget(self.cancel_btn)
        
        layout.addLayout(bottom_btns)
        
        # Connect table selection change
        self.results_table.itemSelectionChanged.connect(self._on_selection_changed)
        
        # Focus on name input
        self.name_input.setFocus()
        
    def _apply_styles(self):
        """Apply modern dark theme styles"""
        self.setStyleSheet("""
            QDialog {
                background-color: #2B2B2B;
                color: #FFFFFF;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QLabel {
                color: #FFFFFF;
                min-width: 100px;
            }
            QLineEdit {
                background-color: #3C3C3C;
                color: #FFFFFF;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px;
            }
            QLineEdit:focus {
                border: 1px solid #0078D4;
            }
            QPushButton {
                background-color: #0078D4;
                color: white;
                padding: 8px 20px;
                border-radius: 4px;
                border: none;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1084D8;
            }
            QPushButton:pressed {
                background-color: #006CC1;
            }
            QPushButton:disabled {
                background-color: #3C3C3C;
                color: #888;
            }
            QPushButton#clear_btn {
                background-color: #3C3C3C;
            }
            QPushButton#clear_btn:hover {
                background-color: #505050;
            }
            QTableWidget {
                background-color: #2B2B2B;
                color: #FFFFFF;
                gridline-color: #444;
                border: 1px solid #555;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QTableWidget::item:selected {
                background-color: #0078D4;
            }
            QHeaderView::section {
                background-color: #3C3C3C;
                color: #FFFFFF;
                padding: 5px;
                border: 1px solid #555;
                font-weight: bold;
            }
        """)
        
        self.clear_btn.setObjectName("clear_btn")
    
    def set_initial_query(
        self,
        name: str = "",
        dob: str = "",
        address: str = "",
        phone: str = "",
    ) -> None:
        """Seed the dialog inputs before showing it."""
        if name:
            self.name_input.setText(name)
        if dob:
            self.dob_input.setText(dob)
        if address:
            self.address_input.setText(address)
        if phone:
            self.phone_input.setText(phone)
        
    def _clear_search(self):
        """Clear all search fields"""
        self.name_input.clear()
        self.dob_input.clear()
        self.address_input.clear()
        self.phone_input.clear()
        self.results_table.setRowCount(0)
        self.results_label.setText("Enter search criteria and click Search")
        self.results_label.setStyleSheet("color: #888; font-style: italic;")
        self.selected_patient_id = None
        self.selected_patient_data = None
        self.select_btn.setEnabled(False)
        self.name_input.setFocus()
        
    def _perform_search(self):
        """Execute patient search with provided criteria"""
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        
        try:
            name = self.name_input.text().strip()
            dob_raw = self.dob_input.text().strip()
            address = self.address_input.text().strip()
            phone_raw = self.phone_input.text().strip()

            dob_context = self._build_dob_context(dob_raw)
            dob_variants = dob_context["variants"]
            dob_digits = dob_context["digits"]

            phone_digits = re.sub(r"\D", "", phone_raw) if phone_raw else ""
            
            # At least one criterion must be provided
            if not any([name, dob_variants, dob_digits, address, phone_digits]):
                QMessageBox.warning(
                    self, 
                    "No Search Criteria",
                    "Please enter at least one search criterion."
                )
                return
            
            # Execute search
            results = self._search_database(name, dob_variants, dob_digits, address, phone_digits)
            
            # Display results
            self._populate_results(results)
            
        finally:
            QApplication.restoreOverrideCursor()
    
    def _build_dob_context(self, dob_str: str) -> dict:
        """Create multiple DOB representations for flexible searching."""
        dob_str = dob_str.strip()
        if not dob_str:
            return {"variants": [], "digits": ""}

        variants = []
        digits = re.sub(r"\D", "", dob_str)

        def _add_formats(dt: datetime):
            variants.extend([
                dt.strftime("%Y-%m-%d"),
                dt.strftime("%m/%d/%Y"),
                dt.strftime("%m-%d-%Y"),
            ])

        # Try to parse a few common formats
        parse_formats = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%m.%d.%Y"]
        for fmt in parse_formats:
            try:
                _add_formats(datetime.strptime(dob_str, fmt))
                break
            except ValueError:
                continue
        else:
            # If parsing with separators failed, try digits-only fallbacks
            if len(digits) == 8:
                for fmt in ("%Y%m%d", "%m%d%Y"):
                    try:
                        _add_formats(datetime.strptime(digits, fmt))
                        break
                    except ValueError:
                        continue

        # Deduplicate while keeping order
        seen = set()
        variants = [v for v in variants if not (v in seen or seen.add(v))]

        # Always include the raw input last so we can still attempt LIKE searches
        if dob_str not in seen:
            variants.append(dob_str)

        return {"variants": variants, "digits": digits}
    
    def _search_database(self, name: str, dob_variants: list[str], dob_digits: str, address: str, phone_digits: str):
        """Search patients database with multiple criteria.

        Special handling: if the Name field looks like "LAST, FIRST" we
        treat the parts as separate, prefix-matched fields. For example,
        typing "SMI, JO" will search for last_name LIKE 'SMI%' AND
        first_name LIKE 'JO%'.
        """
        try:
            from dmelogic.db.base import get_connection
            
            conn = get_connection("patients.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            
            try:
                cur = conn.cursor()
                
                # Build dynamic query
                conditions = []
                params = []
                
                if name:
                    raw = name.lower()
                    if "," in raw:
                        # Support partial "LAST, FIRST" input, e.g. "smi, jo"
                        last_part, first_part = [p.strip() for p in raw.split(",", 1)]
                        if last_part:
                            conditions.append("LOWER(COALESCE(last_name,'')) LIKE ?")
                            params.append(f"{last_part}%")
                        if first_part:
                            conditions.append("LOWER(COALESCE(first_name,'')) LIKE ?")
                            params.append(f"{first_part}%")
                    else:
                        like_name = f"%{raw}%"
                        conditions.append(
                            "(LOWER(COALESCE(first_name,'')) LIKE ? OR "
                            "LOWER(COALESCE(last_name,'')) LIKE ? OR "
                            "LOWER(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')) LIKE ? OR "
                            "LOWER(COALESCE(last_name,'') || ', ' || COALESCE(first_name,'')) LIKE ?)"
                        )
                        params.extend([like_name, like_name, like_name, like_name])
                
                if dob_variants or dob_digits:
                    dob_clauses = []
                    for variant in dob_variants:
                        if not variant:
                            continue
                        dob_clauses.append("LOWER(COALESCE(dob,'')) LIKE ?")
                        params.append(f"%{variant.lower()}%")
                    if dob_digits:
                        dob_clauses.append(
                            "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(dob,''), '-', ''), '/', ''), '.', ''), ' ', ''), '\\', '') LIKE ?"
                        )
                        params.append(f"%{dob_digits}%")
                    if dob_clauses:
                        conditions.append(f"({' OR '.join(dob_clauses)})")
                
                if address:
                    like_addr = f"%{address.lower()}%"
                    conditions.append(
                        "(LOWER(COALESCE(address,'')) LIKE ? OR "
                        "LOWER(COALESCE(city,'')) LIKE ? OR "
                        "LOWER(COALESCE(state,'')) LIKE ? OR "
                        "LOWER(COALESCE(zip,'')) LIKE ?)"
                    )
                    params.extend([like_addr, like_addr, like_addr, like_addr])
                
                if phone_digits:
                    # Strip punctuation from stored phone before comparison (supports spaces, dots, plus, etc.)
                    phone_expr = (
                        "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone,''), '-', ''), '(', ''), ')', ''), ' ', ''), '.', ''), '+', '')"
                    )
                    conditions.append(f"{phone_expr} LIKE ?")
                    params.append(f"%{phone_digits}%")
                
                where_clause = " AND ".join(conditions) if conditions else "1=1"
                
                query = f"""
                    SELECT id, first_name, last_name, dob, phone, 
                           address, city, state, zip,
                           primary_insurance, policy_number
                    FROM patients
                    WHERE {where_clause}
                    ORDER BY last_name, first_name
                    LIMIT 100
                """
                
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
                
            finally:
                conn.close()
                
        except Exception as e:
            QMessageBox.critical(
                self,
                "Search Error",
                f"An error occurred during search:\n{str(e)}"
            )
            return []
    
    def _populate_results(self, results):
        """Populate the results table"""
        self.results_table.setSortingEnabled(False)
        self.results_table.setRowCount(0)
        
        if not results:
            self.results_label.setText("No patients found matching the search criteria.")
            self.results_label.setStyleSheet("color: #FF9800; font-weight: bold;")
            return
        
        self.results_label.setText(f"Found {len(results)} patient(s)")
        self.results_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        for row_idx, patient in enumerate(results):
            self.results_table.insertRow(row_idx)
            
            # Store full patient data in first column
            id_item = QTableWidgetItem(str(patient.get('id', '')))
            id_item.setData(Qt.ItemDataRole.UserRole, patient)  # Store full patient dict
            self.results_table.setItem(row_idx, 0, id_item)
            
            self.results_table.setItem(row_idx, 1, QTableWidgetItem(patient.get('last_name', '')))
            self.results_table.setItem(row_idx, 2, QTableWidgetItem(patient.get('first_name', '')))
            self.results_table.setItem(row_idx, 3, QTableWidgetItem(patient.get('dob', '')))
            self.results_table.setItem(row_idx, 4, QTableWidgetItem(patient.get('phone', '')))
            self.results_table.setItem(row_idx, 5, QTableWidgetItem(patient.get('address', '')))
            self.results_table.setItem(row_idx, 6, QTableWidgetItem(patient.get('city', '')))
            self.results_table.setItem(row_idx, 7, QTableWidgetItem(patient.get('state', '')))
        
        self.results_table.setSortingEnabled(True)
        
    def _on_selection_changed(self):
        """Handle table selection change"""
        selected_rows = self.results_table.selectedItems()
        self.select_btn.setEnabled(len(selected_rows) > 0)
        
        if selected_rows:
            row = self.results_table.currentRow()
            id_item = self.results_table.item(row, 0)
            if id_item:
                self.selected_patient_data = id_item.data(Qt.ItemDataRole.UserRole)
                self.selected_patient_id = self.selected_patient_data.get('id')
    
    def _on_row_double_clicked(self):
        """Handle double-click on a row - select and close"""
        self._on_select()
    
    def _on_select(self):
        """Handle select button click"""
        if self.selected_patient_id:
            self.accept()
    
    def get_selected_patient(self):
        """Return the selected patient data"""
        return self.selected_patient_data


# Import QApplication for cursor handling
from PyQt6.QtWidgets import QApplication
