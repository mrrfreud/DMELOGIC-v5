"""
backup.py — Backup + restore functionality for database and settings.

Features:
- Daily and Weekly automatic backup schedules
- Separate Daily/ and Weekly/ folders with per-type retention
- SHA256 checksums in manifest for integrity verification
- WAL-safe SQLite snapshots using online backup API
- Two-phase restore with integrity validation
- Integration with write gate for blocking writes during backup
"""

import os
import json
import zipfile
import shutil
import tempfile
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from PyQt6.QtCore import QObject, QThread, pyqtSignal, QDateTime, QTimer
from .config import SETTINGS_FILE, DB_FILES, DB_EXCLUDE, debug_log
from .version import APP_VERSION
from .paths import backup_dir, db_dir
from .db.base import enter_backup_mode, exit_backup_mode


# ----------------------------------------------------------
# CONSTANTS
# ----------------------------------------------------------

BACKUP_TYPE = Literal["daily", "weekly", "manual"]
DEFAULT_DAILY_RETENTION = 7
DEFAULT_WEEKLY_RETENTION = 7

# OCR Folder Backup Constants
from .paths import ocr_folder as _ocr_folder_fn
OCR_SOURCE_FOLDER = None  # Resolved lazily via ocr_folder()
OCR_BACKUP_DEST = Path(r"C:\Users\pharmacy\OneDrive - 1st Aid Pharmacy\DME Solutions\OCDRd_FOLDER_BACKUP")
OCR_KEEP_LAST = 7


def _get_ocr_source() -> Path:
    """Get OCR source folder (lazy, so settings are loaded first)."""
    return _ocr_folder_fn()


# ----------------------------------------------------------
# UTILITY FUNCTIONS
# ----------------------------------------------------------

def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def snapshot_sqlite_db(src_db: Path, dst_db: Path) -> None:
    """Create a WAL-safe snapshot of a SQLite database.

    Uses the sqlite3 online backup API so the snapshot is
    consistent even when the source database is using WAL.
    """
    dst_db.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(str(src_db))
    try:
        # Reduce chance of "database is locked" during busy UI moments
        try:
            src.execute("PRAGMA busy_timeout=5000;")
        except Exception:
            pass

        dst = sqlite3.connect(str(dst_db))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def integrity_check(db_path: Path) -> tuple[bool, str]:
    """Run PRAGMA integrity_check on a SQLite file.

    Returns (ok, message). If ok is False, message contains
    the failure reason for logging / diagnostics.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            row = cur.fetchone()
        finally:
            conn.close()
        msg = (row[0] if row else "") or ""
        return (msg.lower() == "ok"), msg
    except Exception as e:
        return False, str(e)


def enforce_retention(bdir: Path, keep_last: int = 7, pattern: str = "dmesolutions_backup_*.zip") -> None:
    """Keep only the most recent N backup zips in a folder.

    Older backups beyond keep_last are deleted on a best-effort
    basis; failures are logged but do not abort the backup.
    
    Args:
        bdir: Backup directory path
        keep_last: Number of backups to keep (default 7)
        pattern: Glob pattern for backup files
    """
    try:
        backups = sorted(
            bdir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[keep_last:]:
            try:
                old.unlink()
                debug_log(f"Retention: deleted old backup {old}")
            except Exception as e:
                debug_log(f"Retention: failed to delete {old}: {e}")
    except Exception as e:
        debug_log(f"Retention: enforcement failed in {bdir}: {e}")


def enforce_retention_glob(dest_folder: Path, pattern: str, keep_last: int) -> None:
    """Keep only the most recent N files matching a glob pattern.
    
    Args:
        dest_folder: Folder to check
        pattern: Glob pattern (e.g., "OCR_DAILY_*.zip")
        keep_last: Number of files to keep
    """
    try:
        if not dest_folder.exists():
            return
        
        files = sorted(
            dest_folder.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[keep_last:]:
            try:
                old.unlink()
                debug_log(f"OCR Retention: deleted old backup {old.name}")
            except Exception as e:
                debug_log(f"OCR Retention: failed to delete {old.name}: {e}")
    except Exception as e:
        debug_log(f"OCR Retention: enforcement failed: {e}")


def backup_folder_to_zip(
    src_folder: Path,
    dest_folder: Path,
    prefix: str = "OCR_DAILY",
    keep_last: int = OCR_KEEP_LAST,
) -> Optional[Path]:
    """Create a ZIP backup of an entire folder with OneDrive-safe atomic write.
    
    Args:
        src_folder: Source folder to backup
        dest_folder: Destination folder for the ZIP
        prefix: Filename prefix (e.g., "OCR_DAILY")
        keep_last: Number of backups to retain
        
    Returns:
        Path to the created ZIP file, or None on failure
        
    Features:
        - Writes to a .tmp file first, then atomically renames (OneDrive-safe)
        - Preserves relative paths inside the ZIP
        - Applies retention policy after successful backup
    """
    try:
        if not src_folder.exists():
            debug_log(f"OCR Backup: source folder does not exist: {src_folder}")
            return None
        
        # Ensure destination exists
        dest_folder.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"{prefix}_{timestamp}.zip"
        final_path = dest_folder / final_name
        tmp_path = dest_folder / f".tmp_{final_name}"
        
        debug_log(f"OCR Backup: starting backup of {src_folder}")
        
        # Create ZIP in temp file first (OneDrive-safe)
        try:
            with zipfile.ZipFile(str(tmp_path), "w", compression=zipfile.ZIP_DEFLATED) as z:
                file_count = 0
                for root, dirs, files in os.walk(str(src_folder)):
                    for file in files:
                        abs_path = Path(root) / file
                        # Compute relative path from source folder
                        rel_path = abs_path.relative_to(src_folder)
                        try:
                            z.write(str(abs_path), str(rel_path))
                            file_count += 1
                        except Exception as e:
                            debug_log(f"OCR Backup: failed to add {rel_path}: {e}")
                
                debug_log(f"OCR Backup: added {file_count} files to archive")
        except Exception as e:
            debug_log(f"OCR Backup: failed to create ZIP: {e}")
            # Clean up temp file
            try:
                tmp_path.unlink()
            except Exception:
                pass
            return None
        
        # Atomic rename (OneDrive-safe)
        try:
            os.replace(str(tmp_path), str(final_path))
            debug_log(f"OCR Backup: successfully created {final_path.name}")
        except Exception as e:
            debug_log(f"OCR Backup: atomic rename failed: {e}")
            try:
                tmp_path.unlink()
            except Exception:
                pass
            return None
        
        # Enforce retention
        pattern = f"{prefix}_*.zip"
        enforce_retention_glob(dest_folder, pattern, keep_last)
        
        return final_path
        
    except Exception as e:
        debug_log(f"OCR Backup: unexpected error: {e}")
        return None


def discover_databases(folder_path: Path, exclude_list=None) -> list:
    """
    Auto-discover all .db files in the specified folder.
    
    Args:
        folder_path: Path to search for databases
        exclude_list: List of database filenames to exclude (e.g., temp.db)
    
    Returns:
        List of database filenames found
    """
    if exclude_list is None:
        exclude_list = DB_EXCLUDE
    
    if not folder_path.exists():
        return []
    
    discovered = []
    for item in folder_path.iterdir():
        if item.is_file() and item.suffix == ".db":
            if item.name not in exclude_list:
                discovered.append(item.name)
    
    return sorted(discovered)


class BackupWorker(QObject):
    """Worker for creating and restoring backups in a background thread.
    
    Features:
    - backup_type: "daily", "weekly", or "manual" for proper labeling
    - Folder separation: Daily/ and Weekly/ subdirectories
    - SHA256 checksums in manifest
    - Per-type retention policies
    - Write gate integration (blocks writes during backup)
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        mode: str,
        source_path: str = None,
        backup_path: str = None,
        auto_discover: bool = False,
        backup_type: BACKUP_TYPE = "manual",
        daily_retention: int = DEFAULT_DAILY_RETENTION,
        weekly_retention: int = DEFAULT_WEEKLY_RETENTION,
    ):
        super().__init__()
        self.mode = mode
        self.source_path = source_path
        self.backup_path = backup_path
        self.auto_discover = auto_discover
        self.backup_type = backup_type
        self.daily_retention = daily_retention
        self.weekly_retention = weekly_retention

    # ----------------------------------------------------------
    # MAIN RUN
    # ----------------------------------------------------------
    def run(self):
        try:
            if self.mode == "backup":
                # Enter backup mode to block writes
                enter_backup_mode(f"Creating {self.backup_type} backup")
                try:
                    result = self.create_backup()
                    self.finished.emit(result)
                finally:
                    exit_backup_mode()

            elif self.mode == "restore":
                # Enter backup mode during restore as well
                enter_backup_mode("Restoring backup")
                try:
                    self.restore_backup()
                    self.finished.emit("Restore complete.")
                finally:
                    exit_backup_mode()

            else:
                self.error.emit(f"[BackupWorker] Unknown mode: {self.mode}")

        except Exception as e:
            # Ensure backup mode is exited on error
            try:
                exit_backup_mode()
            except Exception:
                pass
            self.error.emit(str(e))

    # ----------------------------------------------------------
    # GET BACKUP DIRECTORY FOR TYPE
    # ----------------------------------------------------------
    def _get_backup_dir_for_type(self) -> Path:
        """Get the appropriate backup directory based on backup type."""
        base_dir = backup_dir()
        
        if self.backup_type == "daily":
            target_dir = base_dir / "Daily"
        elif self.backup_type == "weekly":
            target_dir = base_dir / "Weekly"
        else:
            # Manual backups go to root backup directory
            target_dir = base_dir
        
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    # ----------------------------------------------------------
    # GENERATE BACKUP FILENAME
    # ----------------------------------------------------------
    def _generate_backup_filename(self) -> str:
        """Generate a properly labeled backup filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Get app version, default to "0.0.0" if not available
        try:
            version = APP_VERSION.replace(".", "_")
        except Exception:
            version = "0_0_0"
        
        if self.backup_type == "daily":
            return f"dmesolutions_backup_DAILY_{timestamp}_v{version}.zip"
        elif self.backup_type == "weekly":
            # Include day of week for weekly backups
            day_of_week = datetime.now().strftime("%a").upper()
            return f"dmesolutions_backup_WEEKLY_{timestamp}_{day_of_week}_v{version}.zip"
        else:
            return f"dmesolutions_backup_MANUAL_{timestamp}_v{version}.zip"

    # ----------------------------------------------------------
    # CREATE BACKUP ZIP
    # ----------------------------------------------------------
    def create_backup(self) -> str:
        if not self.source_path:
            raise ValueError("BackupWorker: source_path not set.")

        bdir = self._get_backup_dir_for_type()
        backup_filename = self._generate_backup_filename()
        backup_zip = bdir / backup_filename

        # Get db_folder from centralized paths
        db_folder = db_dir()

        # Determine which databases to backup
        if self.auto_discover:
            discovered = discover_databases(db_folder, DB_EXCLUDE)
            db_names = list(set(DB_FILES + discovered))
            debug_log(f"Auto-discovered databases: {discovered}")
            debug_log(f"Total databases to backup: {len(db_names)}")
        else:
            db_names = DB_FILES

        search_paths = [Path(self.source_path), db_folder]

        def find_db(name):
            for base in search_paths:
                p = base / name
                if p.exists():
                    return p
            return None

        self.progress.emit(5)

        # Use a temp folder for WAL-safe snapshots and integrity checks
        tmp_root = Path(tempfile.mkdtemp(prefix="dmelogic_backup_"))
        try:
            snapshot_dir = tmp_root / "databases"
            snapshot_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(str(backup_zip), "w", compression=zipfile.ZIP_DEFLATED) as z:

                # Save settings.json
                settings_path = Path(SETTINGS_FILE)
                if settings_path.exists():
                    z.write(str(settings_path), "settings.json")
                    debug_log(f"Backup includes settings: {settings_path} -> settings.json")
                else:
                    debug_log(f"Backup: settings file not found at {settings_path}")

                # Enhanced manifest with checksums and backup type
                try:
                    version = APP_VERSION
                except Exception:
                    version = "unknown"
                    
                manifest: dict = {
                    "created_at": datetime.now().isoformat(),
                    "app_version": version,
                    "backup_type": self.backup_type,
                    "schedule": {
                        "frequency": self.backup_type,
                        "time": "04:00",
                        "timezone": "local",
                    },
                    "auto_discover": bool(self.auto_discover),
                    "databases": [],
                }
                
                # Add day for weekly backups
                if self.backup_type == "weekly":
                    manifest["schedule"]["day"] = datetime.now().strftime("%A")

                self.progress.emit(15)

                # Include DBs via snapshots with checksums
                step = 60 // max(len(db_names), 1)
                p = 15

                for db in db_names:
                    db_path = find_db(db)
                    if not db_path:
                        debug_log(f"Backup: DB not found: {db}")
                        p += step
                        self.progress.emit(min(p, 90))
                        continue

                    try:
                        snapshot_path = snapshot_dir / db
                        snapshot_sqlite_db(db_path, snapshot_path)
                        ok, msg = integrity_check(snapshot_path)
                        
                        if not ok:
                            debug_log(f"Backup: Integrity check failed for {db}: {msg} - SKIPPING")
                            p += step
                            self.progress.emit(min(p, 90))
                            continue
                        
                        # Compute checksum
                        checksum = compute_sha256(snapshot_path)
                        file_size = snapshot_path.stat().st_size
                        
                        z.write(str(snapshot_path), f"databases/{db}")
                        
                        # Add to manifest with metadata
                        manifest["databases"].append({
                            "name": db,
                            "size": file_size,
                            "sha256": checksum,
                            "integrity_check": "ok",
                        })
                        
                        debug_log(f"Backup: snapshotted {db_path} -> databases/{db} (sha256: {checksum[:16]}...)")
                    except Exception as e:
                        debug_log(f"Backup: failed to snapshot {db_path} : {e}")

                    p += step
                    self.progress.emit(min(p, 90))

                # Include fee schedule / extras from db_folder (if any)
                try:
                    for xlsx in db_folder.glob("*.xlsx"):
                        arcname = f"extras/fee_schedule/{xlsx.name}"
                        z.write(str(xlsx), arcname)
                        debug_log(f"Backup includes fee schedule: {xlsx} -> {arcname}")
                except Exception as e:
                    debug_log(f"Backup: failed to include fee schedule extras: {e}")

                # Write manifest last
                try:
                    z.writestr("manifest.json", json.dumps(manifest, indent=2))
                except Exception as e:
                    debug_log(f"Backup: failed to write manifest.json: {e}")

            self.progress.emit(100)
        finally:
            # Clean up snapshots
            try:
                shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass

        # Enforce retention per backup type
        if self.backup_type == "daily":
            enforce_retention(bdir, keep_last=self.daily_retention, pattern="dmesolutions_backup_DAILY_*.zip")
        elif self.backup_type == "weekly":
            enforce_retention(bdir, keep_last=self.weekly_retention, pattern="dmesolutions_backup_WEEKLY_*.zip")
        else:
            # Manual backups - apply retention to manual folder
            enforce_retention(bdir, keep_last=7, pattern="dmesolutions_backup_MANUAL_*.zip")
        
        # ----------------------------------------------------------
        # OCR FOLDER BACKUP (Daily scheduled backups only)
        # ----------------------------------------------------------
        # After the main DB backup succeeds, also backup the OCR folder
        # to OneDrive. This runs only for daily scheduled backups.
        if self.backup_type == "daily":
            try:
                debug_log("Starting OCR folder backup to OneDrive...")
                ocr_result = backup_folder_to_zip(
                    src_folder=_get_ocr_source(),
                    dest_folder=OCR_BACKUP_DEST,
                    prefix="OCR_DAILY",
                    keep_last=OCR_KEEP_LAST,
                )
                if ocr_result:
                    debug_log(f"OCR folder backup completed: {ocr_result.name}")
                else:
                    debug_log("OCR folder backup skipped or failed (non-fatal)")
            except Exception as e:
                # OCR backup failure should NOT crash the main backup
                debug_log(f"OCR folder backup error (non-fatal): {e}")
        
        return str(backup_zip)


    # ----------------------------------------------------------
    # RESTORE BACKUP ZIP (Two-Phase: Stage + Commit)
    # ----------------------------------------------------------
    def restore_backup(self):
        """
        Two-phase restore with integrity validation:
        
        Phase 1 (Stage): Extract all files to temp folder, validate manifest,
                        verify SHA256 checksums, run integrity_check on all DBs.
                        If ANY validation fails, abort without touching live files.
        
        Phase 2 (Commit): Create timestamped safety backup of current files,
                         then atomically replace all files using os.replace().
        
        This ensures we never corrupt the live database if the backup is bad.
        """
        if not self.backup_path or not os.path.exists(self.backup_path):
            raise ValueError("BackupWorker: backup_path invalid.")

        def _normalize_zip_name(name: str) -> str:
            return name.replace("\\", "/")

        def _find_settings_member(z: zipfile.ZipFile) -> str | None:
            candidates: list[str] = []
            for member in z.namelist():
                norm = _normalize_zip_name(member).lower()
                if norm == "settings.json" or norm.endswith("/settings.json"):
                    candidates.append(member)
            if not candidates:
                return None
            candidates.sort(key=lambda n: (len(_normalize_zip_name(n)), _normalize_zip_name(n).lower()))
            return candidates[0]

        def _safe_orders_count(path: Path) -> int:
            try:
                conn = sqlite3.connect(str(path))
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
                if not cur.fetchone():
                    conn.close()
                    return -1
                cur.execute("SELECT COUNT(*) FROM orders")
                count = int(cur.fetchone()[0])
                conn.close()
                return count
            except Exception:
                return -1

        # ======================================================
        # PHASE 1: STAGING + VALIDATION
        # ======================================================
        debug_log("Restore Phase 1: Staging and validation...")
        self.progress.emit(5)
        
        staging_dir = Path(tempfile.mkdtemp(prefix="dmelogic_restore_"))
        staged_settings: Optional[Path] = None
        staged_dbs: dict[str, Path] = {}  # filename -> staged path
        staged_xlsx: list[Path] = []
        manifest_data: Optional[dict] = None
        
        try:
            with zipfile.ZipFile(self.backup_path, "r") as z:
                # Extract and parse manifest
                if "manifest.json" in z.namelist():
                    manifest_raw = z.read("manifest.json").decode("utf-8")
                    manifest_data = json.loads(manifest_raw)
                    debug_log(f"Restore: manifest loaded, backup_type={manifest_data.get('backup_type', 'unknown')}")
                
                # Stage settings.json
                settings_member = _find_settings_member(z)
                if settings_member:
                    staged_settings = staging_dir / "settings.json"
                    with z.open(settings_member, "r") as src, open(staged_settings, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    debug_log(f"Staged settings.json")
                
                self.progress.emit(15)
                
                # Stage database files
                db_members = [
                    m for m in z.namelist()
                    if _normalize_zip_name(m).lower().startswith("databases/")
                    and _normalize_zip_name(m).lower().endswith(".db")
                ]
                
                step = 30 // max(len(db_members), 1)
                p = 15
                
                for member in db_members:
                    file_name = Path(_normalize_zip_name(member)).name
                    staged_path = staging_dir / file_name
                    
                    with z.open(member, "r") as src, open(staged_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    
                    staged_dbs[file_name] = staged_path
                    debug_log(f"Staged {file_name}")
                    
                    p += step
                    self.progress.emit(min(p, 45))
                
                # Stage Excel files
                xlsx_members = [
                    m for m in z.namelist()
                    if _normalize_zip_name(m).lower().startswith("extras/fee_schedule/")
                    and _normalize_zip_name(m).lower().endswith(".xlsx")
                ]
                for member in xlsx_members:
                    file_name = Path(_normalize_zip_name(member)).name
                    staged_path = staging_dir / file_name
                    with z.open(member, "r") as src, open(staged_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    staged_xlsx.append(staged_path)
            
            self.progress.emit(50)
            
            # ======================================================
            # VALIDATE STAGED FILES
            # ======================================================
            debug_log("Restore: Validating staged files...")
            validation_errors = []
            
            # Validate checksums if manifest has them
            if manifest_data and "databases" in manifest_data:
                for db_info in manifest_data["databases"]:
                    if isinstance(db_info, dict):
                        db_name = db_info.get("name")
                        expected_sha256 = db_info.get("sha256")
                        
                        if db_name and db_name in staged_dbs and expected_sha256:
                            actual_sha256 = compute_sha256(staged_dbs[db_name])
                            if actual_sha256 != expected_sha256:
                                validation_errors.append(
                                    f"Checksum mismatch for {db_name}: "
                                    f"expected {expected_sha256[:16]}..., got {actual_sha256[:16]}..."
                                )
                            else:
                                debug_log(f"Checksum OK for {db_name}")
            
            # Integrity check all staged DBs
            for db_name, staged_path in staged_dbs.items():
                ok, msg = integrity_check(staged_path)
                if not ok:
                    validation_errors.append(f"Integrity check failed for {db_name}: {msg}")
                else:
                    debug_log(f"Integrity OK for {db_name}")
            
            self.progress.emit(60)
            
            # ABORT if any validation failed
            if validation_errors:
                error_msg = "Restore aborted - validation failed:\n" + "\n".join(validation_errors)
                debug_log(error_msg)
                raise RuntimeError(error_msg)
            
            debug_log("Restore Phase 1 complete: All validations passed")
            
            # ======================================================
            # PHASE 2: COMMIT (Atomic Replace)
            # ======================================================
            debug_log("Restore Phase 2: Committing changes...")
            self.progress.emit(65)
            
            # Create timestamped safety backup folder
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safety_dir = backup_dir() / f"restore_safety_{timestamp}"
            safety_dir.mkdir(parents=True, exist_ok=True)
            debug_log(f"Safety backup folder: {safety_dir}")
            
            # Backup current settings
            if staged_settings and os.path.exists(SETTINGS_FILE):
                safety_settings = safety_dir / "settings.json"
                shutil.copy2(SETTINGS_FILE, safety_settings)
                debug_log(f"Safety backup: settings.json")
            
            # Backup current DBs
            db_folder = db_dir()
            db_folder.mkdir(parents=True, exist_ok=True)
            
            for db_name in staged_dbs.keys():
                current_db = db_folder / db_name
                if current_db.exists():
                    safety_db = safety_dir / db_name
                    shutil.copy2(current_db, safety_db)
                    debug_log(f"Safety backup: {db_name}")
            
            self.progress.emit(75)
            
            # Atomic replace settings.json
            if staged_settings:
                settings_dir = os.path.dirname(SETTINGS_FILE) or "."
                if not os.access(settings_dir, os.W_OK):
                    raise PermissionError(f"Cannot write to settings directory: {settings_dir}")
                os.replace(str(staged_settings), SETTINGS_FILE)
                debug_log("Committed settings.json")
                
                # Invalidate cache
                try:
                    from .settings import invalidate_settings_cache
                    invalidate_settings_cache()
                except Exception:
                    pass
            
            self.progress.emit(80)
            
            # Atomic replace DBs
            step = 15 // max(len(staged_dbs), 1)
            p = 80
            
            for db_name, staged_path in staged_dbs.items():
                target_db = db_folder / db_name
                os.replace(str(staged_path), str(target_db))
                debug_log(f"Committed {db_name}")
                p += step
                self.progress.emit(min(p, 95))
            
            # Replace Excel files
            for staged_path in staged_xlsx:
                target_xlsx = db_folder / staged_path.name
                os.replace(str(staged_path), str(target_xlsx))
                debug_log(f"Committed {staged_path.name}")
            
            # Final diagnostic
            try:
                orders_db = db_folder / "orders.db"
                count = _safe_orders_count(orders_db) if orders_db.exists() else -1
                debug_log(f"Post-restore: orders.db rows={count}")
            except Exception:
                pass
            
            self.progress.emit(100)
            debug_log(f"Restore complete. Safety backup at: {safety_dir}")
            
        finally:
            # Clean up staging directory
            try:
                shutil.rmtree(staging_dir, ignore_errors=True)
            except Exception:
                pass


class AutoBackupScheduler(QThread):
    """
    Single-frequency auto-backup scheduler.
    
    Calculates the exact next run time and sleeps until then.
    Emits backup_triggered signal with the backup_type when it's time.
    """
    backup_triggered = pyqtSignal(str)  # Emits backup_type: "daily" or "weekly"

    def __init__(self, frequency: str = "daily", backup_hour: int = 4, backup_minute: int = 0):
        super().__init__()
        self.frequency = frequency.lower()  # "daily" or "weekly"
        self.backup_hour = backup_hour
        self.backup_minute = backup_minute
        self.running = True
        self._last_backup_key: str = ""  # "daily_2026-01-09" format to prevent duplicates

    def run(self):
        """Main thread loop - sleeps until next backup time."""
        debug_log(f"AutoBackupScheduler started ({self.frequency}, {self.backup_hour:02d}:{self.backup_minute:02d})")
        
        while self.running:
            now = QDateTime.currentDateTime()
            next_run = self._calculate_next_run(now)
            
            if next_run is None:
                self.msleep(60000)
                continue
            
            ms_until_next = now.msecsTo(next_run)
            
            if ms_until_next <= 0:
                # Time to run backup
                current_key = f"{self.frequency}_{now.date().toString('yyyy-MM-dd')}"
                
                if self._last_backup_key != current_key:
                    debug_log(f"Triggering {self.frequency} backup at {now.toString('yyyy-MM-dd HH:mm:ss')}")
                    self.backup_triggered.emit(self.frequency)
                    self._last_backup_key = current_key
                    
                    # Sleep for 2 minutes to avoid immediate re-trigger
                    self.msleep(120000)
                else:
                    # Already ran today for this frequency, wait 1 hour
                    self.msleep(3600000)
            else:
                # Sleep until next run (max 1 hour chunks to allow stopping)
                sleep_time = min(ms_until_next, 3600000)
                debug_log(f"Next {self.frequency} backup in {ms_until_next // 60000} minutes")
                self.msleep(int(sleep_time))

    def _calculate_next_run(self, now: QDateTime) -> QDateTime:
        """Calculate the exact next backup time based on frequency."""
        from PyQt6.QtCore import QTime
        target_time = QTime(self.backup_hour, self.backup_minute, 0)
        
        if self.frequency == "daily":
            next_run = QDateTime(now.date(), target_time)
            if next_run <= now:
                next_run = next_run.addDays(1)
            return next_run
        
        elif self.frequency == "weekly":
            # Weekly: every Sunday (dayOfWeek 7) at target time
            next_run = QDateTime(now.date(), target_time)
            days_until_sunday = (7 - now.date().dayOfWeek()) % 7
            
            if days_until_sunday == 0 and next_run > now:
                return next_run
            elif days_until_sunday == 0:
                return next_run.addDays(7)
            else:
                return next_run.addDays(days_until_sunday)
        
        elif self.frequency == "monthly":
            next_run = QDateTime(now.date(), target_time)
            if now.date().day() == 1 and next_run > now:
                return next_run
            else:
                next_run = next_run.addMonths(1)
                next_date = next_run.date()
                next_date.setDate(next_date.year(), next_date.month(), 1)
                return QDateTime(next_date, target_time)
        
        return now.addSecs(86400)

    def stop(self):
        """Stop the scheduler gracefully."""
        debug_log(f"AutoBackupScheduler ({self.frequency}) stopping...")
        self.running = False
        self.quit()
        self.wait(5000)


class DualBackupScheduler(QObject):
    """
    Manages both daily and weekly backup schedules.
    
    This class coordinates two AutoBackupScheduler threads:
    - Daily: runs at 04:00 AM every day
    - Weekly: runs at 04:00 AM every Sunday
    
    Both can be enabled/disabled independently.
    Emits backup_requested signal when a backup should run.
    """
    backup_requested = pyqtSignal(str)  # Emits backup_type: "daily" or "weekly"
    
    def __init__(
        self,
        daily_enabled: bool = True,
        weekly_enabled: bool = True,
        backup_hour: int = 4,
        backup_minute: int = 0,
    ):
        super().__init__()
        self.daily_enabled = daily_enabled
        self.weekly_enabled = weekly_enabled
        self.backup_hour = backup_hour
        self.backup_minute = backup_minute
        
        self._daily_scheduler: Optional[AutoBackupScheduler] = None
        self._weekly_scheduler: Optional[AutoBackupScheduler] = None
        
    def start(self):
        """Start enabled backup schedulers."""
        if self.daily_enabled:
            self._daily_scheduler = AutoBackupScheduler(
                frequency="daily",
                backup_hour=self.backup_hour,
                backup_minute=self.backup_minute,
            )
            self._daily_scheduler.backup_triggered.connect(self._on_backup_triggered)
            self._daily_scheduler.start()
            debug_log("Daily backup scheduler started")
        
        if self.weekly_enabled:
            self._weekly_scheduler = AutoBackupScheduler(
                frequency="weekly",
                backup_hour=self.backup_hour,
                backup_minute=self.backup_minute,
            )
            self._weekly_scheduler.backup_triggered.connect(self._on_backup_triggered)
            self._weekly_scheduler.start()
            debug_log("Weekly backup scheduler started")
    
    def stop(self):
        """Stop all backup schedulers."""
        if self._daily_scheduler:
            self._daily_scheduler.stop()
            self._daily_scheduler = None
        
        if self._weekly_scheduler:
            self._weekly_scheduler.stop()
            self._weekly_scheduler = None
        
        debug_log("All backup schedulers stopped")
    
    def _on_backup_triggered(self, backup_type: str):
        """Forward backup trigger to external handler."""
        self.backup_requested.emit(backup_type)
    
    def set_daily_enabled(self, enabled: bool):
        """Enable or disable daily backups at runtime."""
        if enabled == self.daily_enabled:
            return
        
        self.daily_enabled = enabled
        if enabled and self._daily_scheduler is None:
            self._daily_scheduler = AutoBackupScheduler(
                frequency="daily",
                backup_hour=self.backup_hour,
                backup_minute=self.backup_minute,
            )
            self._daily_scheduler.backup_triggered.connect(self._on_backup_triggered)
            self._daily_scheduler.start()
        elif not enabled and self._daily_scheduler:
            self._daily_scheduler.stop()
            self._daily_scheduler = None
    
    def set_weekly_enabled(self, enabled: bool):
        """Enable or disable weekly backups at runtime."""
        if enabled == self.weekly_enabled:
            return
        
        self.weekly_enabled = enabled
        if enabled and self._weekly_scheduler is None:
            self._weekly_scheduler = AutoBackupScheduler(
                frequency="weekly",
                backup_hour=self.backup_hour,
                backup_minute=self.backup_minute,
            )
            self._weekly_scheduler.backup_triggered.connect(self._on_backup_triggered)
            self._weekly_scheduler.start()
        elif not enabled and self._weekly_scheduler:
            self._weekly_scheduler.stop()
            self._weekly_scheduler = None


# ----------------------------------------------------------
# BACKUP VERIFICATION
# ----------------------------------------------------------

def verify_backup(backup_path: str) -> dict:
    """
    Verify a backup zip file's integrity.
    
    Returns a dict with:
        - valid: bool - whether the backup passed all checks
        - manifest: dict or None - the manifest data
        - errors: list[str] - any validation errors
        - warnings: list[str] - non-fatal issues
        - databases: list[dict] - per-DB validation results
    """
    result = {
        "valid": False,
        "manifest": None,
        "errors": [],
        "warnings": [],
        "databases": [],
    }
    
    if not os.path.exists(backup_path):
        result["errors"].append(f"Backup file not found: {backup_path}")
        return result
    
    try:
        with zipfile.ZipFile(backup_path, "r") as z:
            # Check for manifest
            if "manifest.json" not in z.namelist():
                result["warnings"].append("No manifest.json found (older backup format)")
            else:
                manifest_raw = z.read("manifest.json").decode("utf-8")
                result["manifest"] = json.loads(manifest_raw)
            
            # Extract and validate databases
            tmp_dir = Path(tempfile.mkdtemp(prefix="dmelogic_verify_"))
            try:
                db_members = [m for m in z.namelist() if m.startswith("databases/") and m.endswith(".db")]
                
                for member in db_members:
                    db_name = Path(member).name
                    tmp_path = tmp_dir / db_name
                    
                    with z.open(member, "r") as src, open(tmp_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    
                    # Compute checksum
                    actual_sha256 = compute_sha256(tmp_path)
                    
                    # Check integrity
                    ok, msg = integrity_check(tmp_path)
                    
                    db_result = {
                        "name": db_name,
                        "sha256": actual_sha256,
                        "integrity_ok": ok,
                        "integrity_msg": msg,
                        "checksum_ok": True,
                    }
                    
                    # Validate against manifest if available
                    if result["manifest"] and "databases" in result["manifest"]:
                        for db_info in result["manifest"]["databases"]:
                            if isinstance(db_info, dict) and db_info.get("name") == db_name:
                                expected_sha256 = db_info.get("sha256")
                                if expected_sha256 and expected_sha256 != actual_sha256:
                                    db_result["checksum_ok"] = False
                                    result["errors"].append(f"Checksum mismatch for {db_name}")
                    
                    if not ok:
                        result["errors"].append(f"Integrity check failed for {db_name}: {msg}")
                    
                    result["databases"].append(db_result)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        
        result["valid"] = len(result["errors"]) == 0
        
    except Exception as e:
        result["errors"].append(f"Failed to verify backup: {e}")
    
    return result


def get_latest_backup_info(backup_type: str = "daily") -> Optional[dict]:
    """
    Get information about the most recent backup of the specified type.
    
    Returns dict with:
        - path: str - full path to backup
        - filename: str - just the filename
        - created_at: str - ISO timestamp
        - backup_type: str
        - size: int - file size in bytes
    Or None if no backup found.
    """
    base_dir = backup_dir()
    
    if backup_type == "daily":
        search_dir = base_dir / "Daily"
        pattern = "dmesolutions_backup_DAILY_*.zip"
    elif backup_type == "weekly":
        search_dir = base_dir / "Weekly"
        pattern = "dmesolutions_backup_WEEKLY_*.zip"
    else:
        search_dir = base_dir
        pattern = "dmesolutions_backup_MANUAL_*.zip"
    
    if not search_dir.exists():
        return None
    
    backups = sorted(search_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        return None
    
    latest = backups[0]
    
    # Try to read manifest for more details
    manifest_data = None
    try:
        with zipfile.ZipFile(str(latest), "r") as z:
            if "manifest.json" in z.namelist():
                manifest_data = json.loads(z.read("manifest.json").decode("utf-8"))
    except Exception:
        pass
    
    return {
        "path": str(latest),
        "filename": latest.name,
        "created_at": manifest_data.get("created_at") if manifest_data else datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
        "backup_type": backup_type,
        "size": latest.stat().st_size,
        "app_version": manifest_data.get("app_version") if manifest_data else "unknown",
    }
