"""
UI styling utilities for DMELogic.

Provides helper functions for applying consistent visual styles across the application:
- Table cell color coding (refill status, urgency indicators)
- Standard row heights and fonts
- Status-based backgrounds
"""
from __future__ import annotations

from typing import Optional
from datetime import date, datetime
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem
from PyQt6.QtGui import QColor, QBrush, QFont
from PyQt6.QtCore import Qt


# Standard colors for status indicators
class StatusColors:
    """Standard color palette for status indicators."""
    
    # Refill status colors
    OVERDUE = QColor(220, 53, 69)           # Red - past due date
    DUE_SOON = QColor(255, 193, 7)          # Yellow - due within 7 days
    FUTURE = QColor(40, 167, 69)            # Green - due in future
    
    # Order status colors
    PENDING = QColor(255, 193, 7)           # Yellow
    VERIFIED = QColor(0, 122, 204)          # Blue
    DELIVERED = QColor(40, 167, 69)         # Green
    CANCELLED = QColor(220, 53, 69)         # Red
    
    # Background colors (semi-transparent for better readability)
    BG_OVERDUE = QColor(220, 53, 69, 76)    # 30% opacity
    BG_DUE_SOON = QColor(255, 193, 7, 76)   # 30% opacity
    BG_FUTURE = QColor(40, 167, 69, 51)     # 20% opacity
    
    BG_PENDING = QColor(255, 193, 7, 51)
    BG_VERIFIED = QColor(0, 122, 204, 51)
    BG_DELIVERED = QColor(40, 167, 69, 51)
    BG_CANCELLED = QColor(220, 53, 69, 51)


def apply_standard_table_style(table: QTableWidget):
    """
    Apply standard styling to a table widget.
    
    Sets consistent:
    - Row height (32px)
    - Font (Segoe UI, 9pt)
    - Alternating row colors
    - Selection behavior
    
    Args:
        table: QTableWidget to style
    """
    # Standard row height
    table.verticalHeader().setDefaultSectionSize(32)
    
    # Font
    font = QFont("Segoe UI", 9)
    table.setFont(font)
    
    # Alternating row colors (handled by QSS)
    table.setAlternatingRowColors(True)
    
    # Selection behavior
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)


def create_refill_status_item(
    text: str,
    days_until_due: int,
    align_center: bool = False
) -> QTableWidgetItem:
    """
    Create a table item with refill status color coding.
    
    Color rules:
    - Overdue (days_until_due < 0): Red background
    - Due soon (0-7 days): Yellow background
    - Future (> 7 days): Normal (slight green tint)
    
    Args:
        text: Item text to display
        days_until_due: Days until refill is due (negative = overdue)
        align_center: Center-align the text
    
    Returns:
        Styled QTableWidgetItem
    """
    item = QTableWidgetItem(text)
    
    # Apply color based on urgency
    if days_until_due < 0:
        # Overdue - red
        item.setBackground(QBrush(StatusColors.BG_OVERDUE))
        item.setForeground(QBrush(QColor(255, 255, 255)))  # White text
    elif days_until_due <= 7:
        # Due soon - yellow
        item.setBackground(QBrush(StatusColors.BG_DUE_SOON))
        item.setForeground(QBrush(QColor(0, 0, 0)))  # Black text for readability
    else:
        # Future - subtle green
        item.setBackground(QBrush(StatusColors.BG_FUTURE))
        item.setForeground(QBrush(QColor(229, 229, 229)))  # Light gray text
    
    # Alignment
    if align_center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
    
    return item


def create_order_status_item(text: str, status: str) -> QTableWidgetItem:
    """
    Create a table item with order status color coding.
    
    Args:
        text: Item text to display
        status: Order status (Pending, Verified, Delivered, Cancelled, etc.)
    
    Returns:
        Styled QTableWidgetItem
    """
    item = QTableWidgetItem(text)
    
    status_lower = status.lower()
    
    if "pending" in status_lower:
        item.setBackground(QBrush(StatusColors.BG_PENDING))
    elif "verified" in status_lower or "approved" in status_lower:
        item.setBackground(QBrush(StatusColors.BG_VERIFIED))
    elif "delivered" in status_lower or "completed" in status_lower:
        item.setBackground(QBrush(StatusColors.BG_DELIVERED))
        item.setForeground(QBrush(QColor(255, 255, 255)))
    elif "cancelled" in status_lower or "rejected" in status_lower:
        item.setBackground(QBrush(StatusColors.BG_CANCELLED))
        item.setForeground(QBrush(QColor(255, 255, 255)))
    
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
    
    return item


def calculate_days_until_due(next_refill_due: str, today: Optional[str] = None) -> int:
    """
    Calculate days until refill is due.
    
    Args:
        next_refill_due: Date string 'YYYY-MM-DD'
        today: Reference date 'YYYY-MM-DD' (defaults to today)
    
    Returns:
        Days until due (negative = overdue)
    """
    if not next_refill_due:
        return 999  # Far future (no date set)
    
    try:
        due_date = datetime.strptime(next_refill_due, "%Y-%m-%d").date()
        ref_date = date.today() if today is None else datetime.strptime(today, "%Y-%m-%d").date()
        delta = (due_date - ref_date).days
        return delta
    except (ValueError, TypeError):
        return 999


def color_code_refill_row(
    table: QTableWidget,
    row: int,
    days_until_due: int,
    col_indices: Optional[list[int]] = None
):
    """
    Apply refill status color to entire row or specific columns.
    
    Args:
        table: QTableWidget to modify
        row: Row index
        days_until_due: Days until refill due (negative = overdue)
        col_indices: List of column indices to color (None = all columns)
    """
    if col_indices is None:
        col_indices = list(range(table.columnCount()))
    
    # Determine color
    if days_until_due < 0:
        bg_color = StatusColors.BG_OVERDUE
        fg_color = QColor(255, 255, 255)
    elif days_until_due <= 7:
        bg_color = StatusColors.BG_DUE_SOON
        fg_color = QColor(0, 0, 0)
    else:
        bg_color = StatusColors.BG_FUTURE
        fg_color = QColor(229, 229, 229)
    
    # Apply to columns
    for col in col_indices:
        item = table.item(row, col)
        if item:
            item.setBackground(QBrush(bg_color))
            item.setForeground(QBrush(fg_color))


def highlight_search_match(item: QTableWidgetItem, search_text: str):
    """
    Highlight a table item that matches search text.
    
    Args:
        item: QTableWidgetItem to highlight
        search_text: Search term to match
    """
    if not search_text or not item:
        return
    
    item_text = item.text().lower()
    search_lower = search_text.lower()
    
    if search_lower in item_text:
        # Highlight with subtle blue background
        current_bg = item.background()
        item.setBackground(QBrush(QColor(0, 122, 204, 51)))  # Blue tint


def reset_item_style(item: QTableWidgetItem):
    """
    Reset a table item to default styling.
    
    Args:
        item: QTableWidgetItem to reset
    """
    if not item:
        return
    
    item.setBackground(QBrush())  # Clear background
    item.setForeground(QBrush(QColor(229, 229, 229)))  # Default text color


def format_date_cell(date_str: str, days_diff: Optional[int] = None) -> str:
    """
    Format a date string for display in a table cell.
    
    Optionally adds "(X days)" suffix if days_diff provided.
    
    Args:
        date_str: Date string 'YYYY-MM-DD'
        days_diff: Days difference to display
    
    Returns:
        Formatted date string
    
    Examples:
        "2025-12-05" -> "2025-12-05"
        "2025-12-05", -3 -> "2025-12-05 (3 days overdue)"
        "2025-12-05", 5 -> "2025-12-05 (5 days)"
    """
    if not date_str:
        return ""
    
    if days_diff is None:
        return date_str
    
    if days_diff < 0:
        return f"{date_str} ({abs(days_diff)} days overdue)"
    elif days_diff == 0:
        return f"{date_str} (TODAY)"
    else:
        return f"{date_str} ({days_diff} days)"


def create_centered_item(text: str) -> QTableWidgetItem:
    """
    Create a table item with centered text.
    
    Args:
        text: Text to display
    
    Returns:
        QTableWidgetItem with centered alignment
    """
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
    return item


def create_right_aligned_item(text: str) -> QTableWidgetItem:
    """
    Create a table item with right-aligned text (for numbers).
    
    Args:
        text: Text to display
    
    Returns:
        QTableWidgetItem with right alignment
    """
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item
