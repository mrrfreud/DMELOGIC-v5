"""Low Stock Report - Items below reorder point"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class LowStockEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "⚠️ Low Stock Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("hcpcs", "HCPCS"),
            ReportColumn("description", "Description"),
            ReportColumn("quantity", "Current Stock", data_type="number", alignment="center"),
            ReportColumn("reorder_point", "Reorder Point", data_type="number", alignment="center"),
            ReportColumn("reorder_qty", "Reorder Qty", data_type="number", alignment="center"),
            ReportColumn("supplier", "Supplier"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(hcpcs_code, '') as hcpcs,
                   COALESCE(description, '') as description,
                   COALESCE(stock_quantity, 0) as quantity,
                   COALESCE(reorder_level, 10) as reorder_point,
                   COALESCE(reorder_level * 2, 20) as reorder_qty,
                   COALESCE(supplier, '') as supplier
            FROM inventory
            WHERE COALESCE(stock_quantity, 0) <= COALESCE(reorder_level, 10)
              AND COALESCE(stock_quantity, 0) > 0
            ORDER BY stock_quantity ASC
            """
        )
        return [{'hcpcs': r['hcpcs'], 'description': r['description'], 'quantity': int(r['quantity']),
                 'reorder_point': int(r['reorder_point']), 'reorder_qty': int(r['reorder_qty']),
                 'supplier': r['supplier']} for r in rows]


class LowStockReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("⚠️ Low Stock Report")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_search("Search items...")
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(LowStockEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
