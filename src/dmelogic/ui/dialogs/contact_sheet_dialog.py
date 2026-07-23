"""
contact_sheet_dialog.py
=======================
The contact sheet for an organization we fax — another DME supplier we refer a
patient to, or an insurance / MLTC.

Organizations have no NPI or prescriber details. What matters is who to reach
and where to fax: the facility name, the named person (and their extension),
and the default message that opens the fax cover sheet. Their addresses and fax
numbers live as locations, managed from the same screen.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QFormLayout, QTextEdit, QMessageBox, QDialogButtonBox, QGroupBox,
)

from dmelogic.fax_contacts import (
    CATEGORY_OPTIONS, DEFAULT_CATEGORY, normalize_category, default_cover_message,
)
from dmelogic.db import fax_contact_locations as repo


class ContactSheetDialog(QDialog):
    """Add or edit an organization contact."""

    def __init__(self, parent=None, contact: Optional[dict] = None,
                 category: Optional[str] = None, folder_path: Optional[str] = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.contact = dict(contact) if contact else None
        self.contact_id = self.contact.get("id") if self.contact else None
        is_edit = self.contact_id is not None

        self.setWindowTitle("Edit Contact" if is_edit else "Add Contact")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        # ---- Who ----
        who = QGroupBox("Contact")
        who_form = QFormLayout(who)

        self.name_edit = QLineEdit((self.contact or {}).get("display_name") or "")
        self.name_edit.setPlaceholderText("e.g. NUCARE Pharmacy & Surgical Supplies")
        who_form.addRow("Name:", self.name_edit)

        self.category_combo = QComboBox()
        for code, label in CATEGORY_OPTIONS:
            self.category_combo.addItem(label, code)
        start_cat = normalize_category(
            category or (self.contact or {}).get("category") or DEFAULT_CATEGORY
        )
        for i in range(self.category_combo.count()):
            if self.category_combo.itemData(i) == start_cat:
                self.category_combo.setCurrentIndex(i)
                break
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        who_form.addRow("Type:", self.category_combo)

        self.contact_person = QLineEdit((self.contact or {}).get("contact_person") or "")
        self.contact_person.setPlaceholderText("Person to ask for, e.g. Josefina")
        who_form.addRow("Contact person:", self.contact_person)

        self.contact_position = QLineEdit((self.contact or {}).get("contact_position") or "")
        self.contact_position.setPlaceholderText("e.g. Social Worker, Intake")
        who_form.addRow("Position:", self.contact_position)

        phone_row = QHBoxLayout()
        self.contact_phone = QLineEdit((self.contact or {}).get("contact_phone") or "")
        self.contact_phone.setPlaceholderText("Direct phone")
        self.contact_extension = QLineEdit((self.contact or {}).get("contact_extension") or "")
        self.contact_extension.setPlaceholderText("Ext.")
        self.contact_extension.setMaximumWidth(90)
        phone_row.addWidget(self.contact_phone)
        phone_row.addWidget(self.contact_extension)
        who_form.addRow("Direct phone:", phone_row)

        layout.addWidget(who)

        # ---- Where we fax (the primary location, edited inline) ----
        where = QGroupBox("Address && fax")
        where_form = QFormLayout(where)

        self._primary = None
        if self.contact_id:
            self._primary = repo.get_primary_location(self.contact_id, folder_path=folder_path)
        p = dict(self._primary) if self._primary else {}

        self.fax_edit = QLineEdit(p.get("fax") or "")
        self.fax_edit.setPlaceholderText("Fax number used for this contact")
        where_form.addRow("Fax:", self.fax_edit)

        self.phone_edit = QLineEdit(p.get("phone") or "")
        where_form.addRow("Phone:", self.phone_edit)

        self.address_line1 = QLineEdit(p.get("address_line1") or "")
        where_form.addRow("Address:", self.address_line1)

        self.address_line2 = QLineEdit(p.get("address_line2") or "")
        where_form.addRow("Address 2:", self.address_line2)

        csz = QHBoxLayout()
        self.city_edit = QLineEdit(p.get("city") or "")
        self.city_edit.setPlaceholderText("City")
        self.state_edit = QLineEdit(p.get("state") or "")
        self.state_edit.setPlaceholderText("ST")
        self.state_edit.setMaxLength(2)
        self.state_edit.setMaximumWidth(60)
        self.zip_edit = QLineEdit(p.get("zip_code") or "")
        self.zip_edit.setPlaceholderText("ZIP")
        self.zip_edit.setMaximumWidth(110)
        csz.addWidget(self.city_edit)
        csz.addWidget(self.state_edit)
        csz.addWidget(self.zip_edit)
        where_form.addRow("City / ST / ZIP:", csz)

        layout.addWidget(where)

        # ---- Fax cover sheet ----
        cover = QGroupBox("Fax cover sheet")
        cover_form = QFormLayout(cover)
        self.cover_message = QTextEdit()
        self.cover_message.setPlaceholderText("Message that opens every fax to this contact")
        self.cover_message.setMaximumHeight(80)
        existing_msg = (self.contact or {}).get("default_cover_message")
        self.cover_message.setPlainText(
            existing_msg if existing_msg is not None else default_cover_message(start_cat)
        )
        cover_form.addRow("Default message:", self.cover_message)
        layout.addWidget(cover)

        # ---- Notes ----
        notes_box = QGroupBox("Notes")
        notes_form = QVBoxLayout(notes_box)
        self.notes = QTextEdit((self.contact or {}).get("notes") or "")
        self.notes.setMaximumHeight(70)
        notes_form.addWidget(self.notes)
        layout.addWidget(notes_box)

        # ---- Locations ----
        loc_row = QHBoxLayout()
        self.loc_label = QLabel("")
        self.loc_label.setStyleSheet("color:#64748b;")
        self.btn_locations = QPushButton("📍 More locations…")
        self.btn_locations.setToolTip(
            "The address and fax above are this contact's primary location.\n"
            "Use this to add further offices, each with its own fax."
        )
        self.btn_locations.clicked.connect(self._open_locations)
        loc_row.addWidget(self.loc_label)
        loc_row.addStretch()
        loc_row.addWidget(self.btn_locations)
        layout.addLayout(loc_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_locations_label()

    # ---------------- internals ----------------

    def _on_category_changed(self) -> None:
        """Offer the matching default message when the type changes, if untouched."""
        cat = self.category_combo.currentData()
        current = self.cover_message.toPlainText().strip()
        known_defaults = {default_cover_message(c) for c, _ in CATEGORY_OPTIONS}
        known_defaults.discard("")
        if not current or current in known_defaults:
            self.cover_message.setPlainText(default_cover_message(cat))

    def _refresh_locations_label(self) -> None:
        if not self.contact_id:
            self.loc_label.setText("Additional offices can be added after saving.")
            self.btn_locations.setEnabled(False)
            return
        n = repo.count_locations(self.contact_id, folder_path=self.folder_path)
        extra = max(0, n - 1)
        self.loc_label.setText(
            f"{extra} additional location{'s' if extra != 1 else ''}" if extra
            else "No additional locations"
        )
        self.btn_locations.setEnabled(True)

    def _open_locations(self) -> None:
        from dmelogic.ui.dialogs.contact_locations_dialog import ContactLocationsDialog
        dlg = ContactLocationsDialog(
            contact_id=self.contact_id,
            contact_name=self.name_edit.text().strip(),
            folder_path=self.folder_path,
            parent=self,
        )
        dlg.exec()
        self._refresh_locations_label()

    def values(self) -> dict:
        return {
            "display_name": self.name_edit.text().strip(),
            "category": self.category_combo.currentData(),
            "contact_person": self.contact_person.text().strip(),
            "contact_position": self.contact_position.text().strip(),
            "contact_phone": self.contact_phone.text().strip(),
            "contact_extension": self.contact_extension.text().strip(),
            "default_cover_message": self.cover_message.toPlainText().strip(),
            "notes": self.notes.toPlainText().strip(),
        }

    def location_values(self) -> dict:
        """The address/fax fields on this sheet — they belong to the primary location."""
        return {
            "facility_name": self.name_edit.text().strip(),
            "fax": self.fax_edit.text().strip(),
            "phone": self.phone_edit.text().strip(),
            "address_line1": self.address_line1.text().strip(),
            "address_line2": self.address_line2.text().strip(),
            "city": self.city_edit.text().strip(),
            "state": self.state_edit.text().strip().upper(),
            "zip_code": self.zip_edit.text().strip(),
        }

    def _on_save(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Contact", "Enter a name for this contact.")
            return
        vals = self.values()
        loc_vals = self.location_values()
        try:
            if self.contact_id:
                repo.update_contact(self.contact_id, vals, folder_path=self.folder_path)
            else:
                self.contact_id = repo.create_organization_contact(
                    vals["display_name"], vals["category"],
                    default_cover_message=vals["default_cover_message"],
                    folder_path=self.folder_path,
                )
                if not self.contact_id:
                    QMessageBox.critical(self, "Contact", "Could not create the contact.")
                    return
                repo.update_contact(self.contact_id, vals, folder_path=self.folder_path)

            # Write the address/fax onto the primary location (creating it for a
            # new contact). update_location/add_location mirror it back onto the
            # contact's flat columns, so the fax is immediately usable elsewhere.
            primary = repo.get_primary_location(self.contact_id, folder_path=self.folder_path)
            if primary:
                repo.update_location(primary["id"], loc_vals, folder_path=self.folder_path)
            else:
                repo.add_location(self.contact_id, loc_vals, make_primary=True,
                                  folder_path=self.folder_path)
        except Exception as e:
            QMessageBox.critical(self, "Contact", f"Could not save the contact:\n{e}")
            return
        self.accept()
