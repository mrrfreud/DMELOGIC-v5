"""Invoice Report - Migrated to Foundation"""
from typing import List, Dict, Any
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class InvoiceReportEngine(ReportEngine):
    def get_report_title(self) -> str:
        return "📄 Invoice Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("invoice_id", "Invoice #"),
            ReportColumn("invoice_date", "Date", data_type="date", alignment="center"),
            ReportColumn("patient_name", "Patient"),
            ReportColumn("amount", "Amount", data_type="currency", alignment="right"),
            ReportColumn("paid", "Paid", data_type="currency", alignment="right"),
            ReportColumn("status", "Status", alignment="center"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        rows = self.execute_query(
            "billing.db",
            """
            SELECT claim_id as invoice_id,
                   COALESCE(claim_date, created_date) as invoice_date,
                   COALESCE(patient_id, 0) as patient_id,
                   COALESCE(claim_amount, 0) as amount,
                   COALESCE(paid_amount, 0) as paid,
                   COALESCE(status, 'Pending') as status
            FROM claims
            ORDER BY COALESCE(claim_date, created_date) DESC
            LIMIT 1000
            """
        )

        # Look up patient names
        patient_ids = list(set(r['patient_id'] for r in rows if r['patient_id']))
        patient_names = {}
        for pid in patient_ids:
            name = self.execute_scalar(
                "patients.db",
                "SELECT first_name || ' ' || last_name FROM patients WHERE id = ?",
                (pid,)
            )
            if name:
                patient_names[pid] = name

        return [{'invoice_id': r['invoice_id'], 'invoice_date': self.parse_date(r['invoice_date']),
                 'patient_name': patient_names.get(r['patient_id'], f"Patient #{r['patient_id']}"),
                 'amount': float(r['amount']),
                 'paid': float(r['paid']), 'status': r['status']} for r in rows]


class InvoiceReport(QDialog):
    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."
        self.setWindowTitle("📄 Invoice Report")
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
            engine = InvoiceReportEngine(self.folder_path)
            data = engine.generate()
            # Apply date range filter
            if filters:
                sd, ed = filters.get('start_date'), filters.get('end_date')
                if sd and ed:
                    data.rows = [r for r in data.rows
                                 if r.data.get('invoice_date') and sd <= r.data['invoice_date'] <= ed]
            data.summary = engine._calculate_summary(data.rows)
            self.viewer.load_report(data)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed: {e}")
