"""
Theme utilities for applying consistent styling across the application.

Provides helper functions to apply theme classes to widgets without
using inline setStyleSheet() calls.
"""

from PyQt6.QtWidgets import QPushButton, QLabel, QWidget, QFrame


# ============================================================================
# BUTTON STYLE HELPERS
# ============================================================================

def make_primary_button(button: QPushButton) -> QPushButton:
    """Apply primary button style (blue, for main actions)."""
    button.setProperty("class", "primary")
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def make_secondary_button(button: QPushButton) -> QPushButton:
    """Apply secondary button style (gray, for cancel/close)."""
    button.setProperty("class", "secondary")
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def make_danger_button(button: QPushButton) -> QPushButton:
    """Apply danger button style (red, for delete/remove)."""
    button.setProperty("class", "danger")
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def make_success_button(button: QPushButton) -> QPushButton:
    """Apply success button style (green, for confirm/complete)."""
    button.setProperty("class", "success")
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def make_icon_button(button: QPushButton) -> QPushButton:
    """Apply icon-only button style (transparent, minimal)."""
    button.setProperty("class", "icon")
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def make_folder_quick_button(button: QPushButton) -> QPushButton:
    """Apply quick folder button style (for search pane)."""
    button.setProperty("class", "folder-quick")
    button.setCheckable(True)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


# ============================================================================
# LABEL STYLE HELPERS
# ============================================================================

def make_section_title(label: QLabel) -> QLabel:
    """Apply section title style (large, bold)."""
    label.setProperty("class", "section-title")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_subsection_label(label: QLabel) -> QLabel:
    """Apply subsection label style (smaller, lighter)."""
    label.setProperty("class", "subsection")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_wizard_title(label: QLabel) -> QLabel:
    """Apply wizard title style (large, prominent)."""
    label.setProperty("class", "wizard-title")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_wizard_subtitle(label: QLabel) -> QLabel:
    """Apply wizard subtitle style (descriptive text)."""
    label.setProperty("class", "wizard-subtitle")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_status_label(label: QLabel, status_type: str = "info") -> QLabel:
    """
    Apply status label style with color coding.
    
    Args:
        label: The label to style
        status_type: One of "success", "warning", "error", "info"
    """
    label.setProperty("class", f"status-{status_type}")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_badge_label(label: QLabel, danger: bool = False) -> QLabel:
    """Apply badge style (notification count, status indicator)."""
    label.setProperty("class", "badge-danger" if danger else "badge")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_highlight_label(label: QLabel) -> QLabel:
    """Apply highlight style (emphasized text in blue)."""
    label.setProperty("class", "highlight")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_monospace_label(label: QLabel) -> QLabel:
    """Apply monospace font style (for IDs, codes)."""
    label.setProperty("class", "monospace")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_empty_state_label(label: QLabel) -> QLabel:
    """Apply empty state style (large, muted text)."""
    label.setProperty("class", "empty-state")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_summary_main_label(label: QLabel) -> QLabel:
    """Apply main summary label style (bottom panels)."""
    label.setProperty("class", "summary-main")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_summary_sub_label(label: QLabel) -> QLabel:
    """Apply sub summary label style (secondary info)."""
    label.setProperty("class", "summary-sub")
    label.style().unpolish(label)
    label.style().polish(label)
    return label


# ============================================================================
# CONTAINER STYLE HELPERS
# ============================================================================

def make_card(widget: QWidget, name: str = "OrderCard") -> QWidget:
    """Apply card/panel style with rounded corners and borders."""
    widget.setObjectName(name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def make_section_header(frame: QFrame) -> QFrame:
    """Apply section header style (bottom border accent)."""
    frame.setProperty("class", "section-header")
    frame.style().unpolish(frame)
    frame.style().polish(frame)
    return frame


def make_search_container(widget: QWidget) -> QWidget:
    """Apply search container style (filter bars, search panes)."""
    widget.setObjectName("SearchContainer")
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def make_summary_panel(frame: QFrame) -> QFrame:
    """Apply summary/footer panel style."""
    frame.setObjectName("SummaryPanel")
    frame.style().unpolish(frame)
    frame.style().polish(frame)
    return frame


# ============================================================================
# BATCH STYLE APPLICATION
# ============================================================================

def style_button_row(primary_btn: QPushButton = None,
                    secondary_btn: QPushButton = None,
                    danger_btn: QPushButton = None,
                    success_btn: QPushButton = None):
    """
    Style a row of buttons with appropriate classes.
    
    Example:
        style_button_row(
            primary_btn=save_btn,
            secondary_btn=cancel_btn,
            danger_btn=delete_btn
        )
    """
    if primary_btn:
        make_primary_button(primary_btn)
    if secondary_btn:
        make_secondary_button(secondary_btn)
    if danger_btn:
        make_danger_button(danger_btn)
    if success_btn:
        make_success_button(success_btn)


def style_wizard_buttons(back_btn: QPushButton = None,
                        next_btn: QPushButton = None,
                        finish_btn: QPushButton = None,
                        cancel_btn: QPushButton = None):
    """
    Style wizard navigation buttons.
    
    Example:
        style_wizard_buttons(
            back_btn=self.back_button,
            next_btn=self.next_button,
            finish_btn=self.finish_button,
            cancel_btn=self.cancel_button
        )
    """
    if back_btn:
        back_btn.setProperty("class", "wizard-back")
        back_btn.style().unpolish(back_btn)
        back_btn.style().polish(back_btn)
    
    if next_btn:
        next_btn.setProperty("class", "wizard-next")
        next_btn.style().unpolish(next_btn)
        next_btn.style().polish(next_btn)
    
    if finish_btn:
        finish_btn.setProperty("class", "wizard-finish")
        finish_btn.style().unpolish(finish_btn)
        finish_btn.style().polish(finish_btn)
    
    if cancel_btn:
        make_secondary_button(cancel_btn)


# ============================================================================
# TABLE UTILITIES
# ============================================================================

def setup_modern_table(table):
    """
    Configure table with modern styling (already applied by theme.qss).
    Just sets up behavior and alternating rows.
    """
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(table.SelectionBehavior.SelectRows)
    table.setShowGrid(True)
    # Style is handled by theme.qss automatically


# ============================================================================
# REFRESH STYLES (Force Re-render)
# ============================================================================

def refresh_widget_style(widget: QWidget):
    """Force widget to refresh its style (useful after property changes)."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def refresh_all_buttons_in_layout(layout):
    """Refresh all buttons in a layout (useful after theme changes)."""
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item:
            widget = item.widget()
            if isinstance(widget, QPushButton):
                refresh_widget_style(widget)


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_primary_button(text: str, parent=None) -> QPushButton:
    """Create a new primary button with text."""
    btn = QPushButton(text, parent)
    return make_primary_button(btn)


def create_secondary_button(text: str, parent=None) -> QPushButton:
    """Create a new secondary button with text."""
    btn = QPushButton(text, parent)
    return make_secondary_button(btn)


def create_danger_button(text: str, parent=None) -> QPushButton:
    """Create a new danger button with text."""
    btn = QPushButton(text, parent)
    return make_danger_button(btn)


def create_section_title(text: str, parent=None) -> QLabel:
    """Create a new section title label."""
    label = QLabel(text, parent)
    return make_section_title(label)


def create_wizard_title(text: str, parent=None) -> QLabel:
    """Create a new wizard title label."""
    label = QLabel(text, parent)
    return make_wizard_title(label)


def create_section_header(parent=None) -> QFrame:
    """Create a new section header frame."""
    frame = QFrame(parent)
    return make_section_header(frame)


# ============================================================================
# MIGRATION HELPERS (Remove Inline Styles)
# ============================================================================

def remove_inline_style(widget: QWidget):
    """Remove any inline stylesheet, letting theme.qss take over."""
    widget.setStyleSheet("")
    refresh_widget_style(widget)


def migrate_to_theme(widget: QWidget, style_type: str):
    """
    Migrate a widget from inline styles to theme classes.
    
    Args:
        widget: The widget to migrate
        style_type: One of "primary-button", "secondary-button", "danger-button",
                   "section-title", "subsection", etc.
    """
    remove_inline_style(widget)
    
    if style_type == "primary-button":
        make_primary_button(widget)
    elif style_type == "secondary-button":
        make_secondary_button(widget)
    elif style_type == "danger-button":
        make_danger_button(widget)
    elif style_type == "section-title":
        make_section_title(widget)
    elif style_type == "subsection":
        make_subsection_label(widget)
    # Add more as needed
