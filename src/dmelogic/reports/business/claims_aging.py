"""
Claims Aging Report (Migrated to Foundation)

AR aging report using the reporting foundation.
Much cleaner and more maintainable than standalone version.
"""

from typing import List, Dict, Any
from datetime import datetime, date, timedelta

from PyQt6.QtWidgets import QDialog, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from dmelogic.reports.base import ReportEngine, ReportColumn, ReportRow
from dmelogic.reports.ui import ReportViewer


class ClaimsAgingReportEngine(ReportEngine):
    """
    Claims Aging Report Engine.

    Shows outstanding claims grouped by age buckets.
    """

    def get_report_title(self) -> str:
        return "📊 Accounts Receivable Aging Report"

    def get_columns(self) -> List[ReportColumn]:
        return [
            ReportColumn("claim_id", "Claim ID", alignment="left"),
            ReportColumn("patient_name", "Patient", alignment="left"),
            ReportColumn("insurance_name", "Insurance", alignment="left"),
            ReportColumn("claim_date", "Claim Date", data_type="date", alignment="center"),
            ReportColumn("billed", "Billed", data_type="currency", alignment="right"),
            ReportColumn("paid", "Paid", data_type="currency", alignment="right"),
            ReportColumn("balance", "Balance", data_type="currency", alignment="right"),
            ReportColumn("days_old", "Days Old", data_type="number", alignment="center"),
            ReportColumn("age_bucket", "Age Bucket", alignment="center"),
            ReportColumn("status", "Status", alignment="center")
        ]

    def _fetch_data(self) -> List[Dict[str, Any]]:
        """Fetch claims data from billing database."""
        rows = self.execute_query(
            "billing.db",
            """
            SELECT
                claim_id,
                COALESCE(claim_date, created_date) as claim_date,
                COALESCE(patient_id, 0) as patient_id,
                COALESCE(insurance_name, 'Unknown') as insurance_name,
                COALESCE(claim_amount, 0) as billed,
                COALESCE(paid_amount, 0) as paid,
                COALESCE(status, 'Pending') as status
            FROM claims
            WHERE status NOT IN ('Paid', 'Closed', 'Denied')
            ORDER BY claim_date ASC
            """
        )

        # Look up patient names from patients.db
        patient_ids = list(set(r['patient_id'] for r in rows if r['patient_id']))
        patient_names = {}
        if patient_ids:
            for pid in patient_ids:
                name = self.execute_scalar(
                    "patients.db",
                    "SELECT first_name || ' ' || last_name FROM patients WHERE id = ?",
                    (pid,)
                )
                if name:
                    patient_names[pid] = name

        today = date.today()
        claims = []

        for row in rows:
            # Parse claim date
            claim_date_str = row['claim_date']
            claim_date = self.parse_date(claim_date_str)
            if not claim_date:
                claim_date = today

            # Calculate balance and age
            billed = float(row['billed'])
            paid = float(row['paid'])
            balance = billed - paid

            days_old = (today - claim_date).days

            # Determine age bucket
            if days_old <= 30:
                age_bucket = "0-30 days"
            elif days_old <= 60:
                age_bucket = "31-60 days"
            elif days_old <= 90:
                age_bucket = "61-90 days"
            else:
                age_bucket = "90+ days"

            patient_name = patient_names.get(
                row['patient_id'], f"Patient #{row['patient_id']}"
            )

            claims.append({
                'claim_id': row['claim_id'],
                'patient_name': patient_name,
                'insurance_name': row['insurance_name'],
                'claim_date': claim_date,
                'billed': billed,
                'paid': paid,
                'balance': balance,
                'days_old': days_old,
                'age_bucket': age_bucket,
                'status': row['status']
            })

        return claims

    def _process_data(self, raw_data: List[Dict[str, Any]]) -> List[ReportRow]:
        """Process data and add color coding by age."""
        rows = []

        for claim in raw_data:
            # Color code by age
            days_old = claim['days_old']
            style = {}

            if days_old > 90:
                style['background_color'] = '#ffebee'  # Light red
            elif days_old > 60:
                style['background_color'] = '#fff3e0'  # Light orange
            elif days_old > 30:
                style['background_color'] = '#fffde7'  # Light yellow
            else:
                style['background_color'] = '#e8f5e9'  # Light green

            rows.append(ReportRow(data=claim, style=style))

        return rows

    def _calculate_summary(self, rows: List[ReportRow]) -> Dict[str, Any]:
        """Calculate AR aging summary statistics."""
        if not rows:
            return {'total_rows': 0}

        # Group by age buckets
        buckets = {
            '0-30 days': [],
            '31-60 days': [],
            '61-90 days': [],
            '90+ days': []
        }

        for row in rows:
            bucket = row.data['age_bucket']
            buckets[bucket].append(row.data['balance'])

        # Calculate totals
        total_ar = sum(row.data['balance'] for row in rows)

        summary = {
            'total_rows': len(rows),
            'total_ar': total_ar,
            'bucket_0_30': sum(buckets['0-30 days']),
            'bucket_31_60': sum(buckets['31-60 days']),
            'bucket_61_90': sum(buckets['61-90 days']),
            'bucket_90_plus': sum(buckets['90+ days']),
            'count_0_30': len(buckets['0-30 days']),
            'count_31_60': len(buckets['31-60 days']),
            'count_61_90': len(buckets['61-90 days']),
            'count_90_plus': len(buckets['90+ days']),
        }

        # Calculate percentages
        if total_ar > 0:
            summary['pct_0_30'] = (summary['bucket_0_30'] / total_ar) * 100
            summary['pct_31_60'] = (summary['bucket_31_60'] / total_ar) * 100
            summary['pct_61_90'] = (summary['bucket_61_90'] / total_ar) * 100
            summary['pct_90_plus'] = (summary['bucket_90_plus'] / total_ar) * 100

        return summary


class ClaimsAgingReport(QDialog):
    """
    Claims Aging Report Dialog (using foundation).

    Displays AR aging report with:
    - Age bucket filtering
    - Summary statistics
    - Color-coded rows
    - Export capabilities (automatic via ReportViewer)
    """

    def __init__(self, parent=None, folder_path=None):
        super().__init__(parent)
        self.folder_path = folder_path or "."

        self.setWindowTitle("📊 AR Aging Report")
        self.setModal(False)
        self.resize(1400, 800)

        self._setup_ui()
        self._generate_report()

    def _setup_ui(self):
        """Setup the UI using ReportViewer."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create viewer with filters
        self.viewer = ReportViewer(
            parent=self,
            show_chart_area=False,  # No chart for AR aging
            show_filters=True
        )

        # Add age bucket filter
        self.viewer.add_filter_combo(
            "Age Bucket",
            ["All Claims", "0-30 days", "31-60 days", "61-90 days", "90+ days"],
            default_index=0
        )

        # Connect refresh
        self.viewer.refresh_requested.connect(self._generate_report)

        # Add summary panel above table
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QGroupBox
        from PyQt6.QtGui import QFont

        summary_frame = QFrame()
        summary_frame.setStyleSheet("""
            QFrame {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 6px;
                padding: 12px;
            }
        """)
        summary_layout = QHBoxLayout(summary_frame)

        self.summary_cards = {}
        for bucket, color in [
            ("0-30 days", "#4caf50"),
            ("31-60 days", "#ff9800"),
            ("61-90 days", "#f44336"),
            ("90+ days", "#d32f2f")
        ]:
            card = self._create_summary_card(bucket, "$0", "0", color)
            summary_layout.addWidget(card)
            self.summary_cards[bucket] = card

        # Insert summary at top of viewer
        self.viewer.layout().insertWidget(1, summary_frame)

        layout.addWidget(self.viewer)

    def _create_summary_card(self, title: str, amount: str, count: str, color: str):
        """Create a summary card for age bucket."""
        from PyQt6.QtWidgets import QGroupBox, QVBoxLayout, QLabel
        from PyQt6.QtGui import QFont

        card = QGroupBox()
        card.setStyleSheet(f"""
            QGroupBox {{
                background-color: white;
                border: 2px solid {color};
                border-radius: 8px;
                padding: 12px;
                min-width: 150px;
            }}
        """)

        layout = QVBoxLayout(card)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 10pt; font-weight: 600; color: #666;")
        layout.addWidget(title_label)

        amount_label = QLabel(amount)
        amount_label.setStyleSheet(f"font-size: 18pt; font-weight: bold; color: {color};")
        amount_label.setObjectName("amount_label")
        layout.addWidget(amount_label)

        count_label = QLabel(f"{count} claims")
        count_label.setStyleSheet("font-size: 9pt; color: #888;")
        count_label.setObjectName("count_label")
        layout.addWidget(count_label)

        return card

    def _generate_report(self, filters=None):
        """Generate and display the report."""
        try:
            # Create report engine
            engine = ClaimsAgingReportEngine(self.folder_path)

            # Generate data
            data = engine.generate()

            # Apply filter if specified
            if filters and filters.get('Age Bucket') and filters['Age Bucket'] != 'All Claims':
                selected_bucket = filters['Age Bucket']
                data.rows = [row for row in data.rows if row.data['age_bucket'] == selected_bucket]

            # Update summary cards
            self._update_summary_cards(data.summary)

            # Load into viewer
            self.viewer.load_report(data)

        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Report Error",
                f"Failed to generate AR report:\n{str(e)}"
            )
            import traceback
            traceback.print_exc()

    def _update_summary_cards(self, summary: Dict[str, Any]):
        """Update summary cards with statistics."""
        from PyQt6.QtWidgets import QLabel

        buckets = [
            ("0-30 days", "bucket_0_30", "count_0_30"),
            ("31-60 days", "bucket_31_60", "count_31_60"),
            ("61-90 days", "bucket_61_90", "count_61_90"),
            ("90+ days", "bucket_90_plus", "count_90_plus")
        ]

        for bucket_name, amount_key, count_key in buckets:
            card = self.summary_cards.get(bucket_name)
            if card:
                amount = summary.get(amount_key, 0)
                count = summary.get(count_key, 0)

                amount_label = card.findChild(QLabel, "amount_label")
                if amount_label:
                    amount_label.setText(f"${amount:,.0f}")

                count_label = card.findChild(QLabel, "count_label")
                if count_label:
                    count_label.setText(f"{count} claims")


# ============================================================================
# Standalone test
# ============================================================================

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    dialog = ClaimsAgingReport(folder_path=".")
    dialog.show()
    sys.exit(app.exec())
