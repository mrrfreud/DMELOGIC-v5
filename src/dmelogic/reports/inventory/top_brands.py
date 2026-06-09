"""Top Brands Report"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class TopBrandsEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "🏅 Top Brands"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("brand", "Brand"),
            ReportColumn("items", "Items", data_type="number", alignment="center"),
            ReportColumn("units_sold", "Units Sold", data_type="number", alignment="center"),
            ReportColumn("revenue", "Revenue", data_type="currency", alignment="right"),
            ReportColumn("market_share", "Market Share", data_type="percent", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(brand, 'Generic') as brand,
                   COUNT(*) as items,
                   SUM(COALESCE(stock_quantity, 0)) as total_units,
                   SUM(COALESCE(stock_quantity, 0) * COALESCE(retail_price, 0)) as revenue
            FROM inventory
            GROUP BY brand
            ORDER BY revenue DESC
            """
        )
        total_revenue = sum(float(r['revenue'] or 0) for r in rows)
        return [{'brand': r['brand'], 'items': int(r['items']), 'units_sold': int(r['total_units'] or 0),
                 'revenue': float(r['revenue'] or 0),
                 'market_share': (float(r['revenue'] or 0) / total_revenue * 100) if total_revenue > 0 else 0} for r in rows]


class TopBrandsReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("🏅 Top Brands")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer()
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(TopBrandsEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
