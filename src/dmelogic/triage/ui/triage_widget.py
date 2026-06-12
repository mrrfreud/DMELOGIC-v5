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
        title = QLabel(intake_folder_name())
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
        self.link_btn = QPushButton("🔗 Link patient")
        self.link_btn.setStyleSheet(_BTN_PRIMARY)
        self.link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_btn.clicked.connect(self._link_patient)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.link_btn)
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

        inbox_item = QListWidgetItem(f"📥  {intake_folder_name()}  ({counts.get(None, 0)})")
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
        for w in (self.rename_btn, self.link_btn, self.note_edit, self.routing_host):
            w.setEnabled(on)

    # ── actions ─────────────────────────────────────────────────────────
    def _rename(self):
        if not self._current_doc:
            return
        # Suggest a name read from the document (OCR), falling back to current.
        suggestion = self._current_doc.filename
        if self._current_doc.detected_name:
            suggestion = self._current_doc.detected_name
            if self._current_doc.detected_dob:
                suggestion += f" ({self._current_doc.detected_dob})"
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
        dlg = PatientPickerDialog(self, initial=guess)
        if dlg.exec() and dlg.selected_patient():
            pid, label = dlg.selected_patient()
            self._current_doc = self.svc.link(
                self._current_doc, patient_id=pid, label=label
            )
            self._show_doc(self._current_doc)

    def _manage_buckets(self):
        dlg = BucketManagerDialog(self.svc.store, self)
        dlg.exec()
        self.refresh(keep_selection=True)


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
