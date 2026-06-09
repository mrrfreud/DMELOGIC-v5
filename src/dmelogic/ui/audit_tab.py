"""
Audit Tab - Comprehensive audit reports for DME order compliance and data quality.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import sqlite3

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QDateEdit,
    QGroupBox, QSpinBox, QMessageBox, QSplitter, QTextEdit, QProgressBar,
    QApplication, QFileDialog
)


@dataclass
class AuditResult:
    """Represents a single audit finding."""
    order_id: int
    order_number: str
    patient_name: str
    order_date: str
    status: str
    issue: str
    severity: str  # "Critical", "Warning", "Info"


def build_audit_tab(parent) -> QWidget:
    """Build the Audit tab widget."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(10)

    # Header
    header = QLabel("🔍 Audit & Compliance Reports")
    header_font = QFont("Segoe UI", 14)
    header_font.setBold(True)
    header.setFont(header_font)
    header.setStyleSheet("color: #ffffff; padding: 5px;")
    layout.addWidget(header)

    # Description
    desc = QLabel("Run audit reports to identify orders with missing data, compliance issues, or anomalies.")
    desc.setStyleSheet("color: #94a3b8; font-size: 10pt; margin-bottom: 10px;")
    layout.addWidget(desc)

    # ===== Report Selection Panel =====
    report_group = QGroupBox("Select Audit Report")
    report_group.setStyleSheet("""
        QGroupBox {
            font-weight: 600;
            border: 1px solid #334155;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 10px;
            background-color: #1e293b;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 8px;
            color: #e2e8f0;
        }
    """)
    report_layout = QVBoxLayout(report_group)

    # Report type selector row
    selector_row = QHBoxLayout()
    selector_row.setSpacing(15)

    report_type_label = QLabel("Report Type:")
    report_type_label.setStyleSheet("color: #e2e8f0; font-weight: 500;")
    selector_row.addWidget(report_type_label)

    parent.audit_report_combo = QComboBox()
    parent.audit_report_combo.setMinimumWidth(300)
    parent.audit_report_combo.addItems([
        "📎 Orders Missing Document Attachments",
        "🏥 Orders Missing Insurance Information",
        "👨‍⚕️ Orders Missing Prescriber NPI",
        "🏷️ Orders Missing ICD-10 Codes",
        "⏳ Orders Stuck in Status (Aging)",
        "👤 Orders Without Patient Link",
        "💵 Unbilled Orders Over X Days",
        "📅 Orders Missing Delivery Date",
        "📝 Orders Missing Notes/Instructions",
        "🔗 Orphaned Order Items",
        "📊 Complete Data Quality Summary",
    ])
    parent.audit_report_combo.setStyleSheet("""
        QComboBox {
            background-color: #334155;
            color: #f1f5f9;
            border: 1px solid #475569;
            border-radius: 4px;
            padding: 6px 10px;
            min-height: 24px;
        }
        QComboBox:hover {
            border-color: #60a5fa;
        }
        QComboBox::drop-down {
            border: none;
            padding-right: 8px;
        }
        QComboBox QAbstractItemView {
            background-color: #334155;
            color: #f1f5f9;
            selection-background-color: #2563eb;
        }
    """)
    selector_row.addWidget(parent.audit_report_combo)

    selector_row.addStretch(1)
    report_layout.addLayout(selector_row)

    # Filters row
    filters_row = QHBoxLayout()
    filters_row.setSpacing(15)

    # Date range filter
    date_from_label = QLabel("From:")
    date_from_label.setStyleSheet("color: #e2e8f0;")
    filters_row.addWidget(date_from_label)

    parent.audit_date_from = QDateEdit()
    parent.audit_date_from.setDisplayFormat("MM/dd/yyyy")
    parent.audit_date_from.setCalendarPopup(True)
    parent.audit_date_from.setDate(QDate.currentDate().addMonths(-3))
    parent.audit_date_from.setStyleSheet("""
        QDateEdit {
            background-color: #334155;
            color: #f1f5f9;
            border: 1px solid #475569;
            border-radius: 4px;
            padding: 4px 8px;
        }
    """)
    filters_row.addWidget(parent.audit_date_from)

    date_to_label = QLabel("To:")
    date_to_label.setStyleSheet("color: #e2e8f0;")
    filters_row.addWidget(date_to_label)

    parent.audit_date_to = QDateEdit()
    parent.audit_date_to.setDisplayFormat("MM/dd/yyyy")
    parent.audit_date_to.setCalendarPopup(True)
    parent.audit_date_to.setDate(QDate.currentDate())
    parent.audit_date_to.setStyleSheet("""
        QDateEdit {
            background-color: #334155;
            color: #f1f5f9;
            border: 1px solid #475569;
            border-radius: 4px;
            padding: 4px 8px;
        }
    """)
    filters_row.addWidget(parent.audit_date_to)

    # Days threshold (for aging reports)
    days_label = QLabel("Days Threshold:")
    days_label.setStyleSheet("color: #e2e8f0;")
    filters_row.addWidget(days_label)

    parent.audit_days_threshold = QSpinBox()
    parent.audit_days_threshold.setRange(1, 365)
    parent.audit_days_threshold.setValue(30)
    parent.audit_days_threshold.setStyleSheet("""
        QSpinBox {
            background-color: #334155;
            color: #f1f5f9;
            border: 1px solid #475569;
            border-radius: 4px;
            padding: 4px 8px;
            min-width: 60px;
        }
    """)
    filters_row.addWidget(parent.audit_days_threshold)

    # Status filter
    status_label = QLabel("Status Filter:")
    status_label.setStyleSheet("color: #e2e8f0;")
    filters_row.addWidget(status_label)

    parent.audit_status_filter = QComboBox()
    parent.audit_status_filter.addItems([
        "All Statuses",
        "Pending", "On Hold", "Processing", "Awaiting Auth",
        "Approved", "Shipped", "Delivered", "Picked Up",
        "Billed", "Unbilled", "Paid", "Cancelled"
    ])
    parent.audit_status_filter.setStyleSheet("""
        QComboBox {
            background-color: #334155;
            color: #f1f5f9;
            border: 1px solid #475569;
            border-radius: 4px;
            padding: 4px 8px;
            min-width: 120px;
        }
    """)
    filters_row.addWidget(parent.audit_status_filter)

    filters_row.addStretch(1)
    report_layout.addLayout(filters_row)

    # Action buttons row
    btn_row = QHBoxLayout()
    btn_row.setSpacing(10)

    run_btn = QPushButton("▶️ Run Audit Report")
    run_btn.setStyleSheet("""
        QPushButton {
            background-color: #2563eb;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 20px;
            font-weight: 600;
            font-size: 10pt;
        }
        QPushButton:hover {
            background-color: #1d4ed8;
        }
    """)
    run_btn.clicked.connect(lambda: run_audit_report(parent))
    btn_row.addWidget(run_btn)

    export_btn = QPushButton("📤 Export to CSV")
    export_btn.setStyleSheet("""
        QPushButton {
            background-color: #059669;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 16px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #047857;
        }
    """)
    export_btn.clicked.connect(lambda: export_audit_results(parent))
    btn_row.addWidget(export_btn)

    clear_btn = QPushButton("🗑️ Clear Results")
    clear_btn.setStyleSheet("""
        QPushButton {
            background-color: #6b7280;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 16px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #4b5563;
        }
    """)
    clear_btn.clicked.connect(lambda: clear_audit_results(parent))
    btn_row.addWidget(clear_btn)

    # Sync order docs to patient profiles button
    sync_docs_btn = QPushButton("🔄 Sync Order Docs to Patients")
    sync_docs_btn.setToolTip("Link all order documents to their corresponding patient profiles")
    sync_docs_btn.setStyleSheet("""
        QPushButton {
            background-color: #7c3aed;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 16px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #6d28d9;
        }
    """)
    sync_docs_btn.clicked.connect(lambda: sync_order_docs_to_patients(parent))
    btn_row.addWidget(sync_docs_btn)

    btn_row.addStretch(1)

    # Summary stats label
    parent.audit_summary_label = QLabel("Run a report to see results")
    parent.audit_summary_label.setStyleSheet("color: #94a3b8; font-style: italic;")
    btn_row.addWidget(parent.audit_summary_label)

    report_layout.addLayout(btn_row)
    layout.addWidget(report_group)

    # ===== Results Table =====
    results_group = QGroupBox("Audit Results")
    results_group.setStyleSheet("""
        QGroupBox {
            font-weight: 600;
            border: 1px solid #334155;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 10px;
            background-color: #1e293b;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 8px;
            color: #e2e8f0;
        }
    """)
    results_layout = QVBoxLayout(results_group)

    parent.audit_results_table = QTableWidget()
    parent.audit_results_table.setColumnCount(7)
    parent.audit_results_table.setHorizontalHeaderLabels([
        "Order #", "Patient Name", "Order Date", "Status", "Issue", "Severity", "Actions"
    ])
    parent.audit_results_table.setAlternatingRowColors(True)
    parent.audit_results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    parent.audit_results_table.setStyleSheet("""
        QTableWidget {
            background-color: #1e293b;
            color: #e2e8f0;
            gridline-color: #334155;
            border: 1px solid #334155;
            border-radius: 4px;
        }
        QTableWidget::item {
            color: #e2e8f0;
            padding: 6px;
        }
        QTableWidget::item:selected {
            background-color: #2563eb;
            color: #ffffff;
        }
        QTableWidget::item:alternate {
            background-color: #0f172a;
        }
        QHeaderView::section {
            background-color: #334155;
            color: #f1f5f9;
            padding: 8px;
            border: none;
            font-weight: 600;
        }
    """)

    # Set column widths
    header = parent.audit_results_table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Order #
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Patient Name
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Order Date
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Status
    header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # Issue
    header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # Severity
    header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)  # Actions

    # Double-click to open order
    parent.audit_results_table.doubleClicked.connect(
        lambda idx: open_order_from_audit(parent, idx.row())
    )

    results_layout.addWidget(parent.audit_results_table)
    layout.addWidget(results_group, stretch=1)

    return tab


def run_audit_report(parent) -> None:
    """Execute the selected audit report."""
    report_type = parent.audit_report_combo.currentText()
    
    # Get filter values
    date_from = parent.audit_date_from.date().toString("yyyy-MM-dd")
    date_to = parent.audit_date_to.date().toString("yyyy-MM-dd")
    days_threshold = parent.audit_days_threshold.value()
    status_filter = parent.audit_status_filter.currentText()
    
    folder_path = getattr(parent, "folder_path", None)
    
    try:
        from dmelogic.db.base import get_connection
        
        conn = get_connection("orders.db", folder_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        results: List[AuditResult] = []
        
        # Build base query conditions
        status_condition = ""
        if status_filter != "All Statuses":
            status_condition = f" AND order_status = '{status_filter}'"
        
        if "Missing Document Attachments" in report_type:
            results = audit_missing_documents(cur, date_from, date_to, status_condition)
        elif "Missing Insurance Information" in report_type:
            results = audit_missing_insurance(cur, date_from, date_to, status_condition)
        elif "Missing Prescriber NPI" in report_type:
            results = audit_missing_npi(cur, date_from, date_to, status_condition)
        elif "Missing ICD-10 Codes" in report_type:
            results = audit_missing_icd_codes(cur, date_from, date_to, status_condition)
        elif "Stuck in Status" in report_type:
            results = audit_stuck_orders(cur, days_threshold, status_condition)
        elif "Without Patient Link" in report_type:
            results = audit_orphaned_orders(cur, date_from, date_to, status_condition)
        elif "Unbilled Orders" in report_type:
            results = audit_unbilled_orders(cur, days_threshold)
        elif "Missing Delivery Date" in report_type:
            results = audit_missing_delivery_date(cur, date_from, date_to, status_condition)
        elif "Missing Notes" in report_type:
            results = audit_missing_notes(cur, date_from, date_to, status_condition)
        elif "Orphaned Order Items" in report_type:
            results = audit_orphaned_items(cur)
        elif "Data Quality Summary" in report_type:
            results = audit_data_quality_summary(cur, date_from, date_to)
        
        conn.close()
        
        # Populate table
        populate_audit_table(parent, results)
        
        # Update summary
        critical_count = sum(1 for r in results if r.severity == "Critical")
        warning_count = sum(1 for r in results if r.severity == "Warning")
        info_count = sum(1 for r in results if r.severity == "Info")
        
        parent.audit_summary_label.setText(
            f"Found {len(results)} issues: "
            f"🔴 {critical_count} Critical | "
            f"🟡 {warning_count} Warning | "
            f"🔵 {info_count} Info"
        )
        parent.audit_summary_label.setStyleSheet("color: #e2e8f0; font-weight: 500;")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        QMessageBox.warning(parent, "Audit Error", f"Failed to run audit report:\n{e}")


# SQL helper to convert MM/DD/YYYY to YYYY-MM-DD for proper date comparison
DATE_CONVERT_SQL = "substr(order_date, 7, 4) || '-' || substr(order_date, 1, 2) || '-' || substr(order_date, 4, 2)"


def audit_missing_documents(cur, date_from: str, date_to: str, status_condition: str) -> List[AuditResult]:
    """Find orders without attached documents."""
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status, attached_rx_files
        FROM orders
        WHERE ({DATE_CONVERT_SQL}) BETWEEN ? AND ?
        AND order_status NOT IN ('Cancelled')
        {status_condition}
        AND (attached_rx_files IS NULL OR attached_rx_files = '')
        ORDER BY order_date DESC
    """
    cur.execute(query, (date_from, date_to))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue="No documents attached (RX/CMN missing)",
            severity="Critical"
        ))
    return results


def audit_missing_insurance(cur, date_from: str, date_to: str, status_condition: str) -> List[AuditResult]:
    """Find orders without insurance information."""
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status, 
               primary_insurance, billing_type
        FROM orders
        WHERE ({DATE_CONVERT_SQL}) BETWEEN ? AND ?
        AND order_status NOT IN ('Cancelled')
        {status_condition}
        AND billing_type = 'Insurance'
        AND (primary_insurance IS NULL OR primary_insurance = '')
        ORDER BY order_date DESC
    """
    cur.execute(query, (date_from, date_to))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue="Insurance billing selected but no insurance on file",
            severity="Critical"
        ))
    return results


def audit_missing_npi(cur, date_from: str, date_to: str, status_condition: str) -> List[AuditResult]:
    """Find orders without prescriber NPI."""
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status, 
               prescriber_name, prescriber_npi
        FROM orders
        WHERE ({DATE_CONVERT_SQL}) BETWEEN ? AND ?
        AND order_status NOT IN ('Cancelled')
        {status_condition}
        AND (prescriber_npi IS NULL OR prescriber_npi = '' OR LENGTH(prescriber_npi) != 10)
        ORDER BY order_date DESC
    """
    cur.execute(query, (date_from, date_to))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        prescriber = row['prescriber_name'] or "Unknown"
        npi = row['prescriber_npi'] or "Missing"
        
        if npi == "Missing" or npi == "":
            issue = f"Prescriber NPI missing (Prescriber: {prescriber})"
            severity = "Critical"
        else:
            issue = f"Invalid NPI format: '{npi}' (Prescriber: {prescriber})"
            severity = "Warning"
        
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue=issue,
            severity=severity
        ))
    return results


def audit_missing_icd_codes(cur, date_from: str, date_to: str, status_condition: str) -> List[AuditResult]:
    """Find orders without ICD-10 diagnosis codes."""
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status,
               icd_code_1, icd_code_2, icd_code_3, icd_code_4, icd_code_5
        FROM orders
        WHERE ({DATE_CONVERT_SQL}) BETWEEN ? AND ?
        AND order_status NOT IN ('Cancelled')
        {status_condition}
        AND (icd_code_1 IS NULL OR icd_code_1 = '')
        ORDER BY order_date DESC
    """
    cur.execute(query, (date_from, date_to))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue="No ICD-10 diagnosis codes entered",
            severity="Critical"
        ))
    return results


def audit_stuck_orders(cur, days_threshold: int, status_condition: str) -> List[AuditResult]:
    """Find orders that have been in certain statuses too long."""
    # Calculate threshold date
    from datetime import datetime, timedelta
    threshold_date = (datetime.now() - timedelta(days=days_threshold)).strftime("%Y-%m-%d")
    
    # Focus on actionable statuses
    stuck_statuses = "'Pending', 'On Hold', 'Processing', 'Awaiting Auth'"
    
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status,
               julianday('now') - julianday(order_date) as days_old
        FROM orders
        WHERE order_date < ?
        AND order_status IN ({stuck_statuses})
        {status_condition}
        ORDER BY order_date ASC
    """
    cur.execute(query, (threshold_date,))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        days_old = int(row['days_old']) if row['days_old'] else 0
        
        if days_old > 60:
            severity = "Critical"
        elif days_old > 30:
            severity = "Warning"
        else:
            severity = "Info"
        
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue=f"Order stuck in '{row['order_status']}' for {days_old} days",
            severity=severity
        ))
    return results


def audit_orphaned_orders(cur, date_from: str, date_to: str, status_condition: str) -> List[AuditResult]:
    """Find orders without a linked patient ID."""
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status, patient_id
        FROM orders
        WHERE ({DATE_CONVERT_SQL}) BETWEEN ? AND ?
        AND order_status NOT IN ('Cancelled')
        {status_condition}
        AND (patient_id IS NULL OR patient_id = 0 OR patient_id = '')
        ORDER BY order_date DESC
    """
    cur.execute(query, (date_from, date_to))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue="Order not linked to a patient record",
            severity="Warning"
        ))
    return results


def audit_unbilled_orders(cur, days_threshold: int) -> List[AuditResult]:
    """Find orders delivered but not billed within threshold."""
    from datetime import datetime, timedelta
    threshold_date = (datetime.now() - timedelta(days=days_threshold)).strftime("%Y-%m-%d")
    
    query = """
        SELECT id, patient_last_name, patient_first_name, order_date, order_status, delivery_date,
               julianday('now') - julianday(COALESCE(delivery_date, order_date)) as days_since
        FROM orders
        WHERE order_status IN ('Delivered', 'Shipped', 'Picked Up', 'Unbilled')
        AND COALESCE(delivery_date, order_date) < ?
        ORDER BY delivery_date ASC
    """
    cur.execute(query, (threshold_date,))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        days_since = int(row['days_since']) if row['days_since'] else 0
        
        if days_since > 60:
            severity = "Critical"
        elif days_since > 30:
            severity = "Warning"
        else:
            severity = "Info"
        
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue=f"Delivered {days_since} days ago, not yet billed",
            severity=severity
        ))
    return results


def audit_missing_delivery_date(cur, date_from: str, date_to: str, status_condition: str) -> List[AuditResult]:
    """Find delivered orders without delivery date."""
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status, delivery_date
        FROM orders
        WHERE ({DATE_CONVERT_SQL}) BETWEEN ? AND ?
        AND order_status IN ('Delivered', 'Shipped', 'Picked Up', 'Billed', 'Paid')
        {status_condition}
        AND (delivery_date IS NULL OR delivery_date = '')
        ORDER BY order_date DESC
    """
    cur.execute(query, (date_from, date_to))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue=f"Status is '{row['order_status']}' but no delivery date recorded",
            severity="Warning"
        ))
    return results


def audit_missing_notes(cur, date_from: str, date_to: str, status_condition: str) -> List[AuditResult]:
    """Find orders without any notes or instructions."""
    query = f"""
        SELECT id, patient_last_name, patient_first_name, order_date, order_status,
               notes, doctor_directions
        FROM orders
        WHERE ({DATE_CONVERT_SQL}) BETWEEN ? AND ?
        AND order_status NOT IN ('Cancelled')
        {status_condition}
        AND (notes IS NULL OR notes = '')
        AND (doctor_directions IS NULL OR doctor_directions = '')
        ORDER BY order_date DESC
    """
    cur.execute(query, (date_from, date_to))
    
    results = []
    for row in cur.fetchall():
        patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")
        results.append(AuditResult(
            order_id=row['id'],
            order_number=f"ORD-{row['id']:03d}",
            patient_name=patient_name or "Unknown",
            order_date=row['order_date'] or "",
            status=row['order_status'] or "",
            issue="No notes or doctor directions recorded",
            severity="Info"
        ))
    return results


def audit_orphaned_items(cur) -> List[AuditResult]:
    """Find order items without a valid parent order."""
    query = """
        SELECT oi.id, oi.order_id, oi.hcpcs, oi.description
        FROM order_items oi
        LEFT JOIN orders o ON oi.order_id = o.id
        WHERE o.id IS NULL
    """
    cur.execute(query)
    
    results = []
    for row in cur.fetchall():
        results.append(AuditResult(
            order_id=row['order_id'] or 0,
            order_number=f"ORD-{row['order_id'] or 0:03d}",
            patient_name="N/A",
            order_date="N/A",
            status="N/A",
            issue=f"Orphaned item: {row['hcpcs']} - {row['description'][:50]}...",
            severity="Critical"
        ))
    return results


def audit_data_quality_summary(cur, date_from: str, date_to: str) -> List[AuditResult]:
    """Generate a comprehensive data quality summary."""
    results = []
    
    # Run all audits and aggregate
    all_audits = [
        ("Missing Documents", audit_missing_documents(cur, date_from, date_to, "")),
        ("Missing Insurance", audit_missing_insurance(cur, date_from, date_to, "")),
        ("Missing NPI", audit_missing_npi(cur, date_from, date_to, "")),
        ("Missing ICD-10", audit_missing_icd_codes(cur, date_from, date_to, "")),
        ("Orders Without Patient", audit_orphaned_orders(cur, date_from, date_to, "")),
        ("Missing Delivery Date", audit_missing_delivery_date(cur, date_from, date_to, "")),
    ]
    
    for audit_name, audit_results in all_audits:
        count = len(audit_results)
        if count > 0:
            severity = "Critical" if count > 20 else ("Warning" if count > 5 else "Info")
            results.append(AuditResult(
                order_id=0,
                order_number="SUMMARY",
                patient_name=f"{count} orders",
                order_date=f"{date_from} to {date_to}",
                status="N/A",
                issue=f"{audit_name}: {count} issues found",
                severity=severity
            ))
    
    return results


def populate_audit_table(parent, results: List[AuditResult]) -> None:
    """Populate the audit results table."""
    table = parent.audit_results_table
    table.setRowCount(0)
    table.setRowCount(len(results))
    
    severity_colors = {
        "Critical": "#ef4444",  # Red
        "Warning": "#f59e0b",   # Yellow/Orange
        "Info": "#3b82f6"       # Blue
    }
    base_text_color = QColor("#e2e8f0")
    
    for row_idx, result in enumerate(results):
        # Order #
        order_item = QTableWidgetItem(result.order_number)
        order_item.setData(Qt.ItemDataRole.UserRole, result.order_id)
        order_item.setForeground(base_text_color)
        table.setItem(row_idx, 0, order_item)
        
        # Patient Name
        patient_item = QTableWidgetItem(result.patient_name)
        patient_item.setForeground(base_text_color)
        table.setItem(row_idx, 1, patient_item)
        
        # Order Date
        order_date_item = QTableWidgetItem(result.order_date)
        order_date_item.setForeground(base_text_color)
        table.setItem(row_idx, 2, order_date_item)
        
        # Status
        status_item = QTableWidgetItem(result.status)
        status_item.setForeground(base_text_color)
        table.setItem(row_idx, 3, status_item)
        
        # Issue
        issue_item = QTableWidgetItem(result.issue)
        issue_item.setForeground(base_text_color)
        table.setItem(row_idx, 4, issue_item)
        
        # Severity
        severity_item = QTableWidgetItem(result.severity)
        severity_item.setForeground(QColor(severity_colors.get(result.severity, "#ffffff")))
        severity_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        table.setItem(row_idx, 5, severity_item)
        
        # Actions button
        if result.order_id > 0:
            btn = QPushButton("Open")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2563eb;
                    color: white;
                    border: none;
                    border-radius: 3px;
                    padding: 3px 10px;
                }
                QPushButton:hover {
                    background-color: #1d4ed8;
                }
            """)
            btn.clicked.connect(lambda checked, oid=result.order_id: open_order_by_id(parent, oid))
            table.setCellWidget(row_idx, 6, btn)


def open_order_from_audit(parent, row: int) -> None:
    """Open order from double-clicked row."""
    item = parent.audit_results_table.item(row, 0)
    if item:
        order_id = item.data(Qt.ItemDataRole.UserRole)
        if order_id and order_id > 0:
            open_order_by_id(parent, order_id)


def open_order_by_id(parent, order_id: int) -> None:
    """Open the order editor for the given order ID."""
    try:
        from dmelogic.ui.order_editor import OrderEditorDialog
        
        folder_path = getattr(parent, "folder_path", None)
        
        editor = OrderEditorDialog(
            order_id=order_id,
            folder_path=folder_path,
            parent=parent
        )
        editor.exec()
        
        # Refresh the audit if still on audit tab
        if hasattr(parent, 'audit_results_table'):
            # Re-run the current report to refresh
            pass  # User can click Run again
    except Exception as e:
        import traceback
        traceback.print_exc()
        QMessageBox.warning(parent, "Error", f"Could not open order: {e}")


def export_audit_results(parent) -> None:
    """Export audit results to CSV file."""
    table = parent.audit_results_table
    if table.rowCount() == 0:
        QMessageBox.information(parent, "Export", "No results to export. Run a report first.")
        return
    
    file_path, _ = QFileDialog.getSaveFileName(
        parent,
        "Export Audit Results",
        f"audit_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        "CSV Files (*.csv)"
    )
    
    if not file_path:
        return
    
    try:
        import csv
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Header
            headers = []
            for col in range(table.columnCount() - 1):  # Skip Actions column
                headers.append(table.horizontalHeaderItem(col).text())
            writer.writerow(headers)
            
            # Data
            for row in range(table.rowCount()):
                row_data = []
                for col in range(table.columnCount() - 1):  # Skip Actions column
                    item = table.item(row, col)
                    row_data.append(item.text() if item else "")
                writer.writerow(row_data)
        
        QMessageBox.information(parent, "Export Complete", f"Results exported to:\n{file_path}")
    except Exception as e:
        QMessageBox.warning(parent, "Export Error", f"Failed to export: {e}")


def clear_audit_results(parent) -> None:
    """Clear the audit results table."""
    parent.audit_results_table.setRowCount(0)
    parent.audit_summary_label.setText("Run a report to see results")
    parent.audit_summary_label.setStyleSheet("color: #94a3b8; font-style: italic;")


def sync_order_docs_to_patients(parent) -> None:
    """
    Sync all order documents to their corresponding patient profiles.
    Also syncs documents across related orders (parent + all refills).
    
    This is a one-time bulk operation to backfill patient_documents
    from existing order attachments.
    """
    try:
        from dmelogic.db.base import get_connection, resolve_db_path
        
        folder_path = getattr(parent, 'folder_path', None)
        
        # Get all orders with attached documents
        conn = get_connection("orders.db", folder_path)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, patient_id, patient_last_name, patient_first_name, patient_dob, attached_rx_files, parent_order_id
            FROM orders
            WHERE attached_rx_files IS NOT NULL AND attached_rx_files != ''
        """)
        orders_with_docs = cur.fetchall()
        
        if not orders_with_docs:
            conn.close()
            QMessageBox.information(parent, "Sync Complete", "No orders with documents found.")
            return
        
        # First pass: Sync documents across related orders (parent + refills)
        orders_synced = 0
        for order_id, patient_id, last_name, first_name, dob, attached_files, parent_order_id in orders_with_docs:
            if not attached_files:
                continue
            
            # Get root order ID
            root_id = parent_order_id or order_id
            
            # Get all related orders
            cur.execute(
                "SELECT id FROM orders WHERE id = ? OR parent_order_id = ?",
                (root_id, root_id)
            )
            related_ids = [row[0] for row in cur.fetchall()]
            
            # Parse attached files
            files = []
            for sep in [';', '\n']:
                if sep in attached_files:
                    files = [f.strip() for f in attached_files.split(sep) if f.strip()]
                    break
            if not files:
                files = [attached_files.strip()] if attached_files.strip() else []
            
            # Sync files to all related orders
            for related_id in related_ids:
                if related_id == order_id:
                    continue  # Skip self
                
                cur.execute("SELECT attached_rx_files FROM orders WHERE id = ?", (related_id,))
                row = cur.fetchone()
                current_files = row[0] if row and row[0] else ""
                
                existing = [f.strip() for f in current_files.replace('\n', ';').split(';') if f.strip()]
                
                new_to_add = [f for f in files if f not in existing]
                if new_to_add:
                    if current_files:
                        updated = current_files + ";" + ";".join(new_to_add)
                    else:
                        updated = ";".join(new_to_add)
                    cur.execute("UPDATE orders SET attached_rx_files = ? WHERE id = ?", (updated, related_id))
                    orders_synced += 1
        
        conn.commit()
        
        # Now get updated list for patient sync
        cur.execute("""
            SELECT id, patient_id, patient_last_name, patient_first_name, patient_dob, attached_rx_files
            FROM orders
            WHERE attached_rx_files IS NOT NULL AND attached_rx_files != ''
        """)
        orders = cur.fetchall()
        conn.close()
        
        # Prepare patient database
        patient_db_path = resolve_db_path("patients.db", folder_path=folder_path)
        patient_conn = sqlite3.connect(patient_db_path)
        patient_cur = patient_conn.cursor()
        
        # Ensure table exists
        patient_cur.execute("""
            CREATE TABLE IF NOT EXISTS patient_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                description TEXT,
                original_name TEXT,
                stored_path TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        
        synced_count = 0
        skipped_count = 0
        no_patient_count = 0
        
        for order_id, patient_id, last_name, first_name, dob, attached_files in orders:
            if not attached_files:
                continue
            
            # Try to get patient_id if not set
            if not patient_id:
                patient_cur.execute(
                    "SELECT id FROM patients WHERE last_name = ? AND first_name = ?",
                    (last_name or "", first_name or "")
                )
                row = patient_cur.fetchone()
                if row:
                    patient_id = row[0]
            
            if not patient_id:
                no_patient_count += 1
                continue
            
            # Parse attached files (semicolon or newline separated)
            import os
            files = []
            for sep in [';', '\n']:
                if sep in attached_files:
                    files = [f.strip() for f in attached_files.split(sep) if f.strip()]
                    break
            if not files:
                files = [attached_files.strip()] if attached_files.strip() else []
            
            order_num = f"ORD-{order_id:03d}"
            
            for file_path in files:
                if not file_path:
                    continue
                
                # Check if already linked
                patient_cur.execute(
                    "SELECT id FROM patient_documents WHERE patient_id = ? AND stored_path = ?",
                    (patient_id, file_path)
                )
                if patient_cur.fetchone():
                    skipped_count += 1
                    continue
                
                # Insert new link
                original_name = os.path.basename(file_path)
                description = f"From {order_num}"
                
                patient_cur.execute(
                    "INSERT INTO patient_documents (patient_id, description, original_name, stored_path) VALUES (?, ?, ?, ?)",
                    (patient_id, description, original_name, file_path)
                )
                synced_count += 1
        
        patient_conn.commit()
        patient_conn.close()
        
        QMessageBox.information(
            parent,
            "Sync Complete",
            f"Documents synced successfully:\n\n"
            f"📋 Orders cross-linked: {orders_synced}\n"
            f"✅ Patient documents linked: {synced_count}\n"
            f"⏭️ Already linked (skipped): {skipped_count}\n"
            f"⚠️ Orders without patient match: {no_patient_count}"
        )
        
    except Exception as e:
        QMessageBox.warning(parent, "Sync Error", f"Failed to sync documents: {e}")
