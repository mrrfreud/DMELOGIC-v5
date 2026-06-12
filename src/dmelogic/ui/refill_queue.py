"""
Refill Automation Queue
========================
Enhanced refill processing widget with smart queue management.

Features:
- Auto-loads refills due (no manual "generate" needed)
- Summary stats: Overdue, Due Today, Due This Week, Due This Month
- Quick filter toggles
- One-click "Process All Due" for batch refills
- Patient grouping with visual separators
- Auto-refresh after processing
- Color-coded urgency (Red=overdue, Yellow=due soon, Green=processed)

Integration:
    This replaces the existing Process Refills tab content.
    In app_legacy.py, replace the create_process_refills_tab() body with:

        from dmelogic.ui.refill_queue import RefillQueueWidget
        self.refill_queue = RefillQueueWidget(
            orders_db_file=self.orders_database_file,
            folder_path=getattr(self, 'folder_path', None),
            parent=self
        )
        self.refill_queue.refill_processed.connect(lambda: self.load_orders())
        self.main_tabs.addTab(self.refill_queue, "💊 Refill Queue")
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QProgressDialog, QApplication, QCheckBox,
    QComboBox, QDateEdit, QSizePolicy, QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal, QDate, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush

from dmelogic.db.base import get_connection
from dmelogic.config import debug_log


# Shared modern button styles (light theme), consistent with the main tabs.
_RQ_PRIMARY = (
    "QPushButton { background:#2563eb; color:#ffffff; font-weight:600;"
    " padding:8px 18px; border:none; border-radius:8px; }"
    "QPushButton:hover { background:#1d4ed8; }"
)
_RQ_GHOST = (
    "QPushButton { background:#ffffff; color:#0f172a; font-weight:600;"
    " padding:8px 14px; border:1px solid #e2e8f0; border-radius:8px; }"
    "QPushButton:hover { background:#f1f5f9; border-color:#cbd5e1; }"
)
_RQ_DANGER = (
    "QPushButton { background:#ffffff; color:#dc2626; font-weight:600;"
    " padding:8px 14px; border:1px solid #fecaca; border-radius:8px; }"
    "QPushButton:hover { background:#fef2f2; border-color:#dc2626; }"
)


# ═══════════════════════════════════════════════════════════════════
# Stat Card Widget
# ═══════════════════════════════════════════════════════════════════

class _StatCard(QFrame):
    """Mini stat card for the summary bar."""
    clicked = pyqtSignal()

    def __init__(self, title: str, value: str, color: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame {{
                background: white;
                border: 1px solid #E5E7EB;
                border-left: 4px solid {color};
                border-radius: 8px;
            }}
            QFrame:hover {{
                background: #F9FAFB;
                border-color: {color};
            }}
        """)
        self.setMinimumWidth(130)
        self.setMaximumHeight(72)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color: #6B7280; font-size: 10px; font-weight: 500; border: none;")
        layout.addWidget(self.title_label)

        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: 700; border: none;")
        layout.addWidget(self.value_label)

    def set_value(self, value: str):
        self.value_label.setText(value)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════════
# Refill Queue Widget
# ═══════════════════════════════════════════════════════════════════

class RefillQueueWidget(QWidget):
    """
    Full refill automation queue widget.
    Drop-in replacement for the Process Refills tab.
    """

    refill_processed = pyqtSignal()  # Emitted after any refill is created

    def __init__(
        self,
        orders_db_file: Optional[str] = None,
        folder_path: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.main_window = parent
        self.orders_db_file = orders_db_file
        self.folder_path = folder_path
        self.current_filter = "all_due"  # "overdue", "today", "this_week", "this_month", "all_due"
        self._refill_data: List[Dict[str, Any]] = []

        self._setup_ui()

        # Auto-load on first show
        QTimer.singleShot(200, self.refresh_queue)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- Header ----
        header_row = QHBoxLayout()
        title = QLabel("💊 Refill Automation Queue")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        header_row.addWidget(title)
        header_row.addStretch()

        self.btn_refresh = QPushButton("🔄 Refresh")
        self.btn_refresh.setStyleSheet(_RQ_GHOST)
        self.btn_refresh.clicked.connect(self.refresh_queue)
        header_row.addWidget(self.btn_refresh)

        layout.addLayout(header_row)

        # ---- Stat cards ----
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)

        self.card_overdue = _StatCard("🔴 OVERDUE", "0", "#DC2626")
        self.card_overdue.clicked.connect(lambda: self._set_filter("overdue"))
        stats_row.addWidget(self.card_overdue)

        self.card_today = _StatCard("🟡 DUE TODAY", "0", "#D97706")
        self.card_today.clicked.connect(lambda: self._set_filter("today"))
        stats_row.addWidget(self.card_today)

        self.card_week = _StatCard("🟢 THIS WEEK", "0", "#059669")
        self.card_week.clicked.connect(lambda: self._set_filter("this_week"))
        stats_row.addWidget(self.card_week)

        self.card_month = _StatCard("📅 THIS MONTH", "0", "#3B82F6")
        self.card_month.clicked.connect(lambda: self._set_filter("this_month"))
        stats_row.addWidget(self.card_month)

        self.card_total = _StatCard("📦 TOTAL REFILLABLE", "0", "#6B7280")
        self.card_total.clicked.connect(lambda: self._set_filter("all_due"))
        stats_row.addWidget(self.card_total)

        stats_row.addStretch()
        layout.addLayout(stats_row)

        # ---- Action bar ----
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.btn_process_all_due = QPushButton("🚀 Process All Overdue + Due Today")
        self.btn_process_all_due.setStyleSheet(_RQ_PRIMARY)
        self.btn_process_all_due.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_process_all_due.clicked.connect(self._process_all_due)
        action_row.addWidget(self.btn_process_all_due)

        self.btn_process_selected = QPushButton("✅ Process Selected")
        self.btn_process_selected.setStyleSheet(_RQ_GHOST)
        self.btn_process_selected.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_process_selected.clicked.connect(self._process_selected)
        action_row.addWidget(self.btn_process_selected)

        # Unlock / Reset refill flag button
        self.btn_unlock = QPushButton("🔓 Unlock / Reset Refill")
        self.btn_unlock.setStyleSheet(_RQ_GHOST)
        self.btn_unlock.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_unlock.setToolTip(
            "Manually unlock an order or mark it as fully refilled.\n"
            "Use this to fix stuck orders that show errors."
        )
        self.btn_unlock.clicked.connect(self._show_refill_override_dialog)
        action_row.addWidget(self.btn_unlock)

        # Remove checked orders from the queue (temporary — locks the order so
        # it's hidden until you Unlock / Reset Refill it again).
        self.btn_remove = QPushButton("🚫 Remove from Queue")
        self.btn_remove.setStyleSheet(_RQ_GHOST)
        self.btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove.setToolTip(
            "Hide the checked orders from the refill queue.\n"
            "Reversible — use 'Unlock / Reset Refill' to bring them back."
        )
        self.btn_remove.clicked.connect(self._remove_from_queue)
        action_row.addWidget(self.btn_remove)

        # Permanently remove checked orders (marks refills complete — automation
        # will not pick them up again).
        self.btn_remove_perm = QPushButton("🗑️ Permanently Remove")
        self.btn_remove_perm.setStyleSheet(_RQ_DANGER)
        self.btn_remove_perm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove_perm.setToolTip(
            "Permanently remove the checked orders from refill automation\n"
            "(marks their refills as completed). This cannot be auto-undone."
        )
        self.btn_remove_perm.clicked.connect(self._permanently_remove_from_queue)
        action_row.addWidget(self.btn_remove_perm)

        action_row.addStretch()

        # Select all checkbox
        self.chk_select_all = QCheckBox("Select All Visible")
        self.chk_select_all.setStyleSheet("font-weight: 500; color: #475569;")
        self.chk_select_all.stateChanged.connect(self._toggle_select_all)
        action_row.addWidget(self.chk_select_all)

        # Export
        self.btn_export = QPushButton("📄 Export PDF")
        self.btn_export.setStyleSheet(_RQ_GHOST)
        self.btn_export.clicked.connect(self._export_pdf)
        action_row.addWidget(self.btn_export)

        layout.addLayout(action_row)

        # ---- Summary label ----
        self.summary_label = QLabel("Loading refill queue...")
        self.summary_label.setStyleSheet("color: #6B7280; font-style: italic; font-size: 11px;")
        layout.addWidget(self.summary_label)

        # ---- Table ----
        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels([
            "☑", "Urgency", "Order #", "Patient", "DOB",
            "HCPCS", "Description", "Refills Left",
            "Day Supply", "Next Due", "Days Until", "Prescriber"
        ])

        header = self.table.horizontalHeader()
        header.resizeSection(0, 36)    # Checkbox
        header.resizeSection(1, 70)    # Urgency
        header.resizeSection(2, 80)    # Order #
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)    # Patient
        header.resizeSection(4, 85)    # DOB
        header.resizeSection(5, 80)    # HCPCS
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)    # Description
        header.resizeSection(7, 80)    # Refills
        header.resizeSection(8, 75)    # Day Supply
        header.resizeSection(9, 90)    # Next Due
        header.resizeSection(10, 80)   # Days Until
        header.setSectionResizeMode(11, QHeaderView.ResizeMode.Stretch)   # Prescriber

        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.setStyleSheet("""
            QTableWidget {
                background: white;
                alternate-background-color: #F9FAFB;
                border: 1px solid #E5E7EB;
                border-radius: 6px;
                gridline-color: #F3F4F6;
            }
            QHeaderView::section {
                background: #F3F4F6;
                border: none;
                padding: 8px 6px;
                font-weight: 600;
                color: #374151;
                border-bottom: 2px solid #E5E7EB;
            }
            QTableWidget::item {
                padding: 4px 6px;
            }
        """)

        # Double-click to view order
        self.table.cellDoubleClicked.connect(self._on_double_click)

        layout.addWidget(self.table, 1)

    # ================================================================
    # Data Loading
    # ================================================================

    def refresh_queue(self):
        """Reload all refill data from the database."""
        self._refill_data = self._fetch_refill_data()
        self._update_stats()
        self._populate_table()

    def _fetch_refill_data(self) -> List[Dict[str, Any]]:
        """Fetch all orders with remaining refills and compute due dates."""
        results = []

        try:
            db_path = self.orders_db_file
            if not db_path:
                conn = get_connection("orders.db", folder_path=self.folder_path)
            else:
                conn = sqlite3.connect(db_path)

            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Backward-compatible soft-delete awareness
            has_deleted_at = False
            try:
                cur.execute("PRAGMA table_info(orders)")
                cols = {r[1] for r in cur.fetchall()}
                has_deleted_at = "deleted_at" in cols
            except Exception:
                has_deleted_at = False

            deleted_clause = "AND o.deleted_at IS NULL" if has_deleted_at else ""

            # Get all order items with refills > 0, from non-locked, non-completed orders
            # Exclude orders that already have child refill orders
            cur.execute(f"""
                SELECT 
                    o.id AS order_id,
                    o.order_date,
                    o.rx_date,
                    o.patient_last_name,
                    o.patient_first_name,
                    o.patient_dob,
                    o.patient_phone,
                    o.patient_id,
                    o.prescriber_name,
                    o.prescriber_npi,
                    o.order_status,
                    o.parent_order_id,
                    o.refill_number,
                    oi.rowid AS item_rowid,
                    oi.hcpcs_code,
                    oi.description,
                    oi.refills,
                    oi.day_supply,
                    oi.qty,
                    oi.last_filled_date,
                    oi.directions
                FROM orders o
                JOIN order_items oi ON o.id = oi.order_id
                WHERE CAST(COALESCE(oi.refills, '0') AS INTEGER) > 0
                  AND COALESCE(oi.day_supply, '0') != '0'
                  AND COALESCE(o.is_locked, 0) = 0
                  AND COALESCE(o.refill_completed, 0) = 0
                                    AND COALESCE(o.order_status, '') != 'Cancelled'
                                    {deleted_clause}
                ORDER BY o.patient_last_name, o.patient_first_name, o.order_date DESC
                        """)

            today = date.today()

            for row in cur.fetchall():
                try:
                    num_refills = int(row["refills"] or 0)
                    num_days = int(row["day_supply"] or 30)
                except (ValueError, TypeError):
                    continue

                if num_refills <= 0 or num_days <= 0:
                    continue

                # Calculate next refill due date
                # Use last_filled_date if available, otherwise order_date
                base_date_str = row["last_filled_date"] or row["order_date"] or ""
                base_dt = None
                for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y"]:
                    try:
                        base_dt = datetime.strptime(str(base_date_str).split(" ")[0], fmt).date()
                        break
                    except (ValueError, TypeError):
                        continue

                if not base_dt:
                    continue

                next_due = base_dt + timedelta(days=num_days)
                days_until = (next_due - today).days

                # Determine urgency
                if days_until < 0:
                    urgency = "OVERDUE"
                    urgency_color = "#DC2626"
                    urgency_sort = 0
                elif days_until == 0:
                    urgency = "TODAY"
                    urgency_color = "#D97706"
                    urgency_sort = 1
                elif days_until <= 7:
                    urgency = "THIS WEEK"
                    urgency_color = "#059669"
                    urgency_sort = 2
                elif days_until <= 30:
                    urgency = "THIS MONTH"
                    urgency_color = "#3B82F6"
                    urgency_sort = 3
                else:
                    urgency = "FUTURE"
                    urgency_color = "#9CA3AF"
                    urgency_sort = 4

                # Build order display number
                parent_id = row["parent_order_id"]
                refill_num = row["refill_number"] or 0
                if parent_id and refill_num > 0:
                    display_number = (
                        f"ORD-{row['order_id']:03d} "
                        f"(Refill of ORD-{int(parent_id):03d} R{int(refill_num)})"
                    )
                else:
                    display_number = f"ORD-{row['order_id']:03d}"

                patient_name = f"{row['patient_last_name'] or ''}, {row['patient_first_name'] or ''}".strip(", ")

                results.append({
                    "order_id": row["order_id"],
                    "item_rowid": row["item_rowid"],
                    "display_number": display_number,
                    "order_date": row["order_date"] or "",
                    "patient_name": patient_name,
                    "patient_dob": row["patient_dob"] or "",
                    "patient_phone": row["patient_phone"] or "",
                    "patient_id": row["patient_id"],
                    "hcpcs": row["hcpcs_code"] or "",
                    "description": row["description"] or "",
                    "refills": num_refills,
                    "day_supply": num_days,
                    "next_due": next_due.strftime("%m/%d/%Y"),
                    "days_until": days_until,
                    "prescriber": row["prescriber_name"] or "",
                    "urgency": urgency,
                    "urgency_color": urgency_color,
                    "urgency_sort": urgency_sort,
                })

            conn.close()

        except Exception as e:
            debug_log(f"RefillQueue fetch error: {e}")
            print(f"RefillQueue fetch error: {e}")

        # Sort by urgency (overdue first), then patient name
        results.sort(key=lambda r: (r["urgency_sort"], r["patient_name"], r["order_id"]))
        return results

    # ================================================================
    # Stats & Filters
    # ================================================================

    def _update_stats(self):
        """Update the stat cards from current data."""
        overdue = sum(1 for r in self._refill_data if r["urgency"] == "OVERDUE")
        today_count = sum(1 for r in self._refill_data if r["urgency"] == "TODAY")
        week_count = sum(1 for r in self._refill_data if r["urgency"] == "THIS WEEK")
        month_count = sum(1 for r in self._refill_data if r["urgency"] == "THIS MONTH")
        total = len(self._refill_data)

        self.card_overdue.set_value(str(overdue))
        self.card_today.set_value(str(today_count))
        self.card_week.set_value(str(week_count))
        self.card_month.set_value(str(month_count))
        self.card_total.set_value(str(total))

        # Update process all button text
        actionable = overdue + today_count
        self.btn_process_all_due.setText(f"🚀 Process All Overdue + Due Today ({actionable})")
        self.btn_process_all_due.setEnabled(actionable > 0)

    def _set_filter(self, filter_name: str):
        """Set the active filter and repopulate table."""
        self.current_filter = filter_name
        self._populate_table()

    def _get_filtered_data(self) -> List[Dict[str, Any]]:
        """Return data filtered by current filter."""
        if self.current_filter == "overdue":
            return [r for r in self._refill_data if r["urgency"] == "OVERDUE"]
        elif self.current_filter == "today":
            return [r for r in self._refill_data if r["urgency"] in ("OVERDUE", "TODAY")]
        elif self.current_filter == "this_week":
            return [r for r in self._refill_data if r["urgency"] in ("OVERDUE", "TODAY", "THIS WEEK")]
        elif self.current_filter == "this_month":
            return [r for r in self._refill_data if r["urgency"] in ("OVERDUE", "TODAY", "THIS WEEK", "THIS MONTH")]
        else:  # "all_due"
            return list(self._refill_data)

    # ================================================================
    # Table Population
    # ================================================================

    def _populate_table(self):
        """Fill the table with filtered refill data."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        data = self._get_filtered_data()
        seen_orders = set()

        for item in data:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Checkbox — one per order
            order_id = item["order_id"]
            chk_item = QTableWidgetItem()
            if order_id not in seen_orders:
                chk_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                chk_item.setCheckState(Qt.CheckState.Unchecked)
                seen_orders.add(order_id)
            else:
                chk_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            chk_item.setData(Qt.ItemDataRole.UserRole, item)
            self.table.setItem(row, 0, chk_item)

            # Urgency badge
            urgency_item = QTableWidgetItem(item["urgency"])
            urgency_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            urgency_item.setForeground(QColor("white"))
            urgency_item.setBackground(QColor(item["urgency_color"]))
            urgency_item.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            self.table.setItem(row, 1, urgency_item)

            # Order #
            self.table.setItem(row, 2, QTableWidgetItem(item["display_number"]))

            # Patient (bold)
            patient_item = QTableWidgetItem(item["patient_name"])
            patient_item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            self.table.setItem(row, 3, patient_item)

            # DOB
            self.table.setItem(row, 4, QTableWidgetItem(item["patient_dob"]))

            # HCPCS
            self.table.setItem(row, 5, QTableWidgetItem(item["hcpcs"]))

            # Description
            self.table.setItem(row, 6, QTableWidgetItem(item["description"]))

            # Refills left
            refill_item = QTableWidgetItem(str(item["refills"]))
            refill_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 7, refill_item)

            # Day supply
            ds_item = QTableWidgetItem(str(item["day_supply"]))
            ds_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 8, ds_item)

            # Next due date
            due_item = QTableWidgetItem(item["next_due"])
            due_item.setForeground(QColor(item["urgency_color"]))
            due_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self.table.setItem(row, 9, due_item)

            # Days until
            days_text = str(item["days_until"])
            if item["days_until"] < 0:
                days_text = f"{item['days_until']} (OVERDUE)"
            days_item = QTableWidgetItem(days_text)
            days_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            days_item.setForeground(QColor(item["urgency_color"]))
            self.table.setItem(row, 10, days_item)

            # Prescriber
            self.table.setItem(row, 11, QTableWidgetItem(item["prescriber"]))

        self.table.setSortingEnabled(True)

        # Update summary
        total = len(self._refill_data)
        showing = len(data)
        filter_name = {
            "overdue": "Overdue only",
            "today": "Overdue + Due Today",
            "this_week": "Due within 7 days",
            "this_month": "Due within 30 days",
            "all_due": "All refillable items",
        }.get(self.current_filter, "")
        
        # Count unique orders
        unique_orders = len(set(d["order_id"] for d in data))
        unique_patients = len(set(d["patient_name"] for d in data))

        self.summary_label.setText(
            f"Showing {showing} items from {unique_orders} orders ({unique_patients} patients) — {filter_name}"
        )
        self.summary_label.setStyleSheet("color: #059669; font-weight: 500; font-size: 11px;")

    # ================================================================
    # Batch Processing
    # ================================================================

    def _get_checked_order_ids(self) -> List[int]:
        """Return order IDs of all checked rows."""
        order_ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data:
                    oid = data.get("order_id")
                    if oid and oid not in order_ids:
                        order_ids.append(oid)
        return order_ids

    def _open_orders_conn(self):
        """Open a connection to orders.db using this widget's configured path."""
        if self.orders_db_file:
            conn = sqlite3.connect(self.orders_db_file)
        else:
            conn = get_connection("orders.db", folder_path=self.folder_path)
        return conn

    def _remove_from_queue(self):
        """Temporarily remove (lock) the checked orders so they leave the queue.

        Reversible via the existing 'Unlock / Reset Refill' tool — the loader
        already excludes orders where is_locked = 1.
        """
        order_ids = self._get_checked_order_ids()
        if not order_ids:
            QMessageBox.information(
                self, "Remove from Queue",
                "No orders are checked. Tick the checkbox column to select orders first."
            )
            return

        reply = QMessageBox.question(
            self,
            "Remove from Queue",
            f"Hide {len(order_ids)} order(s) from the refill queue?\n\n"
            "This is reversible — use 'Unlock / Reset Refill' to bring them back.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = self._open_orders_conn()
            cur = conn.cursor()
            cur.executemany(
                "UPDATE orders SET is_locked = 1 WHERE id = ?",
                [(oid,) for oid in order_ids],
            )
            conn.commit()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self, "Remove from Queue", f"Failed to remove orders:\n{e}")
            return

        self.refresh_queue()
        self.summary_label.setText(f"Removed {len(order_ids)} order(s) from the queue")

    def _permanently_remove_from_queue(self):
        """Permanently remove the checked orders from refill automation.

        Marks the orders' refills as completed so the loader (which excludes
        refill_completed = 1) never surfaces them again.
        """
        order_ids = self._get_checked_order_ids()
        if not order_ids:
            QMessageBox.information(
                self, "Permanently Remove",
                "No orders are checked. Tick the checkbox column to select orders first."
            )
            return

        reply = QMessageBox.warning(
            self,
            "Permanently Remove from Queue",
            f"Permanently remove {len(order_ids)} order(s) from refill automation?\n\n"
            "Their refills will be marked completed and the queue will no longer "
            "track them. This is not auto-undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            conn = self._open_orders_conn()
            cur = conn.cursor()
            cur.executemany(
                "UPDATE orders SET refill_completed = 1 WHERE id = ?",
                [(oid,) for oid in order_ids],
            )
            conn.commit()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self, "Permanently Remove", f"Failed to remove orders:\n{e}")
            return

        self.refresh_queue()
        self.summary_label.setText(f"Permanently removed {len(order_ids)} order(s) from refill automation")

    def _process_all_due(self):
        """Process all overdue + due today refills in one click."""
        due_items = [
            r for r in self._refill_data
            if r["urgency"] in ("OVERDUE", "TODAY")
        ]
        if not due_items:
            QMessageBox.information(self, "Nothing Due", "No refills are overdue or due today.")
            return

        order_ids = list(set(r["order_id"] for r in due_items))
        patient_count = len(set(r["patient_name"] for r in due_items))

        reply = QMessageBox.question(
            self,
            "Process All Due Refills",
            f"Create refill orders for {len(order_ids)} order(s) "
            f"across {patient_count} patient(s)?\n\n"
            f"This includes all overdue and due-today items.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._batch_process(order_ids)

    def _process_selected(self):
        """Process only checked orders."""
        order_ids = self._get_checked_order_ids()
        if not order_ids:
            QMessageBox.warning(self, "No Selection", "Please check at least one order to process.")
            return

        reply = QMessageBox.question(
            self,
            "Process Selected Refills",
            f"Create refill orders for {len(order_ids)} selected order(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._batch_process(order_ids)

    def _batch_process(self, order_ids: List[int]):
        """Process a list of order IDs as refills."""
        progress = QProgressDialog("Creating refill orders...", "Cancel", 0, len(order_ids), self)
        progress.setWindowTitle("Batch Refill Processing")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        success_count = 0
        failed = []

        for i, oid in enumerate(order_ids):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(f"Processing order {oid}...")
            QApplication.processEvents()

            try:
                ok = self._create_single_refill(oid)
                if ok:
                    success_count += 1
                else:
                    failed.append(str(oid))
            except Exception as e:
                failed.append(f"{oid} ({e})")

        progress.setValue(len(order_ids))
        progress.close()

        # Show results
        msg = f"✅ Successfully created {success_count} refill order(s)."
        if failed:
            msg += f"\n\n❌ Failed ({len(failed)}):\n" + "\n".join(failed[:10])

        QMessageBox.information(self, "Batch Processing Complete", msg)

        # Refresh
        self.refresh_queue()
        self.refill_processed.emit()

    def _create_single_refill(self, order_id: int) -> bool:
        """Create a refill order for the given order_id. Returns True on success."""
        # Try the new refill service first
        try:
            from dmelogic.refill_service import process_refill
            result = process_refill(order_id, folder_path=self.folder_path)
            return result is not None
        except ValueError as e:
            err_str = str(e)
            if "ICD-10" in err_str or "diagnosis code" in err_str.lower():
                from PyQt6.QtWidgets import QMessageBox
                reply = QMessageBox.warning(
                    self,
                    "Diagnosis Code Missing",
                    f"Order {order_id} is missing an ICD-10 diagnosis code.\n\n"
                    f"Please open the order, add a diagnosis code, then try again.\n\n"
                    f"Would you like to open the order editor now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes and self.main_window:
                    if hasattr(self.main_window, "open_order_editor"):
                        self.main_window.open_order_editor(order_id)
                    elif hasattr(self.main_window, "edit_order_by_id"):
                        self.main_window.edit_order_by_id(order_id)
            else:
                raise
            return False
        except ImportError:
            pass

        # Fallback: use main_window's method if available
        if self.main_window and hasattr(self.main_window, "create_single_refill_order"):
            return self.main_window.create_single_refill_order(order_id)

        # Last resort: direct DB insert
        try:
            db_path = self.orders_db_file
            if not db_path:
                conn = get_connection("orders.db", folder_path=self.folder_path)
            else:
                conn = sqlite3.connect(db_path)

            cur = conn.cursor()

            # Get original order
            cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
            orig = cur.fetchone()
            if not orig:
                conn.close()
                return False

            # Build column names and values from original (exclude id, created_date)
            col_names = [desc[0] for desc in cur.description]
            orig_dict = dict(zip(col_names, orig))

            # Determine base order and next refill number
            base_id = orig_dict.get("parent_order_id") or order_id
            cur.execute(
                "SELECT COALESCE(MAX(refill_number), 0) FROM orders WHERE id = ? OR parent_order_id = ?",
                (base_id, base_id)
            )
            next_refill = (cur.fetchone()[0] or 0) + 1

            today_str = datetime.now().strftime("%Y-%m-%d")

            # Insert new order
            cur.execute("""
                INSERT INTO orders (
                    rx_date, order_date, patient_last_name, patient_first_name,
                    patient_dob, patient_address, patient_phone, patient_name,
                    patient_id, icd_code_1, icd_code_2, icd_code_3, icd_code_4, icd_code_5,
                    prescriber_name, prescriber_npi,
                    primary_insurance, primary_insurance_id,
                    secondary_insurance, secondary_insurance_id,
                    billing_selection, order_status,
                    parent_order_id, refill_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)
            """, (
                orig_dict.get("rx_date"),
                today_str,
                orig_dict.get("patient_last_name"),
                orig_dict.get("patient_first_name"),
                orig_dict.get("patient_dob"),
                orig_dict.get("patient_address"),
                orig_dict.get("patient_phone"),
                orig_dict.get("patient_name"),
                orig_dict.get("patient_id"),
                orig_dict.get("icd_code_1"),
                orig_dict.get("icd_code_2"),
                orig_dict.get("icd_code_3"),
                orig_dict.get("icd_code_4"),
                orig_dict.get("icd_code_5"),
                orig_dict.get("prescriber_name"),
                orig_dict.get("prescriber_npi"),
                orig_dict.get("primary_insurance"),
                orig_dict.get("primary_insurance_id"),
                orig_dict.get("secondary_insurance"),
                orig_dict.get("secondary_insurance_id"),
                orig_dict.get("billing_selection"),
                base_id,
                next_refill,
            ))
            new_order_id = cur.lastrowid

            # Copy order items (decrement refills on source)
            cur.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,))
            item_cols = [desc[0] for desc in cur.description]
            for item_row in cur.fetchall():
                item_dict = dict(zip(item_cols, item_row))
                cur.execute("""
                    INSERT INTO order_items (
                        order_id, rx_no, hcpcs_code, description, item_number,
                        refills, day_supply, qty, cost_ea, total,
                        pa_number, directions
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    new_order_id,
                    item_dict.get("rx_no"),
                    item_dict.get("hcpcs_code"),
                    item_dict.get("description"),
                    item_dict.get("item_number"),
                    item_dict.get("refills"),
                    item_dict.get("day_supply"),
                    item_dict.get("qty"),
                    item_dict.get("cost_ea"),
                    item_dict.get("total"),
                    item_dict.get("pa_number"),
                    item_dict.get("directions"),
                ))

                # Decrement refills on source item
                src_refills = int(item_dict.get("refills") or 0)
                if src_refills > 0:
                    cur.execute("""
                        UPDATE order_items
                        SET refills = ?, last_filled_date = ?
                        WHERE rowid = ?
                    """, (src_refills - 1, today_str, item_dict.get("rowid")))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            debug_log(f"RefillQueue: failed to create refill for order {order_id}: {e}")
            print(f"RefillQueue refill error: {e}")
            return False

    # ================================================================
    # UI Helpers
    # ================================================================

    def _toggle_select_all(self, state):
        """Toggle all visible checkboxes."""
        check = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                item.setCheckState(check)

    def _on_double_click(self, row: int, col: int):
        """Double-click a row to open that order."""
        item = self.table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        order_id = data.get("order_id")
        if order_id and self.main_window:
            # Try to select and edit the order
            if hasattr(self.main_window, "edit_order_by_id"):
                try:
                    self.main_window.edit_order_by_id(order_id)
                except Exception:
                    pass

    def _export_pdf(self):
        """Export current view to PDF."""
        if self.main_window and hasattr(self.main_window, "export_refills_pdf"):
            # Temporarily swap the refills_table reference
            old_table = getattr(self.main_window, "refills_table", None)
            self.main_window.refills_table = self.table
            try:
                self.main_window.export_refills_pdf()
            finally:
                if old_table is not None:
                    self.main_window.refills_table = old_table
        else:
            QMessageBox.information(self, "Export", "PDF export will be available after integration.")

    # ================================================================
    # Manual Override / Unlock Dialog
    # ================================================================

    def _show_refill_override_dialog(self):
        """Show a dialog to search for any order by patient name or order number and
        reset its refill_completed / is_locked flags, or mark it as fully refilled."""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
            QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
            QMessageBox, QRadioButton, QButtonGroup, QGroupBox,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("🔓 Refill Override — Unlock or Reset Orders")
        dlg.resize(800, 500)
        layout = QVBoxLayout(dlg)

        # Instructions
        info = QLabel(
            "Search for an order by patient name or order number.\n"
            "Then choose to UNLOCK it (allow re-refill) or MARK AS FULLY REFILLED (hide from queue)."
        )
        info.setStyleSheet("color: #374151; font-size: 12px; padding: 6px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Search bar
        search_row = QHBoxLayout()
        search_input = QLineEdit()
        search_input.setPlaceholderText("Enter patient last name, first name, or order number (e.g., 275)...")
        search_input.setStyleSheet("padding: 8px; border: 1px solid #D1D5DB; border-radius: 6px; font-size: 13px;")
        search_row.addWidget(search_input)
        search_btn = QPushButton("🔍 Search")
        search_btn.setStyleSheet(
            "background: #3B82F6; color: white; padding: 8px 16px; border-radius: 6px; font-weight: 600;"
        )
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        # Results table
        results_table = QTableWidget()
        results_table.setColumnCount(8)
        results_table.setHorizontalHeaderLabels([
            "Order #", "Patient", "RX Date", "Status",
            "Refill #", "Items Refills", "Locked", "Completed"
        ])
        results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        results_table.setSortingEnabled(True)
        hdr = results_table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(results_table, 1)

        # Action group
        action_box = QGroupBox("Action")
        action_layout = QHBoxLayout(action_box)
        rb_unlock = QRadioButton("🔓 Unlock — Clear flags, allow re-refill")
        rb_mark_done = QRadioButton("🚫 Mark Fully Refilled — Hide from queue")
        rb_unlock.setChecked(True)
        action_layout.addWidget(rb_unlock)
        action_layout.addWidget(rb_mark_done)
        layout.addWidget(action_box)

        # Buttons
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("✅ Apply to Selected Order")
        btn_apply.setStyleSheet(
            "background: #059669; color: white; padding: 10px 20px; border-radius: 6px; font-weight: 700; font-size: 13px;"
        )
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("padding: 10px 20px;")
        btn_row.addStretch()
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        btn_close.clicked.connect(dlg.reject)

        def do_search():
            query = search_input.text().strip()
            if not query:
                return

            try:
                db_path = self.orders_db_file
                if not db_path:
                    conn = get_connection("orders.db", folder_path=self.folder_path)
                else:
                    conn = sqlite3.connect(db_path)
                cur = conn.cursor()

                # Search by order id number or patient name
                try:
                    order_num = int(query.replace("ORD-", "").replace("ORD", "").split("-")[0].strip())
                    cur.execute("""
                        SELECT o.id, o.patient_last_name, o.patient_first_name, o.rx_date,
                               o.order_status, COALESCE(o.refill_number, 0),
                               (SELECT GROUP_CONCAT(CAST(COALESCE(oi.refills, '0') AS INTEGER))
                                FROM order_items oi WHERE oi.order_id = o.id) AS item_refills,
                               COALESCE(o.is_locked, 0), COALESCE(o.refill_completed, 0)
                        FROM orders o
                        WHERE o.id = ? OR o.parent_order_id = ?
                        ORDER BY o.id ASC
                    """, (order_num, order_num))
                except ValueError:
                    order_num = None
                    cur.execute("""
                        SELECT o.id, o.patient_last_name, o.patient_first_name, o.rx_date,
                               o.order_status, COALESCE(o.refill_number, 0),
                               (SELECT GROUP_CONCAT(CAST(COALESCE(oi.refills, '0') AS INTEGER))
                                FROM order_items oi WHERE oi.order_id = o.id) AS item_refills,
                               COALESCE(o.is_locked, 0), COALESCE(o.refill_completed, 0)
                        FROM orders o
                        WHERE UPPER(o.patient_last_name) LIKE UPPER(?) OR UPPER(o.patient_first_name) LIKE UPPER(?)
                        ORDER BY o.patient_last_name, o.id ASC
                        LIMIT 50
                    """, (f"%{query}%", f"%{query}%"))

                rows = cur.fetchall()
                conn.close()

                results_table.setSortingEnabled(False)
                results_table.setRowCount(len(rows))
                for r, (oid, lname, fname, rx_date, status, rno, item_refills, locked, completed) in enumerate(rows):
                    patient = f"{lname or ''}, {fname or ''}".strip(", ")
                    rno_int = int(rno or 0)
                    display = f"ORD-{oid:03d}"

                    results_table.setItem(r, 0, QTableWidgetItem(display))
                    results_table.setItem(r, 1, QTableWidgetItem(patient))
                    results_table.setItem(r, 2, QTableWidgetItem(rx_date or ""))
                    results_table.setItem(r, 3, QTableWidgetItem(status or "Pending"))
                    results_table.setItem(r, 4, QTableWidgetItem(str(rno_int)))
                    results_table.setItem(r, 5, QTableWidgetItem(item_refills or "0"))
                    results_table.setItem(r, 6, QTableWidgetItem("🔒 Yes" if locked else "No"))
                    results_table.setItem(r, 7, QTableWidgetItem("✅ Yes" if completed else "No"))
                    results_table.item(r, 0).setData(Qt.ItemDataRole.UserRole, oid)

                    # Highlight locked/completed rows
                    if locked or completed:
                        for c in range(8):
                            it = results_table.item(r, c)
                            if it:
                                it.setBackground(QColor("#FEF3C7"))

                results_table.setSortingEnabled(True)
                try:
                    results_table.resizeColumnsToContents()
                except Exception:
                    pass

            except Exception as e:
                QMessageBox.warning(dlg, "Search Error", f"Failed to search orders:\n\n{e}")

        def do_apply():
            row = results_table.currentRow()
            if row < 0:
                QMessageBox.warning(dlg, "No Selection", "Please select an order from the search results.")
                return
            item = results_table.item(row, 0)
            if not item:
                return
            oid = item.data(Qt.ItemDataRole.UserRole)
            display = item.text()

            try:
                db_path = self.orders_db_file
                if not db_path:
                    conn = get_connection("orders.db", folder_path=self.folder_path)
                else:
                    conn = sqlite3.connect(db_path)
                cur = conn.cursor()

                if rb_unlock.isChecked():
                    # UNLOCK: clear refill_completed and is_locked
                    confirm = QMessageBox.question(
                        dlg, "Confirm Unlock",
                        f"Unlock {display}?\n\n"
                        "This will clear the refill_completed and is_locked flags,\n"
                        "allowing this order to be refilled again.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes,
                    )
                    if confirm != QMessageBox.StandardButton.Yes:
                        conn.close()
                        return
                    cur.execute(
                        "UPDATE orders SET refill_completed = 0, refill_completed_at = NULL, is_locked = 0 WHERE id = ?",
                        (oid,),
                    )
                    conn.commit()
                    conn.close()
                    QMessageBox.information(dlg, "Unlocked", f"{display} has been unlocked and can now be refilled.")
                else:
                    # MARK AS FULLY REFILLED: set flags
                    confirm = QMessageBox.question(
                        dlg, "Confirm Mark Complete",
                        f"Mark {display} as fully refilled?\n\n"
                        "This will set refill_completed = 1 and is_locked = 1,\n"
                        "removing it from the refill queue.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes,
                    )
                    if confirm != QMessageBox.StandardButton.Yes:
                        conn.close()
                        return
                    cur.execute(
                        "UPDATE orders SET refill_completed = 1, refill_completed_at = datetime('now'), is_locked = 1 WHERE id = ?",
                        (oid,),
                    )
                    conn.commit()
                    conn.close()
                    QMessageBox.information(dlg, "Marked Complete", f"{display} has been marked as fully refilled.")

                # Refresh search results
                do_search()
                # Refresh main queue
                self.refresh_queue()

            except Exception as e:
                QMessageBox.critical(dlg, "Error", f"Failed to update order:\n\n{e}")

        search_btn.clicked.connect(do_search)
        search_input.returnPressed.connect(do_search)
        btn_apply.clicked.connect(do_apply)

        dlg.exec()
