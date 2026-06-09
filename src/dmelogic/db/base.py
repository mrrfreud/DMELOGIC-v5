import os
import sqlite3
import shutil
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable

from dmelogic.config import _default_db_folder  # from your extracted config.py
from dmelogic.settings import load_settings
from dmelogic.config import debug_log  # optional, for logging


# ----------------------------------------------------------
# WRITE GATE - Global backup mode management
# ----------------------------------------------------------

_DB_WRITE_LOCK = threading.RLock()
_WRITES_BLOCKED = threading.Event()
_BACKUP_MODE_REASON: str = ""
_BACKUP_MODE_CALLBACKS: List[Callable[[bool, str], None]] = []


class WritesBlockedError(RuntimeError):
    """Raised when a write is attempted while writes are globally blocked."""


def register_backup_mode_callback(callback: Callable[[bool, str], None]) -> None:
    """Register a callback to be notified when backup mode changes.
    
    Callback signature: callback(is_backup_mode: bool, reason: str)
    Called when entering or exiting backup mode.
    """
    if callback not in _BACKUP_MODE_CALLBACKS:
        _BACKUP_MODE_CALLBACKS.append(callback)


def unregister_backup_mode_callback(callback: Callable[[bool, str], None]) -> None:
    """Remove a previously registered backup mode callback."""
    if callback in _BACKUP_MODE_CALLBACKS:
        _BACKUP_MODE_CALLBACKS.remove(callback)


def _notify_backup_mode_change(entering: bool, reason: str) -> None:
    """Notify all registered callbacks about backup mode change."""
    for cb in _BACKUP_MODE_CALLBACKS:
        try:
            cb(entering, reason)
        except Exception as e:
            debug_log(f"Backup mode callback error: {e}")


def enter_backup_mode(reason: str = "Backup in progress") -> None:
    """Enter backup mode - blocks all database writes.
    
    This should be called before starting any backup or restore operation.
    All registered callbacks will be notified (e.g., to show UI banner).
    """
    global _BACKUP_MODE_REASON
    _BACKUP_MODE_REASON = reason
    _WRITES_BLOCKED.set()
    debug_log(f"DB: entering backup mode: {reason}")
    _notify_backup_mode_change(True, reason)


def exit_backup_mode() -> None:
    """Exit backup mode - allows database writes again.
    
    This should always be called in a finally block after backup operations.
    """
    global _BACKUP_MODE_REASON
    _WRITES_BLOCKED.clear()
    reason = _BACKUP_MODE_REASON
    _BACKUP_MODE_REASON = ""
    debug_log("DB: exiting backup mode")
    _notify_backup_mode_change(False, reason)


def is_backup_mode() -> bool:
    """Check if the application is currently in backup mode."""
    return _WRITES_BLOCKED.is_set()


def get_backup_mode_reason() -> str:
    """Get the reason for current backup mode, or empty string if not in backup mode."""
    return _BACKUP_MODE_REASON if _WRITES_BLOCKED.is_set() else ""


# Legacy aliases for backward compatibility
def block_writes(reason: str = "") -> None:
    """Prevent new write operations while a maintenance task runs.
    
    Deprecated: Use enter_backup_mode() instead for better UI integration.
    """
    enter_backup_mode(reason or "Maintenance in progress")


def unblock_writes() -> None:
    """Re-allow writes after maintenance completes.
    
    Deprecated: Use exit_backup_mode() instead for better UI integration.
    """
    exit_backup_mode()


def ensure_writes_allowed() -> None:
    """Raise WritesBlockedError if a write is attempted while blocked.
    
    Call this at the start of any write operation (create, update, delete).
    """
    if _WRITES_BLOCKED.is_set():
        reason = _BACKUP_MODE_REASON or "backup or maintenance in progress"
        raise WritesBlockedError(
            f"Database writes are temporarily disabled ({reason})."
        )


class WriteLock:
    """Process-wide mutex for coordinating writes with backups/restores."""

    def __enter__(self):  # type: ignore[override]
        _DB_WRITE_LOCK.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[override]
        _DB_WRITE_LOCK.release()
        # Do not suppress exceptions
        return False


def _load_db_folder_preferred() -> Optional[str]:
    """
    Best-effort: resolve the preferred DB folder.

    Priority:
      1) dmelogic.paths.db_dir() (respects installed data_path.txt)
      2) settings.json db_folder
      3) config._default_db_folder()
    """
    # 1) Centralized path resolver (handles installed data_path.txt)
    try:
        from dmelogic.paths import db_dir
        p = db_dir()
        os.makedirs(str(p), exist_ok=True)
        return str(p)
    except Exception as e:
        debug_log(f"DB: failed to read preferred db_dir(): {e}")

    # 2) Settings
    try:
        db_folder = _load_db_folder_from_settings()
        if db_folder:
            return db_folder
    except Exception:
        pass

    # 3) Default
    try:
        return _default_db_folder()
    except Exception:
        return None


def _load_db_folder_from_settings() -> Optional[str]:
    """Read db_folder from settings.json (if present)."""
    try:
        settings = load_settings()
        db_folder = settings.get("db_folder")
        if db_folder and isinstance(db_folder, str) and db_folder.strip():
            os.makedirs(db_folder, exist_ok=True)
            return db_folder
    except Exception as e:
        debug_log(f"DB: failed to read db_folder from settings: {e}")
    return None


def find_existing_db(filename: str, folder_path: Optional[str] = None) -> Optional[str]:
    """
    Search common locations for an existing database file and return the largest one.

    This mirrors your original logic:
      - App root (next to app.py)
      - Current folder_path (user's fax folder) if provided
      - db_folder from settings.json
      - Default DME data folder (C:\\Users\\pharmacy\\Documents\\DmeSolutionsV1\\Data)
      - Parent of folder_path (if provided)
      - Current working directory
    """
    try:
        paths: list[str] = []

        app_root = os.path.dirname(os.path.abspath(__file__))  # dmelogic/db
        app_root = os.path.dirname(app_root)                   # dmelogic/
        app_root = os.path.dirname(app_root)                   # project root

        base_folder = folder_path or app_root
        preferred_db_folder = _load_db_folder_preferred()
        db_folder = _load_db_folder_from_settings()

        paths.append(os.path.join(app_root, filename))
        paths.append(os.path.join(base_folder, filename))

        if preferred_db_folder:
            paths.append(os.path.join(preferred_db_folder, filename))

        if db_folder:
            paths.append(os.path.join(db_folder, filename))

        # Canonical data root (C:\ProgramData\DMELogic\Databases by default,
        # or whatever data_root/db_folder resolves to). This is authoritative.
        try:
            from dmelogic.paths import db_dir
            paths.append(os.path.join(str(db_dir()), filename))
        except Exception:
            pass

        # Legacy ProgramData layout from earlier installs.
        programdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        for legacy_sub in ("DMELogic\\Databases", "DMELogic\\Data"):
            legacy_dir = os.path.join(programdata, legacy_sub)
            if os.path.isdir(legacy_dir):
                paths.append(os.path.join(legacy_dir, filename))

        try:
            parent = os.path.dirname(base_folder)
            paths.append(os.path.join(parent, filename))
        except Exception:
            pass

        paths.append(os.path.join(os.getcwd(), filename))

        existing = [(p, os.path.getsize(p)) for p in paths if os.path.exists(p)]
        if existing:
            existing.sort(key=lambda t: t[1], reverse=True)
            return existing[0][0]
        return None
    except Exception as e:
        debug_log(f"DB: find_existing_db failed for {filename}: {e}")
        return None


def resolve_db_path(filename: str, folder_path: Optional[str] = None) -> str:
    """
    Resolve a stable path for a DB file.
    
    Priority (AUTHORITATIVE):
    0. For CORE databases (patients, orders, prescribers, inventory, etc.),
       ALWAYS use the configured db_folder - ignore folder_path
    1. For folder-specific databases (document_data.db), use folder_path if provided
    2. If settings has db_folder AND the DB exists there -> use it (no searching)
    3. If settings has db_folder but DB doesn't exist -> copy from elsewhere if found
    4. If no settings db_folder -> use largest existing DB or create in default location
    
    Once db_folder is configured, it is the AUTHORITATIVE source.
    We do NOT override it with "larger" databases found elsewhere.
    """
    # Safety: ensure filename ends with .db
    if not filename.endswith(".db"):
        filename = filename + ".db"
    
    # Core databases that should ALWAYS use db_folder, not folder_path
    # These contain shared data across all fax folders
    CORE_DATABASES = {
        "patients.db", "orders.db", "prescribers.db", "inventory.db",
        "billing.db", "clinics.db", "insurance_names.db", "suppliers.db",
        "users.db", "communications.db"
    }
    
    # If this is a core database, ignore folder_path and use db_folder
    if filename in CORE_DATABASES:
        folder_path = None
    
    try:
        app_root = os.path.dirname(os.path.abspath(__file__))
        app_root = os.path.dirname(app_root)  # dmelogic/
        app_root = os.path.dirname(app_root)  # project root

        # If folder_path is explicitly provided (and not a core DB), use it directly.
        # This is for folder-specific databases like document_data.db
        if folder_path:
            try:
                os.makedirs(folder_path, exist_ok=True)
            except Exception:
                pass
            dest_path = os.path.join(folder_path, filename)
            debug_log(f"DB: Using explicit folder_path: {dest_path}")
            return dest_path

        # Canonical data root: db_dir() (or an explicitly configured db_folder).
        #
        # v5: NO cross-folder discovery or seeding. The previous behavior
        # searched other locations and COPIED a found database into the active
        # data root — which leaked a separate (e.g. live) install's data into a
        # fresh one. Databases are created empty in the data root on first use;
        # migrating real data is explicit (configure db_folder, or CSV import).
        target_folder = _load_db_folder_preferred() or app_root
        try:
            os.makedirs(target_folder, exist_ok=True)
        except Exception:
            pass
        return os.path.join(target_folder, filename)

    except Exception as e:
        debug_log(f"DB: resolve_db_path fatal for {filename}: {e}")
        # Fallback to folder_path on error
        try:
            base = folder_path or "."
            return os.path.join(base, filename)
        except Exception:
            return filename


def row_to_dict(row: Optional[sqlite3.Row]) -> Dict[str, Any]:
    """
    Safely convert a sqlite3.Row to a dict.
    
    sqlite3.Row objects don't have a .get() method, which can cause bugs
    when code treats them like dicts. This helper ensures safe conversion.
    
    Args:
        row: sqlite3.Row object or None
    
    Returns:
        dict: Dictionary with column names as keys, or empty dict if row is None
    
    Example:
        cursor.execute("SELECT * FROM patients WHERE id = ?", (1,))
        row = cursor.fetchone()
        patient_dict = row_to_dict(row)
        name = patient_dict.get('name', 'Unknown')  # Safe!
    """
    if row is None:
        return {}
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[Dict[str, Any]]:
    """
    Convert a list of sqlite3.Row objects to list of dicts.
    
    Args:
        rows: List of sqlite3.Row objects
    
    Returns:
        list: List of dictionaries
    """
    return [dict(row) for row in rows]


def get_connection(filename: str, folder_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Open a sqlite3 connection for the given DB filename using resolve_db_path.
    This is the main entry point UI code should use from now on.
    
    Sets important PRAGMAs for:
    - foreign_keys: Enforce referential integrity
    - journal_mode=WAL: Write-Ahead Logging for better concurrency (multi-window safety)
    - synchronous=NORMAL: Balance between safety and performance
    """
    db_path = resolve_db_path(filename, folder_path=folder_path)
    conn = sqlite3.connect(db_path, timeout=10)
    
    # Enable foreign key constraints for data integrity
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Use WAL mode for better concurrency - allows multiple readers while writing
    # This is crucial for multi-user and multi-window operation
    conn.execute("PRAGMA journal_mode = WAL;")
    
    # Wait up to 5 seconds if another process holds a write lock
    conn.execute("PRAGMA busy_timeout = 5000;")
    
    # NORMAL synchronous mode: fsync only at critical moments (checkpoint)
    # Balances performance and safety - much faster than FULL, still safe
    conn.execute("PRAGMA synchronous = NORMAL;")
    
    return conn


class UnitOfWork:
    """
    Lightweight unit-of-work for coordinating multiple sqlite DBs.

    - Lazily opens connections keyed by filename.
    - Reuses connections within the lifetime of the UoW.
    - Allows a single commit()/rollback() decision at the end.
    - NOTE: sqlite cannot guarantee *true* atomicity across multiple .db files.
            This is a coordination helper, not a distributed transaction.
    
    Usage:
        with UnitOfWork(folder_path) as uow:
            orders_conn = uow.connection("orders.db")
            inv_conn = uow.connection("inventory.db")
            # ... do work with both connections ...
            # On success: auto-commits both
            # On exception: auto-rolls back both
    """

    def __init__(self, folder_path: Optional[str] = None):
        self.folder_path = folder_path
        self._conns: Dict[str, sqlite3.Connection] = {}
        self._committed = False
        self._closed = False

    def connection(self, filename: str) -> sqlite3.Connection:
        """
        Get or create a connection for a given DB filename.
        Use this instead of get_connection() inside UoW-aware code.
        """
        if self._closed:
            raise RuntimeError("UnitOfWork is already closed")

        if filename not in self._conns:
            db_path = resolve_db_path(filename, folder_path=self.folder_path)
            conn = sqlite3.connect(db_path, timeout=10)
            # Apply same PRAGMAs as get_connection for consistency
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            self._conns[filename] = conn

        return self._conns[filename]

    def commit(self):
        """Commit all open connections."""
        if self._closed:
            return
        # Honor global write gate and serialize commits with maintenance tasks
        ensure_writes_allowed()
        with _DB_WRITE_LOCK:
            for conn in self._conns.values():
                try:
                    conn.commit()
                except Exception as e:
                    debug_log(f"UoW commit error: {e}")
                    # best-effort; caller may decide to rollback
        self._committed = True

    def rollback(self):
        """Rollback all open connections."""
        if self._closed:
            return
        for conn in self._conns.values():
            try:
                conn.rollback()
            except Exception as e:
                debug_log(f"UoW rollback error: {e}")

    def close(self):
        """Close all connections."""
        if self._closed:
            return
        for conn in self._conns.values():
            try:
                conn.close()
            except Exception as e:
                debug_log(f"UoW close error: {e}")
        self._conns.clear()
        self._closed = True

    # Context manager protocol
    def __enter__(self) -> "UnitOfWork":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # On exception -> rollback, else commit if not already done
        try:
            if exc_type is not None:
                self.rollback()
            elif not self._committed:
                self.commit()
        finally:
            self.close()
        # Do not suppress exceptions
        return False


# ============================================================================
# Schema Migration System
# ============================================================================

def init_schema_version_table(conn: sqlite3.Connection) -> None:
    """
    Initialize the schema_version table if it doesn't exist.
    
    This table tracks which migrations have been applied.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def get_current_schema_version(conn: sqlite3.Connection) -> int:
    """
    Get the current schema version (highest version applied).
    
    Returns 0 if no migrations have been applied yet.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def record_migration(conn: sqlite3.Connection, version: int, description: str) -> None:
    """
    Record that a migration has been applied.
    
    Args:
        conn: Database connection
        version: Migration version number
        description: Migration description
    """
    conn.execute(
        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
        (version, description)
    )
    conn.commit()


class Migration:
    """
    Base class for database migrations.
    
    Subclass this and implement the up() method.
    
    Example:
        class Migration001_AddPatientEmail(Migration):
            version = 1
            description = "Add email column to patients table"
            
            def up(self, conn: sqlite3.Connection) -> None:
                conn.execute("ALTER TABLE patients ADD COLUMN email TEXT")
    """
    
    version: int = 0
    description: str = ""
    
    def up(self, conn: sqlite3.Connection) -> None:
        """
        Apply the migration.
        
        Args:
            conn: Database connection
        """
        raise NotImplementedError("Migration must implement up()")
    
    def down(self, conn: sqlite3.Connection) -> None:
        """
        Rollback the migration (optional, not all migrations are reversible).
        
        Args:
            conn: Database connection
        """
        raise NotImplementedError("Migration rollback not implemented")


def run_migrations(
    db_filename: str,
    migrations: list[Migration],
    folder_path: Optional[str] = None
) -> int:
    """
    Run pending migrations for a database.
    
    Args:
        db_filename: Database filename (e.g., "patients.db")
        migrations: List of Migration instances, sorted by version
        folder_path: Optional database folder path
        
    Returns:
        int: Number of migrations applied
        
    Example:
        migrations = [
            Migration001_AddPatientEmail(),
            Migration002_AddPatientPhone(),
        ]
        count = run_migrations("patients.db", migrations)
        print(f"Applied {count} migrations")
    """
    conn = get_connection(db_filename, folder_path=folder_path)
    
    try:
        # Log DB file location (helps diagnose wrong-DB issues in installed builds)
        try:
            db_list = conn.execute("PRAGMA database_list").fetchall()
            if db_list:
                # rows: (seq, name, file)
                debug_log(f"DB {db_filename}: Using file: {db_list[0][2]}")
        except Exception:
            pass

        # Initialize schema version table
        init_schema_version_table(conn)
        
        # Get current version
        current_version = get_current_schema_version(conn)
        debug_log(f"DB {db_filename}: Current schema version: {current_version}")
        
        # Sort migrations by version
        migrations_sorted = sorted(migrations, key=lambda m: m.version)
        
        # Apply pending migrations
        applied_count = 0
        for migration in migrations_sorted:
            if migration.version <= current_version:
                # Already applied
                continue
            
            debug_log(f"DB {db_filename}: Applying migration {migration.version}: {migration.description}")
            
            try:
                # Run migration
                migration.up(conn)
                
                # Record migration
                record_migration(conn, migration.version, migration.description)
                
                applied_count += 1
                debug_log(f"DB {db_filename}: Migration {migration.version} applied successfully")
                
            except Exception as e:
                debug_log(f"DB {db_filename}: Migration {migration.version} FAILED: {e}")
                conn.rollback()
                raise RuntimeError(
                    f"Migration {migration.version} failed: {e}\n"
                    f"Database is at version {current_version}. "
                    f"Please fix the migration and try again."
                ) from e
        
        if applied_count == 0:
            debug_log(f"DB {db_filename}: No pending migrations")
        else:
            debug_log(f"DB {db_filename}: Applied {applied_count} migrations successfully")
        
        return applied_count
        
    finally:
        conn.close()


def get_migration_history(
    db_filename: str,
    folder_path: Optional[str] = None
) -> list[tuple[int, str, str]]:
    """
    Get migration history for a database.
    
    Args:
        db_filename: Database filename
        folder_path: Optional database folder path
        
    Returns:
        List of (version, description, applied_at) tuples
    """
    conn = get_connection(db_filename, folder_path=folder_path)
    
    try:
        init_schema_version_table(conn)
        
        cursor = conn.cursor()
        cursor.execute("""
            SELECT version, description, applied_at
            FROM schema_version
            ORDER BY version ASC
        """)
        
        return cursor.fetchall()
        
    finally:
        conn.close()


