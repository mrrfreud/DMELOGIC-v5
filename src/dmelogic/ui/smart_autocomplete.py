"""
Smart Local Autocomplete
=========================
QCompleter-based autocomplete that learns from your database.
Provides completions for HCPCS codes, descriptions, categories,
patient names, prescriber names, and insurance companies.

Feature #3: Smart Local Autocomplete

All data stays local — reads directly from your SQLite databases.

Integration:
    from dmelogic.ui.smart_autocomplete import SmartAutoComplete
    
    # Attach to any QLineEdit:
    auto = SmartAutoComplete(folder_path=self.folder_path)
    auto.attach_hcpcs(some_line_edit)
    auto.attach_descriptions(another_edit)
    auto.attach_patient_names(name_edit)
    auto.attach_prescriber_names(prescriber_edit)
    auto.attach_insurance(insurance_edit)
    auto.attach_categories(category_edit)
"""

from __future__ import annotations

import sqlite3
from typing import Optional, List
from functools import lru_cache

from PyQt6.QtWidgets import QLineEdit, QCompleter
from PyQt6.QtCore import Qt, QStringListModel, QTimer


class SmartAutoComplete:
    """
    Builds QCompleters from local DB data and attaches them
    to QLineEdit fields. Data is cached and refreshed lazily.
    """

    def __init__(self, folder_path: Optional[str] = None):
        self.folder_path = folder_path
        self._cache: dict[str, list[str]] = {}
        self._cache_age: dict[str, float] = {}

    # ------------------------------------------------------------------ Public API

    def attach_hcpcs(self, edit: QLineEdit):
        """Attach HCPCS code completer."""
        items = self._get_cached("hcpcs", self._load_hcpcs)
        self._attach(edit, items)

    def attach_descriptions(self, edit: QLineEdit):
        """Attach inventory description completer."""
        items = self._get_cached("descriptions", self._load_descriptions)
        self._attach(edit, items)

    def attach_categories(self, edit: QLineEdit):
        """Attach inventory category completer."""
        items = self._get_cached("categories", self._load_categories)
        self._attach(edit, items)

    def attach_brands(self, edit: QLineEdit):
        """Attach brand completer."""
        items = self._get_cached("brands", self._load_brands)
        self._attach(edit, items)

    def attach_patient_names(self, edit: QLineEdit):
        """Attach patient name completer (LAST, FIRST format)."""
        items = self._get_cached("patient_names", self._load_patient_names)
        self._attach(edit, items)

    def attach_prescriber_names(self, edit: QLineEdit):
        """Attach prescriber name completer."""
        items = self._get_cached("prescribers", self._load_prescriber_names)
        self._attach(edit, items)

    def attach_insurance(self, edit: QLineEdit):
        """Attach insurance company name completer."""
        items = self._get_cached("insurance", self._load_insurance_names)
        self._attach(edit, items)

    def refresh(self, key: Optional[str] = None):
        """Force refresh of cached data. Pass key to refresh only one, or None for all."""
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    # ------------------------------------------------------------------ Internal

    def _get_cached(self, key: str, loader) -> List[str]:
        if key not in self._cache:
            try:
                self._cache[key] = loader()
            except Exception as e:
                print(f"SmartAutoComplete load error for '{key}': {e}")
                self._cache[key] = []
        return self._cache[key]

    def _attach(self, edit: QLineEdit, items: List[str]):
        model = QStringListModel(items)
        completer = QCompleter()
        completer.setModel(model)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(10)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        edit.setCompleter(completer)

    # ------------------------------------------------------------------ Loaders

    def _query(self, db_name: str, sql: str) -> List[str]:
        """Run a query and return a flat list of first-column strings."""
        from dmelogic.db.base import get_connection
        conn = get_connection(db_name, folder_path=self.folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql)
        results = [str(row[0]) for row in cur.fetchall() if row[0]]
        conn.close()
        return results

    def _load_hcpcs(self) -> List[str]:
        return self._query("inventory.db", """
            SELECT DISTINCT hcpcs_code FROM inventory
            WHERE hcpcs_code IS NOT NULL AND hcpcs_code != ''
            ORDER BY hcpcs_code
        """)

    def _load_descriptions(self) -> List[str]:
        return self._query("inventory.db", """
            SELECT DISTINCT description FROM inventory
            WHERE description IS NOT NULL AND description != ''
            ORDER BY description
            LIMIT 500
        """)

    def _load_categories(self) -> List[str]:
        return self._query("inventory.db", """
            SELECT DISTINCT category FROM inventory
            WHERE category IS NOT NULL AND category != ''
            ORDER BY category
        """)

    def _load_brands(self) -> List[str]:
        return self._query("inventory.db", """
            SELECT DISTINCT brand FROM inventory
            WHERE brand IS NOT NULL AND brand != ''
            ORDER BY brand
        """)

    def _load_patient_names(self) -> List[str]:
        return self._query("patients.db", """
            SELECT DISTINCT last_name || ', ' || first_name
            FROM patients
            WHERE last_name IS NOT NULL
            ORDER BY last_name, first_name
            LIMIT 500
        """)

    def _load_prescriber_names(self) -> List[str]:
        try:
            return self._query("patients.db", """
                SELECT DISTINCT prescriber_name FROM patients
                WHERE prescriber_name IS NOT NULL AND prescriber_name != ''
                UNION
                SELECT DISTINCT name FROM prescribers
                WHERE name IS NOT NULL AND name != ''
                ORDER BY 1
                LIMIT 300
            """)
        except Exception:
            # prescribers table might not exist
            return self._query("patients.db", """
                SELECT DISTINCT prescriber_name FROM patients
                WHERE prescriber_name IS NOT NULL AND prescriber_name != ''
                ORDER BY prescriber_name
                LIMIT 300
            """)

    def _load_insurance_names(self) -> List[str]:
        try:
            return self._query("patients.db", """
                SELECT DISTINCT primary_insurance FROM patients
                WHERE primary_insurance IS NOT NULL AND primary_insurance != ''
                ORDER BY primary_insurance
            """)
        except Exception:
            return []
