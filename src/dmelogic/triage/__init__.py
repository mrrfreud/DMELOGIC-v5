"""
dmelogic.triage — New Rx document intake & triage.

A clean, self-contained replacement for the document-viewing portion of the
legacy PDFViewer. A prescription arrives in the **New Rx** folder (faxed,
scanned, or pasted), is viewed here, then renamed and routed to a
company-defined **bucket** (each bucket = a destination folder + a status).
Every document keeps a timestamped history of notes and auto-logged events,
is searchable, and can be optionally linked to a patient/order.

Layers:
    models   — plain dataclasses (Bucket, Document, DocumentEvent)
    store    — documents.db schema + CRUD
    service  — workflow operations (intake, rename, move, note, link, search)
    ui       — the triage screen (standalone-runnable and embeddable)
"""

from dmelogic.triage.models import Bucket, Document, DocumentEvent, EventType

__all__ = ["Bucket", "Document", "DocumentEvent", "EventType"]
