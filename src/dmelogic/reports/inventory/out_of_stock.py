"""Out of Stock Report - Zero inventory items"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class OutOfStockEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "🚫 Out of Stock Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("hcpcs", "HCPCS"),
            ReportColumn("description", "Description"),
            ReportColumn("last_sold", "Last Sold", data_type="date", alignment="center"),
            ReportColumn("demand", "Monthly Demand", data_type="number", alignment="center"),
            ReportColumn("supplier", "Supplier"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(hcpcs_code, '') as hcpcs,
                   COALESCE(description, '') as description,
                   COALESCE(last_used_date, '') as last_sold,
                   0 as demand,
                   COALESCE(supplier, '') as supplier
            FROM inventory
            WHERE COALESCE(stock_quantity, 0) = 0
            ORDER BY last_used_date DESC
            """
        )
        return [{'hcpcs': r['hcpcs'], 'description': r['description'],
                 'last_sold': self.parse_date(r['last_sold']), 'demand': int(r['demand']),
                 'supplier': r['supplier']} for r in rows]


class OutOfStockReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("🚫 Out of Stock Report")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_search("Search...")
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(OutOfStockEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
