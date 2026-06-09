"""
Refill Due Screen - DMELogic

Shows items that are due for refill and allows processing them into new orders.

This screen demonstrates the standard page layout pattern:
1. PageHeader (title + subtitle)
2. FilterRow (search bar + date range filters)
3. QTableWidget (data grid with color-coded status)
4. SummaryFooter (summary text + action buttons)

All styling is handled by dark.qss theme and components.py widgets.
"""
from __future__ import annotations

from typing import Optional, List
from datetime import date, timedelta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QMessageBox, QLabel, QDateEdit, QHBoxLayout
)
from PyQt6.QtCore import Qt, QDate

from dmelogic.ui.components import (
    PageHeader, SearchBar, SummaryFooter, FilterRow, create_standard_page_layout
)
from dmelogic.ui.styling import (
    apply_standard_table_style, create_refill_status_item,
    calculate_days_until_due, color_code_refill_row, create_centered_item
)
from dmelogic.db.refills import fetch_refills_due, RefillRow
from dmelogic.services.refill_service import process_refills
from dmelogic.config import debug_log


class RefillDueScreen(QWidget):
    """
    Screen showing items due for refill with ability to create refill orders.
    
    Features:
    - Date range filter to show refills due in specific period
    - Search bar to filter by patient name, HCPCS, etc.
    - Color-coded rows: red (overdue), yellow (due soon), green (future)
    - Batch processing of selected items
    - Summary footer with count and action buttons
    """
    
    def __init__(self, folder_path: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.refills_data: List[RefillRow] = []
        self._init_ui()
        self._load_refills()
    
    def _init_ui(self):
        """Initialize UI using standard layout pattern."""
        # Create standard page layout
        main_layout, header, content, footer = create_standard_page_layout(
            title="Refills Due",
            subtitle="View and process items due for refill",
            parent=self
        )
        self.setLayout(main_layout)
        
        # Store references
        self.header = header
        self.footer = footer
        
        # === FILTER ROW ===
        filter_row = FilterRow()
        
        # Search bar
        self.search_bar = filter_row.addSearchBar(
            label="Search:",
            placeholder="Patient name, HCPCS code..."
        )
        self.search_bar.textChanged.connect(self._on_search_changed)
        
        # Date range filters
        date_filter_widget = QWidget()
        date_layout = QHBoxLayout(date_filter_widget)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.setSpacing(8)
        
        date_layout.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate())
        self.date_from.dateChanged.connect(self._on_date_changed)
        date_layout.addWidget(self.date_from)
        
        date_layout.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate().addMonths(1))
        self.date_to.dateChanged.connect(self._on_date_changed)
        date_layout.addWidget(self.date_to)
        
        filter_row.addWidget(date_filter_widget)
        filter_row.addSpacer()
        
        content.layout().addWidget(filter_row)
        
        # === TABLE ===
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Patient Name",
            "DOB",
            "Phone",
            "HCPCS",
            "Description",
            "Next Due",
            "Days Until",
            "Refills Left",
            "Prescriber"
        ])
        
        # Apply standard styling
        apply_standard_table_style(self.table)
        
        # Column widths
        self.table.setColumnWidth(0, 180)  # Patient Name
        self.table.setColumnWidth(1, 100)  # DOB
        self.table.setColumnWidth(2, 120)  # Phone
        self.table.setColumnWidth(3, 100)  # HCPCS
        self.table.setColumnWidth(4, 250)  # Description
        self.table.setColumnWidth(5, 120)  # Next Due
        self.table.setColumnWidth(6, 100)  # Days Until
        self.table.setColumnWidth(7, 100)  # Refills Left
        self.table.setColumnWidth(8, 180)  # Prescriber
        
        content.layout().addWidget(self.table, stretch=1)
        
        # === FOOTER ===
        self.footer.addPrimaryButton("Create Refill Orders", self._on_create_refills)
        self.footer.addSecondaryButton("Refresh", self._load_refills)
        self.footer.addSecondaryButton("Export", self._on_export)
        
        self._update_summary()
    
    def _load_refills(self):
        """Load refills due from database."""
        try:
            # Get date range
            start_date = self.date_from.date().toString("yyyy-MM-dd")
            end_date = self.date_to.date().toString("yyyy-MM-dd")
            today = date.today().strftime("%Y-%m-%d")
            
            # Query database
            self.refills_data = fetch_refills_due(
                start_date=start_date,
                end_date=end_date,
                today=today,
                folder_path=self.folder_path
            )
            
            # Populate table
            self._populate_table()
            self._update_summary()
            
            debug_log(f"Loaded {len(self.refills_data)} refills due")
        
        except Exception as e:
            debug_log(f"Error loading refills: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load refills: {e}")
    
    def _populate_table(self, filter_text: str = ""):
        """
        Populate table with refills data.
        
        Args:
            filter_text: Optional search filter text
        """
        # Clear table
        self.table.setRowCount(0)
        
        # Filter data
        filtered_data = self.refills_data
        if filter_text:
            filter_lower = filter_text.lower()
            filtered_data = [
                r for r in self.refills_data
                if filter_lower in r.get('patient_name', '').lower()
                or filter_lower in r.get('hcpcs_code', '').lower()
                or filter_lower in r.get('description', '').lower()
            ]
        
        # Populate rows
        self.table.setRowCount(len(filtered_data))
        
        for row_idx, refill in enumerate(filtered_data):
            days_until = refill.get('days_until_due', 0)
            
            # Patient Name - color coded by urgency
            item = create_refill_status_item(
                refill.get('patient_name', ''),
                days_until,
                align_center=False
            )
            self.table.setItem(row_idx, 0, item)
            
            # DOB
            item = QTableWidgetItem(refill.get('patient_dob', ''))
            self.table.setItem(row_idx, 1, item)
            
            # Phone
            item = QTableWidgetItem(refill.get('patient_phone', ''))
            self.table.setItem(row_idx, 2, item)
            
            # HCPCS
            item = create_centered_item(refill.get('hcpcs_code', ''))
            self.table.setItem(row_idx, 3, item)
            
            # Description
            item = QTableWidgetItem(refill.get('description', ''))
            self.table.setItem(row_idx, 4, item)
            
            # Next Due - color coded
            next_due = refill.get('next_refill_due', '')
            item = create_refill_status_item(next_due, days_until, align_center=True)
            self.table.setItem(row_idx, 5, item)
            
            # Days Until - color coded with special formatting
            if days_until < 0:
                days_text = f"{abs(days_until)} OVERDUE"
            elif days_until == 0:
                days_text = "TODAY"
            else:
                days_text = str(days_until)
            item = create_refill_status_item(days_text, days_until, align_center=True)
            self.table.setItem(row_idx, 6, item)
            
            # Refills Left
            item = create_centered_item(str(refill.get('refills_remaining', 0)))
            self.table.setItem(row_idx, 7, item)
            
            # Prescriber
            item = QTableWidgetItem(refill.get('prescriber_name', ''))
            self.table.setItem(row_idx, 8, item)
            
            # Per-cell refill status coloring is handled by create_refill_status_item
        
        self._update_summary()
    
    def _on_search_changed(self, text: str):
        """Handle search text changed."""
        self._populate_table(filter_text=text)
    
    def _on_date_changed(self):
        """Handle date range changed."""
        self._load_refills()
    
    def _update_summary(self):
        """Update summary footer text."""
        total = len(self.refills_data)
        
        # Count by status
        overdue = sum(1 for r in self.refills_data if r.get('days_until_due', 0) < 0)
        due_soon = sum(1 for r in self.refills_data if 0 <= r.get('days_until_due', 0) <= 7)
        future = sum(1 for r in self.refills_data if r.get('days_until_due', 0) > 7)
        
        summary = f"Total: {total} refills  |  Overdue: {overdue}  |  Due Soon: {due_soon}  |  Future: {future}"
        self.footer.setSummaryText(summary)
    
    def _on_create_refills(self):
        """Process selected refills into new orders."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(
                self,
                "No Selection",
                "Please select one or more refills to process"
            )
            return
        
        # Get item IDs from selected rows
        item_ids = []
        for row in selected_rows:
            row_idx = row.row()
            if row_idx < len(self.refills_data):
                refill = self.refills_data[row_idx]
                item_ids.append(refill['order_item_id'])
        
        # Confirm
        msg = f"Create refill orders for {len(item_ids)} selected items?"
        reply = QMessageBox.question(
            self,
            "Confirm Refill Processing",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Process refills
        try:
            fill_date = date.today().strftime("%Y-%m-%d")
            success_count = process_refills(
                selected_item_ids=item_ids,
                refill_fill_date=fill_date,
                folder_path=self.folder_path
            )
            
            if success_count == len(item_ids):
                QMessageBox.information(
                    self,
                    "Success",
                    f"Successfully created {success_count} refill orders"
                )
            elif success_count > 0:
                QMessageBox.warning(
                    self,
                    "Partial Success",
                    f"Created {success_count} of {len(item_ids)} refill orders.\n"
                    f"Check logs for failed items."
                )
            else:
                QMessageBox.critical(
                    self,
                    "Failed",
                    "No refill orders were created. Check logs for details."
                )
            
            # Refresh table
            self._load_refills()
        
        except Exception as e:
            debug_log(f"Refill processing error: {e}")
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to process refills: {e}"
            )
    
    def _on_export(self):
        """Export refills to CSV."""
        QMessageBox.information(
            self,
            "Export",
            "CSV export feature coming soon!"
        )
