"""Inventory Value Report - Total value by category"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class InventoryValueEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "💎 Inventory Value Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("category", "Category"),
            ReportColumn("item_count", "Items", data_type="number", alignment="center"),
            ReportColumn("total_units", "Total Units", data_type="number", alignment="center"),
            ReportColumn("total_cost", "Cost Value", data_type="currency", alignment="right"),
            ReportColumn("total_retail", "Retail Value", data_type="currency", alignment="right"),
            ReportColumn("profit_potential", "Profit Potential", data_type="currency", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(category, 'Uncategorized') as category,
                   COUNT(*) as item_count,
                   SUM(COALESCE(stock_quantity, 0)) as total_units,
                   SUM(COALESCE(stock_quantity, 0) * COALESCE(cost, 0)) as total_cost,
                   SUM(COALESCE(stock_quantity, 0) * COALESCE(retail_price, 0)) as total_retail
            FROM inventory
            GROUP BY category
            ORDER BY total_cost DESC
            """
        )
        return [{'category': r['category'], 'item_count': int(r['item_count']),
                 'total_units': int(r['total_units']), 'total_cost': float(r['total_cost']),
                 'total_retail': float(r['total_retail']),
                 'profit_potential': float(r['total_retail']) - float(r['total_cost'])} for r in rows]


class InventoryValueReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("💎 Inventory Value")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer()
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(InventoryValueEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
