"""
Report Viewer - Universal widget for displaying reports

Provides consistent UI for all reports with table display, filters, and exports.
"""

from __future__ import annotations
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
from html import escape

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QComboBox, QDateEdit, QLineEdit, QSplitter, QMessageBox
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush, QTextDocument
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog

from dmelogic.reports.base.export_manager import ExportManager


class ReportViewer(QWidget):
    """
    Universal report display widget.

    Features:
    - Table display with sorting
    - Filter controls
    - Export buttons
    - Chart area (optional)
    - Summary statistics
    - Refresh capability
    """

    # Signals
    refresh_requested = pyqtSignal(dict)  # Emits filter parameters
    row_double_clicked = pyqtSignal(dict)  # Emits row data

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        show_chart_area: bool = False,
        show_filters: bool = True
    ):
        """
        Initialize report viewer.

        Args:
            parent: Parent widget
            show_chart_area: Include chart display area
            show_filters: Include filter controls
        """
        super().__init__(parent)

        self.report_data = None
        self.export_manager = ExportManager(self)
        self.show_chart_area = show_chart_area
        self.show_filters = show_filters

        self._setup_ui()

    def _setup_ui(self):
        """Build the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Header
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #f5f5f5;
                border-bottom: 2px solid #1976d2;
                padding: 8px;
            }
        """)
        header_layout = QHBoxLayout(header_frame)

        self.title_label = QLabel("Report Title")
        title_font = QFont("Segoe UI", 13)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #1976d2;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        # Export buttons in header
        self.csv_btn = QPushButton("\U0001f4be CSV")
        self.csv_btn.setStyleSheet(self._button_style("#388e3c"))
        self.csv_btn.clicked.connect(self._export_csv)
        header_layout.addWidget(self.csv_btn)

        self.excel_btn = QPushButton("\U0001f4ca Excel")
        self.excel_btn.setStyleSheet(self._button_style("#1e7e34"))
        self.excel_btn.clicked.connect(self._export_excel)
        header_layout.addWidget(self.excel_btn)

        self.pdf_btn = QPushButton("\U0001f4c4 PDF")
        self.pdf_btn.setStyleSheet(self._button_style("#d32f2f"))
        self.pdf_btn.clicked.connect(self._export_pdf)
        header_layout.addWidget(self.pdf_btn)

        self.print_btn = QPushButton("\U0001f5a8 Print")
        self.print_btn.setStyleSheet(self._button_style("#455a64"))
        self.print_btn.clicked.connect(self._print_report)
        header_layout.addWidget(self.print_btn)

        self.refresh_btn = QPushButton("\U0001f50d Generate")
        self.refresh_btn.setStyleSheet(self._button_style("#1976d2"))
        self.refresh_btn.setToolTip("Generate report using current filters")
        self.refresh_btn.clicked.connect(self._on_refresh)
        header_layout.addWidget(self.refresh_btn)

        layout.addWidget(header_frame)

        # Filter area (optional)
        if self.show_filters:
            self.filter_frame = QFrame()
            self.filter_frame.setStyleSheet("""
                QFrame {
                    background-color: white;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    padding: 8px;
                }
            """)
            self.filter_layout = QHBoxLayout(self.filter_frame)
            layout.addWidget(self.filter_frame)
            self.filter_widgets = {}

        # Main content area
        if self.show_chart_area:
            splitter = QSplitter(Qt.Orientation.Vertical)

            # Chart area
            self.chart_widget = QWidget()
            self.chart_layout = QVBoxLayout(self.chart_widget)
            self.chart_layout.setContentsMargins(0, 0, 0, 0)
            splitter.addWidget(self.chart_widget)

            # Table area
            table_widget = QWidget()
            table_layout = QVBoxLayout(table_widget)
            table_layout.setContentsMargins(0, 0, 0, 0)
            self._create_table(table_layout)
            splitter.addWidget(table_widget)

            splitter.setSizes([300, 400])
            layout.addWidget(splitter, 1)
        else:
            self._create_table(layout)

        # Summary/status bar
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("""
            QLabel {
                background-color: #e8f4f8;
                padding: 6px;
                border-radius: 3px;
                font-size: 10pt;
            }
        """)
        layout.addWidget(self.summary_label)

    def _create_table(self, parent_layout):
        """Create the data table."""
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                gridline-color: #e0e0e0;
                background-color: white;
                alternate-background-color: #f8f9fa;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #1976d2;
                color: white;
                padding: 6px;
                font-weight: 600;
                border: none;
            }
        """)

        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.doubleClicked.connect(self._on_row_double_click)

        parent_layout.addWidget(self.table, 1)

    def _button_style(self, color: str) -> str:
        """Generate button stylesheet."""
        return f"""
            QPushButton {{
                background-color: {color};
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 3px;
                font-weight: 600;
                min-width: 80px;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:pressed {{
                background-color: #000;
            }}
            QPushButton:disabled {{
                background-color: #ccc;
                color: #888;
            }}
        """

    # ========================================================================
    # Data Loading
    # ========================================================================

    def load_report(self, report_data):
        """
        Load report data into viewer.

        Args:
            report_data: ReportData object from ReportEngine
        """
        self.report_data = report_data

        # Update title
        self.title_label.setText(report_data.title)

        # Clear and setup table
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(report_data.columns))

        # Set headers
        headers = [col.display_name for col in report_data.columns]
        self.table.setHorizontalHeaderLabels(headers)

        # Populate rows
        for row in report_data.rows:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)

            for col_idx, col in enumerate(report_data.columns):
                value = row.get(col.name)
                formatted = col.format_value(value)

                item = QTableWidgetItem(formatted)

                # Set alignment
                if col.alignment == 'right':
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                elif col.alignment == 'center':
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                # Apply row styling
                if row.style:
                    bg_color = row.style.get('background_color')
                    if bg_color:
                        item.setBackground(QBrush(QColor(bg_color)))

                    text_color = row.style.get('text_color')
                    if text_color:
                        item.setForeground(QBrush(QColor(text_color)))

                self.table.setItem(row_idx, col_idx, item)

        # Auto-resize columns
        header = self.table.horizontalHeader()
        for i in range(len(report_data.columns)):
            if report_data.columns[i].width:
                header.resizeSection(i, report_data.columns[i].width)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        # Update summary
        self._update_summary()

    def _update_summary(self):
        """Update summary label with report statistics."""
        if not self.report_data:
            self.summary_label.setText("")
            return

        summary_parts = []

        # Row count
        summary_parts.append(f"Rows: {len(self.report_data.rows)}")

        # Additional summary data (only show if matches row count to avoid stale stats)
        if self.report_data.summary:
            for key, value in self.report_data.summary.items():
                if key in ('total_rows', 'row_count', 'generated_at'):
                    continue
                display_key = key.replace('_', ' ').title()
                if isinstance(value, float):
                    if abs(value) >= 1:
                        summary_parts.append(f"{display_key}: ${value:,.2f}")
                    else:
                        summary_parts.append(f"{display_key}: {value:.1%}")
                elif isinstance(value, dict):
                    # Skip dict summaries like status_counts in footer
                    continue
                else:
                    summary_parts.append(f"{display_key}: {value}")

        # Generated time
        if self.report_data.metadata.get('generated_at'):
            gen_time = self.report_data.metadata['generated_at']
            if isinstance(gen_time, datetime):
                gen_str = gen_time.strftime('%m/%d/%Y %I:%M %p')
            else:
                gen_str = str(gen_time)
            summary_parts.append(f"Generated: {gen_str}")

        self.summary_label.setText(" | ".join(summary_parts))

    # ========================================================================
    # Filters
    # ========================================================================

    def add_filter_combo(
        self,
        label: str,
        options: List[str],
        default_index: int = 0
    ) -> QComboBox:
        """
        Add a combo box filter.

        Args:
            label: Filter label
            options: List of options
            default_index: Default selected index

        Returns:
            QComboBox widget
        """
        if not self.show_filters:
            return None

        filter_label = QLabel(f"{label}:")
        filter_label.setStyleSheet("font-weight: 600;")
        self.filter_layout.addWidget(filter_label)

        combo = QComboBox()
        combo.addItems(options)
        combo.setCurrentIndex(default_index)
        combo.currentIndexChanged.connect(self._on_filter_changed)
        self.filter_layout.addWidget(combo)

        self.filter_widgets[label] = combo
        return combo

    def add_filter_date_range(self, start_label: str = "From", end_label: str = "To"):
        """Add date range filter."""
        if not self.show_filters:
            return None, None

        # Start date
        start_lbl = QLabel(f"{start_label}:")
        start_lbl.setStyleSheet("font-weight: 600;")
        self.filter_layout.addWidget(start_lbl)

        start_date = QDateEdit()
        start_date.setCalendarPopup(True)
        start_date.setDisplayFormat("MM/dd/yyyy")
        start_date.setDate(QDate.currentDate().addDays(-90))
        start_date.dateChanged.connect(self._on_filter_changed)
        self.filter_layout.addWidget(start_date)

        # End date
        end_lbl = QLabel(f"{end_label}:")
        end_lbl.setStyleSheet("font-weight: 600;")
        self.filter_layout.addWidget(end_lbl)

        end_date = QDateEdit()
        end_date.setCalendarPopup(True)
        end_date.setDisplayFormat("MM/dd/yyyy")
        end_date.setDate(QDate.currentDate())
        end_date.dateChanged.connect(self._on_filter_changed)
        self.filter_layout.addWidget(end_date)

        self.filter_widgets['start_date'] = start_date
        self.filter_widgets['end_date'] = end_date

        return start_date, end_date

    def add_filter_search(self, placeholder: str = "Search...") -> QLineEdit:
        """Add search box filter."""
        if not self.show_filters:
            return None

        search_box = QLineEdit()
        search_box.setPlaceholderText(placeholder)
        search_box.setMinimumWidth(200)
        search_box.textChanged.connect(self._on_search_changed)
        self.filter_layout.addWidget(search_box)

        self.filter_widgets['search'] = search_box
        return search_box

    def get_filter_values(self) -> Dict[str, Any]:
        """Get current filter values."""
        values = {}
        for name, widget in self.filter_widgets.items():
            if isinstance(widget, QComboBox):
                values[name] = widget.currentText()
            elif isinstance(widget, QDateEdit):
                values[name] = widget.date().toPyDate()
            elif isinstance(widget, QLineEdit):
                values[name] = widget.text()
        return values

    def _on_filter_changed(self):
        """Handle filter change."""
        # Emit refresh signal with current filter values
        self.refresh_requested.emit(self.get_filter_values())

    def _on_search_changed(self, text: str):
        """Handle search box change."""
        # Filter table rows based on search text
        for row in range(self.table.rowCount()):
            match = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and text.lower() in item.text().lower():
                    match = True
                    break
            self.table.setRowHidden(row, not match)

    # ========================================================================
    # Chart Area
    # ========================================================================

    def set_chart(self, canvas):
        """
        Add chart to chart area.

        Args:
            canvas: FigureCanvas from ChartBuilder
        """
        if not self.show_chart_area:
            return

        # Clear existing widgets
        for i in reversed(range(self.chart_layout.count())):
            self.chart_layout.itemAt(i).widget().setParent(None)

        # Add new chart
        self.chart_layout.addWidget(canvas)

    # ========================================================================
    # Export Functions
    # ========================================================================

    def _export_csv(self):
        """Export report to CSV."""
        if not self.report_data:
            QMessageBox.warning(self, "No Data", "No report data to export.")
            return

        self.export_manager.export_csv(self.report_data)

    def _export_excel(self):
        """Export report to Excel."""
        if not self.report_data:
            QMessageBox.warning(self, "No Data", "No report data to export.")
            return

        self.export_manager.export_excel(self.report_data)

    def _export_pdf(self):
        """Export report to PDF."""
        if not self.report_data:
            QMessageBox.warning(self, "No Data", "No report data to export.")
            return

        self.export_manager.export_pdf(self.report_data)

    def _print_report(self):
        """Print current report using system printer dialog."""
        if not self.report_data:
            QMessageBox.warning(self, "No Data", "No report data to print.")
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Print Report")
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return

        doc = QTextDocument()
        doc.setHtml(self._build_print_html())
        doc.print(printer)

    def _build_print_html(self) -> str:
        """Build printer-friendly HTML from current report rows."""
        if not self.report_data:
            return ""

        title = escape(str(self.report_data.title or "Report"))
        generated = datetime.now().strftime("%m/%d/%Y %I:%M %p")
        columns = self.export_manager._effective_columns(self.report_data)
        headers = [escape(str(col.display_name)) for col in columns]

        body_rows: List[str] = []
        for row in self.report_data.rows:
            cells: List[str] = []
            for col in columns:
                value = self.export_manager._value_for_column(row, col.name)
                formatted = col.format_value(value)
                cells.append(f"<td>{escape(str(formatted))}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")

        summary_text = escape(self.summary_label.text() or "")
        return f"""
        <html>
            <head>
                <style>
                    body {{ font-family: Segoe UI, Arial, sans-serif; font-size: 10pt; }}
                    h2 {{ color: #1976d2; margin-bottom: 4px; }}
                    .meta {{ color: #555; margin-bottom: 10px; }}
                    table {{ border-collapse: collapse; width: 100%; }}
                    th, td {{ border: 1px solid #cfd8dc; padding: 6px; }}
                    th {{ background: #1976d2; color: #fff; text-align: left; }}
                </style>
            </head>
            <body>
                <h2>{title}</h2>
                <div class="meta">Generated: {generated}</div>
                <div class="meta">{summary_text}</div>
                <table>
                    <thead><tr>{''.join(f'<th>{h}</th>' for h in headers)}</tr></thead>
                    <tbody>{''.join(body_rows)}</tbody>
                </table>
            </body>
        </html>
        """

    # ========================================================================
    # Events
    # ========================================================================

    def _on_refresh(self):
        """Handle refresh button click."""
        self.refresh_requested.emit(self.get_filter_values())

    def _on_row_double_click(self, index):
        """Handle row double-click."""
        if not self.report_data:
            return

        row_idx = index.row()
        if row_idx < len(self.report_data.rows):
            row_data = self.report_data.rows[row_idx].data
            self.row_double_clicked.emit(row_data)
