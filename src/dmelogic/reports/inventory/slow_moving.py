"""Slow Moving Items Report"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class SlowMovingEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "🐌 Slow Moving Items"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("hcpcs", "HCPCS"),
            ReportColumn("description", "Description"),
            ReportColumn("quantity", "Stock", data_type="number", alignment="center"),
            ReportColumn("last_sold", "Last Sold", data_type="date", alignment="center"),
            ReportColumn("days_since", "Days Since Sale", data_type="number", alignment="center"),
            ReportColumn("tied_up_cost", "Tied Up Cost", data_type="currency", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        from datetime import date
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(hcpcs_code, '') as hcpcs,
                   COALESCE(description, '') as description,
                   COALESCE(stock_quantity, 0) as quantity,
                   COALESCE(last_used_date, '') as last_sold,
                   COALESCE(cost, 0) as unit_cost
            FROM inventory
            WHERE COALESCE(stock_quantity, 0) > 0
            ORDER BY last_used_date ASC
            """
        )
        data = []
        today = date.today()
        for r in rows:
            last_sold_date = self.parse_date(r['last_sold'])
            days_since = (today - last_sold_date).days if last_sold_date else 999
            qty = int(r['quantity'])
            cost = float(r['unit_cost'])
            data.append({'hcpcs': r['hcpcs'], 'description': r['description'], 'quantity': qty,
                         'last_sold': last_sold_date, 'days_since': days_since,
                         'tied_up_cost': qty * cost})
        return data


class SlowMovingReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("🐌 Slow Moving Items")
        self.setModal(False)
        self.resize(1400, 800)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_search("Search...")
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(SlowMovingEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
