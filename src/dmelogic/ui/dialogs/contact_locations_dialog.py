"""
contact_locations_dialog.py
===========================
Manage the locations attached to a fax contact.

A prescriber may practise at several offices; each location carries its own
facility name, address, phone and fax, and exactly one is the primary. The
primary is mirrored onto the contact's flat columns by the repo layer, so the
rest of the app keeps seeing a single address/fax as it always has.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFormLayout,
    QDialogButtonBox, QAbstractItemView,
)

from dmelogic.db import fax_contact_locations as repo


class LocationEditDialog(QDialog):
    """Add / edit a single location."""

    def __init__(self, parent=None, values: Optional[dict] = None, title: str = "Location"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        values = values or {}

        form = QFormLayout()
        self.facility_name = QLineEdit(values.get("facility_name") or "")
        self.facility_name.setPlaceholderText("e.g. USA Vein Clinics — Bronx")
        self.address_line1 = QLineEdit(values.get("address_line1") or "")
        self.address_line2 = QLineEdit(values.get("address_line2") or "")
        self.city = QLineEdit(values.get("city") or "")
        self.state = QLineEdit(values.get("state") or "")
        self.state.setMaxLength(2)
        self.zip_code = QLineEdit(values.get("zip_code") or "")
        self.phone = QLineEdit(values.get("phone") or "")
        self.fax = QLineEdit(values.get("fax") or "")
        self.fax.setPlaceholderText("Used when faxing this location")
        self.notes = QLineEdit(values.get("notes") or "")

        form.addRow("Facility name:", self.facility_name)
        form.addRow("Address:", self.address_line1)
        form.addRow("Address 2:", self.address_line2)
        form.addRow("City:", self.city)
        form.addRow("State:", self.state)
        form.addRow("ZIP:", self.zip_code)
        form.addRow("Phone:", self.phone)
        form.addRow("Fax:", self.fax)
        form.addRow("Notes:", self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        if not self.facility_name.text().strip() and not self.fax.text().strip():
            QMessageBox.warning(
                self, "Location",
                "Enter at least a facility name or a fax number for this location.",
            )
            return
        self.accept()

    def values(self) -> dict:
        return {
            "facility_name": self.facility_name.text().strip(),
            "address_line1": self.address_line1.text().strip(),
            "address_line2": self.address_line2.text().strip(),
            "city": self.city.text().strip(),
            "state": self.state.text().strip().upper(),
            "zip_code": self.zip_code.text().strip(),
            "phone": self.phone.text().strip(),
            "fax": self.fax.text().strip(),
            "notes": self.notes.text().strip(),
        }


class ContactLocationsDialog(QDialog):
    """Table of a contact's locations with add / edit / delete / set-primary."""

    COLS = ["", "Facility Name", "Address", "City", "ST", "ZIP", "Phone", "Fax"]

    def __init__(self, contact_id: int, contact_name: str = "",
                 folder_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.contact_id = int(contact_id)
        self.folder_path = folder_path
        self.setWindowTitle(f"Locations — {contact_name}" if contact_name else "Locations")
        self.resize(880, 420)

        layout = QVBoxLayout(self)

        header = QLabel(
            f"<b>{contact_name}</b><br>"
            "<span style='color:#64748b'>A contact can have several locations "
            "(offices). The ★ primary location is the one used elsewhere in the "
            "app for this contact's address and fax.</span>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._edit_selected)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 28)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in range(2, len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_add = QPushButton("➕ Add Location")
        self.btn_edit = QPushButton("✏️ Edit")
        self.btn_primary = QPushButton("★ Set as Primary")
        self.btn_delete = QPushButton("🗑 Delete")
        self.btn_close = QPushButton("Close")
        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_primary.clicked.connect(self._set_primary)
        self.btn_delete.clicked.connect(self._delete)
        self.btn_close.clicked.connect(self.accept)
        for b in (self.btn_add, self.btn_edit, self.btn_primary, self.btn_delete):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.btn_close)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#64748b;font-size:12px;")
        layout.addWidget(self.status)

        self._reload()

    # ---------------- internals ----------------

    def _reload(self) -> None:
        rows = repo.fetch_locations(self.contact_id, folder_path=self.folder_path)
        self._rows = rows
        self.table.setRowCount(0)
        for i, r in enumerate(rows):
            self.table.insertRow(i)
            star = QTableWidgetItem("★" if r["is_primary"] else "")
            star.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if r["is_primary"]:
                star.setForeground(QBrush(QColor("#d97706")))
            self.table.setItem(i, 0, star)
            addr = " ".join(x for x in (r["address_line1"], r["address_line2"]) if x)
            for col, val in enumerate(
                [r["facility_name"], addr, r["city"], r["state"],
                 r["zip_code"], r["phone"], r["fax"]], start=1
            ):
                item = QTableWidgetItem(val or "")
                if r["is_primary"]:
                    f = item.font(); f.setBold(True); item.setFont(f)
                self.table.setItem(i, col, item)
        n = len(rows)
        self.status.setText(f"{n} location{'s' if n != 1 else ''}")
        self.btn_delete.setEnabled(n > 1)

    def _selected_row(self):
        idx = self.table.currentRow()
        if idx < 0 or idx >= len(self._rows):
            QMessageBox.information(self, "Locations", "Select a location first.")
            return None
        return self._rows[idx]

    def _add(self) -> None:
        dlg = LocationEditDialog(self, title="Add Location")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_id = repo.add_location(self.contact_id, dlg.values(), folder_path=self.folder_path)
            if new_id is None:
                QMessageBox.critical(self, "Locations", "Could not add the location.")
            self._reload()

    def _edit_selected(self) -> None:
        r = self._selected_row()
        if r is None:
            return
        dlg = LocationEditDialog(self, values=dict(r), title="Edit Location")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            repo.update_location(r["id"], dlg.values(), folder_path=self.folder_path)
            self._reload()

    def _set_primary(self) -> None:
        r = self._selected_row()
        if r is None:
            return
        repo.set_primary_location(self.contact_id, r["id"], folder_path=self.folder_path)
        self._reload()
        self.status.setText(
            f"★ {r['facility_name'] or 'Location'} is now primary — "
            "this contact's address and fax now use it."
        )

    def _delete(self) -> None:
        r = self._selected_row()
        if r is None:
            return
        if QMessageBox.question(
            self, "Delete Location",
            f"Delete '{r['facility_name'] or 'this location'}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if not repo.delete_location(r["id"], folder_path=self.folder_path):
            QMessageBox.warning(
                self, "Delete Location",
                "A contact must keep at least one location, so this one can't be removed.",
            )
        self._reload()
