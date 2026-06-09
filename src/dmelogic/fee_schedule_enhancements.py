"""
fee_schedule_enhancements.py
============================
DMELogic — Two enhancements:

1. Step 3 of New Order Wizard:
   - New "Max Units" column pulled live from fee schedule
   - Qty cell turns RED if entered qty exceeds max units
   - Tooltip shows: "Max allowed: X per 30 days"

2. ePACES Helper Dialog:
   - New "PA Type" column showing the PA value from fee schedule
   - PA type values: blank = No PA/No DVS Required, 1 = Full PA Required, 6 = DVS Required
   - Color coded: green = No PA/DVS, orange = Full PA Required, purple = DVS Required

Includes both CSV-based FeeScheduleReader and DB-backed DbFeeScheduleReader
that wraps dmelogic.db.fee_schedule.lookup_fee().
"""

import csv
import os
import sqlite3
from functools import lru_cache
from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QLabel, QWidget,
    QHBoxLayout, QHeaderView, QAbstractItemView, QToolTip
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QColor, QFont, QBrush


# ─────────────────────────────────────────────────────────
#  PA TYPE LABELS (shared by all readers)
# ─────────────────────────────────────────────────────────

PA_TYPE_LABELS = {
    "":  ("No PA/DVS",       "#2e7d32"),   # green — no PA or DVS required
    "0": ("No PA/DVS",       "#2e7d32"),
    "1": ("Full PA Req'd",   "#e65100"),   # orange — full prior authorization required
    "6": ("DVS Req'd",       "#6a1b9a"),   # purple — DVS required
}


# ─────────────────────────────────────────────────────────
#  DB-BACKED FEE SCHEDULE READER
#  Uses dmelogic.db.fee_schedule.lookup_fee() (billing.db)
# ─────────────────────────────────────────────────────────

class DbFeeScheduleReader:
    """
    Fee schedule reader that wraps the existing DB-based lookup_fee().
    This is the preferred reader for DMELogic since the fee schedule
    is stored in billing.db after XLSX import.

    Usage:
        reader = DbFeeScheduleReader(folder_path=self.folder_path)
        info = reader.get_code_info("A4206")
        # Returns: {"fee": 0.2, "max_units": 200, "pa_type": "", "br": ""}
    """

    PA_TYPE_LABELS = PA_TYPE_LABELS

    def __init__(self, folder_path: str = None):
        self.folder_path = folder_path

    def get_code_info(self, hcpcs_code: str) -> dict:
        """Returns dict with fee, rental_fee, br, max_units, pa_type keys."""
        try:
            from dmelogic.db.fee_schedule import lookup_fee
            code = hcpcs_code.strip().upper().split("-")[0][:5]
            result = lookup_fee(code, folder_path=self.folder_path)
            if result:
                return {
                    "fee": str(result.get("fee", "")),
                    "rental_fee": str(result.get("rental_fee", "")),
                    "br": str(result.get("br", "")),
                    "max_units": str(result.get("max_units", "") or ""),
                    "pa_type": str(result.get("pa", "") or ""),
                }
        except Exception as e:
            print(f"[FeeSchedule] DB lookup error: {e}")
        return {}

    def get_max_units(self, hcpcs_code: str) -> str:
        info = self.get_code_info(hcpcs_code)
        val = info.get("max_units", "")
        # Don't show "0" as a max — that means not set
        if val == "0" or val == "0.0":
            return ""
        return val

    def get_pa_type(self, hcpcs_code: str) -> str:
        info = self.get_code_info(hcpcs_code)
        return info.get("pa_type", "")

    def get_pa_label(self, hcpcs_code: str) -> tuple:
        """Returns (label_text, color_hex) for display.  Medicaid only."""
        pa = self.get_pa_type(hcpcs_code).strip()
        return self.PA_TYPE_LABELS.get(pa, ("PA: " + pa, "#555555"))

    def reload(self, folder_path: str = None):
        """Update folder_path if needed. DB is always fresh."""
        if folder_path:
            self.folder_path = folder_path


# ─────────────────────────────────────────────────────────
#  CSV-BASED FEE SCHEDULE READER (fallback)
# ─────────────────────────────────────────────────────────

class FeeScheduleReader:
    """
    Reads the Medicaid Fee Schedule file (CSV/TSV) directly.
    Use DbFeeScheduleReader instead when fee schedule is in billing.db.
    """

    PA_TYPE_LABELS = PA_TYPE_LABELS

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._data: dict = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if not self.file_path or not os.path.exists(self.file_path):
            return
        try:
            ext = os.path.splitext(self.file_path)[1].lower()
            delimiter = "\t" if ext in (".tsv", ".txt") else ","

            with open(self.file_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f, delimiter=delimiter)
                headers = None
                for row in reader:
                    if not row or not row[0].strip():
                        continue
                    if headers is None:
                        upper = [c.strip().upper() for c in row]
                        if "CODE" in upper or "HCPCS" in upper:
                            headers = upper
                            continue
                        else:
                            continue

                    if headers is None:
                        continue

                    def get(col_names):
                        for name in col_names:
                            if name in headers:
                                idx = headers.index(name)
                                if idx < len(row):
                                    return row[idx].strip()
                        return ""

                    code = get(["CODE", "HCPCS", "HCPCS CODE"])
                    if not code or len(code) < 4:
                        continue

                    self._data[code.upper()] = {
                        "fee":       get(["FEE", "AMOUNT", "RATE"]),
                        "rental_fee":get(["RENTAL FEE", "RENTAL"]),
                        "br":        get(["BR"]),
                        "max_units": get(["MAX UNITS", "MAXUNITS", "MAX_UNITS", "UNITS"]),
                        "pa_type":   get(["PA", "PA TYPE", "PA_TYPE", "PRIOR AUTH"]),
                    }
            self._loaded = True
            print(f"[FeeSchedule] Loaded {len(self._data)} codes from {self.file_path}")
        except Exception as e:
            print(f"[FeeSchedule] Load error: {e}")

    def get_code_info(self, hcpcs_code: str) -> dict:
        self._load()
        code = hcpcs_code.strip().upper().split("-")[0][:5]
        return self._data.get(code, {})

    def get_max_units(self, hcpcs_code: str) -> str:
        info = self.get_code_info(hcpcs_code)
        return info.get("max_units", "")

    def get_pa_type(self, hcpcs_code: str) -> str:
        info = self.get_code_info(hcpcs_code)
        return info.get("pa_type", "")

    def get_pa_label(self, hcpcs_code: str) -> tuple:
        pa = self.get_pa_type(hcpcs_code).strip()
        return self.PA_TYPE_LABELS.get(pa, ("PA: " + pa, "#555555"))

    def reload(self, new_path: str = None):
        if new_path:
            self.file_path = new_path
        self._loaded = False
        self._data.clear()
        self._load()


# ─────────────────────────────────────────────────────────
#  ENHANCEMENT 1: STEP 3 WIZARD — MAX UNITS + PA COLUMNS
# ─────────────────────────────────────────────────────────

# Column index constants for the new columns appended to Step 3 table
WIZARD_COL_MAX_UNITS = 11   # After Mod 4 (col 10)
WIZARD_COL_PA_TYPE   = 12


def setup_wizard_fee_columns(table: QTableWidget):
    """
    Call once after building Step 3's QTableWidget.
    Adds two new read-only columns: Max Units and PA Type.
    """
    # Extend column count
    needed = max(table.columnCount(), WIZARD_COL_PA_TYPE + 1)
    table.setColumnCount(needed)

    # Set headers
    _set_header(table, WIZARD_COL_MAX_UNITS, "Max\nUnits")
    _set_header(table, WIZARD_COL_PA_TYPE,   "PA\nType")

    # Column widths
    table.setColumnWidth(WIZARD_COL_MAX_UNITS, 60)
    table.setColumnWidth(WIZARD_COL_PA_TYPE,   70)

    # Resize mode
    header = table.horizontalHeader()
    header.setSectionResizeMode(WIZARD_COL_MAX_UNITS, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(WIZARD_COL_PA_TYPE, QHeaderView.ResizeMode.ResizeToContents)


def _set_header(table: QTableWidget, col: int, text: str):
    item = table.horizontalHeaderItem(col)
    if item is None:
        item = QTableWidgetItem(text)
        table.setHorizontalHeaderItem(col, item)
    else:
        item.setText(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)


def populate_wizard_row_fee_info(table: QTableWidget, row: int,
                                  hcpcs_code: str, fee_reader):
    """
    Fill Max Units and PA Type cells for a row in Step 3.
    Call whenever a row is added or HCPCS code changes.
    """
    if not hcpcs_code or not fee_reader:
        return

    max_units = fee_reader.get_max_units(hcpcs_code)
    pa_label, pa_color = fee_reader.get_pa_label(hcpcs_code)

    # ── Max Units cell ──
    mu_item = QTableWidgetItem(max_units or "—")
    mu_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    mu_item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # read-only
    if max_units:
        mu_item.setToolTip(f"Medicaid max: {max_units} units per authorization period")
        mu_item.setForeground(QBrush(QColor("#1a4a7a")))
        font = mu_item.font()
        font.setBold(True)
        mu_item.setFont(font)
    else:
        mu_item.setForeground(QBrush(QColor("#888888")))
    table.setItem(row, WIZARD_COL_MAX_UNITS, mu_item)

    # ── PA Type cell ──
    pa_item = QTableWidgetItem(pa_label)
    pa_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    pa_item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # read-only
    pa_item.setForeground(QBrush(QColor(pa_color)))
    font2 = pa_item.font()
    font2.setBold(True)
    pa_item.setFont(font2)
    pa_raw = fee_reader.get_pa_type(hcpcs_code)
    pa_item.setToolTip(
        f"Medicaid fee schedule PA column value: '{pa_raw}'\n"
        + ("No PA/No DVS Required." if pa_label == "No PA/DVS"
           else "Full Prior Authorization Required." if pa_label == "Full PA Req'd"
           else "DVS Required." if pa_label == "DVS Req'd"
           else "Prior authorization may be required for this code.")
    )
    table.setItem(row, WIZARD_COL_PA_TYPE, pa_item)


def validate_qty_vs_max(table: QTableWidget, row: int, fee_reader, qty_col: int = 2):
    """
    Colors the Qty cell/widget red if qty exceeds max units.
    Works with both QTableWidgetItem and QSpinBox cell widgets.
    """
    if not fee_reader:
        return

    hcpcs_item = table.item(row, 0)
    if not hcpcs_item:
        return

    hcpcs = hcpcs_item.text().strip().split("-")[0][:5]
    max_units_str = fee_reader.get_max_units(hcpcs)

    # Get qty - may be a QSpinBox cell widget or a QTableWidgetItem
    qty_val = 0
    qty_widget = table.cellWidget(row, qty_col)
    qty_item = table.item(row, qty_col)
    if qty_widget and hasattr(qty_widget, 'value'):
        qty_val = qty_widget.value()
    elif qty_item:
        try:
            qty_val = int(qty_item.text().strip())
        except ValueError:
            return

    try:
        max_u = int(max_units_str) if max_units_str else None
    except ValueError:
        return

    if max_u is not None and max_u > 0 and qty_val > max_u:
        # Over limit — red highlight
        if qty_widget and hasattr(qty_widget, 'setStyleSheet'):
            qty_widget.setStyleSheet("QSpinBox { background-color: #ffcccc; color: #cc0000; font-weight: bold; }")
            qty_widget.setToolTip(
                f"⚠ Qty {qty_val} exceeds Medicaid max of {max_u} for {hcpcs}.\n"
                f"Claim may be rejected. Consider splitting or getting PA."
            )
        elif qty_item:
            qty_item.setBackground(QBrush(QColor("#ffcccc")))
            qty_item.setForeground(QBrush(QColor("#cc0000")))
            qty_item.setToolTip(
                f"⚠ Qty {qty_val} exceeds Medicaid max of {max_u} for {hcpcs}.\n"
                f"Claim may be rejected. Consider splitting or getting PA."
            )
    else:
        # Within limit — clear warning
        if qty_widget and hasattr(qty_widget, 'setStyleSheet'):
            qty_widget.setStyleSheet("")
            qty_widget.setToolTip("")
        elif qty_item:
            qty_item.setBackground(QBrush(QColor(0, 0, 0, 0)))
            qty_item.setForeground(QBrush(QColor("#000000")))
            qty_item.setToolTip("")


# ─────────────────────────────────────────────────────────
#  ENHANCEMENT 2: ePACES HELPER — PA TYPE + MAX UNITS
# ─────────────────────────────────────────────────────────

def add_pa_type_column_to_epaces(table: QTableWidget, fee_reader,
                                   hcpcs_col: int = 1):
    """
    Add a PA Type column to an existing ePACES helper QTableWidget.
    Call after the table is populated with order items.
    Uses cell widgets (not QTableWidgetItem) since ePACES uses setCellWidget.
    """
    if not fee_reader:
        return

    col_count = table.columnCount()
    table.setColumnCount(col_count + 1)
    pa_col = col_count

    # Header
    header_item = QTableWidgetItem("PA Type")
    header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    table.setHorizontalHeaderItem(pa_col, header_item)

    header_view = table.horizontalHeader()
    header_view.setSectionResizeMode(pa_col, QHeaderView.ResizeMode.Fixed)
    header_view.resizeSection(pa_col, 85)

    # Populate each row
    for row in range(table.rowCount()):
        # ePACES uses cell widgets, not items — extract HCPCS from the widget
        hcpcs_code = _extract_cell_text(table, row, hcpcs_col)
        if not hcpcs_code:
            continue

        label_text, color = fee_reader.get_pa_label(hcpcs_code)
        pa_raw = fee_reader.get_pa_type(hcpcs_code)

        badge = QLabel(f" {label_text} ")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if label_text == "No PA/DVS":
            bg, border = "#e8f5e9", "#2e7d32"
        elif "DVS" in label_text:
            bg, border = "#f3e5f5", "#6a1b9a"
        else:
            bg, border = "#fff3e0", "#e65100"

        badge.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10px;
                font-weight: bold;
            }}
        """)
        tip = f"HCPCS: {hcpcs_code} | Medicaid fee schedule PA code: '{pa_raw}'\n"
        if label_text == "No PA/DVS":
            tip += "No PA/No DVS Required."
        elif "DVS" in label_text:
            tip += "DVS Required."
        else:
            tip += "Full Prior Authorization Required — enter PA# before billing."
        badge.setToolTip(tip)

        table.setCellWidget(row, pa_col, badge)


def add_max_units_column_to_epaces(table: QTableWidget, fee_reader,
                                     hcpcs_col: int = 1, qty_col: int = 2):
    """
    Add a Max Units column to an existing ePACES helper QTableWidget.
    Highlights when submitted qty exceeds the limit.
    """
    if not fee_reader:
        return

    col_count = table.columnCount()
    table.setColumnCount(col_count + 1)
    mu_col = col_count

    header_item = QTableWidgetItem("Max\nUnits")
    header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    table.setHorizontalHeaderItem(mu_col, header_item)

    header_view = table.horizontalHeader()
    header_view.setSectionResizeMode(mu_col, QHeaderView.ResizeMode.Fixed)
    header_view.resizeSection(mu_col, 65)

    for row in range(table.rowCount()):
        hcpcs = _extract_cell_text(table, row, hcpcs_col)
        qty_text = _extract_cell_text(table, row, qty_col)
        if not hcpcs:
            continue

        max_units = fee_reader.get_max_units(hcpcs)

        label = QLabel(max_units or "—")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Check if qty exceeds limit
        over_limit = False
        try:
            qty = int(qty_text.strip()) if qty_text else 0
            mu = int(max_units) if max_units else None
            if mu and mu > 0 and qty > mu:
                over_limit = True
        except (ValueError, TypeError):
            pass

        if over_limit:
            label.setStyleSheet("""
                QLabel {
                    color: #cc0000;
                    background-color: #ffcccc;
                    border-radius: 3px;
                    padding: 2px 4px;
                    font-weight: bold;
                    font-size: 10px;
                }
            """)
            label.setToolTip(f"⚠ Submitted qty {qty_text} exceeds max {max_units}!")
        else:
            label.setStyleSheet("""
                QLabel {
                    color: #1a4a7a;
                    padding: 2px 4px;
                    font-weight: bold;
                    font-size: 10px;
                }
            """)
            if max_units:
                label.setToolTip(f"Medicaid max: {max_units} units")

        table.setCellWidget(row, mu_col, label)


def _extract_cell_text(table: QTableWidget, row: int, col: int) -> str:
    """Extract text from either a QTableWidgetItem or a cell widget."""
    # Try item first
    item = table.item(row, col)
    if item:
        return item.text().strip()
    # Try cell widget (ePACES uses QWidget containers with QLabel inside)
    widget = table.cellWidget(row, col)
    if widget:
        # Look for QLabel children
        labels = widget.findChildren(QLabel)
        if labels:
            return labels[0].text().strip()
        # Try direct text
        if hasattr(widget, 'text'):
            return widget.text().strip()
    return ""
