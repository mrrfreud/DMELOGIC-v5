"""
Billing Report - Migrated to Foundation

Shows billing history and payment tracking.
"""

from typing import List, Dict, Any

from PyQt6.QtWidgets import QDialog, QVBoxLayout

from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class BillingReportEngine(ReportEngine):
    """Billing Report Engine."""

    def get_report_title(self) -> str:
        return "💵 Billing Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("invoice_id", "Invoice #", alignment="left"),
            ReportColumn("invoice_date", "Date", data_type="date", alignment="center"),
            ReportColumn("patient_name", "Patient", alignment="left"),
            ReportColumn("insurance", "Insurance", alignment="left"),
            ReportColumn("billed", "Billed", data_type="currency", alignment="right"),
            ReportColumn("paid", "Paid", data_type="currency", alignment="right"),
            ReportColumn("balance", "Balance", data_type="currency", alignment="right"),
            ReportColumn("status", "Status", alignment="center"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch billing data from claims table."""
        rows = self.execute_query(
            "billing.db",
            """
            SELECT
                claim_id as invoice_id,
                COALESCE(claim_date, created_date) as invoice_date,
                COALESCE(patient_id, 0) as patient_id,
                COALESCE(insurance_name, 'Self Pay') as insurance,
                COALESCE(claim_amount, 0) as billed,
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

        billing = []
        for row in rows:
            billed = float(row['billed'])
            paid = float(row['paid'])

            billing.append({
                'invoice_id': row['invoice_id'],
                'invoice_date': self.parse_date(row['invoice_date']),
                'patient_name': patient_names.get(
                    row['patient_id'], f"Patient #{row['patient_id']}"
                ),
                'insurance': row['insurance'],
                'billed': billed,
                'paid': paid,
                'balance': billed - paid,
                'status': row['status'],
            })

        return billing

    def _calculate_summary(self, rows: List[ReportRow]) -> Dict[str, Any]:
        """Calculate summary statistics."""
        if not rows:
            return {'total_rows': 0}

        total_billed = sum(r.data['billed'] for r in rows)
        total_paid = sum(r.data['paid'] for r in rows)
        total_balance = total_billed - total_paid

        return {
            'total_rows': len(rows),
            'total_billed': total_billed,
            'total_paid': total_paid,
            'total_balance': total_balance,
        }


class BillingReport(QDialog):
    """Billing Report Dialog using foundation."""

    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."

        self.setWindowTitle("💵 Billing Report")
        self.setModal(False)
        self.resize(1400, 800)

        self._setup_ui()
        self._generate_report(self.viewer.get_filter_values())

    def _setup_ui(self):
        """Setup UI with ReportViewer."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.viewer = ReportViewer(show_filters=True)

        self.viewer.add_filter_date_range("Start Date", "End Date")
        self.viewer.add_filter_combo("Status", ["All", "Paid", "Pending", "Overdue"])
        self.viewer.add_filter_search("Search...")

        self.viewer.refresh_requested.connect(self._generate_report)
        layout.addWidget(self.viewer)

    def _generate_report(self, filters=None):
        """Generate and display the report."""
        try:
            engine = BillingReportEngine(self.folder_path)
            data = engine.generate()

            # Apply date range filter
            if filters:
                sd, ed = filters.get('start_date'), filters.get('end_date')
                if sd and ed:
                    data.rows = [r for r in data.rows
                                 if r.data.get('invoice_date') and sd <= r.data['invoice_date'] <= ed]

            if filters and filters.get('Status') and filters['Status'] != 'All':
                data.rows = [r for r in data.rows if r.data['status'] == filters['Status']]

            # Recalculate summary from filtered rows
            data.summary = engine._calculate_summary(data.rows)

            self.viewer.load_report(data)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Report Error", f"Failed to generate billing report:\n{str(e)}")


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dialog = BillingReport(folder_path=".")
    dialog.show()
    sys.exit(app.exec())
