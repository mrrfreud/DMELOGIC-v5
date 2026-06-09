"""Orders By Status report using the foundation report framework."""

from datetime import date
from typing import Any, Dict, List, Optional

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QMessageBox

from dmelogic.reports.base import ReportColumn, ReportEngine, ReportRow
from dmelogic.reports.ui import ReportViewer


def _to_iso_sql(expr: str) -> str:
    """Normalize common date formats to YYYY-MM-DD for SQLite filtering."""
    return f"""CASE
        WHEN {expr} LIKE '__/__/____%' THEN
            substr(substr({expr},1,10),7,4)||'-'||substr(substr({expr},1,10),1,2)||'-'||substr(substr({expr},1,10),4,2)
        WHEN {expr} LIKE '____-__-__%' THEN
            substr({expr},1,10)
        ELSE NULL
    END"""


class OrdersByStatusReportEngine(ReportEngine):
    """Report engine for order counts and totals grouped by status."""

    def __init__(
        self,
        folder_path: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ):
        super().__init__(folder_path)
        self.start_date = start_date
        self.end_date = end_date

    def get_report_title(self) -> str:
        return "Orders By Status"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("status", "Status", alignment="left"),
            ReportColumn("order_count", "Orders", data_type="number", alignment="right"),
            ReportColumn("billed_count", "Billed", data_type="number", alignment="right"),
            ReportColumn("paid_count", "Paid", data_type="number", alignment="right"),
            ReportColumn("total_amount", "Order Total", data_type="currency", alignment="right"),
            ReportColumn("last_order_date", "Last Order", data_type="date", alignment="center"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        order_date_iso = _to_iso_sql("COALESCE(o.order_date, o.created_date)")

        where_sql = ""
        params: List[Any] = []
        if self.start_date and self.end_date:
            where_sql = f"WHERE {order_date_iso} >= ? AND {order_date_iso} <= ?"
            params.extend([
                self.start_date.strftime("%Y-%m-%d"),
                self.end_date.strftime("%Y-%m-%d"),
            ])

        rows = self.execute_query(
            "orders.db",
            f"""
            WITH order_totals AS (
                SELECT
                    oi.order_id,
                    SUM(COALESCE(CAST(oi.total AS REAL), 0)) AS order_total
                FROM order_items oi
                GROUP BY oi.order_id
            )
            SELECT
                COALESCE(NULLIF(TRIM(o.order_status), ''), 'Unknown') AS status,
                COUNT(*) AS order_count,
                SUM(CASE WHEN COALESCE(o.billed, 0) = 1 THEN 1 ELSE 0 END) AS billed_count,
                SUM(CASE WHEN COALESCE(o.paid, 0) = 1 THEN 1 ELSE 0 END) AS paid_count,
                SUM(COALESCE(ot.order_total, 0)) AS total_amount,
                MAX({order_date_iso}) AS last_order_date
            FROM orders o
            LEFT JOIN order_totals ot ON ot.order_id = o.id
            {where_sql}
            GROUP BY status
            ORDER BY order_count DESC, status ASC
            """,
            tuple(params),
        )

        return [
            {
                "status": r["status"] or "Unknown",
                "order_count": int(r["order_count"] or 0),
                "billed_count": int(r["billed_count"] or 0),
                "paid_count": int(r["paid_count"] or 0),
                "total_amount": float(r["total_amount"] or 0),
                "last_order_date": self.parse_date(r["last_order_date"]) if r["last_order_date"] else None,
            }
            for r in rows
        ]

    def _calculate_summary(self, rows: List[ReportRow]) -> Dict[str, Any]:
        if not rows:
            return {"total_rows": 0}

        total_orders = sum(int(row.data.get("order_count") or 0) for row in rows)
        total_billed = sum(int(row.data.get("billed_count") or 0) for row in rows)
        total_paid = sum(int(row.data.get("paid_count") or 0) for row in rows)
        total_amount = sum(float(row.data.get("total_amount") or 0) for row in rows)

        return {
            "total_rows": len(rows),
            "total_orders": total_orders,
            "total_billed": total_billed,
            "total_paid": total_paid,
            "total_amount": total_amount,
        }


class OrdersByStatusReport(QDialog):
    """Orders grouped by status report dialog."""

    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."

        self.setWindowTitle("Orders By Status")
        self.setModal(False)
        self.resize(1200, 760)

        self._setup_ui()
        self._generate_report(self.viewer.get_filter_values())

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_date_range("Start Date", "End Date")
        self.viewer.add_filter_search("Search status...")
        self.viewer.refresh_requested.connect(self._generate_report)
        layout.addWidget(self.viewer)

    def _generate_report(self, filters=None):
        try:
            f = filters or {}
            engine = OrdersByStatusReportEngine(
                self.folder_path,
                start_date=f.get("start_date"),
                end_date=f.get("end_date"),
            )
            data = engine.generate()
            data.summary = engine._calculate_summary(data.rows)
            self.viewer.load_report(data)
        except Exception as e:
            QMessageBox.critical(self, "Report Error", f"Failed to generate orders-by-status report:\n{e}")
