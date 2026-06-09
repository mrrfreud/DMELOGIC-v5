"""
Security Module Initialization
"""

from .auth import (
    AuthSession,
    login,
    logout,
    change_password,
    is_logged_in,
    get_current_user,
    get_current_permissions,
)

from .permissions import (
    has_permission,
    require_perm,
    PermissionDeniedError,
)

from .audit import (
    audit_log,
    audit_create,
    audit_update,
    audit_delete,
    audit_view,
    audit_export,
    audit_print,
    audit_login,
    audit_logout,
    audit_password_change,
    audit_permission_denied,
)

__all__ = [
    'AuthSession',
    'login',
    'logout',
    'change_password',
    'is_logged_in',
    'get_current_user',
    'get_current_permissions',
    'has_permission',
    'require_perm',
    'PermissionDeniedError',
    'audit_log',
    'audit_create',
    'audit_update',
    'audit_delete',
    'audit_view',
    'audit_export',
    'audit_print',
    'audit_login',
    'audit_logout',
    'audit_password_change',
    'audit_permission_denied',
]
