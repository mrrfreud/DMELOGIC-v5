"""
store.py — documents.db persistence for triage.

Holds three tables: ``buckets`` (customizable destinations), ``documents``
(tracked files), and ``document_events`` (the history timeline). The database
lives under the canonical data root (``db_dir()/documents.db``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from dmelogic.triage.models import Bucket, Document, DocumentEvent, EventType

DB_FILENAME = "documents.db"

# Seeded only when a company has no buckets yet. Fully editable afterwards.
# (name, status, color, folder, letter_filing)
#   folder=None → a default "Triage/<name>" folder under the data root.
#   "Ready" files completed prescriptions into Scans, organized A–Z by last name.
DEFAULT_BUCKETS = [
    ("Ready", "Ready", "#16a34a", "Scans", True),
    ("Missing Info", "Missing Info", "#d97706", None, False),
    ("Missing Insurance", "Missing Insurance", "#dc2626", None, False),
    ("Unable to Contact Patient", "Unable to Contact", "#7c3aed", None, False),
    ("On Hold", "On Hold", "#0d9488", None, False),
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _db_path() -> Path:
    from dmelogic.paths import db_dir
    return db_dir() / DB_FILENAME


class TriageStore:
    """Thin data-access layer over documents.db."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── connection ──────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS buckets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    status TEXT,
                    color TEXT DEFAULT '#0d9488',
                    sort_order INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    letter_filing INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    current_path TEXT NOT NULL,
                    bucket_id INTEGER REFERENCES buckets(id) ON DELETE SET NULL,
                    status TEXT DEFAULT 'New',
                    patient_id INTEGER,
                    order_id INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    dismissed INTEGER DEFAULT 0,
                    previous_path TEXT,
                    previous_bucket_id INTEGER,
                    ocr_text TEXT,
                    ocr_done INTEGER DEFAULT 0,
                    ocr_quality TEXT,
                    detected_name TEXT,
                    detected_dob TEXT
                );

                CREATE TABLE IF NOT EXISTS document_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ts TEXT NOT NULL,
                    type TEXT NOT NULL,
                    detail TEXT,
                    user TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_documents_bucket ON documents(bucket_id);
                CREATE INDEX IF NOT EXISTS idx_documents_patient ON documents(patient_id);
                CREATE INDEX IF NOT EXISTS idx_events_doc ON document_events(document_id);
                """
            )
            # Migrate older documents.db that predate these columns.
            for table, col, ddl in (
                ("buckets", "letter_filing", "ALTER TABLE buckets ADD COLUMN letter_filing INTEGER DEFAULT 0"),
                ("documents", "dismissed", "ALTER TABLE documents ADD COLUMN dismissed INTEGER DEFAULT 0"),
                ("documents", "previous_path", "ALTER TABLE documents ADD COLUMN previous_path TEXT"),
                ("documents", "previous_bucket_id", "ALTER TABLE documents ADD COLUMN previous_bucket_id INTEGER"),
                ("documents", "ocr_text", "ALTER TABLE documents ADD COLUMN ocr_text TEXT"),
                ("documents", "ocr_done", "ALTER TABLE documents ADD COLUMN ocr_done INTEGER DEFAULT 0"),
                ("documents", "ocr_quality", "ALTER TABLE documents ADD COLUMN ocr_quality TEXT"),
                ("documents", "detected_name", "ALTER TABLE documents ADD COLUMN detected_name TEXT"),
                ("documents", "detected_dob", "ALTER TABLE documents ADD COLUMN detected_dob TEXT"),
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # column already exists

            # One-time upgrade: if an existing "Ready" bucket still has the old
            # auto-generated default folder (i.e. it wasn't customized), point it
            # at Scans with A–Z filing — matching the new default.
            try:
                conn.execute(
                    "UPDATE buckets SET folder='Scans', letter_filing=1 "
                    "WHERE name='Ready' AND folder IN ('Triage/Ready', 'Triage\\Ready') "
                    "AND COALESCE(letter_filing, 0) = 0"
                )
            except sqlite3.OperationalError:
                pass

            # Seed default buckets only when empty.
            count = conn.execute("SELECT COUNT(*) FROM buckets").fetchone()[0]
            if count == 0:
                for i, (name, status, color, folder, letter) in enumerate(DEFAULT_BUCKETS):
                    conn.execute(
                        "INSERT INTO buckets (name, folder, status, color, sort_order, is_active, letter_filing) "
                        "VALUES (?, ?, ?, ?, ?, 1, ?)",
                        (name, folder or self._default_folder_for(name), status, color, i, int(letter)),
                    )
            conn.commit()

    @staticmethod
    def _default_folder_for(name: str) -> str:
        """Default destination folder for a bucket (relative to the data root)."""
        safe = "".join(c for c in name if c.isalnum() or c in " -_").strip()
        return str(Path("Triage") / safe)

    # ── buckets ─────────────────────────────────────────────────────────
    def _row_to_bucket(self, r: sqlite3.Row) -> Bucket:
        keys = r.keys()
        return Bucket(
            id=r["id"], name=r["name"], folder=r["folder"],
            status=r["status"] or "", color=r["color"] or "#0d9488",
            sort_order=r["sort_order"] or 0, is_active=bool(r["is_active"]),
            letter_filing=bool(r["letter_filing"]) if "letter_filing" in keys else False,
        )

    def list_buckets(self, include_inactive: bool = False) -> list[Bucket]:
        q = "SELECT * FROM buckets"
        if not include_inactive:
            q += " WHERE is_active = 1"
        q += " ORDER BY sort_order, name"
        with self._connect() as conn:
            return [self._row_to_bucket(r) for r in conn.execute(q).fetchall()]

    def get_bucket(self, bucket_id: int) -> Optional[Bucket]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM buckets WHERE id = ?", (bucket_id,)).fetchone()
            return self._row_to_bucket(r) if r else None

    def add_bucket(self, name: str, folder: str = "", status: str = "",
                   color: str = "#0d9488", letter_filing: bool = False) -> int:
        folder = folder or self._default_folder_for(name)
        with self._connect() as conn:
            next_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM buckets"
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO buckets (name, folder, status, color, sort_order, is_active, letter_filing) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (name, folder, status or name, color, next_order, int(letter_filing)),
            )
            conn.commit()
            return cur.lastrowid

    def update_bucket(self, bucket: Bucket) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE buckets SET name=?, folder=?, status=?, color=?, sort_order=?, "
                "is_active=?, letter_filing=? WHERE id=?",
                (bucket.name, bucket.folder, bucket.status, bucket.color,
                 bucket.sort_order, int(bucket.is_active), int(bucket.letter_filing),
                 bucket.id),
            )
            conn.commit()

    def remove_bucket(self, bucket_id: int, hard: bool = False) -> None:
        """Soft-disable by default (keeps history); hard-delete optionally."""
        with self._connect() as conn:
            if hard:
                conn.execute("DELETE FROM buckets WHERE id = ?", (bucket_id,))
            else:
                conn.execute("UPDATE buckets SET is_active = 0 WHERE id = ?", (bucket_id,))
            conn.commit()

    # ── documents ───────────────────────────────────────────────────────
    def _row_to_document(self, r: sqlite3.Row) -> Document:
        keys = r.keys()
        return Document(
            id=r["id"], filename=r["filename"], current_path=r["current_path"],
            bucket_id=r["bucket_id"], status=r["status"] or "New",
            patient_id=r["patient_id"], order_id=r["order_id"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            dismissed=bool(r["dismissed"]) if "dismissed" in keys else False,
            previous_path=r["previous_path"] if "previous_path" in keys else None,
            previous_bucket_id=r["previous_bucket_id"] if "previous_bucket_id" in keys else None,
            ocr_done=bool(r["ocr_done"]) if "ocr_done" in keys else False,
            ocr_quality=(r["ocr_quality"] or "") if "ocr_quality" in keys else "",
            detected_name=(r["detected_name"] or "") if "detected_name" in keys else "",
            detected_dob=(r["detected_dob"] or "") if "detected_dob" in keys else "",
        )

    def set_ocr(self, doc_id: int, text: str, quality: str,
                name: str = "", dob: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE documents SET ocr_text=?, ocr_done=1, ocr_quality=?, "
                "detected_name=?, detected_dob=? WHERE id=?",
                (text, quality, name, dob, doc_id),
            )
            conn.commit()

    def pending_ocr(self) -> list[tuple[int, str]]:
        """(id, current_path) for non-dismissed documents not yet OCR'd."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, current_path FROM documents "
                "WHERE COALESCE(ocr_done, 0) = 0 AND COALESCE(dismissed, 0) = 0"
            ).fetchall()
            return [(r["id"], r["current_path"]) for r in rows]

    def get_document(self, doc_id: int) -> Optional[Document]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            return self._row_to_document(r) if r else None

    def find_by_path(self, path: str) -> Optional[Document]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM documents WHERE current_path = ?", (str(path),)
            ).fetchone()
            return self._row_to_document(r) if r else None

    def add_document(self, filename: str, current_path: str,
                     status: str = "New") -> int:
        ts = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO documents (filename, current_path, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (filename, str(current_path), status, ts, ts),
            )
            conn.commit()
            return cur.lastrowid

    def update_document(self, doc: Document) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE documents SET filename=?, current_path=?, bucket_id=?, status=?, "
                "patient_id=?, order_id=?, dismissed=?, previous_path=?, "
                "previous_bucket_id=?, updated_at=? WHERE id=?",
                (doc.filename, str(doc.current_path), doc.bucket_id, doc.status,
                 doc.patient_id, doc.order_id, int(doc.dismissed), doc.previous_path,
                 doc.previous_bucket_id, _now(), doc.id),
            )
            conn.commit()

    def list_documents(self, bucket_id: Optional[int] = "ANY",
                        search: str = "", include_dismissed: bool = False) -> list[Document]:
        """List documents. bucket_id='ANY' = all; None = inbox; int = that bucket."""
        clauses, params = [], []
        if not include_dismissed:
            clauses.append("COALESCE(d.dismissed, 0) = 0")
        if bucket_id != "ANY":
            if bucket_id is None:
                clauses.append("d.bucket_id IS NULL")
            else:
                clauses.append("d.bucket_id = ?")
                params.append(bucket_id)
        if search.strip():
            like = f"%{search.strip()}%"
            clauses.append(
                "(d.filename LIKE ? OR d.status LIKE ? "
                "OR COALESCE(d.ocr_text,'') LIKE ? "
                "OR COALESCE(d.detected_name,'') LIKE ? "
                "OR d.id IN (SELECT document_id FROM document_events WHERE detail LIKE ?))"
            )
            params += [like, like, like, like, like]
        q = "SELECT d.* FROM documents d"
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY d.updated_at DESC"
        with self._connect() as conn:
            return [self._row_to_document(r) for r in conn.execute(q, params).fetchall()]

    def count_by_bucket(self) -> dict:
        """Return {bucket_id_or_None: count} for the tracking view (excludes dismissed)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT bucket_id, COUNT(*) AS n FROM documents "
                "WHERE COALESCE(dismissed, 0) = 0 GROUP BY bucket_id"
            ).fetchall()
            return {r["bucket_id"]: r["n"] for r in rows}

    # ── events ──────────────────────────────────────────────────────────
    def add_event(self, document_id: int, type: EventType, detail: str = "",
                  user: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO document_events (document_id, ts, type, detail, user) "
                "VALUES (?, ?, ?, ?, ?)",
                (document_id, _now(), type.value, detail, user),
            )
            conn.commit()
            return cur.lastrowid

    def list_events(self, document_id: int) -> list[DocumentEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_events WHERE document_id = ? ORDER BY ts DESC, id DESC",
                (document_id,),
            ).fetchall()
            return [
                DocumentEvent(
                    id=r["id"], document_id=r["document_id"], ts=r["ts"],
                    type=EventType(r["type"]) if r["type"] in EventType._value2member_map_
                    else EventType.NOTE,
                    detail=r["detail"] or "", user=r["user"] or "",
                )
                for r in rows
            ]
