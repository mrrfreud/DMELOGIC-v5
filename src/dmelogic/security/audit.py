"""
Centralized Audit Logging Module

Provides easy-to-use functions for logging user actions throughout the application.
All audit entries are stored in the users.db audit_log table.
"""

from typing import Optional, Any, Dict
from datetime import datetime

from ..db.users import log_audit as db_log_audit
from .auth import get_session


def audit_log(
    action: str,
    resource_type: str,
    resource_id: Optional[Any] = None,
    details: Optional[str] = None,
    folder_path: Optional[str] = None
) -> None:
    """
    Log an audit entry for the current user.
    
    Args:
        action: The action performed (e.g., "create", "update", "delete", "view", "login")
        resource_type: Type of resource (e.g., "order", "patient", "inventory", "user")
        resource_id: Optional ID of the affected resource
        details: Optional additional details about the action
        folder_path: Optional folder path for the users database
    
    Example:
        audit_log("create", "order", order_id, "Created order for patient John Doe")
        audit_log("delete", "inventory", item_id, "Deleted item HCPCS E0260")
        audit_log("update", "patient", patient_id, "Updated address")
    """
    session = get_session()
    
    if not session or not session.is_authenticated:
        # Log as system if no user logged in (shouldn't happen normally)
        user_id = None
        username = "SYSTEM"
    else:
        user_id = session.user_id
        username = session.username
    
    # Build details string
    detail_parts = []
    if details:
        detail_parts.append(details)
    
    detail_str = "; ".join(detail_parts) if detail_parts else None
    
    try:
        db_log_audit(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            details=detail_str,
            folder_path=folder_path
        )
    except Exception as e:
        # Don't let audit logging failures break the app
        print(f"[AUDIT WARNING] Failed to log audit: {e}")


# Convenience functions for common operations

def audit_create(resource_type: str, resource_id: Any, details: str = None, folder_path: str = None) -> None:
    """Log a create action"""
    audit_log("create", resource_type, resource_id, details, folder_path)


def audit_update(resource_type: str, resource_id: Any, details: str = None, folder_path: str = None) -> None:
    """Log an update action"""
    audit_log("update", resource_type, resource_id, details, folder_path)


def audit_delete(resource_type: str, resource_id: Any, details: str = None, folder_path: str = None) -> None:
    """Log a delete action"""
    audit_log("delete", resource_type, resource_id, details, folder_path)


def audit_view(resource_type: str, resource_id: Any = None, details: str = None, folder_path: str = None) -> None:
    """Log a view/access action"""
    audit_log("view", resource_type, resource_id, details, folder_path)


def audit_export(resource_type: str, resource_id: Any = None, details: str = None, folder_path: str = None) -> None:
    """Log an export action"""
    audit_log("export", resource_type, resource_id, details, folder_path)


def audit_print(resource_type: str, resource_id: Any = None, details: str = None, folder_path: str = None) -> None:
    """Log a print action"""
    audit_log("print", resource_type, resource_id, details, folder_path)


def audit_login(success: bool, username: str, details: str = None, folder_path: str = None) -> None:
    """Log a login attempt"""
    action = "login_success" if success else "login_failed"
    audit_log(action, "auth", None, f"Username: {username}" + (f"; {details}" if details else ""), folder_path)


def audit_logout(folder_path: str = None) -> None:
    """Log a logout"""
    audit_log("logout", "auth", None, None, folder_path)


def audit_password_change(user_id: int, by_admin: bool = False, folder_path: str = None) -> None:
    """Log a password change"""
    details = "Changed by admin" if by_admin else "Changed by user"
    audit_log("password_change", "user", user_id, details, folder_path)


def audit_permission_denied(resource_type: str, action: str, details: str = None, folder_path: str = None) -> None:
    """Log a permission denied event"""
    audit_log("permission_denied", resource_type, None, f"Attempted: {action}" + (f"; {details}" if details else ""), folder_path)
