"""
Pending Approval Panel — collapsible container shown above the orders list.

Displays agent-created orders awaiting human review. Each row shows order
summary with Approve / Reject / Review buttons plus an optional "View Source"
link when the agent attached a source document.  When approved the order moves
to the normal 'Pending' status. When rejected the order is cancelled (a reason
is required).

Improvements:
  • Auto-refresh via QTimer (checks every 30 s, only repaints when count changes)
  • Completeness-gated approval — warns before approving an incomplete order
  • Rejection dialog now *requires* a reason
  • "View Source" button when order notes contain a source file path
  • Table uses a unique objectName so the dark-theme global QSS can't
    override cell foreground colours
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QSizePolicy, QAbstractItemView, QDialog,
    QDialogButtonBox, QTextEdit, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont

from dmelogic.db.pending_approvals import (
    fetch_pending_approval_orders,
    approve_order,
    reject_order,
    count_pending_approvals,
)
from dmelogic.services.agent_order_watcher import AgentOrderWatcher
from dmelogic.utils.dates import format_dob


# ── Small helper dialog ────────────────────────────────────────────
class _RejectReasonDialog(QDialog):
    """Modal dialog requiring the user to provide a rejection reason."""

    def __init__(self, order_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reject Agent Order")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            f"You are about to <b>reject</b> order <b>{order_label}</b>.\n"
            "Please provide a reason (required):"
        ))

        self._reason_edit = QTextEdit()
        self._reason_edit.setPlaceholderText(
            "e.g. Duplicate order, illegible Rx, wrong patient \u2026"
        )
        self._reason_edit.setMaximumHeight(100)
        layout.addWidget(self._reason_edit)

        self._btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = self._btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("Reject")
        self._ok_btn.setEnabled(False)  # disabled until text entered
        self._btn_box.accepted.connect(self.accept)
        self._btn_box.rejected.connect(self.reject)
        layout.addWidget(self._btn_box)

        self._reason_edit.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self):
        self._ok_btn.setEnabled(bool(self._reason_edit.toPlainText().strip()))

    def reason(self) -> str:
        return self._reason_edit.toPlainText().strip()


# ── Source-file extraction & fuzzy file lookup ─────────────────────
_SOURCE_RE = re.compile(r"\[Source file:\s*(.+?)\]")


def _extract_source_path(notes: str) -> Optional[str]:
    """Pull the first [Source file: …] path out of order notes."""
    m = _SOURCE_RE.search(notes or "")
    return m.group(1).strip() if m else None


def _find_source_file(
    stored_path: Optional[str],
    patient_last: str = "",
    patient_first: str = "",
) -> Optional[str]:
    """Resolve the actual on-disk path for a source Rx document.

    Resolution order:
    1. ``stored_path`` exists exactly → return it.
    2. Fuzzy-match in the same directory as ``stored_path``
       (search for files whose name contains the patient last name).
    3. Walk the standard "Processesd By Cloney" folder (and OCR sub-folders)
       looking for a file matching the patient name.
    """
    from pathlib import Path
    from dmelogic.paths import ocr_folder

    # Normalise name fragments for matching (uppercase, strip whitespace)
    last = (patient_last or "").strip().upper()
    first = (patient_first or "").strip().upper()

    # Strip placeholder brackets e.g. "[Unknown]" → "UNKNOWN"
    if last.startswith("[") and last.endswith("]"):
        last = last[1:-1]
    if first.startswith("[") and first.endswith("]"):
        first = first[1:-1]

    def _name_matches(filename: str) -> bool:
        """Return True if the filename looks like it belongs to this patient."""
        upper = filename.upper()
        if not last:
            return False
        if last not in upper:
            return False
        # If we have a first name, require it too (tolerant of partial)
        if first and first not in upper:
            return False
        return True

    # 1 — exact path
    if stored_path:
        sp = Path(stored_path)
        if sp.is_file():
            return str(sp)
        # 2 — fuzzy search in the same directory
        parent = sp.parent
        if parent.is_dir():
            for f in parent.iterdir():
                if f.is_file() and f.suffix.lower() == ".pdf" and _name_matches(f.name):
                    return str(f)

    # 3 — search standard locations
    try:
        ocr = ocr_folder()
    except Exception:
        return None

    search_dirs = []
    # "Processesd By Cloney" (note the typo matches the real folder)
    cloney_dir = ocr / "Processesd By Cloney"
    if cloney_dir.is_dir():
        search_dirs.append(cloney_dir)
    # Also try the alphabetical sub-folder matching the last name initial
    if last:
        letter_dir = ocr / last[0]
        if letter_dir.is_dir():
            search_dirs.append(letter_dir)
    # OCR root itself
    if ocr.is_dir():
        search_dirs.append(ocr)

    for d in search_dirs:
        try:
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() == ".pdf" and _name_matches(f.name):
                    return str(f)
        except OSError:
            continue

    return None


class PendingApprovalPanel(QWidget):
    """
    Collapsible panel that lists agent-created orders awaiting approval.

    Signals:
        order_approved(int): Emitted with order_id when an order is approved.
        order_rejected(int): Emitted with order_id when an order is rejected.
    """

    order_approved = pyqtSignal(int)
    order_rejected = pyqtSignal(int)

    # Visual constants
    _BANNER_BG = "#1e1b4b"          # Deep indigo
    _BANNER_BORDER = "#4338ca"      # Indigo-600
    _BANNER_TEXT = "#e0e7ff"        # Indigo-100
    _BADGE_BG = "#f59e0b"          # Amber-500
    _BADGE_FG = "#1e1b4b"          # Deep indigo
    _APPROVE_BG = "#16a34a"        # Green-600
    _APPROVE_HOVER = "#15803d"     # Green-700
    _REJECT_BG = "#dc2626"         # Red-600
    _REJECT_HOVER = "#b91c1c"      # Red-700
    _TABLE_BG = "#1a1742"          # Darker indigo
    _TABLE_ALT = "#231f56"         # Slightly lighter
    _TABLE_FG = "#ffffff"          # White text for legibility
    _TABLE_HEADER_BG = "#312e81"   # Indigo-800
    _TABLE_HEADER_FG = "#ffffff"   # White header text

    # Auto-refresh interval (ms)
    _POLL_INTERVAL_MS = 5_000     # 5 seconds for responsive agent order processing

    def __init__(
        self,
        folder_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.folder_path = folder_path
        self._expanded = True
        self._pending_orders: list = []
        self._last_known_count: int = -1   # sentinel so first poll always refreshes
        self._agent_watcher = AgentOrderWatcher(
            on_order_created=self._on_agent_order_created,
            on_order_failed=self._on_agent_order_failed,
        )
        self._setup_ui()
        self._start_auto_refresh()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Banner / header frame ---
        self._header_frame = QFrame()
        self._header_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {self._BANNER_BG};
                border: 1px solid {self._BANNER_BORDER};
                border-radius: 6px 6px 0px 0px;
                padding: 6px 12px;
            }}
        """)
        header_layout = QHBoxLayout(self._header_frame)
        header_layout.setContentsMargins(10, 6, 10, 6)
        header_layout.setSpacing(8)

        # Robot / agent icon
        icon_label = QLabel("\U0001F916")  # 🤖
        icon_label.setStyleSheet(f"font-size: 16pt; color: {self._BANNER_TEXT}; border: none; background: transparent;")
        header_layout.addWidget(icon_label)

        # Title
        self._title_label = QLabel("Agent Orders — Pending Approval")
        self._title_label.setStyleSheet(f"""
            font-size: 11pt; font-weight: 700; color: {self._BANNER_TEXT};
            border: none; background: transparent;
        """)
        header_layout.addWidget(self._title_label)

        # Badge count
        self._badge = QLabel("0")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedSize(26, 20)
        self._badge.setStyleSheet(f"""
            background-color: {self._BADGE_BG};
            color: {self._BADGE_FG};
            font-size: 9pt; font-weight: 800;
            border-radius: 10px;
            border: none;
        """)
        header_layout.addWidget(self._badge)

        # Service status indicator (green dot when running)
        self._service_status = QLabel("●")
        self._service_status.setFixedSize(22, 20)
        self._service_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._service_status.setStyleSheet("""
            color: #4CAF50; font-size: 14pt; font-weight: bold;
            border: none; background: transparent;
        """)
        self._service_status.setToolTip("Agent Order Service: Running")
        header_layout.addWidget(self._service_status)
        self._update_service_status()

        header_layout.addStretch(1)

        # Approve All button
        self._approve_all_btn = QPushButton("Approve All")
        self._approve_all_btn.setToolTip("Approve all pending agent orders")
        self._approve_all_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 9pt; font-weight: 600;
                padding: 4px 14px; border-radius: 4px;
                background-color: {self._APPROVE_BG}; color: #fff;
                border: none;
            }}
            QPushButton:hover {{ background-color: {self._APPROVE_HOVER}; }}
        """)
        self._approve_all_btn.clicked.connect(self._on_approve_all)
        header_layout.addWidget(self._approve_all_btn)

        # Scan button — manually poll for new agent orders in the drop folder
        self._scan_btn = QPushButton("\U0001F4E5 Scan")  # 📥 Scan
        self._scan_btn.setToolTip("Scan agent_orders folder for new JSON files")
        self._scan_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 9pt; font-weight: 600;
                background: #3b82f6; color: #ffffff;
                border: 1px solid #2563eb; border-radius: 4px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background-color: #2563eb; }}
        """)
        self._scan_btn.clicked.connect(self._on_manual_refresh)
        header_layout.addWidget(self._scan_btn)

        # Refresh button — manually poll for new agent orders
        self._refresh_btn = QPushButton("\U0001F504")  # 🔄
        self._refresh_btn.setToolTip("Refresh — check for new agent orders now")
        self._refresh_btn.setFixedWidth(32)
        self._refresh_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 12pt; font-weight: 600;
                background: transparent; color: {self._BANNER_TEXT};
                border: 1px solid {self._BANNER_BORDER}; border-radius: 4px;
                padding: 2px;
            }}
            QPushButton:hover {{ background-color: {self._BANNER_BORDER}; }}
        """)
        self._refresh_btn.clicked.connect(self._on_manual_refresh)
        header_layout.addWidget(self._refresh_btn)

        # Settings button — configure service interval
        self._settings_btn = QPushButton("\u2699")  # ⚙
        self._settings_btn.setToolTip("Service Settings — configure poll interval")
        self._settings_btn.setFixedWidth(32)
        self._settings_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 12pt; font-weight: 600;
                background: transparent; color: {self._BANNER_TEXT};
                border: 1px solid {self._BANNER_BORDER}; border-radius: 4px;
                padding: 2px;
            }}
            QPushButton:hover {{ background-color: {self._BANNER_BORDER}; }}
        """)
        self._settings_btn.clicked.connect(self._on_settings)
        header_layout.addWidget(self._settings_btn)

        # Collapse / expand toggle
        self._toggle_btn = QPushButton("\u25B2")  # ▲
        self._toggle_btn.setToolTip("Collapse / expand pending approvals")
        self._toggle_btn.setFixedWidth(32)
        self._toggle_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 10pt; font-weight: 600;
                background: transparent; color: {self._BANNER_TEXT};
                border: 1px solid {self._BANNER_BORDER}; border-radius: 4px;
                padding: 2px;
            }}
            QPushButton:hover {{ background-color: {self._BANNER_BORDER}; }}
        """)
        self._toggle_btn.clicked.connect(self._toggle_expand)
        header_layout.addWidget(self._toggle_btn)

        layout.addWidget(self._header_frame)

        # --- Table body frame ---
        self._body_frame = QFrame()
        self._body_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {self._TABLE_BG};
                border: 1px solid {self._BANNER_BORDER};
                border-top: none;
                border-radius: 0px 0px 6px 6px;
            }}
        """)
        body_layout = QVBoxLayout(self._body_frame)
        body_layout.setContentsMargins(4, 4, 4, 4)
        body_layout.setSpacing(0)

        # Table widget — unique objectName so specificity beats global theme
        self._table = QTableWidget()
        self._table.setObjectName("PendingApprovalTable")
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "Order #", "Patient", "DOB", "Items", "HCPCS",
            "Rx Date", "Completeness", "Created", "Actions",
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMaximumHeight(260)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._table.verticalHeader().setDefaultSectionSize(42)  # taller rows for buttons

        # Column widths — explicit minimums so text is never clipped
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(60)
        self._table.setColumnWidth(0, 90)    # Order #
        self._table.setColumnWidth(1, 130)   # Patient
        self._table.setColumnWidth(2, 90)    # DOB
        self._table.setColumnWidth(3, 50)    # Items
        self._table.setColumnWidth(4, 120)   # HCPCS
        self._table.setColumnWidth(5, 90)    # Rx Date
        self._table.setColumnWidth(6, 150)   # Completeness
        self._table.setColumnWidth(7, 130)   # Created
        # Column 8 (Actions) stretches to fill remaining space
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)

        # ──────────────────────────────────────────────────────────────
        # Table styling: using #PendingApprovalTable selector to beat
        # the global dark-theme QSS which sets color: #94a3b8 on all
        # QTableWidget::item.  This ensures white text is honoured.
        # ──────────────────────────────────────────────────────────────
        self._table.setStyleSheet(f"""
            QTableWidget#PendingApprovalTable {{
                background-color: {self._TABLE_BG};
                alternate-background-color: {self._TABLE_ALT};
                color: {self._TABLE_FG};
                gridline-color: #2e2b5e;
                border: none;
                font-size: 9pt;
            }}
            QTableWidget#PendingApprovalTable QHeaderView::section {{
                background-color: {self._TABLE_HEADER_BG};
                color: {self._TABLE_HEADER_FG};
                font-weight: 700; font-size: 9pt;
                padding: 4px 8px;
                border: none;
                border-bottom: 2px solid {self._BANNER_BORDER};
            }}
            QTableWidget#PendingApprovalTable::item {{
                padding: 4px 8px;
                color: {self._TABLE_FG};
                background-color: transparent;
            }}
            QTableWidget#PendingApprovalTable::item:hover {{
                color: {self._TABLE_FG};
                background-color: rgba(255,255,255,0.05);
            }}
            QTableWidget#PendingApprovalTable::item:selected {{
                background-color: #3730a3;
                color: #ffffff;
            }}
        """)

        body_layout.addWidget(self._table)

        # Empty-state label (hidden when rows exist)
        self._empty_label = QLabel("No agent orders awaiting approval.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(f"""
            color: #6366f1; font-style: italic; padding: 16px;
            border: none; background: transparent;
        """)
        self._empty_label.setVisible(False)
        body_layout.addWidget(self._empty_label)

        layout.addWidget(self._body_frame)

        # Start hidden — will show when there are pending orders
        self.setVisible(False)

    # ------------------------------------------------------------------
    # Auto-refresh polling
    # ------------------------------------------------------------------

    def _start_auto_refresh(self) -> None:
        """Start a lightweight timer that polls count_pending_approvals()."""
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_for_changes)
        self._poll_timer.start()

    def _poll_for_changes(self) -> None:
        """Check the drop folder for new agent JSONs, then refresh if count changed."""
        from dmelogic.config import debug_log
        
        # 1) Ingest any new JSON order files from the drop folder
        try:
            pending = self._agent_watcher.pending_count()
            if pending > 0:
                debug_log(f"[pending-panel] Found {pending} JSON file(s) in drop folder, processing...")
            created = self._agent_watcher.poll()
            if created > 0:
                debug_log(f"[pending-panel] Agent watcher created {created} order(s)")
                # Force a refresh since new orders were just inserted
                self._last_known_count = -1
        except Exception as e:
            debug_log(f"[pending-panel] Agent watcher poll error: {e}")
            import traceback
            debug_log(traceback.format_exc())

        # 2) Check DB pending count and refresh table if changed
        try:
            current = count_pending_approvals(folder_path=self.folder_path)
            if current != self._last_known_count:
                self.refresh()
        except Exception as e:
            debug_log(f"[pending-panel] Count check error: {e}")

        # 3) Update service status indicator
        self._update_service_status()

    def _update_service_status(self) -> None:
        """Update the service status indicator dot."""
        try:
            from dmelogic.services.service_manager import is_server_mode, get_service_status
            
            if not is_server_mode():
                # Not a server - hide indicator
                self._service_status.setVisible(False)
                return
            
            status = get_service_status()
            if status == "RUNNING":
                self._service_status.setText("●")
                self._service_status.setStyleSheet("""
                    color: #4CAF50; font-size: 14pt; font-weight: bold;
                    border: none; background: transparent;
                """)
                self._service_status.setToolTip("Agent Order Service: Running")
            elif status == "STOPPED":
                self._service_status.setText("○")
                self._service_status.setStyleSheet("""
                    color: #f44336; font-size: 14pt; font-weight: bold;
                    border: none; background: transparent;
                """)
                self._service_status.setToolTip("Agent Order Service: Stopped")
            else:
                self._service_status.setText("○")
                self._service_status.setStyleSheet("""
                    color: #9e9e9e; font-size: 14pt; font-weight: bold;
                    border: none; background: transparent;
                """)
                self._service_status.setToolTip(f"Agent Order Service: {status}")
            self._service_status.setVisible(True)
        except Exception:
            self._service_status.setVisible(False)

    # ------------------------------------------------------------------
    # Agent watcher callbacks
    # ------------------------------------------------------------------

    def _on_agent_order_created(self, order_id: int, filename: str) -> None:
        """Called by the watcher when a JSON file becomes a real order."""
        from dmelogic.config import debug_log
        debug_log(f"[agent-watcher] Order #{order_id} created from drop file: {filename}")

    def _on_agent_order_failed(self, filename: str, reason: str) -> None:
        """Called by the watcher when a JSON file fails processing."""
        from dmelogic.config import debug_log
        debug_log(f"[agent-watcher] Drop file failed: {filename} — {reason}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload pending approval orders from the database."""
        self._pending_orders = fetch_pending_approval_orders(
            folder_path=self.folder_path,
        )
        self._populate_table()
        count = len(self._pending_orders)
        self._last_known_count = count
        self._badge.setText(str(count))
        self._badge.setToolTip(f"{count} order(s) awaiting approval")

        # Auto-show/hide the entire panel based on whether there are pending orders
        self.setVisible(count > 0)

    def pending_count(self) -> int:
        """Return the current count of pending approval orders."""
        return len(self._pending_orders)

    def filter_by_text(self, search_text: str) -> None:
        """
        Filter the pending approval table rows by search text.
        Matches against order #, patient name, DOB, HCPCS, etc.
        """
        search_lower = search_text.strip().lower() if search_text else ""
        
        visible_count = 0
        for row in range(self._table.rowCount()):
            show_row = True
            
            if search_lower:
                # Check all relevant columns for a match
                row_text = ""
                for col in range(min(8, self._table.columnCount())):  # Skip Actions column
                    item = self._table.item(row, col)
                    if item:
                        row_text += " " + (item.text() or "").lower()
                
                show_row = search_lower in row_text
            
            self._table.setRowHidden(row, not show_row)
            if show_row:
                visible_count += 1
        
        # Update badge to show filtered count if filtering
        if search_lower:
            self._badge.setText(f"{visible_count}")
            self._badge.setToolTip(f"{visible_count} of {len(self._pending_orders)} matching '{search_text}'")
        else:
            self._badge.setText(str(len(self._pending_orders)))
            self._badge.setToolTip(f"{len(self._pending_orders)} order(s) awaiting approval")

    # ------------------------------------------------------------------
    # Internal — table population
    # ------------------------------------------------------------------

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        orders = self._pending_orders
        has_data = len(orders) > 0
        self._table.setVisible(has_data)
        self._empty_label.setVisible(not has_data)

        if not has_data:
            return

        self._table.setRowCount(len(orders))

        for row_idx, order in enumerate(orders):
            order_id = order["id"]

            # Formatted order number
            try:
                formatted_id = f"ORD-{order_id:05d}"
            except Exception:
                formatted_id = str(order_id)

            # Patient name — flag if placeholder
            last = order["patient_last_name"] or ""
            first = order["patient_first_name"] or ""
            patient_name = f"{last}, {first}".strip(", ")
            patient_incomplete = "[Unknown]" in patient_name or not patient_name

            # DOB — normalized to MM-DD-YYYY
            dob = format_dob(order["patient_dob"])

            # Item count
            try:
                item_count = str(order["item_count"] or 0)
            except (KeyError, IndexError):
                item_count = "0"

            # HCPCS summary — flag placeholders
            try:
                hcpcs_summary = order["hcpcs_summary"] or ""
            except (KeyError, IndexError):
                hcpcs_summary = ""
            has_placeholder_items = "[TBD]" in hcpcs_summary
            # Truncate if too long
            if len(hcpcs_summary) > 40:
                hcpcs_summary = hcpcs_summary[:37] + "..."

            # Rx date — flag if missing/sentinel
            rx_date = order["rx_date"] or ""
            rx_date_missing = not rx_date or "[NEEDS REVIEW]" in rx_date

            # Created date
            created = order["created_date"] or ""
            if created and len(created) > 16:
                created = created[:16]

            # Insurance — flag if missing
            insurance = order["primary_insurance"] or ""
            insurance_missing = not insurance.strip()

            # Prescriber — flag if placeholder
            prescriber = order["prescriber_name"] or ""
            prescriber_incomplete = "[Unknown]" in prescriber or not prescriber.strip()

            # ---- Completeness assessment ----
            gaps: list[str] = []
            if patient_incomplete:
                gaps.append("Patient")
            if rx_date_missing:
                gaps.append("Rx Date")
            if insurance_missing:
                gaps.append("Insurance")
            if prescriber_incomplete:
                gaps.append("Prescriber")
            # Items: "0" items is worse than having placeholders
            if item_count == "0":
                gaps.append("No Items")
            elif has_placeholder_items:
                gaps.append("Items [TBD]")
            if not dob:
                gaps.append("DOB")
            # ICD-10 codes — important for billing
            try:
                icd_missing = not any(
                    order[f"icd_code_{i}"] for i in range(1, 6)
                )
            except (KeyError, IndexError):
                icd_missing = True
            if icd_missing:
                gaps.append("ICD-10")

            if not gaps:
                completeness_text = "\u2705 Complete"
                completeness_color = QColor("#4ade80")   # green-400 (bright)
            elif len(gaps) <= 2:
                completeness_text = f"\u26A0 {', '.join(gaps)}"
                completeness_color = QColor("#fcd34d")   # amber-300 (bright)
            else:
                completeness_text = f"\u274C {len(gaps)}/{7} missing"
                completeness_color = QColor("#fca5a5")   # red-300 (bright on dark)

            # Set cell values
            _WARN_COLOR = QColor("#fcd34d")   # amber-300 (bright on dark)

            cells = [
                (formatted_id, QColor("#ffffff")),
                (patient_name, _WARN_COLOR if patient_incomplete else QColor("#ffffff")),
                (dob, _WARN_COLOR if not dob else QColor("#e2e8f0")),
                (item_count, QColor("#ffffff")),
                (hcpcs_summary, _WARN_COLOR if has_placeholder_items else QColor("#e2e8f0")),
                (rx_date if not rx_date_missing else "[Missing]", _WARN_COLOR if rx_date_missing else QColor("#e2e8f0")),
                (completeness_text, completeness_color),
                (created, QColor("#e2e8f0")),
            ]
            for col_idx, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, order_id)
                if color:
                    item.setForeground(color)
                if col_idx == 6:  # Completeness column — bold
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                # Tooltip for completeness
                if col_idx == 6 and gaps:
                    item.setToolTip("Missing / incomplete:\n" + "\n".join(f"  \u2022 {g}" for g in gaps))
                self._table.setItem(row_idx, col_idx, item)

            # Source document path — try notes tag first, then fuzzy match
            try:
                notes_text = order["notes"] or ""
            except (KeyError, IndexError):
                notes_text = ""
            stored_path = _extract_source_path(notes_text)
            source_path = _find_source_file(stored_path, last, first)

            # ---- Action buttons cell ----
            actions_widget = QWidget()
            actions_widget.setStyleSheet("background: transparent; border: none;")
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(6)

            _BTN_STYLE = (
                "font-size: 9pt; font-weight: 600; "
                "min-height: 26px; min-width: 60px; "
                "padding: 2px 12px; border-radius: 4px; border: none;"
            )

            approve_btn = QPushButton("Approve")
            approve_btn.setToolTip(f"Approve order {formatted_id}")
            approve_btn.setStyleSheet(f"""
                QPushButton {{ {_BTN_STYLE}
                    background-color: {self._APPROVE_BG}; color: #fff;
                }}
                QPushButton:hover {{ background-color: {self._APPROVE_HOVER}; }}
            """)
            approve_btn.clicked.connect(
                lambda checked, oid=order_id, g=list(gaps): self._on_approve(oid, g)
            )
            actions_layout.addWidget(approve_btn)

            reject_btn = QPushButton("Reject")
            reject_btn.setToolTip(f"Reject order {formatted_id}")
            reject_btn.setStyleSheet(f"""
                QPushButton {{ {_BTN_STYLE}
                    background-color: {self._REJECT_BG}; color: #fff;
                }}
                QPushButton:hover {{ background-color: {self._REJECT_HOVER}; }}
            """)
            reject_btn.clicked.connect(lambda checked, oid=order_id: self._on_reject(oid))
            actions_layout.addWidget(reject_btn)

            review_btn = QPushButton("Review")
            review_btn.setToolTip(f"Open order {formatted_id} for detailed review")
            review_btn.setStyleSheet(f"""
                QPushButton {{ {_BTN_STYLE}
                    background-color: #4338ca; color: #e0e7ff;
                }}
                QPushButton:hover {{ background-color: #3730a3; }}
            """)
            review_btn.clicked.connect(lambda checked, oid=order_id: self._on_review(oid))
            actions_layout.addWidget(review_btn)

            # "View Rx" checkbox — opens source prescription document
            view_rx_cb = QCheckBox("View Rx")
            view_rx_cb.setToolTip(f"View source prescription for {formatted_id}")
            view_rx_cb.setStyleSheet("""
                QCheckBox {
                    font-size: 8pt; color: #c7d2fe;
                    spacing: 4px;
                }
                QCheckBox::indicator {
                    width: 14px; height: 14px;
                    border: 1px solid #6366f1; border-radius: 2px;
                    background: transparent;
                }
                QCheckBox::indicator:checked {
                    background-color: #4338ca;
                }
            """)
            view_rx_cb.stateChanged.connect(
                lambda state, oid=order_id: self._on_view_rx(oid) if state else None
            )
            actions_layout.addWidget(view_rx_cb)

            self._table.setCellWidget(row_idx, 8, actions_widget)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_approve(self, order_id: int, gaps: list[str] | None = None) -> None:
        """Approve a single agent order (with completeness gate)."""

        # ── Completeness gate ────────────────────────────────────
        if gaps:
            n = len(gaps)
            gap_list = "\n".join(f"  \u2022 {g}" for g in gaps)
            reply = QMessageBox.warning(
                self,
                "Incomplete Order",
                f"Order ORD-{order_id:05d} is missing {n} field(s):\n\n"
                f"{gap_list}\n\n"
                "Approve anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            reply = QMessageBox.question(
                self,
                "Approve Agent Order",
                f"Approve order ORD-{order_id:05d} and move it to the active orders list?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        username = self._get_current_username()
        ok = approve_order(order_id, approved_by=username, folder_path=self.folder_path)
        if ok:
            # ── Attach source PDF to order ────────────────────────────
            source_path = self._attach_source_pdf_to_order(order_id)
            
            # ── Offer to move source file to organized folder ─────────
            if source_path:
                self._offer_move_rx_files(order_id, source_path)
            
            self.order_approved.emit(order_id)
            self.refresh()
        else:
            QMessageBox.warning(
                self, "Approval Failed",
                f"Could not approve order ORD-{order_id:05d}. It may have already been processed.",
            )

    def _attach_source_pdf_to_order(self, order_id: int) -> Optional[str]:
        """Find and attach the source PDF to the order's attached_rx_files.
        
        Returns the source path if found, None otherwise.
        """
        from dmelogic.settings import load_settings
        from dmelogic.db.base import get_connection
        from dmelogic.config import debug_log
        
        # Find the order data
        order = None
        for o in self._pending_orders:
            if o["id"] == order_id:
                order = o
                break
        
        if not order:
            return None
        
        source_path = None
        
        # Get patient name for fuzzy matching
        try:
            patient_last = order["patient_last_name"] or ""
            patient_first = order["patient_first_name"] or ""
        except (KeyError, IndexError):
            patient_last = ""
            patient_first = ""
        
        # 1. First priority: search "Processesd By Cloney" folder by patient name
        # This is where the actual source Rx files are located
        source_path = _find_source_file(None, patient_last, patient_first)
        
        # 2. If not found, try notes path with fuzzy matching
        if not source_path or not os.path.exists(source_path):
            try:
                notes_text = order["notes"] or ""
            except (KeyError, IndexError):
                notes_text = ""
            stored_path = _extract_source_path(notes_text)
            
            if stored_path:
                resolved_path = _find_source_file(stored_path, patient_last, patient_first)
                if resolved_path and os.path.exists(resolved_path):
                    source_path = resolved_path
        
        if not source_path or not os.path.exists(source_path):
            debug_log(f"[approve] No source PDF found for order {order_id}")
            return None
        
        # Update the attached_rx_files field in the database
        try:
            conn = get_connection("orders.db", folder_path=self.folder_path)
            cur = conn.cursor()
            
            # Get current attached_rx_files
            cur.execute("SELECT attached_rx_files FROM orders WHERE id = ?", (order_id,))
            row = cur.fetchone()
            current_files = ""
            if row:
                current_files = row[0] or ""
            
            # Add the source path if not already present
            filename = os.path.basename(source_path)
            existing_files = [f.strip() for f in current_files.replace(";", "\n").splitlines() if f.strip()]
            
            if filename not in existing_files and source_path not in existing_files:
                if existing_files:
                    new_files = ";".join(existing_files + [source_path])
                else:
                    new_files = source_path
                
                cur.execute(
                    "UPDATE orders SET attached_rx_files = ? WHERE id = ?",
                    (new_files, order_id)
                )
                conn.commit()
                debug_log(f"[approve] Attached source PDF to order {order_id}: {source_path}")
            
            conn.close()
            return source_path
        except Exception as e:
            debug_log(f"[approve] Error attaching PDF to order {order_id}: {e}")
            return source_path  # Still return the path even if DB update failed

    def _offer_move_rx_files(self, order_id: int, source_path: str) -> None:
        """Offer to move the source Rx file to an organized folder."""
        import json
        import shutil
        from PyQt6.QtWidgets import QFileDialog, QComboBox
        from PyQt6.QtGui import QFont
        from dmelogic.config import debug_log
        
        # Find the order data for patient name
        order = None
        for o in self._pending_orders:
            if o["id"] == order_id:
                order = o
                break
        
        if not order:
            return
        
        patient_last = order.get("patient_last_name", "") or ""
        patient_first = order.get("patient_first_name", "") or ""
        
        # Load last-used folder from settings
        settings_path = os.path.join(os.path.expanduser("~"), ".dmelogic_settings.json")
        last_dest = ""
        recent_dirs = []
        try:
            if os.path.exists(settings_path):
                with open(settings_path, "r") as f:
                    settings = json.load(f)
                    last_dest = settings.get("rx_move_last_folder", "")
                    recent_dirs = settings.get("rx_move_recent_folders", [])
        except Exception:
            pass
        
        # Build the dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Move Rx File")
        dlg.setMinimumWidth(520)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        
        title = QLabel("📁 Move Rx File to Folder")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        layout.addWidget(title)
        
        desc = QLabel(
            f"Order ORD-{order_id:05d} approved for {patient_last}, {patient_first}.\n"
            f"Move the Rx file to a completed/patient folder?"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        # File info
        file_frame = QFrame()
        file_frame.setStyleSheet("background: white; border: 1px solid #E5E7EB; border-radius: 6px;")
        fl = QVBoxLayout(file_frame)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.addWidget(QLabel(f"📄 {os.path.basename(source_path)}"))
        layout.addWidget(file_frame)
        
        # Destination folder
        dest_label = QLabel("Destination Folder:")
        dest_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        layout.addWidget(dest_label)
        
        # Recent folders combo + browse button
        dest_row = QHBoxLayout()
        dest_row.setSpacing(6)
        
        dest_combo = QComboBox()
        dest_combo.setEditable(True)
        dest_combo.setMinimumWidth(350)
        
        # Populate recent folders
        if last_dest:
            dest_combo.addItem(last_dest)
        for d in recent_dirs:
            if d != last_dest:
                dest_combo.addItem(d)
        if dest_combo.count() == 0:
            dest_combo.setCurrentText("")
        
        # Auto-suggest a subfolder based on the patient's last name initial
        if patient_last and dest_combo.currentText().strip():
            base_dest = dest_combo.currentText().strip()
            letter = patient_last[0].upper()
            
            # If the saved path already ends in a single letter subfolder, use its parent
            tail = os.path.basename(base_dest)
            if len(tail) == 1 and tail.isalpha():
                base_dest = os.path.dirname(base_dest)
            
            suggested = os.path.join(base_dest, letter)
            if os.path.isdir(suggested):
                dest_combo.setCurrentText(suggested)
            elif os.path.isdir(base_dest):
                try:
                    subs = [d for d in os.listdir(base_dest)
                            if os.path.isdir(os.path.join(base_dest, d))
                            and len(d) == 1 and d.isalpha()]
                    if subs:
                        dest_combo.setCurrentText(suggested)
                except Exception:
                    pass
        
        dest_row.addWidget(dest_combo, 1)
        
        btn_browse = QPushButton("📂 Browse...")
        btn_browse.setStyleSheet(
            "background: #3B82F6; color: white; border-radius: 6px; "
            "padding: 8px 16px; font-weight: 600; border: none;"
        )
        def _browse_dest():
            folder = QFileDialog.getExistingDirectory(dlg, "Select Destination Folder", dest_combo.currentText())
            if folder:
                dest_combo.setCurrentText(folder)
        btn_browse.clicked.connect(_browse_dest)
        dest_row.addWidget(btn_browse)
        layout.addLayout(dest_row)
        
        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        
        btn_skip = QPushButton("Skip — Don't Move")
        btn_skip.setStyleSheet(
            "background: #E5E7EB; color: #374151; border-radius: 6px; "
            "padding: 10px 20px; font-weight: 500; border: none;"
        )
        btn_skip.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_skip)
        
        btn_move = QPushButton("📁 Move File")
        btn_move.setStyleSheet(
            "background: #059669; color: white; border-radius: 6px; "
            "padding: 10px 24px; font-weight: 700; font-size: 12px; border: none;"
        )
        btn_move.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_move)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        
        dest_folder = dest_combo.currentText().strip()
        if not dest_folder:
            return
        
        # Create destination folder if needed
        os.makedirs(dest_folder, exist_ok=True)
        
        try:
            basename = os.path.basename(source_path)
            dst_path = os.path.join(dest_folder, basename)
            
            # Handle name collision
            if os.path.exists(dst_path):
                name, ext = os.path.splitext(basename)
                dst_path = os.path.join(dest_folder, f"{name}_ORD{order_id:05d}{ext}")
            
            # Move the file
            shutil.move(source_path, dst_path)
            debug_log(f"[approve] Moved Rx file to {dst_path}")
            
            # Save last-used folder to settings
            try:
                settings = {}
                if os.path.exists(settings_path):
                    with open(settings_path, "r") as f:
                        settings = json.load(f)
                settings["rx_move_last_folder"] = dest_folder
                recents = settings.get("rx_move_recent_folders", [])
                if dest_folder in recents:
                    recents.remove(dest_folder)
                recents.insert(0, dest_folder)
                settings["rx_move_recent_folders"] = recents[:10]
                with open(settings_path, "w") as f:
                    json.dump(settings, f, indent=2)
            except Exception:
                pass
            
            QMessageBox.information(
                self, "File Moved",
                f"✅ Moved Rx file to:\n{dst_path}"
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Move Error",
                f"Could not move file:\n{e}"
            )

    def _on_reject(self, order_id: int) -> None:
        """Reject a single agent order — reason is required."""
        dlg = _RejectReasonDialog(f"ORD-{order_id:05d}", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        reason = dlg.reason()
        if not reason:
            # Safety net (button should be disabled)
            QMessageBox.warning(self, "Reason Required",
                                "A rejection reason is required.")
            return

        username = self._get_current_username()
        result = reject_order(
            order_id,
            rejected_by=username,
            reason=reason,
            folder_path=self.folder_path,
        )
        if result:
            self.order_rejected.emit(order_id)
            self.refresh()
        else:
            QMessageBox.warning(
                self, "Rejection Failed",
                f"Could not reject order ORD-{order_id:05d}. It may have already been processed.",
            )

    def _on_review(self, order_id: int) -> None:
        """Open the order editor for detailed review before approving/rejecting."""
        # Walk up to find the MainWindow and use its order editor
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "open_order_editor"):
                parent.open_order_editor(order_id)
                # Refresh after review in case status changed manually
                self.refresh()
                return
            parent = parent.parent() if hasattr(parent, "parent") else None

        QMessageBox.information(
            self, "Review",
            f"Order ORD-{order_id:05d} — use the order editor to review details.",
        )

    def _on_view_rx(self, order_id: int) -> None:
        """Open the source prescription PDF for this order."""
        from dmelogic.settings import load_settings
        
        # Find the order data
        order = None
        for o in self._pending_orders:
            if o["id"] == order_id:
                order = o
                break

        if not order:
            QMessageBox.warning(self, "View Rx", f"Order ORD-{order_id:05d} not found.")
            return

        source_path = None
        
        # Get patient name for fuzzy matching
        try:
            patient_last = order["patient_last_name"] or ""
            patient_first = order["patient_first_name"] or ""
        except (KeyError, IndexError):
            patient_last = ""
            patient_first = ""
        
        # 1. First priority: check attached_rx_files column (direct path)
        try:
            attached = order["attached_rx_files"] or ""
            if attached and os.path.exists(attached):
                source_path = attached
        except (KeyError, TypeError, IndexError):
            pass
        
        # 2. Try reserved_rx_path column
        if not source_path:
            try:
                reserved = order["reserved_rx_path"] or ""
                if reserved and os.path.exists(reserved):
                    source_path = reserved
            except (KeyError, TypeError, IndexError):
                pass
        
        # 3. Try stored path from notes field
        if not source_path:
            try:
                notes_text = order["notes"] or ""
            except (KeyError, IndexError):
                notes_text = ""
            stored_path = _extract_source_path(notes_text)
            
            if stored_path:
                resolved_path = _find_source_file(stored_path, patient_last, patient_first)
                if resolved_path and os.path.exists(resolved_path):
                    source_path = resolved_path
        
        # 4. If not found, search "Processesd By Cloney" folder by patient name
        if not source_path or not os.path.exists(source_path):
            source_path = _find_source_file(None, patient_last, patient_first)

        if not source_path or not os.path.exists(source_path):
            QMessageBox.information(
                self, "View Rx",
                f"No prescription document found for order ORD-{order_id:05d}.\n\n"
                "The source file may have been moved or is not attached.",
            )
            return

        # Open with system default viewer
        try:
            if sys.platform == "win32":
                os.startfile(source_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", source_path])
            else:
                subprocess.Popen(["xdg-open", source_path])
        except Exception as e:
            QMessageBox.warning(self, "View Rx", f"Could not open file:\n{e}")

    def _on_manual_refresh(self) -> None:
        """Manually trigger a refresh — poll for new agent order files and reload."""
        # Check how many files are pending before processing
        pending_before = self._agent_watcher.pending_count()
        
        # Poll the agent watcher for any new JSON files in the drop folder
        new_count = self._agent_watcher.poll()
        
        # Refresh the table from the database
        self.refresh()
        
        if new_count > 0:
            # Flash GREEN — new orders successfully created
            self._scan_btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 9pt; font-weight: 600;
                    background: #27ae60; color: #ffffff;
                    border: 1px solid #27ae60; border-radius: 4px;
                    padding: 4px 10px;
                }}
            """)
            self._scan_btn.setText(f"\U0001F4E5 +{new_count}")
            QTimer.singleShot(1500, self._reset_scan_btn_style)
        elif pending_before > 0:
            # Flash ORANGE — files found but failed to process
            self._scan_btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 9pt; font-weight: 600;
                    background: #f59e0b; color: #ffffff;
                    border: 1px solid #d97706; border-radius: 4px;
                    padding: 4px 10px;
                }}
            """)
            self._scan_btn.setText(f"\U0001F4E5 Failed")
            QTimer.singleShot(1500, self._reset_scan_btn_style)
        else:
            # Quick flash GRAY to show scan happened but no files found
            self._scan_btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 9pt; font-weight: 600;
                    background: #6b7280; color: #ffffff;
                    border: 1px solid #6b7280; border-radius: 4px;
                    padding: 4px 10px;
                }}
            """)
            QTimer.singleShot(500, self._reset_scan_btn_style)

    def _reset_scan_btn_style(self) -> None:
        """Reset the scan button style after a flash."""
        self._scan_btn.setText("\U0001F4E5 Scan")
        self._scan_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 9pt; font-weight: 600;
                background: #3b82f6; color: #ffffff;
                border: 1px solid #2563eb; border-radius: 4px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background-color: #2563eb; }}
        """)

    def _on_settings(self) -> None:
        """Open service settings dialog to configure poll interval."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QDialogButtonBox
        from dmelogic.services.service_manager import get_service_config, set_service_config, is_server_mode
        
        if not is_server_mode():
            QMessageBox.information(
                self,
                "Service Settings",
                "This PC is not configured as a server.\n\n"
                "The Agent Order Service only runs on the server PC.",
            )
            return
        
        # Get current config
        config = get_service_config()
        
        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Agent Order Service Settings")
        dialog.setMinimumWidth(350)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        
        # Interval setting
        interval_layout = QHBoxLayout()
        interval_label = QLabel("Poll Interval:")
        interval_label.setToolTip("How often the service checks for new agent orders")
        interval_spin = QSpinBox()
        interval_spin.setRange(1, 10800)
        interval_spin.setValue(config.get("poll_interval", 5))
        interval_spin.setSuffix(" seconds")
        interval_spin.setToolTip("1-10800 seconds (up to 3 hours)")
        interval_layout.addWidget(interval_label)
        interval_layout.addWidget(interval_spin)
        interval_layout.addStretch()
        layout.addLayout(interval_layout)
        
        # Batch size setting
        batch_layout = QHBoxLayout()
        batch_label = QLabel("Max Batch Size:")
        batch_label.setToolTip("Maximum orders to process per poll cycle")
        batch_spin = QSpinBox()
        batch_spin.setRange(1, 50)
        batch_spin.setValue(config.get("max_batch_size", 5))
        batch_spin.setToolTip("1-50 orders per batch")
        batch_layout.addWidget(batch_label)
        batch_layout.addWidget(batch_spin)
        batch_layout.addStretch()
        layout.addLayout(batch_layout)
        
        # Info label
        info_label = QLabel("Changes take effect within 60 seconds.\nNo service restart required.")
        info_label.setStyleSheet("color: #666; font-size: 9pt;")
        layout.addWidget(info_label)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Save new settings
            set_service_config(
                poll_interval=interval_spin.value(),
                max_batch_size=batch_spin.value(),
            )
            QMessageBox.information(
                self,
                "Settings Saved",
                f"Poll interval: {interval_spin.value()}s\n"
                f"Batch size: {batch_spin.value()}\n\n"
                "Changes will take effect within 60 seconds.",
            )

    def _on_approve_all(self) -> None:
        """Approve all pending agent orders at once."""
        count = len(self._pending_orders)
        if count == 0:
            return

        reply = QMessageBox.question(
            self,
            "Approve All Agent Orders",
            f"Approve all {count} pending agent order(s) and move them to the active orders list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        username = self._get_current_username()
        approved = 0
        for order in self._pending_orders:
            if approve_order(order["id"], approved_by=username, folder_path=self.folder_path):
                self.order_approved.emit(order["id"])
                approved += 1

        self.refresh()
        QMessageBox.information(
            self,
            "Bulk Approval Complete",
            f"Approved {approved} of {count} agent order(s).",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _toggle_expand(self) -> None:
        """Toggle the body frame visibility."""
        self._expanded = not self._expanded
        self._body_frame.setVisible(self._expanded)
        self._toggle_btn.setText("\u25B2" if self._expanded else "\u25BC")  # ▲ / ▼
        self._approve_all_btn.setVisible(self._expanded)

    def _get_current_username(self) -> str:
        """Try to get the logged-in username from the app."""
        try:
            parent = self.parent()
            while parent is not None:
                if hasattr(parent, "current_user") and parent.current_user:
                    return str(parent.current_user)
                if hasattr(parent, "logged_in_user") and parent.logged_in_user:
                    return str(parent.logged_in_user)
                parent = parent.parent() if hasattr(parent, "parent") else None
        except Exception:
            pass
        return "User"
