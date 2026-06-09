"""Top Suppliers Report"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class TopSuppliersEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "🏆 Top Suppliers"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("supplier", "Supplier"),
            ReportColumn("items", "Items", data_type="number", alignment="center"),
            ReportColumn("total_stock", "Total Stock", data_type="number", alignment="center"),
            ReportColumn("inventory_value", "Inventory Value", data_type="currency", alignment="right"),
            ReportColumn("purchases", "Total Purchases", data_type="currency", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(supplier, 'Unknown') as supplier,
                   COUNT(*) as items,
                   SUM(COALESCE(stock_quantity, 0)) as total_stock,
                   SUM(COALESCE(stock_quantity, 0) * COALESCE(cost, 0)) as inventory_value
            FROM inventory
            GROUP BY supplier
            ORDER BY inventory_value DESC
            """
        )
        return [{'supplier': r['supplier'], 'items': int(r['items']), 'total_stock': int(r['total_stock']),
                 'inventory_value': float(r['inventory_value']), 'purchases': float(r['inventory_value'])} for r in rows]


class TopSuppliersReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("🏆 Top Suppliers")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer()
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(TopSuppliersEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
