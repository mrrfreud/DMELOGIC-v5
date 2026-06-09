"""Potential Revenue Report"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class PotentialRevenueEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "💰 Potential Revenue"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("hcpcs", "HCPCS"),
            ReportColumn("description", "Description"),
            ReportColumn("quantity", "Stock", data_type="number", alignment="center"),
            ReportColumn("retail_price", "Retail Price", data_type="currency", alignment="right"),
            ReportColumn("potential_revenue", "Potential Revenue", data_type="currency", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(hcpcs_code, '') as hcpcs,
                   COALESCE(description, '') as description,
                   COALESCE(stock_quantity, 0) as quantity,
                   COALESCE(retail_price, 0) as retail_price
            FROM inventory
            WHERE COALESCE(stock_quantity, 0) > 0
            ORDER BY (COALESCE(stock_quantity, 0) * COALESCE(retail_price, 0)) DESC
            """
        )
        return [{'hcpcs': r['hcpcs'], 'description': r['description'], 'quantity': int(r['quantity']),
                 'retail_price': float(r['retail_price']),
                 'potential_revenue': int(r['quantity']) * float(r['retail_price'])} for r in rows]


class PotentialRevenueReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("💰 Potential Revenue")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_search("Search...")
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(PotentialRevenueEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
