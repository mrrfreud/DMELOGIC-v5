"""
Permission Checking Module

Provides permission checking and enforcement utilities.
"""

from typing import Optional, Union, List
from functools import wraps

from .auth import get_session, is_logged_in


class PermissionDeniedError(Exception):
    """Raised when a user lacks required permission"""
    def __init__(self, permission: str, message: str = None):
        self.permission = permission
        self.message = message or f"Permission denied: {permission}"
        super().__init__(self.message)


def has_permission(permission: str) -> bool:
    """
    Check if the current user has a specific permission.
    
    Args:
        permission: The permission name to check (e.g., "inventory.delete")
    
    Returns:
        True if user has the permission, False otherwise
    """
    session = get_session()
    
    if not session.is_authenticated:
        return False
    
    return permission in session.permissions


def has_any_permission(*permissions: str) -> bool:
    """
    Check if the current user has ANY of the specified permissions.
    
    Args:
        *permissions: Permission names to check
    
    Returns:
        True if user has at least one of the permissions
    """
    session = get_session()
    
    if not session.is_authenticated:
        return False
    
    return any(p in session.permissions for p in permissions)


def has_all_permissions(*permissions: str) -> bool:
    """
    Check if the current user has ALL of the specified permissions.
    
    Args:
        *permissions: Permission names to check
    
    Returns:
        True if user has all permissions
    """
    session = get_session()
    
    if not session.is_authenticated:
        return False
    
    return all(p in session.permissions for p in permissions)


def require_perm(permission: str, message: str = None) -> None:
    """
    Require a permission. Raises PermissionDeniedError if not met.
    
    Use at the start of any protected function/method:
        def delete_inventory_item(item_id):
            require_perm("inventory.delete")
            # ... proceed with deletion
    
    Args:
        permission: The required permission name
        message: Optional custom error message
    
    Raises:
        PermissionDeniedError: If user lacks the permission
    """
    if not has_permission(permission):
        raise PermissionDeniedError(
            permission,
            message or f"You don't have permission to perform this action ({permission})"
        )


def require_any_perm(*permissions: str, message: str = None) -> None:
    """
    Require at least one of the specified permissions.
    
    Args:
        *permissions: Permission names (user needs at least one)
        message: Optional custom error message
    
    Raises:
        PermissionDeniedError: If user lacks all permissions
    """
    if not has_any_permission(*permissions):
        raise PermissionDeniedError(
            ", ".join(permissions),
            message or f"You don't have permission to perform this action (need one of: {', '.join(permissions)})"
        )


def require_all_perms(*permissions: str, message: str = None) -> None:
    """
    Require all of the specified permissions.
    
    Args:
        *permissions: Permission names (user needs all)
        message: Optional custom error message
    
    Raises:
        PermissionDeniedError: If user lacks any permission
    """
    if not has_all_permissions(*permissions):
        raise PermissionDeniedError(
            ", ".join(permissions),
            message or f"You don't have permission to perform this action (need all: {', '.join(permissions)})"
        )


def permission_required(permission: str):
    """
    Decorator to protect a function with a permission check.
    
    Usage:
        @permission_required("inventory.delete")
        def delete_item(item_id):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            require_perm(permission)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def any_permission_required(*permissions: str):
    """
    Decorator to protect a function requiring at least one of the permissions.
    
    Usage:
        @any_permission_required("inventory.edit", "inventory.add")
        def modify_item(item_id):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            require_any_perm(*permissions)
            return func(*args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# UI Helper Functions
# =============================================================================

def can_view_financial() -> bool:
    """Check if current user can view financial information"""
    return has_permission("financial.view")


def can_edit_financial() -> bool:
    """Check if current user can edit financial information"""
    return has_permission("financial.edit")


def can_manage_inventory() -> bool:
    """Check if current user can add/edit inventory"""
    return has_any_permission("inventory.add", "inventory.edit")


def can_delete_inventory() -> bool:
    """Check if current user can delete inventory items"""
    return has_permission("inventory.delete")


def can_manage_users() -> bool:
    """Check if current user can manage other users"""
    return has_permission("users.manage")


def can_override_locks() -> bool:
    """Check if current user can override order locks"""
    return has_permission("orders.lock_override")


def can_generate_1500() -> bool:
    """Check if current user can generate 1500 forms"""
    return has_permission("billing.generate_1500")


def can_export_epaces() -> bool:
    """Check if current user can export to ePACES"""
    return has_permission("billing.export_epaces")


def can_manage_ocr() -> bool:
    """Check if current user can manage OCR index"""
    return has_permission("ocr.manage_index")


# =============================================================================
# Permission Groups for UI
# =============================================================================

INVENTORY_PERMISSIONS = [
    "inventory.view",
    "inventory.add", 
    "inventory.edit",
    "inventory.delete",
]

FINANCIAL_PERMISSIONS = [
    "financial.view",
    "financial.edit",
]

BILLING_PERMISSIONS = [
    "billing.view",
    "billing.export_epaces",
    "billing.generate_1500",
    "billing.edit_claims",
]

ORDER_PERMISSIONS = [
    "orders.view",
    "orders.create",
    "orders.edit",
    "orders.delete",
    "orders.lock_override",
]

PATIENT_PERMISSIONS = [
    "patients.view",
    "patients.add",
    "patients.edit",
    "patients.delete",
]

ADMIN_PERMISSIONS = [
    "users.manage",
    "users.view",
    "settings.edit",
]
