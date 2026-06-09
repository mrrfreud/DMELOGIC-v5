"""Orders Profit Report"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class OrdersProfitEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "💵 Orders Profit"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("order_id", "Order #"),
            ReportColumn("order_date", "Date", data_type="date", alignment="center"),
            ReportColumn("patient", "Patient"),
            ReportColumn("revenue", "Revenue", data_type="currency", alignment="right"),
            ReportColumn("cost", "Cost", data_type="currency", alignment="right"),
            ReportColumn("profit", "Profit", data_type="currency", alignment="right"),
            ReportColumn("margin", "Margin %", data_type="percent", alignment="right"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        # Build inventory cost lookup from inventory.db
        inv_rows = self.execute_query(
            "inventory.db",
            "SELECT hcpcs_code, CAST(cost AS REAL) as cost FROM inventory WHERE cost IS NOT NULL AND cost != ''"
        )
        cost_lookup = {}
        for r in inv_rows:
            if r['hcpcs_code']:
                cost_lookup[r['hcpcs_code']] = float(r['cost'] or 0)

        # Fetch orders with line items
        rows = self.execute_query(
            "orders.db",
            """
            SELECT o.id as order_id,
                   COALESCE(o.order_date, o.created_date) as order_date,
                   COALESCE(o.patient_name,
                            o.patient_last_name || ', ' || o.patient_first_name, '') as patient,
                   oi.hcpcs_code,
                   CAST(COALESCE(oi.qty, 1) AS REAL) as qty,
                   CAST(COALESCE(oi.total, 0) AS REAL) as line_total
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            WHERE LOWER(COALESCE(o.order_status, '')) IN ('billed', 'shipped', 'picked up', 'delivered', 'paid')
            ORDER BY o.id DESC
            """
        )

        # Aggregate per order
        orders = {}
        for r in rows:
            oid = r['order_id']
            if oid not in orders:
                orders[oid] = {
                    'order_id': oid,
                    'order_date': self.parse_date(r['order_date']),
                    'patient': r['patient'],
                    'revenue': 0.0,
                    'cost': 0.0,
                }
            orders[oid]['revenue'] += float(r['line_total'])
            hcpcs = r['hcpcs_code'] or ''
            item_cost = cost_lookup.get(hcpcs, 0.0)
            orders[oid]['cost'] += item_cost * float(r['qty'])

        data = []
        for o in orders.values():
            profit = o['revenue'] - o['cost']
            margin = (profit / o['revenue'] * 100) if o['revenue'] > 0 else 0
            o['profit'] = profit
            o['margin'] = margin
            data.append(o)

        data.sort(key=lambda x: x['order_id'], reverse=True)
        return data[:500]


class OrdersProfitReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("💵 Orders Profit")
        self.setModal(False)
        self.resize(1400, 800)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_date_range("Start", "End")
        self.viewer.add_filter_search("Search...")
        self.viewer.refresh_requested.connect(self._generate_report)
        layout.addWidget(self.viewer)
        self._generate_report(self.viewer.get_filter_values())

    def _generate_report(self, filters=None):
        try:
            engine = OrdersProfitEngine(self.folder_path)
            data = engine.generate()
            # Apply date range filter
            if filters:
                sd, ed = filters.get('start_date'), filters.get('end_date')
                if sd and ed:
                    data.rows = [r for r in data.rows
                                 if r.data.get('order_date') and sd <= r.data['order_date'] <= ed]
            data.summary = engine._calculate_summary(data.rows)
            self.viewer.load_report(data)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
