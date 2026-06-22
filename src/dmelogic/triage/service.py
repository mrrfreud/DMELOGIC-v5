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


def intake_folder_name() -> str:
    """The configured intake folder name (settings ``intake_folder``).

    Defaults to ``New Rx``. Set ``intake_folder`` in settings.json to rename it
    (e.g. ``NEW ORDERS``) — may be a plain name (under the data root) or a full
    path.
    """
    try:
        from dmelogic.settings import load_settings
        name = (load_settings().get("intake_folder") or "").strip()
        if name:
            return name
    except Exception:
        pass
    return "New Rx"


def new_rx_folder() -> Path:
    """The intake folder where prescriptions arrive (faxed/scanned/pasted)."""
    from dmelogic.config import data_root
    name = intake_folder_name()
    p = Path(name)
    if not p.is_absolute():
        p = data_root() / p
    p.mkdir(parents=True, exist_ok=True)
    return p


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

    def migrate_bucketed_docs_to_scans_letters(self) -> int:
        """Move all currently bucketed docs into Scans/<Letter> and mark buckets.

        Returns the number of documents moved.
        """
        moved = 0
        from dmelogic.config import data_root
        scans_root = data_root() / "Scans"

        # Ensure all active buckets now point to Scans with letter filing on.
        try:
            for b in self.store.list_buckets(include_inactive=False):
                changed = False
                if (b.folder or "").strip().replace("\\", "/").lower() != "scans":
                    b.folder = "Scans"
                    changed = True
                if not b.letter_filing:
                    b.letter_filing = True
                    changed = True
                if changed:
                    self.store.update_bucket(b)
        except Exception as e:
            logger.warning("Bucket migration settings update failed: %s", e)

        # Move existing bucketed docs physically into Scans/<Letter>.
        try:
            docs = self.store.list_documents(bucket_id="ANY")
        except Exception as e:
            logger.warning("Bucket migration list failed: %s", e)
            return moved

        for doc in docs:
            if doc.bucket_id is None or doc.dismissed:
                continue
            src = Path(doc.current_path)
            if not src.exists():
                continue

            letter = self._letter_for(doc)
            dest_dir = scans_root / letter
            dest_dir.mkdir(parents=True, exist_ok=True)
            dst = dest_dir / src.name
            try:
                dst = self._move_over(src, dst)
            except OSError as e:
                logger.warning("Bucket migration move failed %s -> %s: %s", src, dst, e)
                continue

            if str(dst) != doc.current_path:
                doc.previous_path = doc.current_path
                doc.current_path = str(dst)
                doc.filename = dst.name
                self.store.update_document(doc)
                try:
                    b = self.store.get_bucket(doc.bucket_id) if doc.bucket_id else None
                    detail = f"{b.name} -> Scans/{letter}" if b else f"Scans/{letter}"
                except Exception:
                    detail = f"Scans/{letter}"
                self.store.add_event(doc.id, EventType.MOVED, detail, _current_user())
                moved += 1

        return moved

    # ── intake ──────────────────────────────────────────────────────────
    def scan_inbox(self) -> list[Document]:
        """Register new files in the intake folder; drop orphaned records.

        Also reconciles the queue: any tracked document whose file no longer
        exists on disk (moved/renamed/deleted outside the app, e.g. after the
        intake folder was renamed) is dropped from the queue so the list only
        ever shows documents that actually exist.
        """
        folder = new_rx_folder()
        new_docs: list[Document] = []

        # Reconcile: drop inbox entries whose file is gone (e.g. orphans left
        # after the intake folder was renamed, or files removed outside the app).
        try:
            for doc in self.store.list_documents(bucket_id=None):
                if not Path(doc.current_path).exists():
                    self.store.remove_document(doc.id)
        except Exception as e:
            logger.warning("Reconcile failed: %s", e)

        try:
            entries = sorted(folder.iterdir())
        except OSError as e:
            logger.warning("Cannot read intake folder %s: %s", folder, e)
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
        """Rename the document's file. Raises a clear error on failure.

        Robust against the three things that used to make renames fail silently:
        illegal filename characters, the file having moved out from under the DB
        record, and the file still being locked by the viewer/OCR on Windows.
        """
        import re

        new_name = (new_name or "").strip()
        if not new_name:
            return doc

        # Strip characters Windows forbids in a filename, plus trailing dots/
        # spaces (also illegal as a name ending on Windows).
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", new_name).strip().rstrip(". ")
        if not cleaned:
            raise ValueError("That name has no usable characters for a file name.")
        new_name = cleaned

        src = Path(doc.current_path)
        # Preserve the original extension if the user didn't supply one.
        if not Path(new_name).suffix and src.suffix:
            new_name += src.suffix

        # If the tracked file isn't where the DB record points (e.g. it was moved
        # or the intake folder was renamed), try to find it in the intake folder
        # before giving up — instead of silently corrupting the record.
        if not src.exists():
            candidate = new_rx_folder() / src.name
            if candidate.exists():
                src = candidate
            else:
                raise FileNotFoundError(
                    f"The file '{src.name}' is no longer in the intake folder.\n"
                    "Click Refresh and try again."
                )

        dst = src.with_name(new_name)
        if dst.exists() and dst != src:
            dst = self._dedupe(dst)

        # The PDF viewer / OCR can briefly hold the file open on Windows, so a
        # rename right after viewing may hit a lock. Force a GC and retry a few
        # times before surfacing the error.
        import gc
        import time
        last_err: OSError | None = None
        for _ in range(6):
            try:
                src.rename(dst)
                last_err = None
                break
            except OSError as e:
                last_err = e
                gc.collect()
                time.sleep(0.2)
        if last_err is not None:
            logger.warning("Rename failed %s -> %s: %s", src, dst, last_err)
            raise OSError(
                "Couldn't rename the file — it may still be open or locked by "
                "another program.\n\nClose any window showing this PDF and try "
                f"again.\n\n(Details: {last_err})"
            )

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

    def delete(self, doc: Document) -> Document:
        """Send the file to a Trash folder and remove it from the queue.

        Not a permanent delete — the file is moved to ``<data_root>/Trash`` so a
        mistaken delete of a prescription record stays recoverable.
        """
        from dmelogic.config import data_subdir
        trash = data_subdir("Trash")
        src = Path(doc.current_path)
        dst = trash / src.name
        if dst.exists() and dst != src:
            dst = self._dedupe(dst)   # keep every trashed file
        try:
            if src.exists():
                shutil.move(str(src), str(dst))
        except OSError as e:
            logger.warning("Delete (move to Trash) failed: %s", e)
            return doc
        doc.current_path = str(dst)
        doc.dismissed = True
        doc.bucket_id = None
        doc.status = "Deleted"
        self.store.update_document(doc)
        self.store.add_event(doc.id, EventType.DELETED, user=_current_user())
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
