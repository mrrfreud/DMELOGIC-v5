"""
service.py — triage workflow operations.

Ties :class:`TriageStore` to the filesystem: pulling new files in from the
**New Rx** folder, renaming, routing into buckets (which physically moves the
file), notes, optional patient/order linking, search, and history. Every
state change is auto-logged to the document's timeline.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from dmelogic.triage.models import Bucket, Document, DocumentEvent, EventType
from dmelogic.triage.store import TriageStore

logger = logging.getLogger("triage")

# Files we never treat as incoming prescriptions.
_IGNORED_SUFFIXES = {".db", ".db-wal", ".db-shm", ".tmp", ".part", ".crdownload"}
_IGNORED_NAMES_PREFIX = (".", "~$")
_VIEWABLE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}


def new_rx_folder() -> Path:
    """The intake folder where prescriptions arrive (faxed/scanned/pasted)."""
    from dmelogic.config import data_subdir
    return data_subdir("New Rx")


def _current_user() -> str:
    try:
        from dmelogic.security.auth import get_session
        session = get_session()
        if session and getattr(session, "username", None):
            return session.username
    except Exception:
        pass
    return ""


class TriageService:
    def __init__(self, store: Optional[TriageStore] = None):
        self.store = store or TriageStore()

    # ── intake ──────────────────────────────────────────────────────────
    def scan_inbox(self) -> list[Document]:
        """Register any new files in the New Rx folder; return the new docs."""
        folder = new_rx_folder()
        new_docs: list[Document] = []
        try:
            entries = sorted(folder.iterdir())
        except OSError as e:
            logger.warning("Cannot read New Rx folder %s: %s", folder, e)
            return new_docs

        for entry in entries:
            if not entry.is_file() or self._is_ignored(entry):
                continue
            if self.store.find_by_path(str(entry)) is not None:
                continue
            doc_id = self.store.add_document(entry.name, str(entry))
            self.store.add_event(doc_id, EventType.ARRIVED, user="")
            doc = self.store.get_document(doc_id)
            if doc:
                new_docs.append(doc)
        if new_docs:
            logger.info("Triage: picked up %d new document(s)", len(new_docs))
        return new_docs

    @staticmethod
    def _is_ignored(path: Path) -> bool:
        name = path.name
        if name.startswith(_IGNORED_NAMES_PREFIX):
            return True
        return path.suffix.lower() in _IGNORED_SUFFIXES

    @staticmethod
    def is_viewable(path: Path | str) -> bool:
        return Path(path).suffix.lower() in _VIEWABLE_SUFFIXES

    # ── rename ──────────────────────────────────────────────────────────
    def rename_document(self, doc: Document, new_name: str) -> Document:
        new_name = new_name.strip()
        if not new_name:
            return doc
        src = Path(doc.current_path)
        # Preserve the original extension if the user didn't supply one.
        if not Path(new_name).suffix and src.suffix:
            new_name += src.suffix
        dst = src.with_name(new_name)
        if dst.exists() and dst != src:
            dst = self._dedupe(dst)
        try:
            if src.exists():
                src.rename(dst)
        except OSError as e:
            logger.warning("Rename failed %s -> %s: %s", src, dst, e)
            return doc
        doc.filename = dst.name
        doc.current_path = str(dst)
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.RENAMED, dst.name, _current_user())
        return doc

    # ── routing ─────────────────────────────────────────────────────────
    def move_to_bucket(self, doc: Document, bucket: Bucket) -> Document:
        dest_dir = self._resolve_bucket_folder(bucket)
        # A–Z filing: drop into a per-last-name letter subfolder (keeps each
        # folder small, so lookups stay fast even with a large archive).
        if bucket.letter_filing:
            dest_dir = dest_dir / self._letter_for(doc)
        dest_dir.mkdir(parents=True, exist_ok=True)
        src = Path(doc.current_path)
        dst = dest_dir / src.name
        try:
            if src.exists():
                dst = self._move_over(src, dst)
            else:
                logger.warning("Source file missing on move: %s", src)
        except OSError as e:
            logger.warning("Move failed %s -> %s: %s", src, dst, e)
            return doc
        # Remember where it came from so the move can be undone.
        doc.previous_path = str(src)
        doc.previous_bucket_id = doc.bucket_id
        doc.current_path = str(dst)
        doc.filename = dst.name
        doc.bucket_id = bucket.id
        doc.status = bucket.effective_status()
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.MOVED, bucket.name, _current_user())
        return doc

    def dismiss(self, doc: Document) -> Document:
        """Remove a document from the queue WITHOUT moving the file."""
        doc.dismissed = True
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.DISMISSED, user=_current_user())
        return doc

    def undo_last_move(self, doc: Document) -> Document:
        """Reverse the most recent move, returning the file to where it was."""
        if not doc.previous_path:
            return doc
        prev = Path(doc.previous_path)
        src = Path(doc.current_path)
        prev.parent.mkdir(parents=True, exist_ok=True)
        dst = prev
        try:
            if src.exists():
                dst = self._move_over(src, dst)
        except OSError as e:
            logger.warning("Undo move failed: %s", e)
            return doc
        restored_bucket = doc.previous_bucket_id
        detail = ""
        if restored_bucket is not None:
            b = self.store.get_bucket(restored_bucket)
            detail = f"back to {b.name}" if b else ""
        else:
            detail = "back to New Rx"
        doc.current_path = str(dst)
        doc.filename = dst.name
        doc.bucket_id = restored_bucket
        doc.status = "New" if restored_bucket is None else doc.status
        doc.previous_path = None
        doc.previous_bucket_id = None
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.UNDONE, detail, _current_user())
        return doc

    @staticmethod
    def _letter_for(doc: Document) -> str:
        """First letter of the last name for A–Z filing.

        Files are renamed to ``LAST, FIRST …`` so the first alphabetic character
        of the filename is the last-name initial. Non-alphabetic → ``#``.
        """
        name = (doc.filename or "").lstrip()
        for ch in name:
            if ch.isalpha():
                return ch.upper()
        return "#"

    def reopen(self, doc: Document) -> Document:
        """Pull a document back into the New Rx inbox (move, not copy)."""
        dest = new_rx_folder()
        src = Path(doc.current_path)
        dst = dest / src.name
        try:
            if src.exists():
                dst = self._move_over(src, dst)
        except OSError as e:
            logger.warning("Reopen move failed: %s", e)
            return doc
        doc.current_path = str(dst)
        doc.filename = dst.name
        doc.bucket_id = None
        doc.status = "New"
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.REOPENED, user=_current_user())
        return doc

    # ── notes & history ─────────────────────────────────────────────────
    def add_note(self, doc: Document, text: str) -> None:
        text = text.strip()
        if text:
            self.store.add_event(doc.id, EventType.NOTE, text, _current_user())

    def history(self, doc: Document) -> list[DocumentEvent]:
        return self.store.list_events(doc.id)

    # ── linking ─────────────────────────────────────────────────────────
    def link(self, doc: Document, *, patient_id: Optional[int] = None,
             order_id: Optional[int] = None, label: str = "") -> Document:
        if patient_id is not None:
            doc.patient_id = patient_id
        if order_id is not None:
            doc.order_id = order_id
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.LINKED,
                             label or self._link_label(doc), _current_user())
        return doc

    def unlink(self, doc: Document) -> Document:
        doc.patient_id = None
        doc.order_id = None
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.UNLINKED, user=_current_user())
        return doc

    @staticmethod
    def _link_label(doc: Document) -> str:
        parts = []
        if doc.patient_id is not None:
            parts.append(f"patient #{doc.patient_id}")
        if doc.order_id is not None:
            parts.append(f"order #{doc.order_id}")
        return ", ".join(parts)

    # ── queries ─────────────────────────────────────────────────────────
    def inbox(self) -> list[Document]:
        return self.store.list_documents(bucket_id=None)

    def in_bucket(self, bucket_id: int) -> list[Document]:
        return self.store.list_documents(bucket_id=bucket_id)

    def search(self, query: str) -> list[Document]:
        return self.store.list_documents(bucket_id="ANY", search=query)

    def buckets(self) -> list[Bucket]:
        return self.store.list_buckets()

    def counts(self) -> dict:
        return self.store.count_by_bucket()

    # ── helpers ─────────────────────────────────────────────────────────
    def _resolve_bucket_folder(self, bucket: Bucket) -> Path:
        p = Path(bucket.folder)
        if p.is_absolute():
            return p
        from dmelogic.config import data_root
        return data_root() / p

    @staticmethod
    def _dedupe(path: Path) -> Path:
        """Return a non-colliding path by appending ' (n)' before the suffix."""
        stem, suffix, parent = path.stem, path.suffix, path.parent
        n = 2
        while True:
            candidate = parent / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    @staticmethod
    def _move_over(src: Path, dst: Path) -> Path:
        """Move src to dst, REPLACING any existing file at dst.

        Used for routing/reopen/undo so a re-filed document never piles up
        duplicate "(2)", "(3)" copies — there is always exactly one file, and
        the source is always removed.
        """
        src, dst = Path(src), Path(dst)
        if dst == src:
            return dst
        try:
            if dst.exists():
                dst.unlink()
        except OSError:
            pass
        shutil.move(str(src), str(dst))
        return dst
