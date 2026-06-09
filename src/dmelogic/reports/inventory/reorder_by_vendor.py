"""Reorder by Vendor Report - Grouped reorder list"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class ReorderByVendorEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "📋 Reorder by Vendor"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("supplier", "Supplier/Vendor"),
            ReportColumn("hcpcs", "HCPCS"),
            ReportColumn("description", "Description"),
            ReportColumn("current_stock", "Current", data_type="number", alignment="center"),
            ReportColumn("reorder_qty", "Reorder Qty", data_type="number", alignment="center"),
            ReportColumn("unit_cost", "Unit Cost", data_type="currency", alignment="right"),
            ReportColumn("total_cost", "Total Cost", data_type="currency", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(supplier, 'Unknown') as supplier,
                   COALESCE(hcpcs_code, '') as hcpcs,
                   COALESCE(description, '') as description,
                   COALESCE(stock_quantity, 0) as current_stock,
                   COALESCE(reorder_level * 2, 20) as reorder_qty,
                   COALESCE(cost, 0) as unit_cost
            FROM inventory
            WHERE COALESCE(stock_quantity, 0) <= COALESCE(reorder_level, 10)
            ORDER BY supplier, hcpcs_code
            """
        )
        return [{'supplier': r['supplier'], 'hcpcs': r['hcpcs'], 'description': r['description'],
                 'current_stock': int(r['current_stock']), 'reorder_qty': int(r['reorder_qty']),
                 'unit_cost': float(r['unit_cost']),
                 'total_cost': int(r['reorder_qty']) * float(r['unit_cost'])} for r in rows]


class ReorderByVendorReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("📋 Reorder by Vendor")
        self.setModal(False)
        self.resize(1400, 800)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_search("Search...")
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(ReorderByVendorEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
