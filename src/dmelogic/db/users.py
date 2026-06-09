"""
User Authentication & Authorization Database Module

Provides RBAC (Role-Based Access Control) with:
- Users with hashed passwords (Argon2)
- Roles with permissions
- Per-user permission overrides
- Audit logging for compliance
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Set, Any, Tuple
import os

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError
    ARGON2_AVAILABLE = True
except ImportError:
    ARGON2_AVAILABLE = False
    print("[WARNING] argon2-cffi not installed. Run: pip install argon2-cffi")


# Password hasher instance
_password_hasher = PasswordHasher() if ARGON2_AVAILABLE else None


# =============================================================================
# Default Roles and Permissions
# =============================================================================

DEFAULT_PERMISSIONS = [
    # User management
    "users.manage",
    "users.view",
    
    # Inventory
    "inventory.view",
    "inventory.add",
    "inventory.edit",
    "inventory.delete",
    
    # Financial (costs, charges, totals)
    "financial.view",
    "financial.edit",
    
    # Orders
    "orders.view",
    "orders.create",
    "orders.edit",
    "orders.delete",
    "orders.lock_override",
    
    # Billing
    "billing.view",
    "billing.export_epaces",
    "billing.generate_1500",
    "billing.edit_claims",
    
    # Patients
    "patients.view",
    "patients.add",
    "patients.edit",
    "patients.delete",
    
    # Prescribers
    "prescribers.view",
    "prescribers.add",
    "prescribers.edit",
    "prescribers.delete",
    
    # Reports
    "reports.view",
    "reports.export",
    
    # OCR/Documents
    "ocr.manage_index",
    "documents.view",
    "documents.upload",
    "documents.delete",
    
    # Settings
    "settings.view",
    "settings.edit",
]

DEFAULT_ROLES = {
    "Admin": DEFAULT_PERMISSIONS,  # Everything
    
    "Manager": [
        "users.view",
        "inventory.view", "inventory.add", "inventory.edit",
        "financial.view", "financial.edit",
        "orders.view", "orders.create", "orders.edit", "orders.lock_override",
        "billing.view", "billing.export_epaces", "billing.generate_1500", "billing.edit_claims",
        "patients.view", "patients.add", "patients.edit",
        "prescribers.view", "prescribers.add", "prescribers.edit",
        "reports.view", "reports.export",
        "documents.view", "documents.upload",
        "settings.view",
    ],
    
    "Billing": [
        "orders.view",
        "billing.view", "billing.export_epaces", "billing.generate_1500", "billing.edit_claims",
        "patients.view",
        "prescribers.view",
        "financial.view",
        "reports.view",
        "documents.view",
    ],
    
    "Inventory": [
        "inventory.view", "inventory.add", "inventory.edit",
        "orders.view",
        "patients.view",
        "prescribers.view",
        "documents.view",
    ],
    
    "Clerk": [
        "orders.view", "orders.create", "orders.edit",
        "patients.view", "patients.add", "patients.edit",
        "prescribers.view",
        "inventory.view",
        "documents.view", "documents.upload",
    ],
}


# =============================================================================
# Database Path Resolution
# =============================================================================

def get_users_db_path(folder_path: Optional[str] = None) -> str:
    """Get the path to users.db using centralized db_dir() or provided folder."""
    if folder_path:
        folder = folder_path
    else:
        # Use centralized path resolution
        try:
            from dmelogic.paths import db_dir
            folder = str(db_dir())
        except Exception:
            # Fallback to the canonical data root if paths can't be imported.
            import os as _os
            _pd = _os.environ.get("PROGRAMDATA", r"C:\ProgramData")
            folder = _os.path.join(_pd, "DMELogic", "Databases")
    Path(folder).mkdir(parents=True, exist_ok=True)
    return os.path.join(folder, "users.db")


def get_connection(folder_path: Optional[str] = None) -> sqlite3.Connection:
    """Get a connection to users.db"""
    db_path = get_users_db_path(folder_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# =============================================================================
# Database Initialization
# =============================================================================

def init_users_db(folder_path: Optional[str] = None) -> None:
    """Initialize users.db with all required tables"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_agent INTEGER NOT NULL DEFAULT 0,
            force_password_change INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT
        )
    """)

    # --- Migration: add is_agent column for existing databases ---
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_agent INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Roles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT DEFAULT ''
        )
    """)
    
    # Permissions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT ''
        )
    """)
    
    # Role-Permission mapping
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER NOT NULL,
            perm_id INTEGER NOT NULL,
            PRIMARY KEY (role_id, perm_id),
            FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE,
            FOREIGN KEY(perm_id) REFERENCES permissions(id) ON DELETE CASCADE
        )
    """)
    
    # User-Role mapping
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, role_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE
        )
    """)
    
    # Per-user permission overrides
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_permissions (
            user_id INTEGER NOT NULL,
            perm_id INTEGER NOT NULL,
            allowed INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, perm_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(perm_id) REFERENCES permissions(id) ON DELETE CASCADE
        )
    """)
    
    # Audit log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            details TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    
    # Indexes for performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
    
    conn.commit()
    conn.close()
    print("[OK] Users database initialized")


def seed_default_roles_and_permissions(folder_path: Optional[str] = None) -> None:
    """Seed default roles and permissions if not present"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    # Insert permissions
    for perm_name in DEFAULT_PERMISSIONS:
        cursor.execute(
            "INSERT OR IGNORE INTO permissions (name) VALUES (?)",
            (perm_name,)
        )
    
    # Insert roles and their permissions
    for role_name, perms in DEFAULT_ROLES.items():
        cursor.execute(
            "INSERT OR IGNORE INTO roles (name) VALUES (?)",
            (role_name,)
        )
        cursor.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
        role_row = cursor.fetchone()
        if role_row:
            role_id = role_row["id"]
            for perm_name in perms:
                cursor.execute("SELECT id FROM permissions WHERE name = ?", (perm_name,))
                perm_row = cursor.fetchone()
                if perm_row:
                    cursor.execute(
                        "INSERT OR IGNORE INTO role_permissions (role_id, perm_id) VALUES (?, ?)",
                        (role_id, perm_row["id"])
                    )
    
    conn.commit()
    conn.close()
    print("[OK] Default roles and permissions seeded")


def ensure_admin_user(folder_path: Optional[str] = None) -> bool:
    """
    Ensure at least one admin user exists.
    If no users exist, create default admin with password 'admin123'.
    Returns True if a new admin was created.
    """
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    # Check if any users exist
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    count = cursor.fetchone()["cnt"]
    
    if count == 0:
        # Create default admin
        now = datetime.now().isoformat()
        password_hash = hash_password("admin123")
        
        cursor.execute("""
            INSERT INTO users (username, display_name, password_hash, is_active, force_password_change, created_at, updated_at)
            VALUES (?, ?, ?, 1, 1, ?, ?)
        """, ("admin", "Administrator", password_hash, now, now))
        
        user_id = cursor.lastrowid
        
        # Assign Admin role
        cursor.execute("SELECT id FROM roles WHERE name = 'Admin'")
        admin_role = cursor.fetchone()
        if admin_role:
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user_id, admin_role["id"])
            )
        
        conn.commit()
        conn.close()
        print("[OK] Default admin user created (username: admin, password: admin123)")
        print("[WARNING] Please change the admin password immediately!")
        return True
    
    conn.close()
    return False


# =============================================================================
# Password Hashing
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a password using Argon2"""
    if not ARGON2_AVAILABLE:
        raise RuntimeError("argon2-cffi is required for password hashing. Run: pip install argon2-cffi")
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password against its hash"""
    if not ARGON2_AVAILABLE:
        raise RuntimeError("argon2-cffi is required for password verification")
    try:
        _password_hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


# =============================================================================
# User CRUD Operations
# =============================================================================

def get_user_by_username(username: str, folder_path: Optional[str] = None) -> Optional[Dict]:
    """Get user by username"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int, folder_path: Optional[str] = None) -> Optional[Dict]:
    """Get user by ID"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users(folder_path: Optional[str] = None) -> List[Dict]:
    """Get all users"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY username")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(
    username: str,
    display_name: str,
    password: str,
    roles: List[str] = None,
    is_active: bool = True,
    is_agent: bool = False,
    force_password_change: bool = True,
    folder_path: Optional[str] = None
) -> int:
    """Create a new user and return their ID"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    now = datetime.now().isoformat()
    password_hash = hash_password(password)
    
    cursor.execute("""
        INSERT INTO users (username, display_name, password_hash, is_active, is_agent, force_password_change, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (username, display_name, password_hash, int(is_active), int(is_agent), int(force_password_change), now, now))
    
    user_id = cursor.lastrowid
    
    # Assign roles
    if roles:
        for role_name in roles:
            cursor.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
            role_row = cursor.fetchone()
            if role_row:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    (user_id, role_row["id"])
                )
    
    conn.commit()
    conn.close()
    return user_id


def update_user(
    user_id: int,
    display_name: Optional[str] = None,
    is_active: Optional[bool] = None,
    is_agent: Optional[bool] = None,
    folder_path: Optional[str] = None
) -> bool:
    """Update user details"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    updates = []
    params = []
    
    if display_name is not None:
        updates.append("display_name = ?")
        params.append(display_name)
    
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(int(is_active))
    
    if is_agent is not None:
        updates.append("is_agent = ?")
        params.append(int(is_agent))
    
    if updates:
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(user_id)
        
        cursor.execute(
            f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()
    
    conn.close()
    return True


def set_user_password(user_id: int, new_password: str, folder_path: Optional[str] = None) -> bool:
    """Set a user's password"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    password_hash = hash_password(new_password)
    now = datetime.now().isoformat()
    
    cursor.execute("""
        UPDATE users 
        SET password_hash = ?, force_password_change = 0, updated_at = ?
        WHERE id = ?
    """, (password_hash, now, user_id))
    
    conn.commit()
    conn.close()
    return True


def set_user_active(user_id: int, is_active: bool, folder_path: Optional[str] = None) -> bool:
    """Enable or disable a user"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?
    """, (int(is_active), datetime.now().isoformat(), user_id))
    
    conn.commit()
    conn.close()
    return True


def delete_user(user_id: int, folder_path: Optional[str] = None) -> bool:
    """Delete a user (use with caution - prefer deactivating)"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return True


def update_last_login(user_id: int, folder_path: Optional[str] = None) -> None:
    """Update user's last login timestamp"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?",
        (datetime.now().isoformat(), user_id)
    )
    conn.commit()
    conn.close()


# =============================================================================
# Role Management
# =============================================================================

def get_all_roles(folder_path: Optional[str] = None) -> List[Dict]:
    """Get all roles"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM roles ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_roles(user_id: int, folder_path: Optional[str] = None) -> List[str]:
    """Get role names for a user"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.name FROM roles r
        JOIN user_roles ur ON ur.role_id = r.id
        WHERE ur.user_id = ?
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [r["name"] for r in rows]


def set_user_roles(user_id: int, role_names: List[str], folder_path: Optional[str] = None) -> bool:
    """Set all roles for a user (replaces existing)"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    # Clear existing roles
    cursor.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    
    # Add new roles
    for role_name in role_names:
        cursor.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
        role_row = cursor.fetchone()
        if role_row:
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user_id, role_row["id"])
            )
    
    conn.commit()
    conn.close()
    return True


# =============================================================================
# Permission Resolution
# =============================================================================

def get_all_permissions(folder_path: Optional[str] = None) -> List[str]:
    """Get all permission names"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM permissions ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_role_permissions(role_name: str, folder_path: Optional[str] = None) -> List[str]:
    """Get permission names for a role"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.name FROM permissions p
        JOIN role_permissions rp ON rp.perm_id = p.id
        JOIN roles r ON r.id = rp.role_id
        WHERE r.name = ?
    """, (role_name,))
    rows = cursor.fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_user_effective_permissions(user_id: int, folder_path: Optional[str] = None) -> Set[str]:
    """
    Get effective permissions for a user.
    Combines all role permissions + per-user overrides.
    """
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    # Get permissions from all user's roles
    cursor.execute("""
        SELECT DISTINCT p.name FROM permissions p
        JOIN role_permissions rp ON rp.perm_id = p.id
        JOIN user_roles ur ON ur.role_id = rp.role_id
        WHERE ur.user_id = ?
    """, (user_id,))
    role_perms = {r["name"] for r in cursor.fetchall()}
    
    # Get per-user permission overrides
    cursor.execute("""
        SELECT p.name, up.allowed FROM permissions p
        JOIN user_permissions up ON up.perm_id = p.id
        WHERE up.user_id = ?
    """, (user_id,))
    
    effective_perms = role_perms.copy()
    for row in cursor.fetchall():
        perm_name = row["name"]
        allowed = row["allowed"]
        if allowed:
            effective_perms.add(perm_name)
        else:
            effective_perms.discard(perm_name)
    
    conn.close()
    return effective_perms


def set_user_permission_override(
    user_id: int,
    permission_name: str,
    allowed: bool,
    folder_path: Optional[str] = None
) -> bool:
    """Set a per-user permission override"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM permissions WHERE name = ?", (permission_name,))
    perm_row = cursor.fetchone()
    if not perm_row:
        conn.close()
        return False
    
    cursor.execute("""
        INSERT OR REPLACE INTO user_permissions (user_id, perm_id, allowed)
        VALUES (?, ?, ?)
    """, (user_id, perm_row["id"], int(allowed)))
    
    conn.commit()
    conn.close()
    return True


def clear_user_permission_override(
    user_id: int,
    permission_name: str,
    folder_path: Optional[str] = None
) -> bool:
    """Remove a per-user permission override"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM permissions WHERE name = ?", (permission_name,))
    perm_row = cursor.fetchone()
    if perm_row:
        cursor.execute(
            "DELETE FROM user_permissions WHERE user_id = ? AND perm_id = ?",
            (user_id, perm_row["id"])
        )
        conn.commit()
    
    conn.close()
    return True


# =============================================================================
# Audit Logging
# =============================================================================

def log_audit(
    user_id: Optional[int],
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[str] = None,
    username: Optional[str] = None,
    folder_path: Optional[str] = None
) -> int:
    """Log an auditable action"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO audit_log (at, user_id, username, action, entity_type, entity_id, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        user_id,
        username,
        action,
        entity_type,
        str(entity_id) if entity_id is not None else None,
        details
    ))
    
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def get_audit_logs(
    limit: int = 100,
    user_id: Optional[int] = None,
    action_filter: Optional[str] = None,
    folder_path: Optional[str] = None
) -> List[Dict]:
    """Get audit log entries"""
    conn = get_connection(folder_path)
    cursor = conn.cursor()
    
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    
    if action_filter:
        query += " AND action LIKE ?"
        params.append(f"%{action_filter}%")
    
    query += " ORDER BY at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# =============================================================================
# Initialization Helper
# =============================================================================

def initialize_auth_system(folder_path: Optional[str] = None) -> bool:
    """
    Initialize the complete auth system.
    Call this on app startup.
    Returns True if a new admin was created (first run).
    """
    init_users_db(folder_path)
    seed_default_roles_and_permissions(folder_path)
    return ensure_admin_user(folder_path)
