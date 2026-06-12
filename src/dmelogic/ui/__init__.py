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


def create_icd10_search_dialog(parent=None):
    """Lazy import seam for ICD-10 dialog while extraction is in progress."""
    from .icd10_search_dialog import create_icd10_search_dialog as _create_icd10_search_dialog

    return _create_icd10_search_dialog(parent)


def create_prescriber_dialog(parent=None, prescriber_data=None):
    """Lazy import seam for Prescriber dialog while extraction is in progress."""
    from .prescriber_dialog import create_prescriber_dialog as _create_prescriber_dialog

    return _create_prescriber_dialog(parent, prescriber_data)


def create_inventory_item_dialog(parent=None, item_data=None):
    """Lazy import seam for Inventory Item dialog while extraction is in progress."""
    from .inventory_item_dialog import create_inventory_item_dialog as _create_inventory_item_dialog

    return _create_inventory_item_dialog(parent, item_data)

__all__ = [
    "create_main_window", "create_icd10_search_dialog", "create_prescriber_dialog", "create_inventory_item_dialog", "LoginDialog", "ChangePasswordDialog",
    "ToastNotification", "ToastManager",
    "AnimationHelper", "AnimatedWidget",
    "DesignSystem", "DS",
    "CommandBar", "CommandBarResult",
]
