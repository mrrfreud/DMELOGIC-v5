from .login_dialog import LoginDialog
from .change_password_dialog import ChangePasswordDialog
from .toast_notifications import ToastNotification, ToastManager
from .animations import AnimationHelper, AnimatedWidget
from .design_system import DesignSystem, DS
from .command_bar import CommandBar, CommandBarResult


def create_main_window():
    """Lazy import to avoid loading the legacy-backed UI chain at module import."""
    from .main_window import create_main_window as _create_main_window

    return _create_main_window()

__all__ = [
    "create_main_window", "LoginDialog", "ChangePasswordDialog",
    "ToastNotification", "ToastManager",
    "AnimationHelper", "AnimatedWidget",
    "DesignSystem", "DS",
    "CommandBar", "CommandBarResult",
]
