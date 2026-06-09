"""
Toast Notification System - Modern, non-blocking notifications

Provides beautiful toast notifications with animations for:
- Success messages
- Error messages
- Warning messages
- Info messages
"""

from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, pyqtProperty
from PyQt6.QtGui import QFont, QColor, QPalette


class ToastNotification(QWidget):
    """
    Modern toast notification widget.
    
    Features:
    - Auto-dismiss after timeout
    - Smooth fade in/out animations
    - Click to dismiss
    - Multiple types (success, error, warning, info)
    - Icon support
    - Modern styling
    """
    
    # Toast types with colors
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    
    COLORS = {
        SUCCESS: {"bg": "#4caf50", "icon": "\u2713"},
        ERROR: {"bg": "#f44336", "icon": "\u2715"},
        WARNING: {"bg": "#ff9800", "icon": "\u26a0"},
        INFO: {"bg": "#2196f3", "icon": "\u2139"}
    }
    
    def __init__(
        self,
        message: str,
        toast_type: str = INFO,
        duration: int = 3000,
        parent=None
    ):
        """
        Initialize toast notification.
        
        Args:
            message: Message to display
            toast_type: Type of toast (success, error, warning, info)
            duration: Duration in milliseconds (0 = no auto-dismiss)
            parent: Parent widget
        """
        super().__init__(parent)
        
        self.message = message
        self.toast_type = toast_type
        self.duration = duration
        
        self._setup_ui()
        self._setup_animations()
        
        # Auto-dismiss timer
        if duration > 0:
            QTimer.singleShot(duration, self.hide_animated)
    
    def _setup_ui(self):
        """Setup the toast UI."""
        # Window flags - frameless, stays on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        # Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        
        # Get color scheme
        color_info = self.COLORS.get(self.toast_type, self.COLORS[self.INFO])
        bg_color = color_info["bg"]
        icon = color_info["icon"]
        
        # Icon label
        icon_label = QLabel(icon)
        icon_font = QFont("Segoe UI", 16)
        icon_font.setBold(True)
        icon_label.setFont(icon_font)
        icon_label.setStyleSheet("color: white;")
        layout.addWidget(icon_label)
        
        # Message label
        msg_label = QLabel(self.message)
        msg_font = QFont("Segoe UI", 10)
        msg_label.setFont(msg_font)
        msg_label.setStyleSheet("color: white;")
        msg_label.setWordWrap(True)
        msg_label.setMaximumWidth(400)
        layout.addWidget(msg_label, 1)
        
        # Styling
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {bg_color};
                border-radius: 8px;
                padding: 0px;
            }}
        """)
        
        # Size
        self.setMinimumWidth(250)
        self.setMaximumWidth(500)
        self.adjustSize()
        
        # Click to dismiss
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    
    def _setup_animations(self):
        """Setup fade in/out animations."""
        # Opacity effect
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        
        # Fade in animation
        self.fade_in = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_in.setDuration(300)
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(1.0)
        self.fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Fade out animation
        self.fade_out = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_out.setDuration(300)
        self.fade_out.setStartValue(1.0)
        self.fade_out.setEndValue(0.0)
        self.fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self.fade_out.finished.connect(self.close)
    
    def show_animated(self):
        """Show toast with fade in animation."""
        self.show()
        self.fade_in.start()
    
    def hide_animated(self):
        """Hide toast with fade out animation."""
        self.fade_out.start()
    
    def mousePressEvent(self, event):
        """Click to dismiss."""
        self.hide_animated()
    
    def position_toast(self, parent_widget):
        """
        Position toast at bottom-right of parent widget.
        
        Args:
            parent_widget: Widget to position relative to
        """
        if parent_widget and parent_widget.isVisible():
            parent_geo = parent_widget.geometry()
            
            # Bottom-right corner with margin
            x = parent_geo.right() - self.width() - 20
            y = parent_geo.bottom() - self.height() - 20
            
            self.move(x, y)
        else:
            # Center of screen
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen().geometry()
            x = (screen.width() - self.width()) // 2
            y = screen.height() - self.height() - 50
            self.move(x, y)


class ToastManager:
    """
    Manages multiple toast notifications.
    
    Features:
    - Stack toasts vertically
    - Automatic positioning
    - Queue management
    """
    
    def __init__(self, parent_widget=None):
        """
        Initialize toast manager.
        
        Args:
            parent_widget: Parent widget for positioning
        """
        self.parent_widget = parent_widget
        self.active_toasts = []
        self.spacing = 10  # Spacing between toasts
    
    def show_toast(
        self,
        message: str,
        toast_type: str = ToastNotification.INFO,
        duration: int = 3000
    ):
        """
        Show a toast notification.
        
        Args:
            message: Message to display
            toast_type: Type of toast
            duration: Duration in milliseconds
        """
        # Create toast
        toast = ToastNotification(message, toast_type, duration, self.parent_widget)
        
        # Position it
        self._position_toast(toast)
        
        # Show with animation
        toast.show_animated()
        
        # Track active toasts
        self.active_toasts.append(toast)
        toast.fade_out.finished.connect(lambda: self._on_toast_closed(toast))
    
    def _position_toast(self, toast):
        """Position toast in the stack."""
        if not self.parent_widget or not self.parent_widget.isVisible():
            # Use screen positioning
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen().geometry()
            x = screen.width() - toast.width() - 20
            y = screen.height() - toast.height() - 20
            
            # Stack existing toasts
            for existing in self.active_toasts:
                if existing.isVisible():
                    y -= existing.height() + self.spacing
            
            toast.move(x, y)
        else:
            # Position relative to parent
            parent_geo = self.parent_widget.geometry()
            x = parent_geo.right() - toast.width() - 20
            y = parent_geo.bottom() - toast.height() - 20
            
            # Stack existing toasts
            for existing in self.active_toasts:
                if existing.isVisible():
                    y -= existing.height() + self.spacing
            
            toast.move(x, y)
    
    def _on_toast_closed(self, toast):
        """Handle toast closing."""
        if toast in self.active_toasts:
            self.active_toasts.remove(toast)
        
        # Reposition remaining toasts
        self._reposition_toasts()
    
    def _reposition_toasts(self):
        """Reposition all active toasts."""
        for toast in self.active_toasts:
            if toast.isVisible():
                self._position_toast(toast)
    
    # Convenience methods
    def success(self, message: str, duration: int = 3000):
        """Show success toast."""
        self.show_toast(message, ToastNotification.SUCCESS, duration)
    
    def error(self, message: str, duration: int = 4000):
        """Show error toast."""
        self.show_toast(message, ToastNotification.ERROR, duration)
    
    def warning(self, message: str, duration: int = 3500):
        """Show warning toast."""
        self.show_toast(message, ToastNotification.WARNING, duration)
    
    def info(self, message: str, duration: int = 3000):
        """Show info toast."""
        self.show_toast(message, ToastNotification.INFO, duration)
