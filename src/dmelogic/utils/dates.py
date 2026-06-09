"""
Centralized date formatting utilities for DMELogic.

The canonical display format for **Date of Birth** is  ``MM-DD-YYYY``
(e.g. ``03-12-1964``).  All UI code should call :func:`format_dob` instead
of hand-rolling strftime / string manipulation.

General-purpose dates (order dates, Rx dates, delivery dates…) continue to
use ``MM/DD/YYYY`` via :func:`format_date`.
"""

from __future__ import annotations

import re
from datetime import datetime, date
from typing import Optional, Union

# ---------------------------------------------------------------------------
# Internal parse helpers
# ---------------------------------------------------------------------------

_TIME_SEP_RE = re.compile(r"[\sT]")  # split date from time portion

_DATE_FMTS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m.%d.%Y",
    "%m/%d/%y",
    "%m-%d-%y",
)


def _parse_date(value: Union[str, date, None]) -> Optional[date]:
    """Try to parse *value* into a :class:`date`.  Returns ``None`` on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    # QDate support (PyQt6/5)
    try:
        from PyQt6.QtCore import QDate
        if isinstance(value, QDate):
            return date(value.year(), value.month(), value.day())
    except Exception:
        pass

    s = str(value).strip()
    if not s:
        return None

    # Strip time portion
    parts = _TIME_SEP_RE.split(s, maxsplit=1)
    s_date = parts[0]

    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s_date, fmt).date()
        except ValueError:
            continue

    # Last-resort: try the full string (handles e.g. "2024-03-12 08:00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_dob(value: Union[str, date, None]) -> str:
    """Format a date-of-birth value as **MM-DD-YYYY**.

    Accepts ISO strings, ``MM/DD/YYYY``, ``MM-DD-YYYY``, ``MM.DD.YYYY``,
    ``date`` / ``datetime`` objects, ``QDate``, or ``None`` / empty.

    Returns the empty string when the input cannot be parsed.

    >>> format_dob("1964-03-12")
    '03-12-1964'
    >>> format_dob("03/12/1964")
    '03-12-1964'
    >>> format_dob(None)
    ''
    """
    dt = _parse_date(value)
    if dt is None:
        return str(value).strip() if value else ""
    return dt.strftime("%m-%d-%Y")


def format_date(value: Union[str, date, None]) -> str:
    """Format a general date as **MM/DD/YYYY** (order date, Rx date, etc.).

    Behaves identically to :func:`format_dob` except the output uses
    forward-slashes.

    >>> format_date("2024-07-01")
    '07/01/2024'
    """
    dt = _parse_date(value)
    if dt is None:
        return str(value).strip() if value else ""
    return dt.strftime("%m/%d/%Y")
