"""Category Profit Report"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class CategoryProfitEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "📈 Category Profit"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("category", "Category"),
            ReportColumn("items", "Items", data_type="number", alignment="center"),
            ReportColumn("revenue", "Revenue", data_type="currency", alignment="right"),
            ReportColumn("cost", "Cost", data_type="currency", alignment="right"),
            ReportColumn("profit", "Profit", data_type="currency", alignment="right"),
            ReportColumn("margin", "Margin %", data_type="percent", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "inventory.db",
            """
            SELECT COALESCE(category, 'Uncategorized') as category,
                   COUNT(*) as items,
                   SUM(COALESCE(stock_quantity, 0) * COALESCE(retail_price, 0)) as revenue,
                   SUM(COALESCE(stock_quantity, 0) * COALESCE(cost, 0)) as cost
            FROM inventory
            GROUP BY category
            ORDER BY revenue DESC
            """
        )
        data = []
        for r in rows:
            revenue = float(r['revenue'])
            cost = float(r['cost'])
            profit = revenue - cost
            margin = (profit / revenue * 100) if revenue > 0 else 0
            data.append({'category': r['category'], 'items': int(r['items']), 'revenue': revenue,
                         'cost': cost, 'profit': profit, 'margin': margin})
        return data


class CategoryProfitReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("📈 Category Profit")
        self.setModal(False)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer()
        layout.addWidget(self.viewer)
        try:
            self.viewer.load_report(CategoryProfitEngine(self.folder_path).generate())
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
