"""Gross Margin Report"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class GrossMarginEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "📊 Gross Margin Analysis"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("hcpcs", "HCPCS"),
            ReportColumn("description", "Description"),
            ReportColumn("cost", "Unit Cost", data_type="currency", alignment="right"),
            ReportColumn("price", "Retail Price", data_type="currency", alignment="right"),
            ReportColumn("margin_dollars", "Margin $", data_type="currency", alignment="right"),
            ReportColumn("margin_percent", "Margin %", data_type="percent", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(hcpcs_code, '') as hcpcs,
                   COALESCE(description, '') as description,
                   COALESCE(cost, 0) as cost,
                   COALESCE(retail_price, 0) as price
            FROM inventory
            WHERE COALESCE(retail_price, 0) > 0
            ORDER BY ((COALESCE(retail_price, 0) - COALESCE(cost, 0)) / COALESCE(retail_price, 1) * 100) DESC
            """
        )
        data = []
        for r in rows:
            cost = float(r['cost'])
            price = float(r['price'])
            margin_dollars = price - cost
            margin_percent = (margin_dollars / price * 100) if price > 0 else 0
            data.append({'hcpcs': r['hcpcs'], 'description': r['description'], 'cost': cost,
                         'price': price, 'margin_dollars': margin_dollars, 'margin_percent': margin_percent})
        return data


class GrossMarginReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("📊 Gross Margin")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_search("Search...")
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(GrossMarginEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
