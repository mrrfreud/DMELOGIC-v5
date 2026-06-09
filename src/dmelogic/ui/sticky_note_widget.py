from __future__ import annotations

from typing import Optional, Dict, Any, List, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QColorDialog,
    QComboBox,
    QLabel,
    QMenu,
    QInputDialog,
    QMessageBox,
)

from dmelogic.db import sticky_notes as notes_db

DEFAULT_COLORS = ["#FFF59D", "#C8E6C9", "#BBDEFB", "#E1BEE7"]


def _lookup_patient_name(patient_id: int) -> str:
    """Get patient name by ID."""
    try:
        from dmelogic.db.patients import get_patient
        p = get_patient(patient_id)
        if p:
            return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or f"Patient #{patient_id}"
    except Exception:
        pass
    return f"Patient #{patient_id}"


def _lookup_order_info(order_id: int) -> str:
    """Get order info by ID."""
    try:
        from dmelogic.db.orders import get_order
        o = get_order(order_id)
        if o:
            status = o.get('order_status') or o.get('status', 'Unknown')
            patient = o.get('patient_name', '')
            if not patient:
                first = o.get('patient_first_name', '')
                last = o.get('patient_last_name', '')
                patient = f"{first} {last}".strip()
            if patient:
                return f"Order #{order_id} - {patient} ({status})"
            return f"Order #{order_id} ({status})"
    except Exception:
        pass
    return f"Order #{order_id}"


def _lookup_prescriber_name(prescriber_id: int) -> str:
    """Get prescriber name by ID."""
    try:
        from dmelogic.db.prescribers import get_prescriber
        p = get_prescriber(prescriber_id)
        if p:
            return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or f"Prescriber #{prescriber_id}"
    except Exception:
        pass
    return f"Prescriber #{prescriber_id}"


class StickyNoteWindow(QWidget):
    """Floating sticky note window with auto-save, color, pin, archive, and linking."""

    def __init__(self, note: Dict[str, Any], folder_path: Optional[str], parent=None):
        super().__init__(parent)
        self.note_id = note["id"]
        self.folder_path = folder_path
        self._minimized = False
        self._color = note.get("color") or DEFAULT_COLORS[0]
        self._pinned = bool(note.get("pinned"))
        self._links: List[Tuple[str, int]] = note.get("links", [])

        self.setWindowTitle(note.get("title") or f"Note #{self.note_id}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_save)

        self._build_ui()
        self._apply_color(self._color)
        self._apply_pinned(self._pinned)
        self._update_links_button()

        self.resize(340, 300)

        self.title.setText(note.get("title") or "")
        self.body.setPlainText(note.get("body") or "")

        self.title.textChanged.connect(self._schedule_save)
        self.body.textChanged.connect(self._schedule_save)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Top row: Title + action buttons
        top = QHBoxLayout()
        top.setSpacing(4)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Title")
        top.addWidget(self.title, 1)

        self.btn_pin = QPushButton("Pin")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setToolTip("Keep this note on top of other windows")
        self.btn_pin.clicked.connect(self._toggle_pin)
        top.addWidget(self.btn_pin)

        self.btn_minimize = QPushButton("Min")
        self.btn_minimize.setCheckable(True)
        self.btn_minimize.setToolTip("Collapse note to title bar only")
        self.btn_minimize.clicked.connect(self._toggle_minimize)
        top.addWidget(self.btn_minimize)

        self.btn_archive = QPushButton("Archive")
        self.btn_archive.setToolTip("Move note to archive (hide from main list)")
        self.btn_archive.clicked.connect(self._archive)
        top.addWidget(self.btn_archive)

        root.addLayout(top)

        # Links banner - shows what entities this note is linked to
        self.links_banner = QLabel("")
        self.links_banner.setWordWrap(True)
        # Banner styling set in _apply_color to match note color
        self.links_banner.hide()  # Hidden by default, shown when there are links
        root.addWidget(self.links_banner)

        # Body
        self.body = QTextEdit()
        self.body.setPlaceholderText("Write a quick note…")
        root.addWidget(self.body, 1)

        # Bottom row: color swatches + links
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        bottom.setContentsMargins(0, 0, 0, 0)

        # Color swatches - smaller size
        self._color_buttons = []
        for c in DEFAULT_COLORS:
            btn = QPushButton()
            btn.setFixedSize(18, 18)
            btn.setToolTip("Click to change note color")
            btn.clicked.connect(lambda checked, col=c: self._apply_color(col))
            bottom.addWidget(btn)
            self._color_buttons.append((btn, c))

        self.btn_pick_color = QPushButton("⋯")
        self.btn_pick_color.setFixedSize(18, 18)
        self.btn_pick_color.setToolTip("Pick a custom color")
        self.btn_pick_color.clicked.connect(self._pick_color)
        bottom.addWidget(self.btn_pick_color)

        bottom.addStretch()

        # Links button
        self.btn_links = QPushButton("Links")
        self.btn_links.setToolTip("Link this note to a patient, order, or prescriber")
        self.btn_links.clicked.connect(self._show_links_menu_btn)
        bottom.addWidget(self.btn_links)

        root.addLayout(bottom)

        self.setMinimumSize(280, 200)

    def _apply_color(self, hex_color: str) -> None:
        self._color = hex_color
        # Highlight selected swatch with updated styling
        for btn, c in self._color_buttons:
            if c == hex_color:
                btn.setStyleSheet(f"background-color: {c}; border: 2px solid #000; border-radius: 3px;")
            else:
                btn.setStyleSheet(f"background-color: {c}; border: 1px solid #666; border-radius: 3px;")
        
        # Custom color picker button
        self.btn_pick_color.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255,255,255,0.4);
                border: 1px solid #666;
                border-radius: 3px;
                color: #333;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255,255,255,0.7);
            }}
        """)
        
        # Update links banner styling to match note
        self.links_banner.setStyleSheet(f"""
            QLabel {{
                background: rgba(0,0,0,0.12);
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 9pt;
                font-weight: bold;
                color: #222;
            }}
        """)
        
        self.setStyleSheet(
            f"""
            QWidget {{
                background: {hex_color};
                border-radius: 6px;
            }}
            QLineEdit {{
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(0,0,0,0.2);
                border-radius: 4px;
                padding: 4px 6px;
                font-weight: 600;
                font-size: 11pt;
                color: #000000;
            }}
            QTextEdit {{
                background: rgba(255,255,255,0.6);
                border: 1px solid rgba(0,0,0,0.15);
                border-radius: 4px;
                padding: 4px;
                font-size: 10pt;
                color: #000000;
            }}
            QPushButton {{
                background: rgba(255,255,255,0.5);
                border: 1px solid rgba(0,0,0,0.15);
                border-radius: 3px;
                color: #000000;
                font-size: 9pt;
                padding: 3px 6px;
                min-width: 32px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.8);
            }}
            QPushButton:checked {{
                background: rgba(80,80,80,0.3);
                font-weight: bold;
            }}
            """
        )
        self._schedule_save()

    def _apply_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.btn_pin.setChecked(pinned)
        self.btn_pin.setText("Pinned" if pinned else "Pin")
        self.btn_pin.setToolTip("Currently pinned - stays on top" if pinned else "Click to keep on top")
        flags = self.windowFlags()
        if pinned:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()
        self._schedule_save()

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Pick note color")
        if color.isValid():
            self._apply_color(color.name())

    def _toggle_pin(self) -> None:
        self._apply_pinned(self.btn_pin.isChecked())

    def _toggle_minimize(self) -> None:
        self._minimized = self.btn_minimize.isChecked()
        self.btn_minimize.setText("Restore" if self._minimized else "Minimize")
        self.body.setVisible(not self._minimized)
        self._schedule_save()

    def _archive(self) -> None:
        notes_db.archive_note(self.note_id, archived=True, folder_path=self.folder_path)
        self.close()

    def _schedule_save(self) -> None:
        self._save_timer.start(400)

    def _flush_save(self) -> None:
        notes_db.update_note(
            self.note_id,
            title=self.title.text().strip(),
            body=self.body.toPlainText(),
            color=self._color,
            pinned=self._pinned,
            archived=False,
            folder_path=self.folder_path,
        )
        self.setWindowTitle(self.title.text().strip() or f"Note #{self.note_id}")

    def _update_links_button(self) -> None:
        """Update links button text and banner to show linked entities."""
        if not self._links:
            self.btn_links.setText("Links")
            self.btn_links.setToolTip("Link this note to a patient, order, or prescriber")
            self.links_banner.hide()
        else:
            self.btn_links.setText(f"Links ({len(self._links)})")
            # Build tooltip and banner showing linked items
            tips = []
            banner_parts = []
            for etype, eid in self._links:
                if etype == "patient":
                    name = _lookup_patient_name(eid)
                    tips.append(f"Patient: {name}")
                    banner_parts.append(f"👤 {name}")
                elif etype == "order":
                    info = _lookup_order_info(eid)
                    tips.append(f"Order: {info}")
                    banner_parts.append(f"📋 {info}")
                elif etype == "prescriber":
                    name = _lookup_prescriber_name(eid)
                    tips.append(f"Prescriber: {name}")
                    banner_parts.append(f"🩺 Dr. {name}")
            self.btn_links.setToolTip("Linked to:\n" + "\n".join(tips))
            
            # Update banner
            self.links_banner.setText("Linked to: " + " | ".join(banner_parts))
            self.links_banner.show()

    def _show_links_menu_btn(self) -> None:
        """Show links menu from button click."""
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { color: #000000; } QMenu::item { padding: 6px 20px; }")
        menu.addAction("Link to Patient...", lambda: self._add_link("patient"))
        menu.addAction("Link to Order...", lambda: self._add_link("order"))
        menu.addAction("Link to Prescriber...", lambda: self._add_link("prescriber"))
        if self._links:
            menu.addSeparator()
            for etype, eid in self._links:
                if etype == "patient":
                    name = _lookup_patient_name(eid)
                elif etype == "order":
                    name = _lookup_order_info(eid)
                else:
                    name = _lookup_prescriber_name(eid)
                action = menu.addAction(f"Remove: {name}")
                action.triggered.connect(lambda checked, t=etype, i=eid: self._remove_link(t, i))
        menu.exec(self.btn_links.mapToGlobal(self.btn_links.rect().bottomLeft()))

    def _add_link(self, entity_type: str) -> None:
        """Add a link using name-based search."""
        if entity_type == "patient":
            self._link_patient()
        elif entity_type == "order":
            self._link_order()
        elif entity_type == "prescriber":
            self._link_prescriber()

    def _link_patient(self) -> None:
        """Search and link to a patient by name."""
        try:
            from dmelogic.db.patients import search_patients
            search_term, ok = QInputDialog.getText(self, "Link to Patient", "Search patient by name:")
            if not ok or not search_term.strip():
                return
            results = search_patients(search_term.strip())
            if not results:
                QMessageBox.information(self, "No Results", f"No patients found matching '{search_term}'")
                return
            # Build choice list
            choices = []
            for p in results[:20]:  # Limit to 20
                name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                dob = p.get('dob', '')
                choices.append(f"{name} (DOB: {dob}) [ID: {p['id']}]")
            choice, ok = QInputDialog.getItem(self, "Select Patient", "Choose patient to link:", choices, 0, False)
            if not ok:
                return
            idx = choices.index(choice)
            patient_id = int(results[idx]["id"])
            if ("patient", patient_id) not in self._links:
                self._links.append(("patient", patient_id))
                notes_db.set_note_links(self.note_id, self._links, folder_path=self.folder_path)
                self._update_links_button()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not search patients: {e}")

    def _link_order(self) -> None:
        """Search and link to an order."""
        try:
            from dmelogic.db.orders import search_orders
            search_term, ok = QInputDialog.getText(self, "Link to Order", "Search by order # or patient name:")
            if not ok or not search_term.strip():
                return
            results = search_orders(search_term.strip())
            if not results:
                QMessageBox.information(self, "No Results", f"No orders found matching '{search_term}'")
                return
            choices = []
            for o in results[:20]:
                patient = o.get('patient_name', f"Patient #{o.get('patient_id', '?')}")
                status = o.get('status', 'Unknown')
                choices.append(f"Order #{o['id']} - {patient} ({status})")
            choice, ok = QInputDialog.getItem(self, "Select Order", "Choose order to link:", choices, 0, False)
            if not ok:
                return
            idx = choices.index(choice)
            order_id = int(results[idx]["id"])
            if ("order", order_id) not in self._links:
                self._links.append(("order", order_id))
                notes_db.set_note_links(self.note_id, self._links, folder_path=self.folder_path)
                self._update_links_button()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not search orders: {e}")

    def _link_prescriber(self) -> None:
        """Search and link to a prescriber by name."""
        try:
            from dmelogic.db.prescribers import search_prescribers
            search_term, ok = QInputDialog.getText(self, "Link to Prescriber", "Search prescriber by name:")
            if not ok or not search_term.strip():
                return
            results = search_prescribers(search_term.strip())
            if not results:
                QMessageBox.information(self, "No Results", f"No prescribers found matching '{search_term}'")
                return
            choices = []
            for p in results[:20]:
                name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                npi = p.get('npi', '')
                choices.append(f"{name} (NPI: {npi}) [ID: {p['id']}]")
            choice, ok = QInputDialog.getItem(self, "Select Prescriber", "Choose prescriber to link:", choices, 0, False)
            if not ok:
                return
            idx = choices.index(choice)
            prescriber_id = int(results[idx]["id"])
            if ("prescriber", prescriber_id) not in self._links:
                self._links.append(("prescriber", prescriber_id))
                notes_db.set_note_links(self.note_id, self._links, folder_path=self.folder_path)
                self._update_links_button()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not search prescribers: {e}")

    def _remove_link(self, entity_type: str, entity_id: int) -> None:
        self._links = [(t, i) for (t, i) in self._links if not (t == entity_type and i == entity_id)]
        notes_db.set_note_links(self.note_id, self._links, folder_path=self.folder_path)
        self._update_links_button()

    def closeEvent(self, event):
        self._flush_save()
        super().closeEvent(event)

    def moveEvent(self, event):
        self._schedule_save()
        super().moveEvent(event)

    def resizeEvent(self, event):
        self._schedule_save()
        super().resizeEvent(event)
