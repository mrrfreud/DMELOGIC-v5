"""
Reusable UI components for DMELogic application.

Provides standardized widgets that enforce visual consistency across all screens:
- SearchBar: Label + QLineEdit + Clear button
- PageHeader: Title + optional subtitle with consistent typography
- SummaryFooter: Summary label + action buttons row
- ActionButtonRow: Standardized button layout (primary left, secondary right)

All components automatically apply theme styles from dark.qss when instantiated.
"""
from __future__ import annotations

from typing import Optional, List, Callable
from PyQt6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class SearchBar(QWidget):
    """
    Standardized search bar with label, input field, and clear button.
    
    Emits:
        textChanged(str): When search text changes
        searchCleared(): When clear button clicked
    
    Usage:
        search_bar = SearchBar(label="Search Orders:")
        search_bar.textChanged.connect(self.on_search_text_changed)
        search_bar.searchCleared.connect(self.on_search_cleared)
    """
    
    textChanged = pyqtSignal(str)
    searchCleared = pyqtSignal()
    
    def __init__(
        self,
        label: str = "Search:",
        placeholder: str = "Type to search...",
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self._init_ui(label, placeholder)
    
    def _init_ui(self, label: str, placeholder: str):
        """Initialize search bar UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Search label
        self.label = QLabel(label)
        self.label.setObjectName("search-label")
        layout.addWidget(self.label)
        
        # Search input
        self.search_input = QLineEdit()
        self.search_input.setObjectName("search-input")
        self.search_input.setPlaceholderText(placeholder)
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.setMinimumWidth(200)
        layout.addWidget(self.search_input, stretch=1)
        
        # Clear button
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("secondary-button")
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        self.clear_btn.setMaximumWidth(80)
        layout.addWidget(self.clear_btn)
    
    def _on_text_changed(self, text: str):
        """Handle text changed event."""
        self.textChanged.emit(text)
        self.clear_btn.setEnabled(bool(text))
    
    def _on_clear_clicked(self):
        """Handle clear button clicked."""
        self.search_input.clear()
        self.searchCleared.emit()
    
    def text(self) -> str:
        """Get current search text."""
        return self.search_input.text()
    
    def setText(self, text: str):
        """Set search text."""
        self.search_input.setText(text)
    
    def clear(self):
        """Clear search text."""
        self.search_input.clear()
    
    def setFocus(self):
        """Set focus to search input."""
        self.search_input.setFocus()


class PageHeader(QWidget):
    """
    Standardized page header with title and optional subtitle.
    
    Usage:
        header = PageHeader(
            title="Order Management",
            subtitle="View and manage customer orders"
        )
    """
    
    def __init__(
        self,
        title: str,
        subtitle: Optional[str] = None,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self._init_ui(title, subtitle)
    
    def _init_ui(self, title: str, subtitle: Optional[str]):
        """Initialize header UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(4)
        
        # Title
        self.title_label = QLabel(title)
        self.title_label.setObjectName("page-title")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        layout.addWidget(self.title_label)
        
        # Subtitle (optional)
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("page-subtitle")
            subtitle_font = QFont()
            subtitle_font.setPointSize(10)
            self.subtitle_label.setFont(subtitle_font)
            self.subtitle_label.setStyleSheet("color: #888;")
            layout.addWidget(self.subtitle_label)
        else:
            self.subtitle_label = None
    
    def setTitle(self, title: str):
        """Update title text."""
        self.title_label.setText(title)
    
    def setSubtitle(self, subtitle: str):
        """Update subtitle text."""
        if self.subtitle_label:
            self.subtitle_label.setText(subtitle)
        else:
            # Create subtitle if it doesn't exist
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("page-subtitle")
            subtitle_font = QFont()
            subtitle_font.setPointSize(10)
            self.subtitle_label.setFont(subtitle_font)
            self.subtitle_label.setStyleSheet("color: #888;")
            self.layout().addWidget(self.subtitle_label)


class SummaryFooter(QWidget):
    """
    Standardized footer with summary text and action buttons.
    
    Usage:
        footer = SummaryFooter()
        footer.setSummaryText("Total: 25 orders")
        footer.addPrimaryButton("Create Order", self.on_create)
        footer.addSecondaryButton("Export", self.on_export)
    """
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_ui()
    
    def _init_ui(self):
        """Initialize footer UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)
        
        # Summary label (left-aligned)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("summary-label")
        summary_font = QFont()
        summary_font.setPointSize(10)
        summary_font.setBold(True)
        self.summary_label.setFont(summary_font)
        layout.addWidget(self.summary_label)
        
        # Spacer
        layout.addStretch()
        
        # Button container (right-aligned)
        self.button_container = QWidget()
        self.button_layout = QHBoxLayout(self.button_container)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.setSpacing(8)
        layout.addWidget(self.button_container)
    
    def setSummaryText(self, text: str):
        """Set summary text."""
        self.summary_label.setText(text)
    
    def addPrimaryButton(self, text: str, callback: Callable) -> QPushButton:
        """Add a primary action button."""
        btn = QPushButton(text)
        btn.setObjectName("primary-button")
        btn.clicked.connect(callback)
        btn.setMinimumWidth(100)
        self.button_layout.addWidget(btn)
        return btn
    
    def addSecondaryButton(self, text: str, callback: Callable) -> QPushButton:
        """Add a secondary action button."""
        btn = QPushButton(text)
        btn.setObjectName("secondary-button")
        btn.clicked.connect(callback)
        btn.setMinimumWidth(100)
        self.button_layout.addWidget(btn)
        return btn
    
    def clearButtons(self):
        """Remove all buttons."""
        while self.button_layout.count():
            item = self.button_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class ActionButtonRow(QWidget):
    """
    Standardized row of action buttons with consistent spacing and alignment.
    
    Usage:
        button_row = ActionButtonRow()
        button_row.addPrimaryButton("Save", self.on_save)
        button_row.addSecondaryButton("Cancel", self.on_cancel)
        button_row.addSpacer()  # Push remaining buttons to right
        button_row.addDangerButton("Delete", self.on_delete)
    """
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_ui()
    
    def _init_ui(self):
        """Initialize button row UI."""
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)
    
    def addPrimaryButton(self, text: str, callback: Callable) -> QPushButton:
        """Add a primary action button."""
        btn = QPushButton(text)
        btn.setObjectName("primary-button")
        btn.clicked.connect(callback)
        btn.setMinimumWidth(100)
        self.layout.addWidget(btn)
        return btn
    
    def addSecondaryButton(self, text: str, callback: Callable) -> QPushButton:
        """Add a secondary action button."""
        btn = QPushButton(text)
        btn.setObjectName("secondary-button")
        btn.clicked.connect(callback)
        btn.setMinimumWidth(100)
        self.layout.addWidget(btn)
        return btn
    
    def addDangerButton(self, text: str, callback: Callable) -> QPushButton:
        """Add a danger/destructive action button."""
        btn = QPushButton(text)
        btn.setObjectName("danger-button")
        btn.clicked.connect(callback)
        btn.setMinimumWidth(100)
        self.layout.addWidget(btn)
        return btn
    
    def addSpacer(self):
        """Add a spacer to push subsequent buttons to the right."""
        self.layout.addStretch()
    
    def clearButtons(self):
        """Remove all buttons and spacers."""
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class Separator(QFrame):
    """
    Horizontal or vertical separator line.
    
    Usage:
        separator = Separator()  # Horizontal
        separator = Separator(orientation=Qt.Orientation.Vertical)
    """
    
    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.setObjectName("separator")
        
        if orientation == Qt.Orientation.Horizontal:
            self.setFrameShape(QFrame.Shape.HLine)
            self.setFixedHeight(1)
        else:
            self.setFrameShape(QFrame.Shape.VLine)
            self.setFixedWidth(1)
        
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setStyleSheet("background-color: #444;")


class FilterRow(QWidget):
    """
    Standardized filter row with multiple filter controls.
    
    Usage:
        filter_row = FilterRow()
        filter_row.addSearchBar("Search:", "Type to search...")
        filter_row.addComboBox("Status:", ["All", "Pending", "Delivered"])
        filter_row.addDateRange("From:", "To:")
    """
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_ui()
    
    def _init_ui(self):
        """Initialize filter row UI."""
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(16)
    
    def addSearchBar(
        self,
        label: str = "Search:",
        placeholder: str = "Type to search..."
    ) -> SearchBar:
        """Add a search bar to the filter row."""
        search_bar = SearchBar(label=label, placeholder=placeholder)
        self.layout.addWidget(search_bar, stretch=1)
        return search_bar
    
    def addWidget(self, widget: QWidget, stretch: int = 0):
        """Add a custom widget to the filter row."""
        self.layout.addWidget(widget, stretch=stretch)
    
    def addSpacer(self):
        """Add a spacer to push subsequent widgets to the right."""
        self.layout.addStretch()


class StatusBadge(QLabel):
    """
    Status badge with color-coded background.
    
    Usage:
        badge = StatusBadge("Pending", status_type="warning")
        badge = StatusBadge("Completed", status_type="success")
        badge = StatusBadge("Overdue", status_type="danger")
    """
    
    STATUS_COLORS = {
        "success": ("#2ecc71", "#ffffff"),  # Green background, white text
        "warning": ("#f39c12", "#000000"),  # Yellow background, black text
        "danger": ("#e74c3c", "#ffffff"),   # Red background, white text
        "info": ("#3498db", "#ffffff"),     # Blue background, white text
        "neutral": ("#95a5a6", "#ffffff"),  # Gray background, white text
    }
    
    def __init__(
        self,
        text: str,
        status_type: str = "neutral",
        parent: Optional[QWidget] = None
    ):
        super().__init__(text, parent)
        self.setObjectName("status-badge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Apply color scheme
        bg_color, text_color = self.STATUS_COLORS.get(
            status_type, self.STATUS_COLORS["neutral"]
        )
        
        self.setStyleSheet(f"""
            QLabel#status-badge {{
                background-color: {bg_color};
                color: {text_color};
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 10pt;
            }}
        """)
        
        self.setMinimumWidth(80)
    
    def setStatus(self, text: str, status_type: str = "neutral"):
        """Update badge text and color."""
        self.setText(text)
        bg_color, text_color = self.STATUS_COLORS.get(
            status_type, self.STATUS_COLORS["neutral"]
        )
        self.setStyleSheet(f"""
            QLabel#status-badge {{
                background-color: {bg_color};
                color: {text_color};
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 10pt;
            }}
        """)


# Convenience functions for creating common layouts

def create_standard_page_layout(
    title: str,
    subtitle: Optional[str] = None,
    parent: Optional[QWidget] = None
) -> tuple[QVBoxLayout, PageHeader, QWidget, SummaryFooter]:
    """
    Create a standard page layout with header, content area, and footer.
    
    Returns:
        (main_layout, header, content_widget, footer)
    
    Usage:
        layout, header, content, footer = create_standard_page_layout(
            title="Orders",
            subtitle="Manage customer orders"
        )
        
        # Add your content widgets to content.layout()
        content.layout().addWidget(my_table)
        
        # Configure footer
        footer.setSummaryText("Total: 25 orders")
        footer.addPrimaryButton("New Order", self.on_create)
    """
    main_layout = QVBoxLayout()
    main_layout.setContentsMargins(16, 16, 16, 16)
    main_layout.setSpacing(12)
    
    # Header
    header = PageHeader(title=title, subtitle=subtitle)
    main_layout.addWidget(header)
    
    # Content area
    content = QWidget(parent)
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(12)
    main_layout.addWidget(content, stretch=1)
    
    # Footer
    footer = SummaryFooter()
    main_layout.addWidget(footer)
    
    return main_layout, header, content, footer


def create_filter_bar_with_search(
    search_label: str = "Search:",
    search_placeholder: str = "Type to search...",
    parent: Optional[QWidget] = None
) -> tuple[FilterRow, SearchBar]:
    """
    Create a filter bar with a search field.
    
    Returns:
        (filter_row, search_bar)
    
    Usage:
        filter_row, search_bar = create_filter_bar_with_search(
            search_label="Search Orders:",
            search_placeholder="Search by patient name, RX#..."
        )
        search_bar.textChanged.connect(self.on_search)
    """
    filter_row = FilterRow(parent)
    search_bar = filter_row.addSearchBar(
        label=search_label,
        placeholder=search_placeholder
    )
    return filter_row, search_bar
