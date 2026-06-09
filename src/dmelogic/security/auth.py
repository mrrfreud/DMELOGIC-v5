"""
Authentication Module

Handles user login/logout, session management, and password changes.
"""

from dataclasses import dataclass, field
from typing import Optional, Set, Dict, Any
from datetime import datetime

from ..db.users import (
    get_user_by_username,
    get_user_effective_permissions,
    verify_password,
    update_last_login,
    set_user_password,
    log_audit,
    hash_password,
)


@dataclass
class AuthSession:
    """
    Singleton-like session object holding the current logged-in user.
    Access via AuthSession.instance or the module-level functions.
    """
    user_id: Optional[int] = None
    username: Optional[str] = None
    display_name: Optional[str] = None
    permissions: Set[str] = field(default_factory=set)
    is_authenticated: bool = False
    is_agent: bool = False
    force_password_change: bool = False
    login_time: Optional[datetime] = None
    _folder_path: Optional[str] = None
    
    # Singleton instance
    _instance: 'AuthSession' = None
    
    @classmethod
    def get_instance(cls) -> 'AuthSession':
        """Get or create the singleton session instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset(cls) -> None:
        """Reset the session (for logout)"""
        cls._instance = cls()
    
    def clear(self) -> None:
        """Clear session data"""
        self.user_id = None
        self.username = None
        self.display_name = None
        self.permissions = set()
        self.is_authenticated = False
        self.is_agent = False
        self.force_password_change = False
        self.login_time = None


# Module-level convenience functions

def get_session() -> AuthSession:
    """Get the current auth session"""
    return AuthSession.get_instance()


def is_logged_in() -> bool:
    """Check if a user is currently logged in"""
    return get_session().is_authenticated


def get_current_user() -> Optional[Dict[str, Any]]:
    """Get current user info as a dict, or None if not logged in"""
    session = get_session()
    if not session.is_authenticated:
        return None
    return {
        'id': session.user_id,
        'username': session.username,
        'display_name': session.display_name,
        'permissions': session.permissions,
        'is_agent': session.is_agent,
        'force_password_change': session.force_password_change,
        'login_time': session.login_time,
    }


def is_current_user_agent() -> bool:
    """Check if the currently logged-in user is marked as an agent"""
    session = get_session()
    return session.is_authenticated and session.is_agent


def get_current_permissions() -> Set[str]:
    """Get current user's permissions"""
    return get_session().permissions


def login(username: str, password: str, folder_path: Optional[str] = None) -> tuple[bool, str]:
    """
    Attempt to log in a user.
    
    Returns:
        (success: bool, message: str)
        - On success: (True, "Login successful")
        - On failure: (False, error_message)
    """
    session = get_session()
    session._folder_path = folder_path
    
    # Get user from database
    user = get_user_by_username(username, folder_path)
    
    if not user:
        log_audit(None, "auth.login_failed", details=f"Unknown user: {username}", folder_path=folder_path)
        return False, "Invalid username or password"
    
    # Check if user is active
    if not user.get('is_active', 0):
        log_audit(user['id'], "auth.login_failed", username=username, details="Account disabled", folder_path=folder_path)
        return False, "Account is disabled. Contact administrator."
    
    # Verify password
    if not verify_password(user['password_hash'], password):
        log_audit(user['id'], "auth.login_failed", username=username, details="Invalid password", folder_path=folder_path)
        return False, "Invalid username or password"
    
    # Load effective permissions
    permissions = get_user_effective_permissions(user['id'], folder_path)
    
    # Update session
    session.user_id = user['id']
    session.username = user['username']
    session.display_name = user.get('display_name', user['username'])
    session.permissions = permissions
    session.is_authenticated = True
    session.is_agent = bool(user.get('is_agent', 0))
    session.force_password_change = bool(user.get('force_password_change', 0))
    session.login_time = datetime.now()
    
    # Update last login timestamp
    update_last_login(user['id'], folder_path)
    
    # Log successful login
    log_audit(user['id'], "auth.login", username=username, details="Login successful", folder_path=folder_path)
    
    return True, "Login successful"


def logout(folder_path: Optional[str] = None) -> None:
    """Log out the current user"""
    session = get_session()
    
    if session.is_authenticated:
        log_audit(
            session.user_id,
            "auth.logout",
            username=session.username,
            details="User logged out",
            folder_path=folder_path or session._folder_path
        )
    
    session.clear()
    AuthSession.reset()


def change_password(
    old_password: str,
    new_password: str,
    folder_path: Optional[str] = None
) -> tuple[bool, str]:
    """
    Change the current user's password.
    
    Returns:
        (success: bool, message: str)
    """
    session = get_session()
    
    if not session.is_authenticated:
        return False, "Not logged in"
    
    folder = folder_path or session._folder_path
    
    # Verify old password
    user = get_user_by_username(session.username, folder)
    if not user:
        return False, "User not found"
    
    if not verify_password(user['password_hash'], old_password):
        log_audit(
            session.user_id,
            "auth.password_change_failed",
            username=session.username,
            details="Invalid current password",
            folder_path=folder
        )
        return False, "Current password is incorrect"
    
    # Validate new password
    if len(new_password) < 6:
        return False, "Password must be at least 6 characters"
    
    if new_password == old_password:
        return False, "New password must be different from current password"
    
    # Update password
    set_user_password(session.user_id, new_password, folder)
    session.force_password_change = False
    
    log_audit(
        session.user_id,
        "auth.password_changed",
        username=session.username,
        details="Password changed successfully",
        folder_path=folder
    )
    
    return True, "Password changed successfully"


def refresh_permissions(folder_path: Optional[str] = None) -> None:
    """Refresh the current user's permissions from the database"""
    session = get_session()
    
    if session.is_authenticated:
        folder = folder_path or session._folder_path
        session.permissions = get_user_effective_permissions(session.user_id, folder)
