"""
reserved_rx_manager.py
======================
DMELogic — Reserved RX on File Feature
Combines Option 1 (visual badge) + Option 4 (smart refill warning suppression)

WHAT THIS DOES:
  - Adds "RX on File" toggle to any order
  - Stores reserved RX metadata (doc path, date received, prescriber)
  - Suppresses "Fax MD for new RX" warning when RX is already on file
  - Shows badge in order list for orders with a reserved RX
  - Prompts to create new order when last refill ships

INTEGRATION POINTS (see bottom of file for copy-paste snippets):
  1. Run migrate_reserved_rx_columns() once on startup
  2. Add ReservedRxBadge to your order list row rendering
  3. Add ReservedRxPanel to your order detail / edit view
  4. Replace your last-refill warning logic with check_refill_warning()
"""

import os
import sqlite3
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QCheckBox, QFileDialog, QMessageBox, QDialog, QDialogButtonBox,
    QFrame, QToolTip, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QFont, QCursor, QIcon, QPixmap, QPainter


# ─────────────────────────────────────────────
#  DATABASE MIGRATION
# ─────────────────────────────────────────────

def migrate_reserved_rx_columns(db_path: str):
    """
    Run once on app startup. Safely adds Reserved RX columns to orders table.
    Call this in your main app init or database setup routine.

    Example:
        from dmelogic.reserved_rx_manager import migrate_reserved_rx_columns
        migrate_reserved_rx_columns(self.orders_db_path)
    """
    columns_to_add = [
        ("rx_on_file",         "INTEGER DEFAULT 0"),   # bool: 1 = yes
        ("reserved_rx_path",   "TEXT"),                # file path to scanned RX
        ("reserved_rx_date",   "TEXT"),                # date received (ISO)
        ("reserved_rx_md",     "TEXT"),                # prescriber name
        ("reserved_rx_notes",  "TEXT"),                # free-text notes
    ]
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(orders)")
        existing = {row[1] for row in cursor.fetchall()}
        for col_name, col_def in columns_to_add:
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE orders ADD COLUMN {col_name} {col_def}")
                print(f"[ReservedRX] Added column: {col_name}")
        conn.commit()
        conn.close()
        print("[ReservedRX] Migration complete.")
    except Exception as e:
        print(f"[ReservedRX] Migration error: {e}")


# ─────────────────────────────────────────────
#  CORE DATA FUNCTIONS
# ─────────────────────────────────────────────

def get_reserved_rx_data(db_path: str, order_id: str) -> dict:
    """Return reserved RX info for an order, or empty dict if none."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT rx_on_file, reserved_rx_path, reserved_rx_date,
                   reserved_rx_md, reserved_rx_notes
            FROM orders WHERE id = ?
        """, (order_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {}
    except Exception as e:
        print(f"[ReservedRX] Read error: {e}")
        return {}


def save_reserved_rx_data(db_path: str, order_id: str, data: dict):
    """
    Save reserved RX data for an order.
    data keys: rx_on_file, reserved_rx_path, reserved_rx_date,
                reserved_rx_md, reserved_rx_notes
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE orders SET
                rx_on_file        = :rx_on_file,
                reserved_rx_path  = :reserved_rx_path,
                reserved_rx_date  = :reserved_rx_date,
                reserved_rx_md    = :reserved_rx_md,
                reserved_rx_notes = :reserved_rx_notes
            WHERE id = :order_id
        """, {**data, "order_id": order_id})
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ReservedRX] Save error: {e}")


def clear_reserved_rx(db_path: str, order_id: str):
    """Clear the reserved RX flag (e.g., after new order is created from it)."""
    save_reserved_rx_data(db_path, order_id, {
        "rx_on_file": 0,
        "reserved_rx_path": None,
        "reserved_rx_date": None,
        "reserved_rx_md": None,
        "reserved_rx_notes": None,
    })


def get_all_orders_with_reserved_rx(db_path: str) -> list:
    """Return all orders that have rx_on_file = 1. Useful for a reserved RX queue view."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, patient_name, reserved_rx_date,
                   reserved_rx_md, reserved_rx_notes
            FROM orders
            WHERE rx_on_file = 1
            ORDER BY reserved_rx_date ASC
        """)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[ReservedRX] Queue fetch error: {e}")
        return []


# ─────────────────────────────────────────────
#  REFILL WARNING LOGIC
# ─────────────────────────────────────────────

def check_refill_warning(db_path: str, order_id: str, refills_remaining: int) -> dict:
    """
    Call this wherever you currently show the "last refill — fax MD" warning.

    Returns a dict:
      {
        "action":  "none" | "fax_md" | "create_new_order",
        "message": str,
        "rx_on_file": bool
      }

    Example usage:
        result = check_refill_warning(db_path, order_id, refills_remaining)
        if result["action"] == "fax_md":
            show_fax_md_dialog()
        elif result["action"] == "create_new_order":
            show_create_order_prompt(result["message"])
    """
    rx_data = get_reserved_rx_data(db_path, order_id)
    rx_on_file = bool(rx_data.get("rx_on_file", 0))

    if refills_remaining > 1:
        return {"action": "none", "message": "", "rx_on_file": rx_on_file}

    if refills_remaining == 1:
        # Last refill about to ship
        if rx_on_file:
            md = rx_data.get("reserved_rx_md", "Unknown MD")
            date = rx_data.get("reserved_rx_date", "")
            return {
                "action": "create_new_order",
                "message": (
                    f"This is the LAST refill on this order.\n\n"
                    f"✅ New RX on file from {md} (received {date}).\n\n"
                    f"Would you like to create a new order now using the reserved RX?"
                ),
                "rx_on_file": True
            }
        else:
            return {
                "action": "fax_md",
                "message": "This is the last refill. No RX on file — send fax to MD?",
                "rx_on_file": False
            }

    if refills_remaining == 0:
        if rx_on_file:
            return {
                "action": "create_new_order",
                "message": "Order complete. Reserved RX on file — create new order now?",
                "rx_on_file": True
            }
        else:
            return {
                "action": "fax_md",
                "message": "No refills remaining and no RX on file. Fax MD for new prescription?",
                "rx_on_file": False
            }

    return {"action": "none", "message": "", "rx_on_file": rx_on_file}


# ─────────────────────────────────────────────
#  UI: ORDER LIST BADGE
# ─────────────────────────────────────────────

class ReservedRxBadge(QLabel):
    """
    Small colored badge to show in the order list row.
    Drop this into your table row or list item widget wherever order ID is shown.

    Usage:
        badge = ReservedRxBadge(rx_on_file=True, tooltip_text="RX on file from Dr. Smith (02/18/2026)")
        layout.addWidget(badge)
    """

    def __init__(self, rx_on_file: bool = False, tooltip_text: str = "", parent=None):
        super().__init__(parent)
        self.rx_on_file = rx_on_file
        self._setup(tooltip_text)

    def _setup(self, tooltip_text: str):
        if self.rx_on_file:
            self.setText(" 📋 RX ")
            self.setStyleSheet("""
                QLabel {
                    background-color: #1a7f37;
                    color: white;
                    border-radius: 4px;
                    padding: 1px 5px;
                    font-size: 10px;
                    font-weight: bold;
                }
            """)
            self.setToolTip(tooltip_text or "New RX on file — ready for next order")
        else:
            self.setText("")
            self.setStyleSheet("")
        self.setFixedHeight(18)

    def update_status(self, rx_on_file: bool, tooltip_text: str = ""):
        self.rx_on_file = rx_on_file
        self._setup(tooltip_text)


# ─────────────────────────────────────────────
#  UI: ORDER DETAIL PANEL
# ─────────────────────────────────────────────

class ReservedRxPanel(QFrame):
    """
    Panel widget to embed in your order detail / edit view.
    Shows the RX-on-file toggle, date received, MD name, notes, and file attachment.

    Signals:
        data_changed(dict) — emitted whenever user changes any field

    Usage:
        self.rx_panel = ReservedRxPanel(db_path, order_id)
        self.rx_panel.data_changed.connect(self.on_rx_data_changed)
        layout.addWidget(self.rx_panel)

        # To load existing data:
        self.rx_panel.load(order_id)

        # To save:
        self.rx_panel.save()
    """

    data_changed = pyqtSignal(dict)

    def __init__(self, db_path: str, order_id: str = None, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.order_id = order_id
        self._build_ui()
        if order_id:
            self.load(order_id)

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #f0f7ff;
                border: 1px solid #90c2f5;
                border-radius: 6px;
                padding: 4px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(10, 8, 10, 8)

        # ── Header row ──
        header_row = QHBoxLayout()

        self.toggle = QCheckBox("  RX on File")
        self.toggle.setStyleSheet("""
            QCheckBox {
                font-weight: bold;
                font-size: 12px;
                color: #1a4a7a;
            }
            QCheckBox::indicator:checked {
                background-color: #1a7f37;
                border: 1px solid #1a7f37;
                border-radius: 3px;
            }
        """)
        self.toggle.stateChanged.connect(self._on_toggle)
        header_row.addWidget(self.toggle)

        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size: 11px; color: #555;")
        header_row.addWidget(self.status_label)
        header_row.addStretch()

        main_layout.addLayout(header_row)

        # ── Detail section (shown when toggled on) ──
        self.detail_frame = QWidget()
        detail_layout = QVBoxLayout(self.detail_frame)
        detail_layout.setSpacing(5)
        detail_layout.setContentsMargins(0, 4, 0, 0)

        # Date received
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("Date Received:"))
        self.date_label = QLabel("—")
        self.date_label.setStyleSheet("font-weight: bold; color: #1a4a7a;")
        date_row.addWidget(self.date_label)
        self.set_today_btn = QPushButton("Set Today")
        self.set_today_btn.setFixedWidth(80)
        self.set_today_btn.setStyleSheet("""
            QPushButton {
                background: #1a7f37; color: white;
                border-radius: 4px; font-size: 10px; padding: 2px 6px;
            }
            QPushButton:hover { background: #145c27; }
        """)
        self.set_today_btn.clicked.connect(self._set_today)
        date_row.addWidget(self.set_today_btn)
        date_row.addStretch()
        detail_layout.addLayout(date_row)

        # Prescriber
        md_row = QHBoxLayout()
        md_row.addWidget(QLabel("Prescriber:"))
        self.md_label = QLabel("—")
        self.md_label.setStyleSheet("font-weight: bold; color: #1a4a7a;")
        md_row.addWidget(self.md_label)
        self.set_md_btn = QPushButton("Set MD")
        self.set_md_btn.setFixedWidth(65)
        self.set_md_btn.setStyleSheet("""
            QPushButton {
                background: #1a4a7a; color: white;
                border-radius: 4px; font-size: 10px; padding: 2px 6px;
            }
            QPushButton:hover { background: #0d3055; }
        """)
        self.set_md_btn.clicked.connect(self._set_md)
        md_row.addWidget(self.set_md_btn)
        md_row.addStretch()
        detail_layout.addLayout(md_row)

        # Notes
        notes_row = QHBoxLayout()
        notes_row.addWidget(QLabel("Notes:"))
        self.notes_label = QLabel("—")
        self.notes_label.setStyleSheet("color: #555; font-style: italic;")
        self.notes_label.setWordWrap(True)
        notes_row.addWidget(self.notes_label)
        self.set_notes_btn = QPushButton("Edit")
        self.set_notes_btn.setFixedWidth(45)
        self.set_notes_btn.setStyleSheet("""
            QPushButton {
                background: #888; color: white;
                border-radius: 4px; font-size: 10px; padding: 2px 6px;
            }
            QPushButton:hover { background: #555; }
        """)
        self.set_notes_btn.clicked.connect(self._set_notes)
        notes_row.addWidget(self.set_notes_btn)
        notes_row.addStretch()
        detail_layout.addLayout(notes_row)

        # File attachment
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("RX Document:"))
        self.file_label = QLabel("No file attached")
        self.file_label.setStyleSheet("color: #888; font-size: 10px;")
        file_row.addWidget(self.file_label)
        self.attach_btn = QPushButton("📎 Attach")
        self.attach_btn.setFixedWidth(75)
        self.attach_btn.setStyleSheet("""
            QPushButton {
                background: #e07b00; color: white;
                border-radius: 4px; font-size: 10px; padding: 2px 6px;
            }
            QPushButton:hover { background: #b56200; }
        """)
        self.attach_btn.clicked.connect(self._attach_file)
        file_row.addWidget(self.attach_btn)
        self.view_btn = QPushButton("View")
        self.view_btn.setFixedWidth(45)
        self.view_btn.setEnabled(False)
        self.view_btn.setStyleSheet("""
            QPushButton {
                background: #555; color: white;
                border-radius: 4px; font-size: 10px; padding: 2px 6px;
            }
            QPushButton:hover { background: #333; }
            QPushButton:disabled { background: #ccc; }
        """)
        self.view_btn.clicked.connect(self._view_file)
        file_row.addWidget(self.view_btn)
        file_row.addStretch()
        detail_layout.addLayout(file_row)

        main_layout.addWidget(self.detail_frame)

        # Internal data store
        self._data = {
            "rx_on_file": 0,
            "reserved_rx_path": None,
            "reserved_rx_date": None,
            "reserved_rx_md": None,
            "reserved_rx_notes": None,
        }

        self._refresh_visibility()

    # ── Internal helpers ──

    def _on_toggle(self, state):
        self._data["rx_on_file"] = 1 if state == Qt.CheckState.Checked.value else 0
        if self._data["rx_on_file"] and not self._data["reserved_rx_date"]:
            self._set_today()
        self._refresh_visibility()
        self._emit()

    def _refresh_visibility(self):
        on = bool(self._data.get("rx_on_file", 0))
        self.detail_frame.setVisible(on)

        if on:
            d = self._data.get("reserved_rx_date") or "Not set"
            md = self._data.get("reserved_rx_md") or "Not set"
            self.status_label.setText(f"  ✅ RX from {md} on {d}")
            self.setStyleSheet("""
                QFrame {
                    background-color: #e8f5e9;
                    border: 2px solid #1a7f37;
                    border-radius: 6px;
                    padding: 4px;
                }
            """)
        else:
            self.status_label.setText("")
            self.setStyleSheet("""
                QFrame {
                    background-color: #f0f7ff;
                    border: 1px solid #90c2f5;
                    border-radius: 6px;
                    padding: 4px;
                }
            """)

        # Update sub-labels
        self.date_label.setText(self._data.get("reserved_rx_date") or "—")
        self.md_label.setText(self._data.get("reserved_rx_md") or "—")
        notes = self._data.get("reserved_rx_notes") or "—"
        self.notes_label.setText(notes[:80] + "…" if len(notes) > 80 else notes)

        path = self._data.get("reserved_rx_path")
        if path and os.path.exists(path):
            self.file_label.setText(os.path.basename(path))
            self.file_label.setStyleSheet("color: #1a7f37; font-size: 10px;")
            self.view_btn.setEnabled(True)
        else:
            self.file_label.setText("No file attached")
            self.file_label.setStyleSheet("color: #888; font-size: 10px;")
            self.view_btn.setEnabled(False)

    def _set_today(self):
        self._data["reserved_rx_date"] = datetime.today().strftime("%m/%d/%Y")
        self._refresh_visibility()
        self._emit()

    def _set_md(self):
        from PyQt6.QtWidgets import QInputDialog
        current = self._data.get("reserved_rx_md") or ""
        text, ok = QInputDialog.getText(self, "Set Prescriber", "Prescriber name:", text=current)
        if ok and text.strip():
            self._data["reserved_rx_md"] = text.strip()
            self._refresh_visibility()
            self._emit()

    def _set_notes(self):
        from PyQt6.QtWidgets import QInputDialog
        current = self._data.get("reserved_rx_notes") or ""
        text, ok = QInputDialog.getText(self, "Notes", "Notes about this reserved RX:", text=current)
        if ok:
            self._data["reserved_rx_notes"] = text.strip()
            self._refresh_visibility()
            self._emit()

    def _attach_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach RX Document", "",
            "PDF/Images (*.pdf *.png *.jpg *.jpeg *.tiff);;All Files (*)"
        )
        if path:
            self._data["reserved_rx_path"] = path
            self._refresh_visibility()
            self._emit()

    def _view_file(self):
        path = self._data.get("reserved_rx_path")
        if path and os.path.exists(path):
            import subprocess, sys
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])

    def _emit(self):
        self.data_changed.emit(dict(self._data))

    # ── Public API ──

    def load(self, order_id: str):
        """Load existing reserved RX data from DB."""
        self.order_id = order_id
        data = get_reserved_rx_data(self.db_path, order_id)
        if data:
            self._data.update(data)
        # Block signals to avoid save loop
        self.toggle.blockSignals(True)
        self.toggle.setChecked(bool(self._data.get("rx_on_file", 0)))
        self.toggle.blockSignals(False)
        self._refresh_visibility()

    def save(self):
        """Save current state to DB. Call this when user saves the order."""
        if self.order_id:
            save_reserved_rx_data(self.db_path, self.order_id, self._data)

    def get_data(self) -> dict:
        """Return current in-memory data without saving."""
        return dict(self._data)

    def is_rx_on_file(self) -> bool:
        return bool(self._data.get("rx_on_file", 0))


# ─────────────────────────────────────────────
#  UI: NEW PRESCRIPTION PROMPT DIALOG
# ─────────────────────────────────────────────

class NewPrescriptionPromptDialog(QDialog):
    """
    Dialog shown when last refill ships but NO RX is on file.
    Asks user if they have a new prescription and offers to:
      1. Create new order with new prescription
      2. Fax MD for new prescription

    Usage:
        dlg = NewPrescriptionPromptDialog(
            parent=self,
            patient_name="Rodriguez, Natividad",
            source_order_id=328
        )
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            action = dlg.get_action()  # "create_new_order" or "fax_md"
            # Use action to determine next step
    """

    def __init__(self, parent=None, patient_name: str = "", source_order_id: int = None):
        super().__init__(parent)
        self.patient_name = patient_name
        self.source_order_id = source_order_id
        self._chosen_action = None  # "create_new_order" or "fax_md"
        self._build()

    def get_action(self) -> str:
        """Return the user's chosen action: 'create_new_order' or 'fax_md'."""
        return self._chosen_action or "fax_md"

    def _build(self):
        self.setWindowTitle("Refills Exhausted — New Prescription?")
        self.setMinimumWidth(460)
        self.setStyleSheet("background-color: #f8f9fa;")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 16)

        # Icon + title
        title_row = QHBoxLayout()
        icon_lbl = QLabel("⚠️")
        icon_lbl.setStyleSheet("font-size: 32px;")
        title_row.addWidget(icon_lbl)
        title_lbl = QLabel("No More Refills")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #c53030;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        layout.addLayout(title_row)

        # Patient
        if self.patient_name:
            pt_lbl = QLabel(f"Patient: <b>{self.patient_name}</b>")
            pt_lbl.setStyleSheet("font-size: 12px;")
            layout.addWidget(pt_lbl)

        # Message
        msg = QLabel(
            "The order has no refills remaining and no prescription on file.\n\n"
            "Do you have a new prescription for this patient?"
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 12px; color: #333; margin: 8px 0;")
        layout.addWidget(msg)

        layout.addSpacing(8)

        # Action buttons
        btn_row = QHBoxLayout()

        fax_btn = QPushButton("📠  Fax MD for New Prescription")
        fax_btn.setStyleSheet("""
            QPushButton {
                background: #ed8936; color: white;
                border-radius: 5px; padding: 8px 16px; font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover { background: #dd6b20; }
        """)
        fax_btn.clicked.connect(self._choose_fax_md)
        btn_row.addWidget(fax_btn)

        btn_row.addStretch()

        create_btn = QPushButton("✚  Yes, Create New Order")
        create_btn.setStyleSheet("""
            QPushButton {
                background: #1a7f37; color: white;
                border-radius: 5px; padding: 8px 18px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #145c27; }
        """)
        create_btn.setDefault(True)
        create_btn.clicked.connect(self._choose_create_order)
        btn_row.addWidget(create_btn)

        layout.addLayout(btn_row)

    def _choose_create_order(self):
        self._chosen_action = "create_new_order"
        self.accept()

    def _choose_fax_md(self):
        self._chosen_action = "fax_md"
        self.accept()


# ─────────────────────────────────────────────
#  UI: NEW ORDER PROMPT DIALOG
# ─────────────────────────────────────────────

class CreateNewOrderDialog(QDialog):
    """
    Dialog shown when last refill ships and RX is on file.
    User can choose to create new order now or later.

    Usage:
        dlg = CreateNewOrderDialog(
            parent=self,
            patient_name="Rodriguez, Natividad",
            rx_data={
                "reserved_rx_md": "Dr. Hernandez",
                "reserved_rx_date": "02/18/2026",
                "reserved_rx_notes": "New size ordered",
                "reserved_rx_path": "/path/to/rx.pdf"
            }
        )
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            # User clicked "Create New Order Now"
            pass
        elif dlg.clicked_later:
            # User clicked "Remind Me Later"
            pass
    """

    def __init__(self, parent=None, patient_name: str = "", rx_data: dict = None):
        super().__init__(parent)
        self.clicked_later = False
        self.rx_data = rx_data or {}
        self._build(patient_name)

    def _build(self, patient_name: str):
        self.setWindowTitle("Reserved RX — Ready to Use")
        self.setMinimumWidth(420)
        self.setStyleSheet("background-color: #f8f9fa;")

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 16)

        # Icon + title
        title_row = QHBoxLayout()
        icon_lbl = QLabel("📋")
        icon_lbl.setStyleSheet("font-size: 28px;")
        title_row.addWidget(icon_lbl)
        title_lbl = QLabel("New RX On File")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #1a4a7a;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        layout.addLayout(title_row)

        # Patient
        if patient_name:
            pt_lbl = QLabel(f"Patient: <b>{patient_name}</b>")
            pt_lbl.setStyleSheet("font-size: 12px;")
            layout.addWidget(pt_lbl)

        # RX details box
        details_frame = QFrame()
        details_frame.setStyleSheet("""
            QFrame {
                background: #e8f5e9;
                border: 1px solid #1a7f37;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        details_layout = QVBoxLayout(details_frame)
        details_layout.setSpacing(4)

        md = self.rx_data.get("reserved_rx_md") or "Unknown"
        date = self.rx_data.get("reserved_rx_date") or "Unknown"
        notes = self.rx_data.get("reserved_rx_notes") or ""
        has_doc = bool(self.rx_data.get("reserved_rx_path"))

        details_layout.addWidget(QLabel(f"✅  Prescriber: <b>{md}</b>"))
        details_layout.addWidget(QLabel(f"📅  Received: <b>{date}</b>"))
        if notes:
            details_layout.addWidget(QLabel(f"📝  Notes: {notes}"))
        details_layout.addWidget(QLabel(f"📎  Document: {'Attached' if has_doc else 'Not attached'}"))

        for child in details_frame.findChildren(QLabel):
            child.setStyleSheet("font-size: 11px; background: transparent;")

        layout.addWidget(details_frame)

        # Message
        msg = QLabel(
            "The last refill on this order has shipped.\n"
            "Would you like to create a new order using this RX now?"
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 11px; color: #444;")
        layout.addWidget(msg)

        # Buttons
        btn_row = QHBoxLayout()

        later_btn = QPushButton("Remind Me Later")
        later_btn.setStyleSheet("""
            QPushButton {
                background: #888; color: white;
                border-radius: 5px; padding: 6px 14px; font-size: 11px;
            }
            QPushButton:hover { background: #555; }
        """)
        later_btn.clicked.connect(self._later)
        btn_row.addWidget(later_btn)

        btn_row.addStretch()

        create_btn = QPushButton("✚  Create New Order Now")
        create_btn.setStyleSheet("""
            QPushButton {
                background: #1a7f37; color: white;
                border-radius: 5px; padding: 6px 18px;
                font-size: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #145c27; }
        """)
        create_btn.setDefault(True)
        create_btn.clicked.connect(self.accept)
        btn_row.addWidget(create_btn)

        layout.addLayout(btn_row)

    def _later(self):
        self.clicked_later = True
        self.reject()


# ─────────────────────────────────────────────
#  CONVENIENCE: SHOW REFILL WARNING
# ─────────────────────────────────────────────

def handle_last_refill(parent_widget, db_path: str, order_id: str,
                        patient_name: str, refills_remaining: int,
                        on_create_order_callback=None,
                        on_fax_md_callback=None):
    """
    All-in-one handler. Call this when processing/shipping a refill.

    - If RX is on file and this is the last refill → shows CreateNewOrderDialog
    - If no RX on file and last refill → shows NewPrescriptionPromptDialog
    - Otherwise → does nothing

    on_create_order_callback(rx_data: dict) — called if user accepts new order creation
    on_fax_md_callback() — called if user accepts faxing MD

    Example:
        handle_last_refill(
            parent_widget=self,
            db_path=self.orders_db_path,
            order_id=order_id,
            patient_name=patient_name,
            refills_remaining=refills_remaining,
            on_create_order_callback=self.open_new_order_wizard,
            on_fax_md_callback=self.open_fax_dialog
        )
    """
    result = check_refill_warning(db_path, order_id, refills_remaining)

    if result["action"] == "create_new_order":
        rx_data = get_reserved_rx_data(db_path, order_id)
        dlg = CreateNewOrderDialog(
            parent=parent_widget,
            patient_name=patient_name,
            rx_data=rx_data
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if on_create_order_callback:
                on_create_order_callback(rx_data)

    elif result["action"] == "fax_md":
        # Show new prescription prompt instead of just asking to fax
        prompt_dlg = NewPrescriptionPromptDialog(
            parent=parent_widget,
            patient_name=patient_name,
            source_order_id=int(order_id)
        )
        if prompt_dlg.exec() == QDialog.DialogCode.Accepted:
            chosen_action = prompt_dlg.get_action()
            
            if chosen_action == "create_new_order":
                # User has a new prescription — open reorder flow
                if on_create_order_callback:
                    # Pass empty rx_data since they're entering new prescription details
                    on_create_order_callback({})
            elif chosen_action == "fax_md":
                # User wants to fax MD
                if on_fax_md_callback:
                    on_fax_md_callback()
