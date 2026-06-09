"""
Report Engine - Base class for all DMELogic reports

Provides standardized structure, data handling, and common functionality
for all business intelligence reports.
"""

from __future__ import annotations
import sqlite3
from datetime import datetime, date
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class ReportColumn:
    """Define a report column with display properties."""
    name: str
    display_name: str
    data_type: str = "text"  # text, number, currency, date, percent
    alignment: str = "left"  # left, center, right
    width: Optional[int] = None
    format_string: Optional[str] = None
    sortable: bool = True

    def format_value(self, value: Any) -> str:
        """Format value according to column type."""
        if value is None:
            return ""

        if self.data_type == "currency":
            try:
                return f"${float(value):,.2f}"
            except (ValueError, TypeError):
                return str(value)

        elif self.data_type == "percent":
            try:
                return f"{float(value):.1f}%"
            except (ValueError, TypeError):
                return str(value)

        elif self.data_type == "number":
            try:
                return f"{float(value):,.1f}"
            except (ValueError, TypeError):
                return str(value)

        elif self.data_type == "date":
            if isinstance(value, (date, datetime)):
                return value.strftime(self.format_string or "%m/%d/%Y")
            return str(value)

        else:  # text
            return str(value)


@dataclass
class ReportRow:
    """A single row of report data."""
    data: Dict[str, Any]
    style: Optional[Dict[str, Any]] = None  # Color, font, etc.

    def get(self, column: str, default: Any = None) -> Any:
        """Get column value with default."""
        return self.data.get(column, default)


@dataclass
class ReportData:
    """Complete report dataset with metadata."""
    title: str
    columns: List[ReportColumn]
    rows: List[ReportRow] = field(default_factory=list)
    summary: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_row(self, data: Dict[str, Any], style: Optional[Dict[str, Any]] = None):
        """Add a row to the report."""
        self.rows.append(ReportRow(data=data, style=style))

    def get_column_values(self, column_name: str) -> List[Any]:
        """Get all values for a specific column."""
        return [row.get(column_name) for row in self.rows]

    def filter_rows(self, predicate) -> List[ReportRow]:
        """Filter rows based on a predicate function."""
        return [row for row in self.rows if predicate(row)]

    def sort_rows(self, column_name: str, reverse: bool = False):
        """Sort rows by column value."""
        self.rows.sort(key=lambda row: row.get(column_name, ""), reverse=reverse)


class ReportEngine(ABC):
    """
    Base class for all DMELogic reports.

    Provides:
    - Database connection management
    - Standardized data structure
    - Common query helpers
    - Export functionality hooks
    - Error handling

    Subclasses implement:
    - _fetch_data() - Query the database
    - _process_data() - Transform raw data
    - get_report_title() - Report name
    - get_columns() - Column definitions
    """

    def __init__(self, folder_path: Optional[str] = None):
        """
        Initialize report engine.

        Args:
            folder_path: Path to database folder
        """
        self.folder_path = Path(folder_path or ".")
        self.report_data: Optional[ReportData] = None
        self._connections: Dict[str, sqlite3.Connection] = {}

    # ========================================================================
    # Abstract methods - Subclasses must implement
    # ========================================================================

    @abstractmethod
    def get_report_title(self) -> str:
        """Return the report title."""
        pass

    @abstractmethod
    def get_columns(self) -> List[ReportColumn]:
        """Return column definitions."""
        pass

    @abstractmethod
    def _fetch_data(self) -> List[Dict[str, Any]]:
        """
        Fetch raw data from database.

        Returns:
            List of dictionaries with raw data
        """
        pass

    # ========================================================================
    # Optional methods - Subclasses can override
    # ========================================================================

    def _process_data(self, raw_data: List[Dict[str, Any]]) -> List[ReportRow]:
        """
        Process raw data into report rows.

        Override this to add calculations, formatting, styling, etc.

        Args:
            raw_data: Raw data from _fetch_data()

        Returns:
            List of ReportRow objects
        """
        rows = []
        for item in raw_data:
            rows.append(ReportRow(data=item))
        return rows

    def _calculate_summary(self, rows: List[ReportRow]) -> Optional[Dict[str, Any]]:
        """
        Calculate summary statistics.

        Override this to add totals, averages, etc.

        Args:
            rows: Processed report rows

        Returns:
            Dictionary of summary statistics
        """
        return {
            'total_rows': len(rows),
            'generated_at': datetime.now().strftime('%m/%d/%Y %I:%M %p')
        }

    def get_filter_options(self) -> Dict[str, List[str]]:
        """
        Return available filter options.

        Override this to provide dropdown filters in UI.

        Returns:
            Dictionary mapping filter name to list of options
        """
        return {}

    # ========================================================================
    # Database helpers
    # ========================================================================

    def get_connection(self, db_name: str) -> sqlite3.Connection:
        """
        Get or create database connection.

        Args:
            db_name: Database filename (e.g., 'orders.db')

        Returns:
            SQLite connection with Row factory
        """
        if db_name not in self._connections:
            db_path = None

            # Use centralized DB resolution so reports read from the same active
            # databases as the rest of the application.
            try:
                from dmelogic.db.base import resolve_db_path
                db_path = Path(resolve_db_path(db_name, folder_path=str(self.folder_path)))
            except Exception:
                db_path = self.folder_path / db_name

            if not db_path.exists():
                raise FileNotFoundError(f"Database not found: {db_path}")

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            self._connections[db_name] = conn

        return self._connections[db_name]

    def execute_query(
        self,
        db_name: str,
        query: str,
        params: Tuple = ()
    ) -> List[sqlite3.Row]:
        """
        Execute a SELECT query and return results.

        Args:
            db_name: Database filename
            query: SQL query
            params: Query parameters

        Returns:
            List of Row objects
        """
        conn = self.get_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    def execute_scalar(
        self,
        db_name: str,
        query: str,
        params: Tuple = ()
    ) -> Any:
        """
        Execute query and return single value.

        Args:
            db_name: Database filename
            query: SQL query
            params: Query parameters

        Returns:
            Single value or None
        """
        rows = self.execute_query(db_name, query, params)
        if rows and len(rows) > 0:
            return rows[0][0]
        return None

    def close_connections(self):
        """Close all database connections."""
        for conn in self._connections.values():
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()

    # ========================================================================
    # Report generation
    # ========================================================================

    def generate(self, **filters) -> ReportData:
        """
        Generate the complete report.

        Args:
            **filters: Optional filter parameters

        Returns:
            ReportData object with all report content
        """
        try:
            # Fetch raw data
            raw_data = self._fetch_data()

            # Process into rows
            rows = self._process_data(raw_data)

            # Calculate summary
            summary = self._calculate_summary(rows)

            # Build report data
            self.report_data = ReportData(
                title=self.get_report_title(),
                columns=self.get_columns(),
                rows=rows,
                summary=summary,
                metadata={
                    'generated_at': datetime.now(),
                    'filters': filters,
                    'row_count': len(rows)
                }
            )

            return self.report_data

        finally:
            self.close_connections()

    def refresh(self, **filters) -> ReportData:
        """Alias for generate() - refresh report data."""
        return self.generate(**filters)

    # ========================================================================
    # Data access
    # ========================================================================

    def get_data(self) -> Optional[ReportData]:
        """Get current report data (generate if needed)."""
        if self.report_data is None:
            return self.generate()
        return self.report_data

    def get_rows_as_dicts(self) -> List[Dict[str, Any]]:
        """Get report rows as list of dictionaries."""
        data = self.get_data()
        if not data:
            return []
        return [row.data for row in data.rows]

    # ========================================================================
    # Utility methods
    # ========================================================================

    def parse_date(
        self,
        date_str: str,
        formats: List[str] = None
    ) -> Optional[date]:
        """
        Parse date string with multiple format attempts.

        Args:
            date_str: Date string to parse
            formats: List of format strings to try

        Returns:
            date object or None
        """
        if not date_str:
            return None

        if isinstance(date_str, datetime):
            return date_str.date()

        if isinstance(date_str, date):
            return date_str

        raw = str(date_str).strip()
        if not raw:
            return None

        # Common case for ISO timestamps and mixed date-time strings.
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            pass

        date_only = raw.split("T", 1)[0].split(" ", 1)[0]

        if formats is None:
            formats = [
                '%Y-%m-%d',
                '%Y/%m/%d',
                '%m/%d/%Y',
                '%m-%d-%Y',
                '%m.%d.%Y',
                '%m/%d/%y',
                '%m-%d-%y',
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d %H:%M:%S.%f',
                '%m/%d/%Y %H:%M:%S',
                '%m-%d-%Y %H:%M:%S',
                '%m/%d/%Y %I:%M:%S %p'
            ]

        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt).date()
            except (ValueError, TypeError):
                continue

        for fmt in (
            '%Y-%m-%d',
            '%Y/%m/%d',
            '%m/%d/%Y',
            '%m-%d-%Y',
            '%m.%d.%Y',
            '%m/%d/%y',
            '%m-%d-%y',
        ):
            try:
                return datetime.strptime(date_only, fmt).date()
            except (ValueError, TypeError):
                continue

        return None

    def format_currency(self, value: Any) -> str:
        """Format value as currency."""
        try:
            return f"${float(value):,.2f}"
        except (ValueError, TypeError):
            return "$0.00"

    def format_percent(self, value: Any) -> str:
        """Format value as percentage."""
        try:
            return f"{float(value):.1f}%"
        except (ValueError, TypeError):
            return "0.0%"

    def calculate_growth_rate(self, current: float, previous: float) -> float:
        """Calculate growth rate percentage."""
        if previous == 0:
            return 0.0
        return ((current - previous) / previous) * 100

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connections."""
        self.close_connections()

    def __del__(self):
        """Destructor - ensure connections are closed."""
        self.close_connections()


class SimpleReport(ReportEngine):
    """
    Simple report implementation for quick reports.

    Usage:
        report = SimpleReport(
            title="My Report",
            columns=[...],
            query_func=lambda: fetch_data()
        )
        data = report.generate()
    """

    def __init__(
        self,
        title: str,
        columns: List[ReportColumn],
        query_func,
        folder_path: Optional[str] = None
    ):
        super().__init__(folder_path)
        self._title = title
        self._columns = columns
        self._query_func = query_func

    def get_report_title(self) -> str:
        return self._title

    def get_columns(self) -> List[ReportColumn]:
        return self._columns

    def _fetch_data(self) -> List[Dict[str, Any]]:
        return self._query_func()
