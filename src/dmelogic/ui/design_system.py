"""
Design System - Consistent spacing, colors, and styles

Provides professional design constants for:
- Spacing (margins, padding, gaps)
- Colors (palette, semantic colors)
- Typography (fonts, sizes)
- Shadows and effects
- Border radius
"""

from PyQt6.QtGui import QColor, QFont


class DesignSystem:
    """
    Centralized design system for consistent UI.
    
    Usage:
        from dmelogic.ui.design_system import DesignSystem as DS
        
        layout.setContentsMargins(*DS.MARGINS_STANDARD)
        layout.setSpacing(DS.SPACING_MD)
        button.setStyleSheet(f"background-color: {DS.COLOR_PRIMARY};")
    """
    
    # ========================================================================
    # SPACING SYSTEM
    # ========================================================================
    
    # Base unit (all spacing is multiples of this)
    UNIT = 4
    
    # Spacing scale
    SPACING_XS = UNIT * 1      # 4px  - Tight spacing
    SPACING_SM = UNIT * 2      # 8px  - Small gaps
    SPACING_MD = UNIT * 3      # 12px - Standard spacing
    SPACING_LG = UNIT * 4      # 16px - Large gaps
    SPACING_XL = UNIT * 6      # 24px - Extra large
    SPACING_2XL = UNIT * 8     # 32px - Section spacing
    
    # Margins (for layouts)
    MARGINS_NONE = (0, 0, 0, 0)
    MARGINS_TIGHT = (SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
    MARGINS_STANDARD = (SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
    MARGINS_COMFORTABLE = (SPACING_LG, SPACING_LG, SPACING_LG, SPACING_LG)
    MARGINS_SPACIOUS = (SPACING_XL, SPACING_XL, SPACING_XL, SPACING_XL)
    
    # Padding (for widgets)
    PADDING_XS = f"{SPACING_XS}px"
    PADDING_SM = f"{SPACING_SM}px"
    PADDING_MD = f"{SPACING_MD}px"
    PADDING_LG = f"{SPACING_LG}px"
    PADDING_XL = f"{SPACING_XL}px"
    
    # Component-specific spacing
    CARD_PADDING = SPACING_LG
    BUTTON_PADDING_H = SPACING_LG      # Horizontal padding
    BUTTON_PADDING_V = SPACING_SM      # Vertical padding
    INPUT_PADDING = SPACING_SM
    SECTION_SPACING = SPACING_2XL
    
    # ========================================================================
    # COLOR SYSTEM
    # ========================================================================
    
    # Primary colors
    COLOR_PRIMARY = "#1976d2"           # Blue - main brand color
    COLOR_PRIMARY_DARK = "#1565c0"      # Darker blue
    COLOR_PRIMARY_LIGHT = "#42a5f5"     # Lighter blue
    
    # Secondary colors
    COLOR_SECONDARY = "#9c27b0"         # Purple
    COLOR_SECONDARY_DARK = "#7b1fa2"
    COLOR_SECONDARY_LIGHT = "#ba68c8"
    
    # Semantic colors
    COLOR_SUCCESS = "#4caf50"           # Green
    COLOR_SUCCESS_LIGHT = "#81c784"
    COLOR_SUCCESS_DARK = "#388e3c"
    
    COLOR_ERROR = "#f44336"             # Red
    COLOR_ERROR_LIGHT = "#e57373"
    COLOR_ERROR_DARK = "#d32f2f"
    
    COLOR_WARNING = "#ff9800"           # Orange
    COLOR_WARNING_LIGHT = "#ffb74d"
    COLOR_WARNING_DARK = "#f57c00"
    
    COLOR_INFO = "#2196f3"              # Light blue
    COLOR_INFO_LIGHT = "#64b5f6"
    COLOR_INFO_DARK = "#1976d2"
    
    # Neutral colors (for dark theme)
    COLOR_BG_PRIMARY = "#1e1e1e"        # Main background
    COLOR_BG_SECONDARY = "#2d2d2d"      # Cards, panels
    COLOR_BG_TERTIARY = "#3d3d3d"       # Hover states
    
    COLOR_TEXT_PRIMARY = "#ffffff"      # Main text
    COLOR_TEXT_SECONDARY = "#b0b0b0"    # Secondary text
    COLOR_TEXT_DISABLED = "#666666"     # Disabled text
    
    COLOR_BORDER = "#404040"            # Standard borders
    COLOR_BORDER_LIGHT = "#4a4a4a"      # Light borders
    COLOR_DIVIDER = "#333333"           # Divider lines
    
    # Status colors
    COLOR_PENDING = "#ff9800"           # Orange
    COLOR_APPROVED = "#4caf50"          # Green
    COLOR_DENIED = "#f44336"            # Red
    COLOR_ON_HOLD = "#9e9e9e"           # Gray
    
    # ========================================================================
    # TYPOGRAPHY
    # ========================================================================
    
    # Font families
    FONT_FAMILY = "Segoe UI"
    FONT_FAMILY_MONO = "Consolas, Courier New, monospace"
    
    # Font sizes
    FONT_SIZE_XS = 9
    FONT_SIZE_SM = 10
    FONT_SIZE_MD = 11
    FONT_SIZE_LG = 13
    FONT_SIZE_XL = 16
    FONT_SIZE_2XL = 20
    FONT_SIZE_3XL = 24
    
    # Font weights
    FONT_WEIGHT_NORMAL = "normal"
    FONT_WEIGHT_MEDIUM = "500"
    FONT_WEIGHT_SEMIBOLD = "600"
    FONT_WEIGHT_BOLD = "bold"
    
    # Line heights
    LINE_HEIGHT_TIGHT = 1.2
    LINE_HEIGHT_NORMAL = 1.5
    LINE_HEIGHT_RELAXED = 1.8
    
    @staticmethod
    def get_font(size: int = 11, bold: bool = False, family: str = None) -> QFont:
        """
        Get a QFont with design system settings.
        
        Args:
            size: Font size
            bold: Bold weight
            family: Font family (default: Segoe UI)
        
        Returns:
            QFont instance
        """
        font = QFont(family or DesignSystem.FONT_FAMILY, size)
        if bold:
            font.setBold(True)
        return font
    
    # ========================================================================
    # BORDERS & SHADOWS
    # ========================================================================
    
    # Border radius
    RADIUS_NONE = "0px"
    RADIUS_SM = "4px"
    RADIUS_MD = "6px"
    RADIUS_LG = "8px"
    RADIUS_XL = "12px"
    RADIUS_FULL = "9999px"
    
    # Border widths
    BORDER_THIN = "1px"
    BORDER_MEDIUM = "2px"
    BORDER_THICK = "3px"
    
    # Shadow definitions (for stylesheets)
    SHADOW_SM = "0 1px 2px rgba(0, 0, 0, 0.2)"
    SHADOW_MD = "0 2px 4px rgba(0, 0, 0, 0.3)"
    SHADOW_LG = "0 4px 8px rgba(0, 0, 0, 0.4)"
    SHADOW_XL = "0 8px 16px rgba(0, 0, 0, 0.5)"
    
    # ========================================================================
    # COMPONENT STYLES (Ready-to-use)
    # ========================================================================
    
    @staticmethod
    def button_primary() -> str:
        """Primary button stylesheet."""
        return f"""
            QPushButton {{
                background-color: {DesignSystem.COLOR_PRIMARY};
                color: {DesignSystem.COLOR_TEXT_PRIMARY};
                border: none;
                border-radius: {DesignSystem.RADIUS_MD};
                padding: {DesignSystem.BUTTON_PADDING_V}px {DesignSystem.BUTTON_PADDING_H}px;
                font-size: {DesignSystem.FONT_SIZE_MD}pt;
                font-weight: {DesignSystem.FONT_WEIGHT_SEMIBOLD};
                min-height: 32px;
            }}
            QPushButton:hover {{
                background-color: {DesignSystem.COLOR_PRIMARY_DARK};
            }}
            QPushButton:pressed {{
                background-color: {DesignSystem.COLOR_PRIMARY_LIGHT};
            }}
            QPushButton:disabled {{
                background-color: {DesignSystem.COLOR_BG_TERTIARY};
                color: {DesignSystem.COLOR_TEXT_DISABLED};
            }}
        """
    
    @staticmethod
    def button_success() -> str:
        """Success button stylesheet."""
        return f"""
            QPushButton {{
                background-color: {DesignSystem.COLOR_SUCCESS};
                color: white;
                border: none;
                border-radius: {DesignSystem.RADIUS_MD};
                padding: {DesignSystem.BUTTON_PADDING_V}px {DesignSystem.BUTTON_PADDING_H}px;
                font-size: {DesignSystem.FONT_SIZE_MD}pt;
                font-weight: {DesignSystem.FONT_WEIGHT_SEMIBOLD};
            }}
            QPushButton:hover {{
                background-color: {DesignSystem.COLOR_SUCCESS_DARK};
            }}
        """
    
    @staticmethod
    def button_danger() -> str:
        """Danger/delete button stylesheet."""
        return f"""
            QPushButton {{
                background-color: {DesignSystem.COLOR_ERROR};
                color: white;
                border: none;
                border-radius: {DesignSystem.RADIUS_MD};
                padding: {DesignSystem.BUTTON_PADDING_V}px {DesignSystem.BUTTON_PADDING_H}px;
                font-size: {DesignSystem.FONT_SIZE_MD}pt;
                font-weight: {DesignSystem.FONT_WEIGHT_SEMIBOLD};
            }}
            QPushButton:hover {{
                background-color: {DesignSystem.COLOR_ERROR_DARK};
            }}
        """
    
    @staticmethod
    def button_secondary() -> str:
        """Secondary button stylesheet."""
        return f"""
            QPushButton {{
                background-color: {DesignSystem.COLOR_BG_TERTIARY};
                color: {DesignSystem.COLOR_TEXT_PRIMARY};
                border: {DesignSystem.BORDER_THIN} solid {DesignSystem.COLOR_BORDER};
                border-radius: {DesignSystem.RADIUS_MD};
                padding: {DesignSystem.BUTTON_PADDING_V}px {DesignSystem.BUTTON_PADDING_H}px;
                font-size: {DesignSystem.FONT_SIZE_MD}pt;
            }}
            QPushButton:hover {{
                background-color: {DesignSystem.COLOR_BG_PRIMARY};
                border-color: {DesignSystem.COLOR_BORDER_LIGHT};
            }}
        """
    
    @staticmethod
    def card() -> str:
        """Card container stylesheet."""
        return f"""
            QFrame {{
                background-color: {DesignSystem.COLOR_BG_SECONDARY};
                border: {DesignSystem.BORDER_THIN} solid {DesignSystem.COLOR_BORDER};
                border-radius: {DesignSystem.RADIUS_LG};
                padding: {DesignSystem.CARD_PADDING}px;
            }}
        """
    
    @staticmethod
    def input_field() -> str:
        """Input field stylesheet."""
        return f"""
            QLineEdit, QTextEdit {{
                background-color: {DesignSystem.COLOR_BG_TERTIARY};
                color: {DesignSystem.COLOR_TEXT_PRIMARY};
                border: {DesignSystem.BORDER_THIN} solid {DesignSystem.COLOR_BORDER};
                border-radius: {DesignSystem.RADIUS_SM};
                padding: {DesignSystem.INPUT_PADDING}px;
                font-size: {DesignSystem.FONT_SIZE_MD}pt;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border-color: {DesignSystem.COLOR_PRIMARY};
                background-color: {DesignSystem.COLOR_BG_PRIMARY};
            }}
            QLineEdit:disabled, QTextEdit:disabled {{
                background-color: {DesignSystem.COLOR_BG_PRIMARY};
                color: {DesignSystem.COLOR_TEXT_DISABLED};
            }}
        """


# ============================================================================
# Convenience class for quick imports
# ============================================================================

# Alias for shorter imports
DS = DesignSystem
