"""
Prescriber Search Dialog - for selecting prescribers from database
"""

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

from dmelogic.db.prescribers import fetch_active_prescribers


class PrescriberSearchDialog(QDialog):
    """Dialog for searching and selecting prescribers from database."""
    
    def __init__(self, folder_path: str = None, parent=None):
        super().__init__(parent)
        self.folder_path = folder_path  # Changed from db_path
        self.selected_prescriber = None
        
        self.setWindowTitle("Search Prescribers")
        self.setModal(False)  # Non-modal so it can be minimized
        
        # Enable window controls: minimize, maximize, resize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        self.resize(800, 600)
        
        self.setup_ui()
        self.load_all_prescribers()
    
    def setup_ui(self):
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Search box
        search_layout = QHBoxLayout()
        search_layout.setSpacing(6)
        
        search_label = QLabel("Search:")
        search_layout.addWidget(search_label)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Type to search by name, NPI, or phone...")
        self.search_edit.textChanged.connect(self.on_search)
        search_layout.addWidget(self.search_edit, 1)
        
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.search_edit.clear)
        search_layout.addWidget(clear_btn)
        
        layout.addLayout(search_layout)
        
        # Results tree
        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabels(["Name", "Title", "NPI", "Phone", "Specialty"])
        self.results_tree.setAlternatingRowColors(True)
        self.results_tree.setSortingEnabled(True)
        self.results_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.results_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.results_tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        # Adjust column widths
        header = self.results_tree.header()
        header.resizeSection(0, 200)  # Name
        header.resizeSection(1, 80)   # Title
        header.resizeSection(2, 100)  # NPI
        header.resizeSection(3, 120)  # Phone
        header.resizeSection(4, 250)  # Specialty
        
        layout.addWidget(self.results_tree, 1)
        
        # Results count
        self.results_label = QLabel("0 prescribers")
        self.results_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.results_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.select_btn = QPushButton("Select")
        self.select_btn.setDefault(True)
        self.select_btn.clicked.connect(self.on_select)
        button_layout.addWidget(self.select_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def load_all_prescribers(self):
        """Load all prescribers from database."""
        try:
            prescribers = fetch_active_prescribers(folder_path=self.folder_path)
            
            self.all_prescribers = []
            for row in prescribers:
                self.all_prescribers.append({
                    'id': row["id"],
                    'first_name': row["first_name"] or '',
                    'last_name': row["last_name"] or '',
                    'title': row["title"] or '',
                    'npi': row["npi_number"] or '',
                    'phone': row["phone"] or '',
                    'specialty': row["specialty"] or ''
                })
            
            self.display_prescribers(self.all_prescribers)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Database Error",
                f"Failed to load prescribers: {str(e)}"
            )
    
    def display_prescribers(self, prescribers):
        """Display prescribers in the tree widget."""
        self.results_tree.clear()
        
        for presc in prescribers:
            # Format name as "LAST, FIRST"
            name = f"{presc['last_name']}, {presc['first_name']}"
            
            item = QTreeWidgetItem([
                name,
                presc['title'],
                presc['npi'],
                presc['phone'],
                presc['specialty']
            ])
            
            # Store full prescriber data in item
            item.setData(0, Qt.ItemDataRole.UserRole, presc)
            
            # Set text color to black for visibility on light background
            text_color = QBrush(QColor("#000000"))
            for col in range(5):
                item.setForeground(col, text_color)
            
            self.results_tree.addTopLevelItem(item)
        
        # Update count
        count = len(prescribers)
        self.results_label.setText(f"{count} prescriber{'s' if count != 1 else ''}")
    
    def on_search(self, text: str):
        """Filter prescribers based on search text."""
        term = text.strip().upper()
        
        if not term:
            self.display_prescribers(self.all_prescribers)
            return
        
        # Remove commas and split search term into parts for partial matching
        search_term_cleaned = term.replace(',', ' ')
        search_parts = [p for p in search_term_cleaned.split() if p]
        
        filtered = []
        for presc in self.all_prescribers:
            # Check NPI and phone for exact substring match
            npi_match = term in presc['npi'].upper()
            phone_match = term in presc['phone'].upper()
            
            if npi_match or phone_match:
                filtered.append(presc)
                continue
            
            # For name matching, create searchable name string
            # Include both "LAST FIRST" and title if present
            name_text = f"{presc['last_name']} {presc['first_name']}".upper()
            if presc['title']:
                name_text = f"{presc['title']} {name_text}".upper()
            
            # Split name into parts for matching
            name_parts = name_text.split()
            
            # Check if all search parts match at least one name part (partial match)
            match = True
            for search_part in search_parts:
                if not any(search_part in name_part for name_part in name_parts):
                    match = False
                    break
            
            if match:
                filtered.append(presc)
        
        self.display_prescribers(filtered)
    
    def on_item_double_clicked(self, item, column):
        """Handle double-click on an item."""
        self.on_select()
    
    def on_select(self):
        """Handle Select button click."""
        selected_items = self.results_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self,
                "No Selection",
                "Please select a prescriber from the list."
            )
            return
        
        item = selected_items[0]
        self.selected_prescriber = item.data(0, Qt.ItemDataRole.UserRole)
        self.accept()
    
    def get_selected_prescriber(self):
        """Get the selected prescriber data."""
        return self.selected_prescriber
    
    def set_initial_query(self, text: str) -> None:
        """
        Set the search box content and immediately run the search.
        This allows seeding the dialog with text typed by the user.
        """
        if text:
            self.search_edit.setText(text)
            # The textChanged signal will automatically trigger on_search()
