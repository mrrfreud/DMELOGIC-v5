"""Stock Overview Report - Complete inventory snapshot"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class StockOverviewEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "📊 Stock Overview"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("hcpcs", "HCPCS"),
            ReportColumn("description", "Description"),
            ReportColumn("category", "Category"),
            ReportColumn("quantity", "Stock", data_type="number", alignment="center"),
            ReportColumn("unit_cost", "Cost", data_type="currency", alignment="right"),
            ReportColumn("total_value", "Total Value", data_type="currency", alignment="right"),
            ReportColumn("status", "Status", alignment="center"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(hcpcs_code, '') as hcpcs,
                   COALESCE(description, '') as description,
                   COALESCE(category, 'Uncategorized') as category,
                   COALESCE(stock_quantity, 0) as quantity,
                   COALESCE(cost, 0) as unit_cost,
                   COALESCE(reorder_level, 10) as reorder_point
            FROM inventory
            ORDER BY category, hcpcs_code
            """
        )
        data = []
        for r in rows:
            qty = int(r['quantity'])
            cost = float(r['unit_cost'])
            status = 'Out' if qty == 0 else ('Low' if qty <= r['reorder_point'] else 'OK')
            data.append({'hcpcs': r['hcpcs'], 'description': r['description'], 'category': r['category'],
                         'quantity': qty, 'unit_cost': cost, 'total_value': qty * cost, 'status': status})
        return data


class StockOverviewReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("📊 Stock Overview")
        self.setModal(False)
        self.resize(1400, 800)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_combo("Status", ["All", "OK", "Low", "Out"])
        self.viewer.add_filter_search("Search...")
        self.viewer.refresh_requested.connect(self._generate_report)
        layout.addWidget(self.viewer)
        self._generate_report()

    def _generate_report(self, filters=None):
        try:
            data = StockOverviewEngine(self.folder_path).generate()
            if filters and filters.get('Status') and filters['Status'] != 'All':
                data.rows = [r for r in data.rows if r.data['status'] == filters['Status']]
            self.viewer.load_report(data)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
