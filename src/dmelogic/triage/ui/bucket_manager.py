"""
bucket_manager.py — add / remove / rename triage buckets.

Buckets are fully customizable per company: each has a name, a destination
folder, a status label, and a color. This dialog is the admin surface for that.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from dmelogic.triage.store import TriageStore


class BucketManagerDialog(QDialog):
    def __init__(self, store: TriageStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Manage Triage Buckets")
        self.resize(520, 440)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Buckets are the destinations a document can be routed to. "
            "Each maps to a folder and a status. Customize them to match your workflow."
        ))

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        body.addWidget(self.list, 1)

        # Editor panel
        editor = QVBoxLayout()
        editor.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit()
        editor.addWidget(self.name_edit)
        editor.addWidget(QLabel("Status label"))
        self.status_edit = QLineEdit()
        editor.addWidget(self.status_edit)
        editor.addWidget(QLabel("Destination folder (relative to data root, or absolute)"))
        self.folder_edit = QLineEdit()
        editor.addWidget(self.folder_edit)

        color_row = QHBoxLayout()
        self.color_btn = QPushButton("Color…")
        self.color_btn.clicked.connect(self._pick_color)
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(28, 28)
        self._set_swatch("#0d9488")
        color_row.addWidget(self.color_btn)
        color_row.addWidget(self.color_swatch)
        color_row.addStretch()
        editor.addLayout(color_row)

        self.letter_filing_cb = QCheckBox("File into A–Z subfolder by last name")
        self.letter_filing_cb.setToolTip(
            "When on, documents routed here are filed into a per-last-name letter "
            "subfolder (e.g. SMITH → …\\S\\). Keeps each folder small and lookups fast.")
        editor.addWidget(self.letter_filing_cb)

        self.save_btn = QPushButton("Save changes")
        self.save_btn.clicked.connect(self._save_current)
        editor.addWidget(self.save_btn)
        editor.addStretch()
        body.addLayout(editor, 1)
        root.addLayout(body, 1)

        # Bottom buttons
        btns = QHBoxLayout()
        add_btn = QPushButton("➕ Add bucket")
        add_btn.clicked.connect(self._add)
        self.remove_btn = QPushButton("🗑 Remove")
        self.remove_btn.clicked.connect(self._remove)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(add_btn)
        btns.addWidget(self.remove_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        root.addLayout(btns)

        self._current_color = "#0d9488"
        self.reload()

    # ── data ────────────────────────────────────────────────────────────
    def reload(self) -> None:
        self.list.clear()
        for b in self.store.list_buckets(include_inactive=False):
            item = QListWidgetItem(b.name)
            item.setData(Qt.ItemDataRole.UserRole, b.id)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _current_id(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_select(self, *_):
        bid = self._current_id()
        if bid is None:
            return
        b = self.store.get_bucket(bid)
        if not b:
            return
        self.name_edit.setText(b.name)
        self.status_edit.setText(b.status)
        self.folder_edit.setText(b.folder)
        self._set_swatch(b.color)
        self.letter_filing_cb.setChecked(b.letter_filing)

    # ── actions ─────────────────────────────────────────────────────────
    def _add(self):
        name, ok = QInputDialog.getText(self, "Add bucket", "Bucket name:")
        if ok and name.strip():
            self.store.add_bucket(name.strip())
            self.reload()
            # select the newly added (last) item
            self.list.setCurrentRow(self.list.count() - 1)

    def _remove(self):
        bid = self._current_id()
        if bid is None:
            return
        b = self.store.get_bucket(bid)
        if not b:
            return
        resp = QMessageBox.question(
            self, "Remove bucket",
            f"Remove the “{b.name}” bucket?\n\n"
            "Documents already routed there keep their history; the bucket is "
            "hidden from new routing.",
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.store.remove_bucket(bid)
            self.reload()

    def _save_current(self):
        bid = self._current_id()
        if bid is None:
            return
        b = self.store.get_bucket(bid)
        if not b:
            return
        b.name = self.name_edit.text().strip() or b.name
        b.status = self.status_edit.text().strip()
        b.folder = self.folder_edit.text().strip() or b.folder
        b.color = self._current_color
        b.letter_filing = self.letter_filing_cb.isChecked()
        self.store.update_bucket(b)
        self.reload()

    def _pick_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self._set_swatch(color.name())

    def _set_swatch(self, hex_color: str):
        self._current_color = hex_color
        self.color_swatch.setStyleSheet(
            f"background:{hex_color}; border:1px solid #94a3b8; border-radius:4px;"
        )
