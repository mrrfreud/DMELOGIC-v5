"""
Delivery Report - Migrated to Foundation

Shows delivery schedule and tracking.
"""

from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class DeliveryReportEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "🚚 Delivery Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("order_number", "Order #"),
            ReportColumn("patient_name", "Patient"),
            ReportColumn("delivery_date", "Delivery Date", data_type="date", alignment="center"),
            ReportColumn("address", "Address"),
            ReportColumn("status", "Status", alignment="center"),
            ReportColumn("driver", "Driver"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "orders.db",
            """
            SELECT id as order_number,
                   COALESCE(patient_name,
                            patient_last_name || ', ' || patient_first_name, '') as patient_name,
                   COALESCE(delivery_date, order_date) as delivery_date,
                   '' as address,
                   COALESCE(order_status, 'Pending') as status,
                   '' as driver
            FROM orders
            WHERE delivery_date IS NOT NULL
            ORDER BY delivery_date ASC
            """
        )
        return [{'order_number': r['order_number'], 'patient_name': r['patient_name'] or '',
                 'delivery_date': self.parse_date(r['delivery_date']),
                 'address': r['address'], 'status': r['status'], 'driver': r['driver']} for r in rows]


class DeliveryReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("🚚 Delivery Report")
        self.setModal(False)
        self.resize(1400, 800)
        self._setup_ui()
        self._generate_report(self.viewer.get_filter_values())

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = ReportViewer(show_filters=True)
        self.viewer.add_filter_date_range("Start Date", "End Date")
        self.viewer.add_filter_combo("Status", ["All", "Pending", "Delivered", "In Transit"])
        self.viewer.refresh_requested.connect(self._generate_report)
        layout.addWidget(self.viewer)

    def _generate_report(self, filters=None):
        try:
            engine = DeliveryReportEngine(self.folder_path)
            data = engine.generate()
            # Apply date range filter
            if filters:
                sd, ed = filters.get('start_date'), filters.get('end_date')
                if sd and ed:
                    data.rows = [r for r in data.rows
                                 if r.data.get('delivery_date') and sd <= r.data['delivery_date'] <= ed]
            if filters and filters.get('Status') and filters['Status'] != 'All':
                data.rows = [r for r in data.rows if r.data['status'] == filters['Status']]
            data.summary = engine._calculate_summary(data.rows)
            self.viewer.load_report(data)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Report Error", f"Failed to generate delivery report:\n{str(e)}")
