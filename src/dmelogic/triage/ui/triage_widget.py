"""
triage_widget.py — the New Rx triage screen.

Layout:  [ locations + document queue ] | [ document viewer ] | [ details ]

Locations are the New Rx inbox plus each customizable bucket (with live
counts). Selecting a document shows it in the viewer and exposes its details:
rename, route-to-bucket, optional patient link, notes, and the full history
timeline. The screen is self-contained — runnable standalone for testing and
embeddable as a tab.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QThread
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from dmelogic.triage.models import Document
from dmelogic.triage.service import TriageService, new_rx_folder, intake_folder_name
from dmelogic.triage.ui.bucket_manager import BucketManagerDialog
from dmelogic.triage.ui.viewer import DocumentViewer

logger = logging.getLogger("triage.ui")

_INBOX_KEY = "__inbox__"
_ALL_KEY = "__all__"

# Shared modern button styles (light theme) — same original palette as the
# main tabs: one blue accent + neutral slate/white surfaces.
_BTN_PRIMARY = (
    "QPushButton { background:#2563eb; color:#ffffff; font-weight:600;"
    " padding:7px 14px; border:none; border-radius:8px; }"
    "QPushButton:hover { background:#1d4ed8; }"
)
_BTN_GHOST = (
    "QPushButton { background:#ffffff; color:#0f172a; font-weight:600;"
    " padding:7px 14px; border:1px solid #e2e8f0; border-radius:8px; }"
    "QPushButton:hover { background:#f1f5f9; border-color:#cbd5e1; }"
)


def _inbox_display_label() -> str:
    """Always show New Rx in UI, even when intake folder is custom-named."""
    configured = (intake_folder_name() or "").strip()
    if configured and configured.lower() != "new rx":
        return f"New Rx ({configured})"
    return "New Rx"


class _DropLocationsList(QListWidget):
    """Locations list that accepts a document dragged from the queue.

    Dropping a document onto a bucket routes it there; dropping onto New Rx
    reopens it. The actual move is handled by the parent via ``on_drop``.
    """

    def __init__(self, on_drop, parent: QWidget | None = None):
        super().__init__(parent)
        self._on_drop = on_drop
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        if event.source() is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        # Note: do NOT change the current item here — switching locations
        # mid-drag would change which document is selected.
        item = self.itemAt(event.position().toPoint())
        if item is not None and event.source() is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item is not None:
            try:
                self._on_drop(item.data(Qt.ItemDataRole.UserRole))
            except Exception as e:  # pragma: no cover
                logger.warning("drop routing failed: %s", e)
        event.acceptProposedAction()
        # Deliberately not calling super().dropEvent — we route the document
        # ourselves and never want the default item-reparenting behavior.


class TriageWidget(QWidget):
    def __init__(self, service: TriageService | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.svc = service or TriageService()
        self._current_doc: Document | None = None
        self._location = _INBOX_KEY

        self._run_bucket_scans_migration_once()

        self._build_ui()
        self.refresh(scan=True)

        # Auto-pick up newly arrived files every few seconds.
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(lambda: self.refresh(scan=True, keep_selection=True))
        self._timer.start()

    # ── UI construction ─────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Header
        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel(_inbox_display_label())
        title.setStyleSheet("font-size:20px; font-weight:800; color:#0f172a;")
        subtitle = QLabel("Review incoming prescriptions, route them, and track what's happening")
        subtitle.setStyleSheet("color:#64748b; font-size:11px;")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block)
        header.addSpacing(24)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search documents, notes, status…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        self.search_edit.setFixedWidth(300)
        header.addWidget(self.search_edit)
        header.addStretch()
        capture_btn = QPushButton("📱 Capture Rx")
        capture_btn.setStyleSheet(_BTN_PRIMARY)
        capture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        capture_btn.setToolTip("Use your phone to photograph an Rx — generates a QR code, converts photos to PDF, and places the file in New Orders")
        capture_btn.clicked.connect(self._open_phone_capture)
        header.addWidget(capture_btn)
        refresh_btn = QPushButton("↻ Refresh New Rx")
        refresh_btn.setStyleSheet(_BTN_GHOST)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setToolTip(f"Rescan and reload documents from: {new_rx_folder()}")
        refresh_btn.clicked.connect(lambda: self.refresh(scan=True, keep_selection=True))
        header.addWidget(refresh_btn)
        manage_btn = QPushButton("⚙ Manage Buckets")
        manage_btn.setStyleSheet(_BTN_GHOST)
        manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_btn.clicked.connect(self._manage_buckets)
        header.addWidget(manage_btn)
        root.addLayout(header)

        # Body splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: locations + queue (card)
        left = QFrame()
        left.setFrameShape(QFrame.Shape.StyledPanel)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(10, 10, 10, 10)
        left_l.addWidget(self._muted("LOCATIONS"))
        self.locations = _DropLocationsList(self._on_document_dropped)
        self.locations.setMaximumWidth(240)
        self.locations.currentItemChanged.connect(self._on_location_changed)
        left_l.addWidget(self.locations, 1)
        left_l.addWidget(self._muted("DOCUMENTS"))
        self.doc_list = QListWidget()
        self.doc_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.doc_list.currentItemChanged.connect(self._on_doc_changed)
        # Allow dragging a document onto a bucket in Locations to route it.
        self.doc_list.setDragEnabled(True)
        self.doc_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.doc_list.setToolTip("Tip: drag a document onto a bucket to route it, "
                                 "or use the MOVE TO buttons.")
        left_l.addWidget(self.doc_list, 3)
        splitter.addWidget(left)

        # Center: viewer (card)
        viewer_card = QFrame()
        viewer_card.setFrameShape(QFrame.Shape.StyledPanel)
        vc_l = QVBoxLayout(viewer_card)
        vc_l.setContentsMargins(8, 8, 8, 8)
        self.viewer = DocumentViewer()
        self.viewer.trimRequested.connect(self._apply_trim)
        vc_l.addWidget(self.viewer)
        splitter.addWidget(viewer_card)

        # Right: details
        splitter.addWidget(self._build_details_panel())

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 3)
        root.addWidget(splitter, 1)

    def _build_details_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)

        self.detail_name = QLabel("—")
        self.detail_name.setWordWrap(True)
        self.detail_name.setStyleSheet("font-weight:700; font-size:14px;")
        layout.addWidget(self.detail_name)

        self.detail_status = QLabel("")
        self.detail_status.setStyleSheet("color:#475569;")
        layout.addWidget(self.detail_status)

        self.detail_link = QLabel("")
        self.detail_link.setStyleSheet("color:#0d9488;")
        layout.addWidget(self.detail_link)

        # Action buttons
        actions = QHBoxLayout()
        self.rename_btn = QPushButton("✎ Rename")
        self.rename_btn.setStyleSheet(_BTN_GHOST)
        self.rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rename_btn.clicked.connect(self._rename)
        self.trim_doc_btn = QPushButton("✂ Trim")
        self.trim_doc_btn.setStyleSheet(_BTN_GHOST)
        self.trim_doc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.trim_doc_btn.setToolTip("Drag a box on the document to trim it manually")
        self.trim_doc_btn.clicked.connect(self._toggle_trim_mode)
        self.undo_trim_btn = QPushButton("↶ Undo Trim")
        self.undo_trim_btn.setStyleSheet(_BTN_GHOST)
        self.undo_trim_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_trim_btn.setToolTip("Restore the document to its state before the last trim")
        self.undo_trim_btn.clicked.connect(self._undo_trim)
        self.link_btn = QPushButton("🔗 Link patient")
        self.link_btn.setStyleSheet(_BTN_PRIMARY)
        self.link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_btn.clicked.connect(self._link_patient)
        self.create_order_btn = QPushButton("🛒 Create Order")
        self.create_order_btn.setStyleSheet(_BTN_PRIMARY)
        self.create_order_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.create_order_btn.setToolTip("Create a new order for the linked patient")
        self.create_order_btn.clicked.connect(self._create_order)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.trim_doc_btn)
        actions.addWidget(self.undo_trim_btn)
        actions.addWidget(self.link_btn)
        actions.addWidget(self.create_order_btn)
        layout.addLayout(actions)

        # Routing
        layout.addWidget(self._muted("MOVE TO"))
        self.routing_host = QWidget()
        self.routing_layout = QVBoxLayout(self.routing_host)
        self.routing_layout.setContentsMargins(0, 0, 0, 0)
        self.routing_layout.setSpacing(4)
        layout.addWidget(self.routing_host)

        # Notes
        layout.addWidget(self._muted("ADD NOTE"))
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("Record what's going on with this document…")
        self.note_edit.setFixedHeight(60)
        layout.addWidget(self.note_edit)
        add_note_btn = QPushButton("Add note")
        add_note_btn.setStyleSheet(_BTN_GHOST)
        add_note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_note_btn.clicked.connect(self._add_note)
        layout.addWidget(add_note_btn)

        # History
        layout.addWidget(self._muted("HISTORY"))
        self.history_list = QListWidget()
        layout.addWidget(self.history_list, 1)

        self._set_details_enabled(False)
        return panel

    @staticmethod
    def _muted(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#94a3b8; font-size:11px; font-weight:600; margin-top:6px;")
        return lbl

    def _run_bucket_scans_migration_once(self) -> None:
        """One-time migration: bucketed docs -> Scans/<Letter> for current DB."""
        if getattr(self, "_bucket_scans_migration_done", False):
            return
        self._bucket_scans_migration_done = True
        try:
            moved = self.svc.migrate_bucketed_docs_to_scans_letters()
            if moved:
                logger.info("Triage migration moved %d bucketed doc(s) into Scans A-Z", moved)
        except Exception as e:
            logger.warning("Triage bucket->Scans migration failed: %s", e)

    # ── refresh ─────────────────────────────────────────────────────────
    def refresh(self, scan: bool = False, keep_selection: bool = False) -> None:
        if scan:
            try:
                self.svc.scan_inbox()
            except Exception as e:
                logger.warning("Inbox scan failed: %s", e)
        self._refresh_locations()
        self._refresh_doc_list(keep_selection=keep_selection)
        self._refresh_routing_buttons()
        if scan:
            self._start_ocr_if_needed()

    # ── background OCR ──────────────────────────────────────────────────
    def _start_ocr_if_needed(self) -> None:
        """OCR any new documents in the background (text search + auto-read)."""
        if getattr(self, "_ocr_thread", None) is not None and self._ocr_thread.isRunning():
            return
        try:
            jobs = self.svc.store.pending_ocr()
        except Exception:
            jobs = []
        if not jobs:
            return
        from dmelogic.triage.ocr import OcrWorker
        self._ocr_thread = QThread(self)
        self._ocr_worker = OcrWorker(jobs)
        self._ocr_worker.moveToThread(self._ocr_thread)
        self._ocr_thread.started.connect(self._ocr_worker.run)
        self._ocr_worker.one_done.connect(self._on_ocr_done)
        self._ocr_worker.finished.connect(self._ocr_thread.quit)
        self._ocr_thread.start()

    def _on_ocr_done(self, doc_id: int, result: dict) -> None:
        try:
            self.svc.store.set_ocr(
                doc_id, result.get("text", ""), result.get("quality", ""),
                result.get("name", ""), result.get("dob", ""),
            )
        except Exception as e:
            logger.warning("save OCR result failed: %s", e)
            return
        # Refresh labels (badges) and the selected document's details.
        self._refresh_doc_list(keep_selection=True)
        if self._current_doc and self._current_doc.id == doc_id:
            self._current_doc = self.svc.store.get_document(doc_id)
            self._show_doc(self._current_doc)

    def _refresh_locations(self) -> None:
        counts = self.svc.counts()
        prev = self._location
        self.locations.blockSignals(True)
        self.locations.clear()

        inbox_item = QListWidgetItem(f"📥  {_inbox_display_label()}  ({counts.get(None, 0)})")
        inbox_item.setData(Qt.ItemDataRole.UserRole, _INBOX_KEY)
        self.locations.addItem(inbox_item)

        all_item = QListWidgetItem("🗂  All documents")
        all_item.setData(Qt.ItemDataRole.UserRole, _ALL_KEY)
        self.locations.addItem(all_item)

        for b in self.svc.buckets():
            n = counts.get(b.id, 0)
            item = QListWidgetItem(f"●  {b.name}  ({n})")
            item.setData(Qt.ItemDataRole.UserRole, b.id)
            item.setForeground(Qt.GlobalColor.darkGray)
            self.locations.addItem(item)

        # Restore selection with signals still blocked — otherwise re-applying
        # the highlight re-fires _on_location_changed, which rebuilds the doc
        # list with keep_selection=False and snaps back to the first document.
        self._select_location(prev)
        self.locations.blockSignals(False)

    def _select_location(self, key) -> None:
        for i in range(self.locations.count()):
            if self.locations.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.locations.setCurrentRow(i)
                return
        if self.locations.count():
            self.locations.setCurrentRow(0)

    def _refresh_doc_list(self, keep_selection: bool = False) -> None:
        prev_id = self._current_doc.id if (keep_selection and self._current_doc) else None
        search = self.search_edit.text().strip()

        if search:
            docs = self.svc.search(search)
        elif self._location == _INBOX_KEY:
            docs = self.svc.inbox()
        elif self._location == _ALL_KEY:
            docs = self.svc.store.list_documents(bucket_id="ANY")
        else:
            docs = self.svc.in_bucket(self._location)

        # Always show newest first.
        # Priority:
        #  1) leading filename timestamp (e.g. 20260618_111125_*),
        #  2) file modified time on disk,
        #  3) DB updated/created timestamps.
        docs = sorted(docs, key=self._doc_sort_key, reverse=True)

        from dmelogic.triage.ocr import quality_badge, GOOD, FAIR
        from PyQt6.QtGui import QColor
        self.doc_list.blockSignals(True)
        self.doc_list.clear()
        for d in docs:
            # Flag low-confidence / unreadable scans (strict OCR) in the label.
            prefix = ""
            if d.ocr_done and d.ocr_quality not in (GOOD, FAIR, ""):
                prefix = "⚠  "
            item = QListWidgetItem(prefix + d.filename)
            item.setData(Qt.ItemDataRole.UserRole, d.id)
            badge = quality_badge(d.ocr_quality) if d.ocr_done else "OCR pending…"
            tip = f"{d.status}  •  {badge}"
            if d.is_linked:
                tip += "  •  linked"
            if d.detected_name:
                tip += f"  •  read: {d.detected_name}" + (f" (DOB {d.detected_dob})" if d.detected_dob else "")
            item.setToolTip(tip)
            if prefix:
                item.setForeground(QColor("#b45309"))
            self.doc_list.addItem(item)
        self.doc_list.blockSignals(False)

        # Reselect. When the previously-shown document is still in the list we
        # restore its highlight WITH SIGNALS BLOCKED so _on_doc_changed does not
        # re-fire — re-firing would reload the PDF and snap the viewer back to
        # page 1 on every 5-second poll. Only when the selection genuinely
        # changes (different doc, or the old one is gone) do we let _show_doc run.
        if prev_id is not None:
            for i in range(self.doc_list.count()):
                if self.doc_list.item(i).data(Qt.ItemDataRole.UserRole) == prev_id:
                    self.doc_list.blockSignals(True)
                    self.doc_list.setCurrentRow(i)
                    self.doc_list.blockSignals(False)
                    return
        if self.doc_list.count():
            self.doc_list.setCurrentRow(0)
        else:
            self._show_doc(None)

    def _doc_sort_key(self, doc: Document) -> tuple[float, int]:
        """Return comparable key so list order is newest -> oldest."""
        # 1) Prefer filename leading timestamp: YYYYMMDD[_-]HHMMSS
        ts = self._filename_timestamp(doc.filename)
        if ts is not None:
            return (ts, int(doc.id or 0))

        # 2) Then filesystem mtime (most reliable arrival proxy for renamed docs).
        try:
            mtime = Path(doc.current_path).stat().st_mtime
            return (float(mtime), int(doc.id or 0))
        except Exception:
            pass

        # 3) Fallback to DB timestamps.
        for s in (doc.updated_at, doc.created_at):
            if not s:
                continue
            try:
                dt = datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
                return (dt.timestamp(), int(doc.id or 0))
            except Exception:
                continue

        return (0.0, int(doc.id or 0))

    @staticmethod
    def _filename_timestamp(name: str) -> float | None:
        """Parse leading file timestamp like 20260618_111125 (or date-only)."""
        raw = (name or "").strip()
        # Matches: 20260618_111125..., 20260618-111125..., 20260618...
        m = re.match(r"^(\d{8})(?:[_-]?(\d{6}))?", raw)
        if not m:
            return None
        ymd = m.group(1)
        hms = m.group(2) or "000000"
        try:
            dt = datetime.strptime(f"{ymd}{hms}", "%Y%m%d%H%M%S")
            return dt.timestamp()
        except Exception:
            return None

    def _refresh_routing_buttons(self) -> None:
        while self.routing_layout.count():
            item = self.routing_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for b in self.svc.buckets():
            btn = QPushButton(b.name)
            # White "card" button with the bucket's color as a left accent
            # stripe and text tint — keeps the color coding without a solid fill.
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ text-align:left; padding:8px 12px; font-weight:600;"
                f" color:{b.color}; background:#ffffff;"
                f" border:1px solid #e2e8f0; border-left:4px solid {b.color};"
                f" border-radius:8px; }}"
                f"QPushButton:hover {{ background:#f8fafc; border-color:#cbd5e1;"
                f" border-left:4px solid {b.color}; }}"
            )
            btn.clicked.connect(lambda _=False, bucket=b: self._move_to(bucket))
            self.routing_layout.addWidget(btn)
        reopen = QPushButton("↩ Back to New Rx")
        reopen.setProperty("flat", True)
        reopen.clicked.connect(self._reopen)
        self.routing_layout.addWidget(reopen)

        undo = QPushButton("↶ Undo last move")
        undo.setProperty("flat", True)
        undo.clicked.connect(self._undo)
        self.routing_layout.addWidget(undo)

        dismiss = QPushButton("✕ Dismiss (leave file in place)")
        dismiss.setProperty("flat", True)
        dismiss.clicked.connect(self._dismiss)
        self.routing_layout.addWidget(dismiss)

        delete = QPushButton("🗑 Delete (move to Trash)")
        delete.setStyleSheet(
            "text-align:left; padding:6px 10px; color:#b91c1c; border:1px solid #fecaca;"
            " border-radius:8px; background:#ffffff;")
        delete.clicked.connect(self._delete)
        self.routing_layout.addWidget(delete)

    # ── selection ───────────────────────────────────────────────────────
    def _on_location_changed(self, *_):
        item = self.locations.currentItem()
        if item:
            self._location = item.data(Qt.ItemDataRole.UserRole)
            self._refresh_doc_list()

    def _on_document_dropped(self, key):
        """A document was dragged from the queue onto a location — route it."""
        if not self._current_doc:
            return
        if key == _INBOX_KEY:
            self._reopen()
        elif key == _ALL_KEY:
            return
        elif isinstance(key, int):
            bucket = self.svc.store.get_bucket(key)
            if bucket:
                self._move_to(bucket)

    def _on_doc_changed(self, *_):
        item = self.doc_list.currentItem()
        if not item:
            self._show_doc(None)
            return
        doc = self.svc.store.get_document(item.data(Qt.ItemDataRole.UserRole))
        self._show_doc(doc)

    def _on_search(self, _text: str):
        self._refresh_doc_list()

    def _show_doc(self, doc: Document | None) -> None:
        self._current_doc = doc
        if doc is None:
            self.viewer.clear()
            self.detail_name.setText("—")
            self.detail_status.setText("")
            self.detail_link.setText("")
            self.history_list.clear()
            self._set_details_enabled(False)
            return
        self._set_details_enabled(True)
        self.viewer.load(doc.current_path)
        self.detail_name.setText(doc.filename)
        from dmelogic.triage.ocr import quality_badge
        status_line = f"Status: {doc.status}"
        if doc.ocr_done:
            status_line += f"   ·   OCR: {quality_badge(doc.ocr_quality)}"
            if doc.detected_name:
                status_line += f"   ·   read: {doc.detected_name}"
                if doc.detected_dob:
                    status_line += f" (DOB {doc.detected_dob})"
        else:
            status_line += "   ·   OCR pending…"
        self.detail_status.setText(status_line)
        if doc.is_linked:
            bits = []
            if doc.patient_id is not None:
                # Show the patient's name (fall back to the id if not found).
                name = self._patient_display_name(doc.patient_id)
                bits.append(name or f"patient #{doc.patient_id}")
            if doc.order_id is not None:
                bits.append(f"order #{doc.order_id}")
            self.detail_link.setText("🔗 " + ", ".join(bits))
        else:
            self.detail_link.setText("Not linked")
        # Enable create order button only if patient is linked
        self.create_order_btn.setEnabled(doc.patient_id is not None)
        self._refresh_history()

    def _patient_display_name(self, patient_id: int) -> str:
        """Resolve a patient_id to a 'LAST, FIRST' display name (best-effort)."""
        try:
            from dmelogic.db.patients import fetch_patient_by_id
            row = fetch_patient_by_id(patient_id)
            if row is None:
                return ""
            d = dict(row)
            last = (d.get("last_name") or "").strip()
            first = (d.get("first_name") or "").strip()
            return ", ".join(p for p in (last, first) if p)
        except Exception:
            return ""

    def _refresh_history(self) -> None:
        self.history_list.clear()
        if not self._current_doc:
            return
        for ev in self.svc.history(self._current_doc):
            who = f"  — {ev.user}" if ev.user else ""
            item = QListWidgetItem(f"{ev.ts}{who}\n{ev.describe()}")
            self.history_list.addItem(item)

    def _set_details_enabled(self, on: bool) -> None:
        for w in (self.rename_btn, self.trim_doc_btn, self.undo_trim_btn, self.link_btn, self.create_order_btn, self.note_edit, self.routing_host):
            w.setEnabled(on)

    # ── actions ─────────────────────────────────────────────────────────
    def _rename(self):
        if not self._current_doc:
            return
        # Suggest: LAST, FIRST (DOB) CATEGORY RXDATE
        # Falls back to current filename when parsing is unavailable.
        suggestion = self._build_rename_suggestion(self._current_doc)
        new, ok = QInputDialog.getText(
            self, "Rename document", "New file name:", text=suggestion
        )
        if ok and new.strip():
            self.viewer.release()  # free the handle so the rename can succeed
            try:
                self._current_doc = self.svc.rename_document(self._current_doc, new)
            except Exception as e:
                # Surface the real reason instead of failing silently.
                QMessageBox.warning(self, "Rename failed", str(e))
            self.refresh(keep_selection=True)
            self._show_doc(self._current_doc)

    def _toggle_trim_mode(self):
        if not self._current_doc:
            return
        self.viewer.enable_trim_mode()

    def _apply_trim(self):
        if not self._current_doc:
            return
        crop_box = self.viewer.current_trim_box()
        if crop_box is None:
            QMessageBox.information(
                self,
                "Trim document",
                "Drag a box over the document before applying trim.",
            )
            return
        self.viewer.release()
        try:
            self._current_doc = self.svc.trim_document(
                self._current_doc,
                crop_box,
                page_index=self.viewer.current_page_index(),
            )
        except Exception as e:
            QMessageBox.warning(self, "Trim failed", str(e))
            return
        self.viewer.cancel_trim()
        self.refresh(keep_selection=True)
        self._show_doc(self._current_doc)

    def _undo_trim(self):
        if not self._current_doc:
            return
        self.viewer.release()
        try:
            self._current_doc = self.svc.undo_trim(self._current_doc)
        except Exception as e:
            QMessageBox.information(self, "Undo trim", str(e))
            return
        self.viewer.cancel_trim()
        self.refresh(keep_selection=True)
        self._show_doc(self._current_doc)

    def _build_rename_suggestion(self, doc: Document) -> str:
        """Build rename seed: LAST, FIRST (DOB) CATEGORY RXDATE."""
        suggestion = doc.filename

        # Base name + DOB from quick OCR fields.
        patient_name = (doc.detected_name or "").strip()
        patient_dob = (doc.detected_dob or "").strip()
        item_category = ""
        rx_date = ""

        try:
            from dmelogic.ocr_tools import extract_text_from_pdf
            from dmelogic.services.rx_parser import RxParser

            text = extract_text_from_pdf(doc.current_path) or ""
            parsed = RxParser().parse_text(text) if text.strip() else []
            if parsed:
                rx = parsed[0]
                if not patient_name:
                    patient_name = self._compose_patient_name(
                        (rx.patient.last_name or "").strip(),
                        (rx.patient.first_name or "").strip(),
                        (rx.patient.full_name or "").strip(),
                    )
                if not patient_dob:
                    patient_dob = (rx.patient.dob or "").strip()
                rx_date = self._normalize_date((rx.rx_date or "").strip())
                item_category = self._infer_item_category(
                    item_text=(rx.item.drug_name or ""),
                    source_text=(rx.source_text or ""),
                    icd_codes=getattr(rx, "icd_codes", None) or [],
                )
        except Exception as e:
            logger.debug("rename suggestion parse skipped: %s", e)

        parts: list[str] = []
        if patient_name:
            if patient_dob:
                parts.append(f"{patient_name} ({self._normalize_date(patient_dob)})")
            else:
                parts.append(patient_name)
        if item_category:
            parts.append(item_category)
        if rx_date:
            parts.append(rx_date)

        return " ".join(p for p in parts if p).strip() or suggestion

    @staticmethod
    def _compose_patient_name(last_name: str, first_name: str, full_name: str) -> str:
        if last_name or first_name:
            return ", ".join(p for p in (last_name, first_name) if p)
        return (full_name or "").strip()

    @staticmethod
    def _normalize_date(raw: str) -> str:
        txt = (raw or "").strip()
        if not txt:
            return ""
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
            try:
                return datetime.strptime(txt, fmt).strftime("%m-%d-%Y")
            except Exception:
                continue
        return txt.replace("/", "-")

    @staticmethod
    def _infer_item_category(item_text: str, source_text: str, icd_codes: list[str]) -> str:
        """Map parsed RX details into a short user-facing category label."""
        blob = " ".join([
            str(item_text or ""),
            str(source_text or ""),
            " ".join(str(c or "") for c in (icd_codes or [])),
        ]).lower()

        category_rules = [
            ("Incontinence", [
                "incontinence", "underpad", "underpads", "diaper", "diapers", "brief", "briefs", "pull-up", "pullups", "chux",
                "n39.", "r39.81", "r15.", "t452", "t453", "a4554",
            ]),
            ("Urological", ["catheter", "urinary bag", "urology", "foley"]),
            ("Ostomy", ["ostomy", "colostomy", "ileostomy"]),
            ("Wound Care", ["wound", "dressing", "bandage", "alginate", "foam dressing"]),
            ("Respiratory", ["cpap", "bipap", "nebulizer", "oxygen"]),
            ("Mobility", ["wheelchair", "walker", "rollator", "cane", "crutch"]),
            ("Diabetic", ["diabetic", "glucose", "glucometer", "test strip", "lancet"]),
            ("Compression", ["compression", "mmhg", "stocking", "jobst", "sigvaris"]),
        ]

        for label, keys in category_rules:
            if any(k in blob for k in keys):
                return label

        return "DME"

    def _add_note(self):
        if not self._current_doc:
            return
        text = self.note_edit.toPlainText().strip()
        if not text:
            return
        self.svc.add_note(self._current_doc, text)
        self.note_edit.clear()
        self._refresh_history()

    def _move_to(self, bucket):
        if not self._current_doc:
            return
        self.viewer.release()  # free the file handle so the move can succeed
        self._current_doc = self.svc.move_to_bucket(self._current_doc, bucket)
        self.refresh(keep_selection=True)

    def _undo(self):
        if not self._current_doc:
            return
        if not self._current_doc.previous_path:
            QMessageBox.information(self, "Undo", "Nothing to undo for this document.")
            return
        self.viewer.release()
        self._current_doc = self.svc.undo_last_move(self._current_doc)
        self.refresh(keep_selection=True)

    def _dismiss(self):
        if not self._current_doc:
            return
        resp = QMessageBox.question(
            self, "Dismiss document",
            "Remove this document from the queue?\n\n"
            "The file is left exactly where it is — only its place in the "
            "triage queue is cleared.",
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.svc.dismiss(self._current_doc)
            self._current_doc = None
            self.refresh()

    def _delete(self):
        if not self._current_doc:
            return
        resp = QMessageBox.question(
            self, "Delete document",
            f"Delete “{self._current_doc.filename}”?\n\n"
            "The file is moved to the Trash folder (recoverable) and removed "
            "from the queue.",
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.viewer.release()  # free the handle so the file can be moved
            self.svc.delete(self._current_doc)
            self._current_doc = None
            self.refresh()

    def _reopen(self):
        if not self._current_doc:
            return
        self.viewer.release()
        self._current_doc = self.svc.reopen(self._current_doc)
        self.refresh(keep_selection=True)

    def _link_patient(self):
        if not self._current_doc:
            return
        # Pre-fill the search with the OCR-read name (or the filename's last
        # name) so the right patient is usually one click away.
        source = self._current_doc.detected_name or self._current_doc.filename or ""
        guess = source.split(",")[0].strip()
        from dmelogic.triage.ui.patient_picker import PatientPickerDialog
        while True:
            dlg = PatientPickerDialog(self, initial=guess)
            result = dlg.exec()
            if result and dlg.selected_patient():
                pid, label = dlg.selected_patient()
                self._current_doc = self.svc.link(
                    self._current_doc, patient_id=pid, label=label
                )
                self._show_doc(self._current_doc)
                return

            if result == PatientPickerDialog.RESULT_CREATE_PATIENT:
                prefill = self._extract_patient_prefill(initial_name=guess)
                if self._open_new_patient_profile(guess, prefill=prefill):
                    continue
                return

            # If user couldn't find/select a patient, offer immediate creation.
            create_now = QMessageBox.question(
                self,
                "Patient Not Found",
                "Patient not found in the list.\n\nCreate a new patient profile now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if create_now != QMessageBox.StandardButton.Yes:
                return

            prefill = self._extract_patient_prefill(initial_name=guess)
            if not self._open_new_patient_profile(guess, prefill=prefill):
                return

    def _extract_patient_prefill(self, initial_name: str = "") -> dict:
        """Build patient defaults from detected RX data, for user completion."""
        if not self._current_doc:
            return {}

        def _split_name(name: str) -> tuple[str, str]:
            raw = (name or "").strip()
            if not raw:
                return "", ""
            # Strip trailing DOB hints from labels like "LAST, FIRST (DOB ...)".
            raw = re.sub(r"\s*\([^)]*DOB[^)]*\)\s*$", "", raw, flags=re.IGNORECASE).strip()
            if "," in raw:
                last, first = raw.split(",", 1)
                return last.strip(), first.strip()
            parts = raw.split()
            if len(parts) >= 2:
                return parts[-1].strip(), " ".join(parts[:-1]).strip()
            return raw, ""

        prefill: dict = {}

        # First pass: from already-read triage metadata.
        guessed_name = (self._current_doc.detected_name or initial_name or "").strip()
        last, first = _split_name(guessed_name)
        if last:
            prefill["last_name"] = last
        if first:
            prefill["first_name"] = first
        if self._current_doc.detected_dob:
            prefill["dob"] = str(self._current_doc.detected_dob).replace("-", "/")

        # Second pass: parse full OCR text to extract more demographics.
        try:
            from dmelogic.ocr_tools import extract_text_from_pdf
            from dmelogic.services.rx_parser import RxParser

            text = extract_text_from_pdf(self._current_doc.current_path) or ""
            parsed = RxParser().parse_text(text) if text.strip() else []
            if parsed:
                patient = parsed[0].patient
                if not prefill.get("last_name") and (patient.last_name or "").strip():
                    prefill["last_name"] = patient.last_name.strip()
                if not prefill.get("first_name") and (patient.first_name or "").strip():
                    prefill["first_name"] = patient.first_name.strip()
                if (not prefill.get("last_name") or not prefill.get("first_name")) and (patient.full_name or "").strip():
                    p_last, p_first = _split_name(patient.full_name)
                    if not prefill.get("last_name") and p_last:
                        prefill["last_name"] = p_last
                    if not prefill.get("first_name") and p_first:
                        prefill["first_name"] = p_first

                if not prefill.get("dob") and (patient.dob or "").strip():
                    prefill["dob"] = patient.dob.strip().replace("-", "/")
                if (patient.phone or "").strip():
                    prefill["phone"] = patient.phone.strip()
                if (patient.gender or "").strip():
                    prefill["gender"] = patient.gender.strip()
                if (patient.address or "").strip():
                    prefill["address"] = patient.address.strip()
                if (patient.city or "").strip():
                    prefill["city"] = patient.city.strip()
                if (patient.state or "").strip():
                    prefill["state"] = patient.state.strip()
                if (patient.zip_code or "").strip():
                    prefill["zip"] = patient.zip_code.strip()
        except Exception as e:
            logger.debug("RX prefill parse skipped: %s", e)

        return prefill

    def _open_new_patient_profile(self, initial_name: str = "", prefill: dict | None = None) -> bool:
        """Open the host app's patient creation flow from triage.

        Returns True if a create-profile flow was launched and should be followed
        by another link attempt.
        """
        parent = self.parent()
        while parent is not None:
            try:
                if hasattr(parent, "open_quick_add_patient_dialog"):
                    parent.open_quick_add_patient_dialog(prefill=(prefill or {}), initial_name=initial_name)
                    return True
                if hasattr(parent, "add_new_patient"):
                    parent.add_new_patient()
                    return True
            except Exception as e:
                QMessageBox.warning(self, "Create Patient", f"Could not open patient profile form:\n{e}")
                return False
            parent = parent.parent()

        QMessageBox.information(
            self,
            "Create Patient",
            "Patient creation form is not available in this view.",
        )
        return False

    def _create_order(self):
        """Create a new order for the linked patient from the current document."""
        if not self._current_doc or self._current_doc.patient_id is None:
            return
        
        # Fetch patient details to pre-fill the wizard
        patient_context = {'patient_id': self._current_doc.patient_id}
        try:
            from dmelogic.db.patients import fetch_patient_by_id
            row = fetch_patient_by_id(self._current_doc.patient_id)
            if row is not None:
                d = dict(row)
                # Pre-fill patient name as "LAST, FIRST"
                last = (d.get("last_name") or "").strip()
                first = (d.get("first_name") or "").strip()
                if last or first:
                    patient_context['name'] = ", ".join(p for p in (last, first) if p)
                # Pre-fill DOB if available (column name is 'dob', not 'date_of_birth')
                if d.get("dob"):
                    patient_context['dob'] = d.get("dob")
                # Pre-fill phone if available
                if d.get("phone"):
                    patient_context['phone'] = d.get("phone")
        except Exception:
            pass  # If patient lookup fails, at least pass the patient_id
        
        # Find the main window (parent chain)
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, 'open_new_order_wizard'):
                # Pass patient context to the wizard
                parent.open_new_order_wizard(
                    patient_context=patient_context
                )
                break
            parent = parent.parent()

    def _manage_buckets(self):
        dlg = BucketManagerDialog(self.svc.store, self)
        dlg.exec()
        self.refresh(keep_selection=True)

    def _open_phone_capture(self) -> None:
        """Show QR-code dialog so a phone can upload an Rx photo to New Orders."""
        from dmelogic.ui.dialogs.phone_rx_upload_dialog import PhoneRxUploadDialog
        folder = str(new_rx_folder())
        dlg = PhoneRxUploadDialog(save_folder=folder, parent=self)
        result = dlg.exec()
        if result == PhoneRxUploadDialog.DialogCode.Accepted:
            # Upload succeeded — refresh so the new PDF appears immediately
            self.refresh(scan=True, keep_selection=False)


# ── standalone runner ───────────────────────────────────────────────────
def main() -> int:
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("DMELogic — New Rx Triage (preview)")
    try:
        from dmelogic.ui.theme_modern import apply_modern_theme
        apply_modern_theme(app)
    except Exception:
        pass
    w = TriageWidget()
    w.resize(1280, 800)
    w.setWindowTitle("DMELogic — New Rx Triage (preview)")
    w.show()
    print(f"New Rx folder: {new_rx_folder()}")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
