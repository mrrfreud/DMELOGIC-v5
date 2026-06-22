"""
patient_picker.py — search-and-pick a patient by name for linking.

Replaces the old "enter a numeric Patient ID" prompt: type part of a name and
pick from live results out of patients.db.
"""

from __future__ import annotations

import sqlite3
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


def _patients_db():
    from dmelogic.paths import db_dir
    return db_dir() / "patients.db"


def search_patients(term: str, limit: int = 50):
    """Return [(id, last, first, dob, phone), …] matching the term."""
    term = (term or "").strip()
    if not term:
        return []
    db = _patients_db()
    if not db.exists():
        return []
    like = f"%{term}%"
    # Also support "Last, First" typed directly.
    combined = f"%{term.replace(',', '').strip()}%"
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, last_name, first_name, dob, phone FROM patients "
            "WHERE last_name LIKE ? OR first_name LIKE ? "
            "OR (COALESCE(last_name,'') || ' ' || COALESCE(first_name,'')) LIKE ? "
            "OR (COALESCE(last_name,'') || ', ' || COALESCE(first_name,'')) LIKE ? "
            "ORDER BY last_name, first_name LIMIT ?",
            (like, like, combined, like, limit),
        ).fetchall()
        conn.close()
        return [(r["id"], r["last_name"] or "", r["first_name"] or "",
                 r["dob"] or "", r["phone"] or "") for r in rows]
    except Exception:
        return []


class PatientPickerDialog(QDialog):
    """Type a name, pick a patient. Result via selected_patient()."""

    RESULT_CREATE_PATIENT = 1001

    def __init__(self, parent: QWidget | None = None, initial: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Link to Patient")
        self.resize(560, 460)
        self._selected: Optional[Tuple[int, str]] = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Search by patient name:"))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Start typing a last or first name…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._schedule_search)
        root.addWidget(self.search)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Last name", "First name", "DOB", "Phone"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._accept_selection)
        root.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#64748b;")
        root.addWidget(self.status)

        btns = QHBoxLayout()
        self.create_btn = QPushButton("Create patient profile")
        self.create_btn.setProperty("flat", True)
        self.create_btn.clicked.connect(self._request_create_patient)
        btns.addWidget(self.create_btn)
        btns.addStretch()
        cancel = QPushButton("Cancel"); cancel.setProperty("flat", True)
        cancel.clicked.connect(self.reject)
        self.link_btn = QPushButton("Link patient")
        self.link_btn.setEnabled(False)
        self.link_btn.clicked.connect(self._accept_selection)
        self.table.itemSelectionChanged.connect(
            lambda: self.link_btn.setEnabled(self.table.currentRow() >= 0))
        btns.addWidget(cancel)
        btns.addWidget(self.link_btn)
        root.addLayout(btns)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self._run_search)

        if initial:
            self.search.setText(initial)
        self.search.setFocus()

    def _schedule_search(self, *_):
        self._timer.start()

    def _run_search(self):
        rows = search_patients(self.search.text())
        self.table.setRowCount(0)
        self._rows = rows
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            for col, val in enumerate((r[1], r[2], r[3], r[4])):
                self.table.setItem(i, col, QTableWidgetItem(str(val)))
        self.status.setText(
            f"{len(rows)} match(es)" if rows else "No matching patients")
        if rows:
            self.table.selectRow(0)

    def _request_create_patient(self):
        self.done(self.RESULT_CREATE_PATIENT)

    def _accept_selection(self, *_):
        row = self.table.currentRow()
        if row < 0 or row >= len(getattr(self, "_rows", [])):
            return
        pid, last, first, dob, _phone = self._rows[row]
        label = f"{last}, {first}".strip(", ")
        if dob:
            label += f" (DOB {dob})"
        self._selected = (pid, label)
        self.accept()

    def selected_patient(self) -> Optional[Tuple[int, str]]:
        """Return (patient_id, display_label) or None if cancelled."""
        return self._selected
