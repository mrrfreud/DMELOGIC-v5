"""Plain data models for the triage subsystem (no Qt, no DB — easy to test)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    """Kinds of entries in a document's history timeline."""
    ARRIVED = "arrived"      # file appeared in the New Rx folder
    RENAMED = "renamed"      # file was renamed
    MOVED = "moved"          # routed into a bucket
    NOTE = "note"            # manual note added by a user
    LINKED = "linked"        # linked to a patient/order
    UNLINKED = "unlinked"    # link removed
    REOPENED = "reopened"    # pulled back into the New Rx queue
    DISMISSED = "dismissed"  # removed from the queue without moving the file
    UNDONE = "undone"        # last move was reversed
    DELETED = "deleted"      # file sent to Trash and removed from the queue


@dataclass
class Bucket:
    """A company-defined triage destination (e.g. 'On Hold', 'Missing Insurance').

    Each bucket maps to a destination folder on disk and a status that travels
    with the document. Buckets are fully customizable per company.
    """
    id: Optional[int]
    name: str
    folder: str                 # destination folder (absolute, or relative to data_root)
    status: str = ""            # status label; defaults to name when blank
    color: str = "#0d9488"      # accent for the UI chip
    sort_order: int = 0
    is_active: bool = True
    letter_filing: bool = False  # file into an A–Z subfolder by last name

    def effective_status(self) -> str:
        return self.status.strip() or self.name.strip()


@dataclass
class Document:
    """A document tracked through triage."""
    id: Optional[int]
    filename: str
    current_path: str
    bucket_id: Optional[int] = None     # None → still in the New Rx inbox
    status: str = "New"
    patient_id: Optional[int] = None
    order_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    dismissed: bool = False              # removed from the queue without moving
    previous_path: Optional[str] = None      # for Undo: where the file came from
    previous_bucket_id: Optional[int] = None  # for Undo: prior bucket (None=inbox)

    @property
    def is_in_inbox(self) -> bool:
        return self.bucket_id is None and not self.dismissed

    @property
    def is_linked(self) -> bool:
        return self.patient_id is not None or self.order_id is not None


@dataclass
class DocumentEvent:
    """A single entry in a document's history ('the map of what transpired')."""
    id: Optional[int]
    document_id: int
    ts: str
    type: EventType
    detail: str = ""
    user: str = ""

    def describe(self) -> str:
        """Human-readable one-liner for the timeline."""
        text = {
            EventType.ARRIVED: "Arrived in New Rx",
            EventType.RENAMED: f"Renamed → {self.detail}",
            EventType.MOVED: f"Moved to “{self.detail}”",
            EventType.NOTE: f"Note: {self.detail}",
            EventType.LINKED: f"Linked to {self.detail}",
            EventType.UNLINKED: f"Unlinked {self.detail}".rstrip(),
            EventType.REOPENED: "Reopened into New Rx",
            EventType.DISMISSED: "Dismissed from queue (file left in place)",
            EventType.UNDONE: f"Undid move{(' — ' + self.detail) if self.detail else ''}",
            EventType.DELETED: "Deleted (moved to Trash)",
        }.get(self.type, self.detail or self.type.value)
        return text
