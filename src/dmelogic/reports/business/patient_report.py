"""
Patient Report - Migrated to Foundation

Shows patient demographics, insurance, and contact information.
"""

from typing import List, Dict, Any

from PyQt6.QtWidgets import QDialog, QVBoxLayout

from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class PatientReportEngine(ReportEngine):
    """Patient Report Engine - Shows patient demographics."""

    def get_report_title(self) -> str:
        return "👥 Patient Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("patient_id", "Patient ID", alignment="left"),
            ReportColumn("full_name", "Patient Name", alignment="left"),
            ReportColumn("dob", "Date of Birth", data_type="date", alignment="center"),
            ReportColumn("phone", "Phone", alignment="left"),
            ReportColumn("insurance", "Primary Insurance", alignment="left"),
            ReportColumn("address", "Address", alignment="left"),
            ReportColumn("status", "Status", alignment="center"),
            ReportColumn("last_order", "Last Order", data_type="date", alignment="center"),
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch patients from database."""
        rows = self.execute_query(
            "patients.db",
            """
            SELECT
                id as patient_id,
                first_name || ' ' || last_name as full_name,
                COALESCE(dob, '') as dob,
                COALESCE(phone, '') as phone,
                COALESCE(primary_insurance, 'Self Pay') as insurance,
                COALESCE(address || ', ' || city || ', ' || state || ' ' || zip,
                         COALESCE(address, '')) as address
            FROM patients
            ORDER BY last_name, first_name ASC
            """
        )

        patients = []
        for row in rows:
            # Get last order date for this patient
            last_order = self.execute_scalar(
                "orders.db",
                """
                SELECT MAX(COALESCE(order_date, created_date))
                FROM orders
                WHERE patient_id = ?
                """,
                (row['patient_id'],)
            )

            patients.append({
                'patient_id': row['patient_id'],
                'full_name': row['full_name'] or '',
                'dob': self.parse_date(row['dob']),
                'phone': row['phone'],
                'insurance': row['insurance'],
                'address': row['address'] or '',
                'status': 'Active',
                'last_order': self.parse_date(last_order) if last_order else None,
            })

        return patients

    def _calculate_summary(self, rows: List[ReportRow]) -> Dict[str, Any]:
        """Calculate summary statistics."""
        if not rows:
            return {'total_rows': 0}

        # Count by status
        active = sum(1 for r in rows if r.data.get('status') == 'Active')
        inactive = sum(1 for r in rows if r.data.get('status') == 'Inactive')

        # Count by insurance
        insurance_counts = {}
        for row in rows:
            ins = row.data.get('insurance', 'Unknown')
            insurance_counts[ins] = insurance_counts.get(ins, 0) + 1

        return {
            'total_rows': len(rows),
            'active_count': active,
            'inactive_count': inactive,
            'insurance_breakdown': insurance_counts
        }


class PatientReport(QDialog):
    """Patient Report Dialog using foundation."""

    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."

        self.setWindowTitle("👥 Patient Report")
        self.setModal(False)
        self.resize(1400, 800)

        self._setup_ui()
        self._generate_report()

    def _setup_ui(self):
        """Setup UI with ReportViewer."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.viewer = ReportViewer(show_filters=True)

        # Add filters
        self.viewer.add_filter_combo("Status", ["All", "Active", "Inactive"])
        self.viewer.add_filter_search("Search patients...")

        self.viewer.refresh_requested.connect(self._generate_report)
        layout.addWidget(self.viewer)

    def _generate_report(self, filters=None):
        """Generate and display the report."""
        try:
            engine = PatientReportEngine(self.folder_path)
            data = engine.generate()

            # Apply status filter
            if filters and filters.get('Status') and filters['Status'] != 'All':
                data.rows = [r for r in data.rows if r.data['status'] == filters['Status']]

            self.viewer.load_report(data)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Report Error", f"Failed to generate patient report:\n{str(e)}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dialog = PatientReport(folder_path=".")
    dialog.show()
    sys.exit(app.exec())
