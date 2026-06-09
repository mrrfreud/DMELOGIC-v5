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

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from dmelogic.triage.models import Document
from dmelogic.triage.service import TriageService, new_rx_folder
from dmelogic.triage.ui.bucket_manager import BucketManagerDialog
from dmelogic.triage.ui.viewer import DocumentViewer

logger = logging.getLogger("triage.ui")

_INBOX_KEY = "__inbox__"
_ALL_KEY = "__all__"


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
        title = QLabel("New Rx")
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
        self.locations = QListWidget()
        self.locations.setMaximumWidth(240)
        self.locations.currentItemChanged.connect(self._on_location_changed)
        left_l.addWidget(self.locations, 1)
        left_l.addWidget(self._muted("DOCUMENTS"))
        self.doc_list = QListWidget()
        self.doc_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.doc_list.currentItemChanged.connect(self._on_doc_changed)
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
        self.rename_btn.clicked.connect(self._rename)
        self.link_btn = QPushButton("🔗 Link patient")
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

    def _refresh_locations(self) -> None:
        counts = self.svc.counts()
        prev = self._location
        self.locations.blockSignals(True)
        self.locations.clear()

        inbox_item = QListWidgetItem(f"📥  New Rx  ({counts.get(None, 0)})")
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

        # Restore selection
        self.locations.blockSignals(False)
        self._select_location(prev)

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

        self.doc_list.blockSignals(True)
        self.doc_list.clear()
        for d in docs:
            item = QListWidgetItem(d.filename)
            item.setData(Qt.ItemDataRole.UserRole, d.id)
            tip = d.status + (f"  •  linked" if d.is_linked else "")
            item.setToolTip(tip)
            self.doc_list.addItem(item)
        self.doc_list.blockSignals(False)

        # Reselect
        if prev_id is not None:
            for i in range(self.doc_list.count()):
                if self.doc_list.item(i).data(Qt.ItemDataRole.UserRole) == prev_id:
                    self.doc_list.setCurrentRow(i)
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
            btn.setStyleSheet(
                f"text-align:left; padding:6px 10px; border-left:4px solid {b.color};"
            )
            btn.clicked.connect(lambda _=False, bucket=b: self._move_to(bucket))
            self.routing_layout.addWidget(btn)
        reopen = QPushButton("↩ Back to New Rx")
        reopen.clicked.connect(self._reopen)
        self.routing_layout.addWidget(reopen)

    # ── selection ───────────────────────────────────────────────────────
    def _on_location_changed(self, *_):
        item = self.locations.currentItem()
        if item:
            self._location = item.data(Qt.ItemDataRole.UserRole)
            self._refresh_doc_list()

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
        self.detail_status.setText(f"Status: {doc.status}")
        if doc.is_linked:
            bits = []
            if doc.patient_id is not None:
                bits.append(f"patient #{doc.patient_id}")
            if doc.order_id is not None:
                bits.append(f"order #{doc.order_id}")
            self.detail_link.setText("🔗 " + ", ".join(bits))
        else:
            self.detail_link.setText("Not linked")
        self._refresh_history()

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
        new, ok = QInputDialog.getText(
            self, "Rename document", "New file name:", text=self._current_doc.filename
        )
        if ok and new.strip():
            self._current_doc = self.svc.rename_document(self._current_doc, new)
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
        self._current_doc = self.svc.move_to_bucket(self._current_doc, bucket)
        self.refresh(keep_selection=True)

    def _reopen(self):
        if not self._current_doc:
            return
        self._current_doc = self.svc.reopen(self._current_doc)
        self.refresh(keep_selection=True)

    def _link_patient(self):
        if not self._current_doc:
            return
        # Lightweight linker for now; a full patient picker arrives when the
        # triage screen is wired into the main window's Patients tab.
        pid, ok = QInputDialog.getInt(
            self, "Link to patient", "Patient ID:", min=1
        )
        if ok:
            self._current_doc = self.svc.link(
                self._current_doc, patient_id=pid, label=f"patient #{pid}"
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
