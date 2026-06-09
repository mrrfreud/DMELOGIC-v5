"""
ocr_indexer.py — OCR Cache + Search Module

This is the heart of the system.
Provides FTS5-based full-text search with automatic OCR caching.
"""

# Import SQLite with FTS5 support
import sqlite3 as sql
try:
    from pysqlite3 import dbapi2 as sqlite3
    print("Using pysqlite3 with FTS5 support")
except ImportError:
    import sqlite3
    print("Using standard sqlite3 - FTS5 may not be available")

import os
import time
from typing import List, Optional
from pathlib import Path
from threading import Thread, Event, Lock
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    # Degrade gracefully: folder watching disabled
    print("⚠️ watchdog not installed - automatic folder watching disabled. Install 'watchdog' to enable live updates.")

from dmelogic.ocr_tools import extract_text_from_pdf, get_last_modified


class OCRIndexer:
    """
    Full-text search indexer for PDF documents with OCR caching.
    Uses SQLite FTS5 for fast text search with thread safety.
    """
    
    def __init__(self, db_path: str):
        """
        Initialize the OCR indexer.
        
        Args:
            db_path (str): Path to the SQLite database file
        """
        self.db_path = Path(db_path)
        self._db_lock = Lock()  # Thread safety for database operations
        self._ensure_db()

    def _ensure_db(self):
        """Creates database and FTS5 table if not present."""
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                # Check FTS5 availability
                try:
                    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(c)")
                    conn.execute("DROP TABLE IF EXISTS _fts5_check")
                    print("[OK] FTS5 is available")
                except sqlite3.OperationalError:
                    print("[WARNING] FTS5 not available - install pysqlite3-binary for better performance")
                    # Fall back to regular table if FTS5 not available
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS ocr_index (
                            filepath TEXT PRIMARY KEY,
                            text TEXT,
                            last_modified REAL
                        )
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_filepath ON ocr_index(filepath)")
                    conn.commit()
                    print(f"[OK] Database initialized (without FTS5): {self.db_path}")
                    return
                
                # ── Migrate existing table → FTS5 with rel_path if needed ──
                try:
                    row = conn.execute(
                        "SELECT sql FROM sqlite_master WHERE name = 'ocr_index' AND type = 'table'"
                    ).fetchone()
                    if row:
                        ddl = row[0] or ""
                        is_fts5 = "VIRTUAL" in ddl.upper() and "FTS5" in ddl.upper()
                        has_rel_path = "rel_path" in ddl.lower()
                        needs_migration = (not is_fts5) or (not has_rel_path)
                        
                        if needs_migration:
                            reason = "regular→FTS5" if not is_fts5 else "add rel_path column"
                            print(f"[MIGRATE] Converting ocr_index ({reason})...")
                            
                            # Detect columns in old table
                            cols = [r[1] for r in conn.execute("PRAGMA table_info(ocr_index)").fetchall()]
                            old_has_rel = 'rel_path' in cols
                            
                            # Read all existing data
                            if old_has_rel:
                                old_data = conn.execute(
                                    "SELECT filepath, text, last_modified, rel_path FROM ocr_index"
                                ).fetchall()
                            else:
                                old_data = [
                                    (r[0], r[1], r[2], os.path.basename(r[0]))
                                    for r in conn.execute(
                                        "SELECT filepath, text, last_modified FROM ocr_index"
                                    ).fetchall()
                                ]
                            
                            # Drop old table
                            conn.execute("DROP TABLE ocr_index")
                            conn.commit()
                            
                            # Create FTS5 virtual table with rel_path
                            conn.execute("""
                                CREATE VIRTUAL TABLE ocr_index USING fts5(
                                    filepath,
                                    text,
                                    last_modified UNINDEXED,
                                    rel_path UNINDEXED
                                )
                            """)
                            
                            # Re-insert data
                            if old_data:
                                conn.executemany(
                                    "INSERT INTO ocr_index (filepath, text, last_modified, rel_path) VALUES (?, ?, ?, ?)",
                                    old_data
                                )
                            conn.commit()
                            print(f"[MIGRATE] Successfully migrated {len(old_data)} entries to FTS5")
                except Exception as e:
                    print(f"[MIGRATE] Migration check: {e}")
                
                # Create FTS5 virtual table (if it doesn't exist yet)
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS ocr_index USING fts5(
                        filepath,
                        text,
                        last_modified UNINDEXED,
                        rel_path UNINDEXED
                    )
                """)
                
                # Create metadata table for tracking
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS index_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                
                conn.commit()
                print(f"✓ Database initialized with FTS5: {self.db_path}")
                # Suppression table (paths or basenames marked to ignore)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS suppressed_files (
                        pattern TEXT PRIMARY KEY,
                        created_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()

    def add_or_update(self, file_path: str, text: str, last_modified: float, rel_path: str = None):
        """
        Inserts or updates OCR text for a file (thread-safe).
        
        Args:
            file_path (str): Full path to the PDF file
            text (str): Extracted text content
            last_modified (float): File modification timestamp
            rel_path (str): Relative path from root (e.g. 'D\\file.pdf')
        """
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                # Ensure rel_path column exists (migration)
                try:
                    conn.execute("ALTER TABLE ocr_index ADD COLUMN rel_path TEXT")
                    conn.commit()
                    print("[OK] Added rel_path column to ocr_index")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                
                # First, remove any existing entry for this file
                conn.execute("DELETE FROM ocr_index WHERE filepath = ?", (file_path,))
                
                # Use rel_path if provided, otherwise compute from filepath
                if rel_path is None:
                    rel_path = os.path.basename(file_path)
                
                # Insert the new/updated entry
                conn.execute(
                    "INSERT INTO ocr_index (filepath, text, last_modified, rel_path) VALUES (?, ?, ?, ?)",
                    (file_path, text, last_modified, rel_path)
                )
                
                conn.commit()
                print(f"[OK] Indexed: {rel_path} ({len(text)} chars)")

    def get_last_modified(self, file_path: str) -> Optional[float]:
        """
        Returns stored last_modified timestamp for a file, if any (thread-safe).
        
        Args:
            file_path (str): Path to the PDF file
            
        Returns:
            float | None: Last modified timestamp, or None if not found
        """
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT last_modified FROM ocr_index WHERE filepath = ?",
                    (file_path,)
                )
                result = cursor.fetchone()
                return result[0] if result else None

    def _build_snippet(self, text: str, query: str, context: int = 60) -> Optional[str]:
        """Return a small context window around the first match."""
        if not text or not query:
            return None
        haystack = text.lower()
        needle = query.lower()
        pos = haystack.find(needle)
        if pos == -1:
            snippet = text[: context * 2]
        else:
            start = max(0, pos - context)
            end = min(len(text), pos + len(query) + context)
            snippet = text[start:end]
        return " ".join(snippet.split()) if snippet else None

    def search(self, query: str) -> List[dict]:
        """
        Performs full-text search and returns all matching files with metadata (thread-safe).
        
        Args:
            query (str): Search query
            
        Returns:
            List[dict]: List of dicts with 'filepath', 'rel_path', 'filename', and 'snippet' for each match
        """
        if not query.strip():
            return []
        
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                # Enable row factory for dict-like access
                conn.row_factory = sqlite3.Row
                needle = query.strip()
                
                try:
                    # Try FTS5 MATCH for full-text search
                    cursor = conn.execute(
                        """
                        SELECT filepath, rel_path,
                               snippet(ocr_index, 1, '[', ']', '...', 24) AS snippet_text
                        FROM ocr_index
                        WHERE ocr_index MATCH ?
                        ORDER BY rank
                        """,
                        (needle,)
                    )
                    fts_available = True
                except sqlite3.OperationalError:
                    # Fall back to LIKE search if FTS5 not available
                    cursor = conn.execute(
                        "SELECT filepath, rel_path, text FROM ocr_index WHERE text LIKE ? ORDER BY filepath",
                        (f"%{needle}%",)
                    )
                    fts_available = False
                
                # Build result list with all metadata
                results = []
                for row in cursor.fetchall():
                    filepath = row['filepath']
                    rel_path = row['rel_path'] if row['rel_path'] else os.path.basename(filepath)
                    snippet_val = None
                    if 'snippet_text' in row.keys():
                        snippet_val = row['snippet_text']
                    elif not fts_available and 'text' in row.keys():
                        snippet_val = self._build_snippet(row['text'], needle)
                    snippet_val = " ".join(snippet_val.split()) if snippet_val else None
                    results.append({
                        'filepath': filepath,
                        'rel_path': rel_path,
                        'filename': os.path.basename(filepath),
                        'snippet': snippet_val
                    })
                
                # Filter suppressed entries (by full path or basename pattern)
                try:
                    sup_rows = conn.execute("SELECT pattern FROM suppressed_files").fetchall()
                    suppressed = {r[0] for r in sup_rows}
                    if suppressed:
                        filtered = []
                        for item in results:
                            fp = item['filepath']
                            base = item['filename']
                            if fp in suppressed or base in suppressed:
                                continue
                            filtered.append(item)
                        if len(filtered) != len(results):
                            print(f"🚫 Suppressed {len(results)-len(filtered)} result(s) by pattern")
                        results = filtered
                except Exception as e:
                    print(f"Suppression filter error: {e}")
                print(f"🔍 Search '{query}': Found {len(results)} matches")
                return results

    def get_file_text(self, file_path: str) -> Optional[str]:
        """
        Get the cached text content for a specific file (thread-safe).
        
        Args:
            file_path (str): Path to the PDF file
            
        Returns:
            str | None: Cached text content, or None if not found
        """
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT text FROM ocr_index WHERE filepath = ?",
                    (file_path,)
                )
                result = cursor.fetchone()
                return result[0] if result else None

    def remove_file(self, file_path: str):
        """
        Remove a file from the index (thread-safe).
        
        Args:
            file_path (str): Path to the PDF file to remove
        """
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM ocr_index WHERE filepath = ?", (file_path,))
                conn.commit()
                print(f"🗑️ Removed from index: {os.path.basename(file_path)}")

    def update_file_path(self, old_path: str, new_path: str):
        """
        Update a file's path in the index when it's been moved (thread-safe).
        
        Args:
            old_path (str): Original file path
            new_path (str): New file path after move
        """
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                # Get the existing entry
                cursor = conn.execute(
                    "SELECT text, last_modified FROM ocr_index WHERE filepath = ?",
                    (old_path,)
                )
                result = cursor.fetchone()
                
                if result:
                    text, last_modified = result
                    
                    # Delete old entry
                    conn.execute("DELETE FROM ocr_index WHERE filepath = ?", (old_path,))
                    
                    # Insert with new path
                    conn.execute(
                        "INSERT INTO ocr_index (filepath, text, last_modified) VALUES (?, ?, ?)",
                        (new_path, text, last_modified)
                    )
                    
                    conn.commit()
                    print(f"✏️ Updated path in index: {os.path.basename(old_path)} -> {new_path}")
                    return True
                else:
                    print(f"⚠️ File not found in index for path update: {old_path}")
                    return False

    def find_and_update_stale_paths(self, folder_root: str) -> int:
        """
        Find files with stale paths and update them if the file exists elsewhere.
        Searches recursively in folder_root for moved files.
        
        Args:
            folder_root (str): Root folder to search for moved files
            
        Returns:
            int: Number of paths updated
        """
        updated_count = 0
        
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                # Get all indexed files
                cursor = conn.execute("SELECT filepath FROM ocr_index")
                all_paths = [row[0] for row in cursor.fetchall()]
        
        # Check each path
        for old_path in all_paths:
            if not os.path.exists(old_path):
                # File doesn't exist at indexed location - search for it
                filename = os.path.basename(old_path)
                
                # Walk folder tree to find the file
                for root, dirs, files in os.walk(folder_root):
                    if filename in files:
                        new_path = os.path.join(root, filename)
                        print(f"📍 Found moved file: {filename}")
                        print(f"   Old: {old_path}")
                        print(f"   New: {new_path}")
                        
                        # Update the path in the index
                        if self.update_file_path(old_path, new_path):
                            updated_count += 1
                        break
        
        if updated_count > 0:
            print(f"✅ Updated {updated_count} stale path(s) in index")
        
        return updated_count

    def get_stats(self) -> dict:
        """
        Get indexer statistics (thread-safe).
        
        Returns:
            dict: Statistics about the index
        """
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM ocr_index")
                total_files = cursor.fetchone()[0]
                
                cursor = conn.execute("SELECT SUM(LENGTH(text)) FROM ocr_index")
                total_chars = cursor.fetchone()[0] or 0
                
                return {
                    'total_files': total_files,
                    'total_characters': total_chars,
                    'db_size': os.path.getsize(self.db_path) if self.db_path.exists() else 0
                }

    def add_or_update_batch(self, file_data: List[tuple]):
        """
        Batch insert/update multiple files for better performance.
        
        Args:
            file_data (List[tuple]): List of (file_path, text, last_modified) or
                                     (file_path, text, last_modified, rel_path) tuples
        """
        if not file_data:
            return
        
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                # Ensure rel_path column exists (migration)
                try:
                    conn.execute("ALTER TABLE ocr_index ADD COLUMN rel_path TEXT")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # Column already exists
                
                # Use a single transaction for all operations
                conn.execute("BEGIN TRANSACTION")
                try:
                    for item in file_data:
                        if len(item) >= 4:
                            file_path, text, last_modified, rel_path = item[0], item[1], item[2], item[3]
                        else:
                            file_path, text, last_modified = item[0], item[1], item[2]
                            rel_path = os.path.basename(file_path)
                        # Remove existing entry
                        conn.execute("DELETE FROM ocr_index WHERE filepath = ?", (file_path,))
                        # Insert new entry with rel_path
                        conn.execute(
                            "INSERT INTO ocr_index (filepath, text, last_modified, rel_path) VALUES (?, ?, ?, ?)",
                            (file_path, text, last_modified, rel_path)
                        )
                    
                    conn.execute("COMMIT")
                    print(f"✓ Batch indexed: {len(file_data)} files")
                    
                except Exception as e:
                    conn.execute("ROLLBACK")
                    print(f"❌ Batch indexing failed: {e}")
                    raise
    
    def vacuum(self):
        """Optimizes database by reclaiming space (thread-safe)."""
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("VACUUM")
                conn.commit()
                print("✓ Database optimized")

    def prune_missing(self) -> int:
        """
        Remove all index entries whose files no longer exist on disk (thread-safe).

        Returns:
            int: Number of entries removed
        """
        removed = 0
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT filepath FROM ocr_index")
                rows = cursor.fetchall()
                for (path,) in rows:
                    try:
                        if not os.path.exists(path):
                            conn.execute("DELETE FROM ocr_index WHERE filepath = ?", (path,))
                            removed += 1
                    except Exception:
                        # Best-effort pruning; continue on errors
                        pass
                conn.commit()
        if removed:
            print(f"🧹 Pruned {removed} missing file(s) from index")
        return removed

    def remove_missing_by_basename(self, base_name: str) -> int:
        """
        Remove all index entries whose basename matches base_name and whose
        files are missing on disk. Useful after rename+move where the old
        name no longer exists anywhere.

        Args:
            base_name (str): Filename only, e.g. 'file.pdf'

        Returns:
            int: Number of entries removed
        """
        removed = 0
        base_name_lower = os.path.normcase(base_name)
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT filepath FROM ocr_index")
                rows = cursor.fetchall()
                for (path,) in rows:
                    try:
                        if os.path.normcase(os.path.basename(path)) == base_name_lower and not os.path.exists(path):
                            conn.execute("DELETE FROM ocr_index WHERE filepath = ?", (path,))
                            removed += 1
                    except Exception:
                        pass
                conn.commit()
        if removed:
            print(f"🧹 Removed {removed} missing entries for basename: {base_name}")
        return removed

    # --- Suppression API -------------------------------------------------
    def suppress_pattern(self, pattern: str) -> bool:
        """Persistently suppress a full path or basename from future results."""
        if not pattern:
            return False
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                try:
                    conn.execute("INSERT OR IGNORE INTO suppressed_files(pattern) VALUES (?)", (pattern,))
                    conn.commit()
                    print(f"🚫 Suppressed pattern: {pattern}")
                    return True
                except Exception as e:
                    print(f"Failed to suppress pattern {pattern}: {e}")
                    return False

    def unsuppress_pattern(self, pattern: str) -> bool:
        """Remove a suppression pattern (full path or basename)."""
        if not pattern:
            return False
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                try:
                    conn.execute("DELETE FROM suppressed_files WHERE pattern=?", (pattern,))
                    conn.commit()
                    print(f"✅ Unsuppressed pattern: {pattern}")
                    return True
                except Exception as e:
                    print(f"Failed to unsuppress pattern {pattern}: {e}")
                    return False

    def list_suppressed(self) -> List[str]:
        """Return list of suppressed patterns."""
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                try:
                    rows = conn.execute("SELECT pattern FROM suppressed_files ORDER BY created_ts DESC").fetchall()
                    return [r[0] for r in rows]
                except Exception:
                    return []

    def clear_suppressed(self) -> int:
        """Remove all suppression patterns."""
        with self._db_lock:
            with sqlite3.connect(self.db_path) as conn:
                try:
                    count = conn.execute("SELECT COUNT(*) FROM suppressed_files").fetchone()[0]
                    conn.execute("DELETE FROM suppressed_files")
                    conn.commit()
                    print(f"✅ Cleared {count} suppression pattern(s)")
                    return count
                except Exception as e:
                    print(f"Failed to clear suppression patterns: {e}")
                    return 0


def update_index_from_folder(folder_path: str, db_path: str, indexer: OCRIndexer = None):
    """
    Walks through folder, detects new/changed PDFs, and updates the index.
    
    Args:
        folder_path (str): Path to the folder containing PDF files
        db_path (str): Path to the database file
        indexer (OCRIndexer, optional): Existing indexer instance
    """
    if indexer is None:
        indexer = OCRIndexer(db_path)
    
    folder = Path(folder_path)
    if not folder.exists():
        print(f"❌ Folder not found: {folder_path}")
        return
    
    print(f"🔄 Updating index from (recursive): {folder_path}")
    
    # Find all PDF files recursively
    pdf_files = list(folder.rglob("*.pdf"))
    updated_count = 0
    skipped_count = 0
    batch_data = []
    batch_size = 50  # Process in batches for better performance
    
    for i, pdf_file in enumerate(pdf_files):
        try:
            file_path = str(pdf_file)
            # Skip suppressed basenames or full paths
            try:
                suppressed = set(indexer.list_suppressed())
            except Exception:
                suppressed = set()
            base = os.path.basename(file_path)
            if file_path in suppressed or base in suppressed:
                skipped_count += 1
                continue
            current_modified = get_last_modified(file_path)
            cached_modified = indexer.get_last_modified(file_path)
            
            if cached_modified is None or current_modified > cached_modified:
                # File is new or has been modified
                print(f"📄 Processing: {pdf_file.name}")
                
                try:
                    # Extract text using OCR tools
                    text = extract_text_from_pdf(file_path)
                    
                    # Compute rel_path from folder root
                    rel_path = os.path.relpath(file_path, folder_path)
                    
                    # Add to batch (with rel_path)
                    batch_data.append((file_path, text, current_modified, rel_path))
                    updated_count += 1
                    
                    # Process batch when it reaches batch_size or at the end
                    if len(batch_data) >= batch_size or i == len(pdf_files) - 1:
                        indexer.add_or_update_batch(batch_data)
                        batch_data = []
                        
                except Exception as e:
                    print(f"❌ OCR failed for {pdf_file.name}: {e}")
                    # Log OCR failure
                    try:
                        with open('ocr_failures.log', 'a', encoding='utf-8') as f:
                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {pdf_file.name}: {e}\n")
                    except:
                        pass
                    
            else:
                # File hasn't changed, skip
                skipped_count += 1
                
        except Exception as e:
            print(f"❌ Error processing {pdf_file.name}: {e}")
    
    # Process any remaining items in the batch
    if batch_data:
        indexer.add_or_update_batch(batch_data)
    
    print(f"✅ Index update complete: {updated_count} updated, {skipped_count} skipped")
    
    # Show statistics
    stats = indexer.get_stats()
    print(f"📊 Index stats: {stats['total_files']} files, {stats['total_characters']:,} characters")


class PDFWatchHandler(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):
    """File system event handler for PDF files."""
    
    def __init__(self, indexer: OCRIndexer, pending=None, pending_lock: Lock = None):
        self.indexer = indexer
        self._pending = pending if pending is not None else set()
        self._pending_lock = pending_lock or Lock()
        super().__init__()
    
    def _enqueue(self, file_path: str):
        normalized = os.path.abspath(file_path)
        with self._pending_lock:
            self._pending.add(normalized)
    
    def drain_pending(self) -> List[str]:
        with self._pending_lock:
            items = list(self._pending)
            self._pending.clear()
            return items
    
    def _drop_pending(self, file_path: str):
        normalized = os.path.abspath(file_path)
        with self._pending_lock:
            self._pending.discard(normalized)
    
    def on_created(self, event):
        """Handle file creation."""
        if not event.is_directory and event.src_path.lower().endswith('.pdf'):
            self._enqueue(event.src_path)
    
    def on_modified(self, event):
        """Handle file modification."""
        if not event.is_directory and event.src_path.lower().endswith('.pdf'):
            self._enqueue(event.src_path)
    
    def on_deleted(self, event):
        """Handle file deletion."""
        if not event.is_directory and event.src_path.lower().endswith('.pdf'):
            self._drop_pending(event.src_path)
            try:
                self.indexer.remove_file(event.src_path)
            except Exception as e:
                print(f"Error removing {event.src_path}: {e}")

    def _process_file(self, file_path: str, action: str):
        """Process a PDF file (created or modified) immediately."""
        try:
            time.sleep(0.5)
            
            if os.path.exists(file_path):
                current_modified = get_last_modified(file_path)
                cached_modified = self.indexer.get_last_modified(file_path)
                
                if cached_modified is None or current_modified > cached_modified:
                    text = extract_text_from_pdf(file_path)
                    self.indexer.add_or_update(file_path, text, current_modified)
                
        except Exception as e:
            error_msg = f"{os.path.basename(file_path)}: {e}\n"
            print(f"Error processing {action} file {file_path}: {e}")
            try:
                with open('ocr_failures.log', 'a', encoding='utf-8') as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {error_msg}")
            except:
                pass


def watch_folder(folder_path: str, db_path: str, stop_event: Event = None):
    """
    Watches for new/modified PDFs and updates index automatically.
    Runs in a background thread.
    
    Args:
        folder_path (str): Path to the folder to watch
        db_path (str): Path to the database file
        stop_event (Event, optional): Event to stop the watcher
    """
    if not WATCHDOG_AVAILABLE:
        print("⏭️ Folder watch skipped (watchdog unavailable).")
        return

    indexer = OCRIndexer(db_path)
    pending = set()
    pending_lock = Lock()
    event_handler = PDFWatchHandler(indexer, pending, pending_lock)
    observer = Observer()
    
    try:
        observer.schedule(event_handler, folder_path, recursive=False)
        observer.start()
        
        print(f"[OK] Watching folder: {folder_path}")
        
        # Keep running until stop event is set
        while True:
            if stop_event and stop_event.is_set():
                break
            time.sleep(1)
            batch_paths = event_handler.drain_pending()
            if not batch_paths:
                continue
            batch_data = []
            for file_path in batch_paths:
                try:
                    if not os.path.exists(file_path):
                        continue
                    current_modified = get_last_modified(file_path)
                    cached_modified = indexer.get_last_modified(file_path)
                    if cached_modified is not None and current_modified <= cached_modified:
                        continue
                    text = extract_text_from_pdf(file_path)
                    # Compute rel_path from watched folder root
                    rel_path = os.path.relpath(file_path, folder_path)
                    batch_data.append((file_path, text, current_modified, rel_path))
                except Exception as e:
                    error_msg = f"{os.path.basename(file_path)}: {e}\n"
                    print(f"Error updating index for {file_path}: {e}")
                    try:
                        with open('ocr_failures.log', 'a', encoding='utf-8') as f:
                            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {error_msg}")
                    except:
                        pass
            if batch_data:
                try:
                    indexer.add_or_update_batch(batch_data)
                except Exception as e:
                    print(f"Batch update failed: {e}")
            
    except KeyboardInterrupt:
        print("\n[STOP] Stopping folder watcher...")
    except Exception as e:
        print(f"[ERROR] Watcher error: {e}")
    finally:
        observer.stop()
        observer.join()
        print("[OK] Folder watcher stopped")


if __name__ == "__main__":
    # Test the indexer
    print("OCR Indexer Test")
    print("=" * 30)
    
    # Configuration
    test_db = r"C:\Users\pharmacy\Documents\FaxManagerData\ocr_cache.db"
    test_folder = "faxes"
    
    # Create indexer
    indexer = OCRIndexer(test_db)
    
    # Show current stats
    stats = indexer.get_stats()
    print(f"Current stats: {stats}")
    
    # Update from folder if it exists
    if os.path.exists(test_folder):
        update_index_from_folder(test_folder, test_db, indexer)
        
        # Test search
        test_query = "feli"
        results = indexer.search(test_query)
        print(f"\nSearch results for '{test_query}': {results}")
    else:
        print(f"Test folder not found: {test_folder}")