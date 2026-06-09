"""Reconciliation Report - per-order paid reconciliation for ePACES workflow."""

from typing import List, Dict, Any, Optional
from datetime import date, datetime
import sqlite3

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QTableWidgetItem
from PyQt6.QtCore import Qt

from dmelogic.reports.base import ReportEngine, ReportColumn
from dmelogic.reports.ui import ReportViewer
from dmelogic.db.base import resolve_db_path


def _to_iso_sql(expr: str) -> str:
    """Normalize supported date/datetime strings to YYYY-MM-DD in SQL."""
    return f"""CASE
        WHEN {expr} LIKE '__/__/____%' THEN
            substr(substr({expr},1,10),7,4)||'-'||substr(substr({expr},1,10),1,2)||'-'||substr(substr({expr},1,10),4,2)
        WHEN {expr} LIKE '____-__-__%' THEN
            substr({expr},1,10)
        ELSE NULL
    END"""


class ReconciliationReportEngine(ReportEngine):
    def __init__(
        self,
        folder_path: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        insurance_filter: Optional[str] = None,
    ):
        super().__init__(folder_path)
        self.start_date = start_date
        self.end_date = end_date
        self.insurance_filter = insurance_filter

    def get_report_title(self) -> str:
        return "Reconciliation Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("order_number", "Order #", alignment="center"),
            ReportColumn("date", "Date", data_type="date", alignment="center"),
            ReportColumn("patient", "Patient"),
            ReportColumn("expected", "Expected", data_type="currency", alignment="right"),
            ReportColumn("paid", "Paid", alignment="center"),
            ReportColumn("paid_date", "Paid Date", alignment="center"),
            ReportColumn("status", "Status", alignment="center"),
            ReportColumn("insurance", "Insurance"),
        ]

    def _fetch_order_rows(self) -> List[Dict[str, Any]]:
        sd = self.start_date.strftime("%Y-%m-%d") if self.start_date else "2000-01-01"
        ed = self.end_date.strftime("%Y-%m-%d") if self.end_date else "2099-12-31"
        params: list[Any] = [sd, ed]
        insurance_where = ""
        if self.insurance_filter and self.insurance_filter != "All":
            insurance_where = " AND TRIM(COALESCE(o.primary_insurance, '')) = ?"
            params.append(self.insurance_filter)

        order_date_iso = _to_iso_sql("o.order_date")
        rows = self.execute_query(
            "orders.db",
            f"""
            WITH order_totals AS (
                SELECT oi.order_id, SUM(COALESCE(CAST(oi.total AS REAL), 0)) AS order_total
                FROM order_items oi
                GROUP BY oi.order_id
            )
            SELECT
                o.id AS order_id,
                ('ORD-' || o.id) AS order_number,
                {order_date_iso} AS date,
                (TRIM(COALESCE(o.patient_last_name, '')) || ', ' || TRIM(COALESCE(o.patient_first_name, ''))) AS patient,
                COALESCE(ot.order_total, 0) AS expected,
                COALESCE(o.paid, 0) AS paid,
                COALESCE(o.paid_date, '') AS paid_date,
                COALESCE(o.order_status, '') AS status,
                TRIM(COALESCE(o.primary_insurance, '')) AS insurance
            FROM orders o
            LEFT JOIN order_totals ot ON ot.order_id = o.id
            WHERE {order_date_iso} IS NOT NULL
              AND {order_date_iso} >= ?
              AND {order_date_iso} <= ?
              AND COALESCE(o.order_status, '') NOT IN ('Cancelled', 'Deleted')
              AND (
                    COALESCE(o.billed, 0) = 1
                 OR COALESCE(o.paid, 0) = 1
                 OR TRIM(COALESCE(o.billing_confirmation_number, '')) <> ''
                 OR UPPER(TRIM(COALESCE(o.order_status, ''))) IN ('BILLED', 'PAID', 'SUBMITTED', 'APPROVED')
              )
                            {insurance_where}
            ORDER BY {order_date_iso} DESC, o.id DESC
            """,
                        tuple(params),
        )
        return [
            {
                "order_id": int(r["order_id"]),
                "order_number": r["order_number"] or f"ORD-{r['order_id']}",
                "date": self.parse_date(r["date"]),
                "patient": (r["patient"] or "").strip().strip(","),
                "expected": float(r["expected"] or 0),
                "paid": "Yes" if int(r["paid"] or 0) else "No",
                "paid_bool": int(r["paid"] or 0),
                "paid_date": r["paid_date"] or "",
                "paid_date_raw": r["paid_date"] or "",
                "status": r["status"] or "",
                "insurance": r["insurance"] or "",
            }
            for r in rows
            if r["date"]
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        return self._fetch_order_rows()

    def _calculate_summary(self, rows):
        """Show order-level paid/unpaid totals in the footer."""
        base = super()._calculate_summary(rows) or {}

        paid_orders = 0
        unpaid_orders = 0
        total_expected = 0.0

        for row in rows:
            data = row.data if hasattr(row, "data") else row
            if int(data.get("paid_bool") or 0):
                paid_orders += 1
            else:
                unpaid_orders += 1

            total_expected += float(data.get("expected") or 0)

        base.update({
            "paid_orders": paid_orders,
            "unpaid_orders": unpaid_orders,
            "total_expected": total_expected,
        })
        return base


class ReconciliationReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("Reconciliation Report")
        self.setModal(False)
        self.resize(1400, 800)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_date_range("Start", "End")
        self.viewer.add_filter_combo("Insurance", self._insurance_options())
        self.viewer.refresh_requested.connect(self._generate_report)
        layout.addWidget(self.viewer)

        actions = QHBoxLayout()
        actions.addStretch()
        self.submit_btn = QPushButton("Submit Paid Updates")
        self.submit_btn.clicked.connect(self._submit_paid_updates)
        actions.addWidget(self.submit_btn)
        layout.addLayout(actions)

        self._generate_report(self.viewer.get_filter_values())

    def _insurance_options(self) -> List[str]:
        """Return insurance filter options: All + distinct insurance names."""
        options = ["All"]
        try:
            db_path = resolve_db_path("orders.db", folder_path=self.folder_path)
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT TRIM(COALESCE(primary_insurance, '')) AS insurance
                FROM orders
                WHERE TRIM(COALESCE(primary_insurance, '')) <> ''
                ORDER BY insurance COLLATE NOCASE
                """
            )
            options.extend([str(r[0]) for r in cur.fetchall() if r and r[0]])
            conn.close()
        except Exception:
            pass
        return options

    def _generate_report(self, filters=None):
        try:
            f = filters or {}
            engine = ReconciliationReportEngine(
                self.folder_path,
                start_date=f.get("start_date"),
                end_date=f.get("end_date"),
                insurance_filter=f.get("Insurance"),
            )
            data = engine.generate()
            data.summary = engine._calculate_summary(data.rows)
            self.viewer.load_report(data)
            self._configure_paid_editors()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed: {e}")

    def _configure_paid_editors(self):
        """Render the Paid column as checkboxes and keep Paid Date editable."""
        if not self.viewer.report_data:
            return

        table = self.viewer.table
        columns = {col.name: idx for idx, col in enumerate(self.viewer.report_data.columns)}
        paid_col = columns.get("paid")
        paid_date_col = columns.get("paid_date")
        if paid_col is None or paid_date_col is None:
            return

        for row_idx, report_row in enumerate(self.viewer.report_data.rows):
            data = report_row.data

            paid_item = table.item(row_idx, paid_col) or QTableWidgetItem("")
            paid_item.setText("")
            paid_item.setFlags(
                (paid_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            paid_item.setCheckState(
                Qt.CheckState.Checked if int(data.get("paid_bool") or 0) else Qt.CheckState.Unchecked
            )
            paid_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_idx, paid_col, paid_item)

            pd_value = str(data.get("paid_date") or "")
            paid_date_item = table.item(row_idx, paid_date_col) or QTableWidgetItem(pd_value)
            paid_date_item.setText(pd_value)
            paid_date_item.setFlags(
                paid_date_item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            paid_date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_idx, paid_date_col, paid_date_item)

    def _submit_paid_updates(self):
        """Persist Paid checkbox and Paid Date edits back to orders.db."""
        if not self.viewer.report_data:
            QMessageBox.information(self, "No Data", "Generate the report first.")
            return

        table = self.viewer.table
        columns = {col.name: idx for idx, col in enumerate(self.viewer.report_data.columns)}
        paid_col = columns.get("paid")
        paid_date_col = columns.get("paid_date")
        if paid_col is None or paid_date_col is None:
            QMessageBox.warning(self, "Error", "Paid columns were not found.")
            return

        updates = []
        for row_idx, report_row in enumerate(self.viewer.report_data.rows):
            data = report_row.data
            order_id = int(data.get("order_id") or 0)
            if order_id <= 0:
                continue

            paid_item = table.item(row_idx, paid_col)
            if paid_item is None:
                continue

            new_paid = 1 if paid_item.checkState() == Qt.CheckState.Checked else 0
            paid_date_item = table.item(row_idx, paid_date_col)
            new_paid_date = (paid_date_item.text().strip() if paid_date_item else "")

            if new_paid and not new_paid_date:
                new_paid_date = datetime.now().strftime("%m/%d/%Y")
            if not new_paid:
                new_paid_date = ""

            old_paid = int(data.get("paid_bool") or 0)
            old_paid_date = str(data.get("paid_date_raw") or "").strip()

            if new_paid != old_paid or new_paid_date != old_paid_date:
                updates.append((order_id, new_paid, new_paid_date or None))

        if not updates:
            QMessageBox.information(self, "No Changes", "No paid updates to submit.")
            return

        try:
            db_path = resolve_db_path("orders.db", folder_path=self.folder_path)
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for order_id, paid, paid_date in updates:
                cur.execute(
                    "UPDATE orders SET paid = ?, paid_date = ?, updated_date = ? WHERE id = ?",
                    (paid, paid_date, now, order_id),
                )
            conn.commit()
            conn.close()

            QMessageBox.information(self, "Saved", f"Updated {len(updates)} order(s).")
            self._generate_report(self.viewer.get_filter_values())
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save updates: {e}")
