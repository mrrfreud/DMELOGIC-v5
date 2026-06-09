"""
Dashboard Overview Widget
=========================
At-a-glance status view showing inventory stats, order pipeline,
alerts, and recent activity.

Feature #5: Dashboard Overview

Integration:
    In your tab setup (where you create the QTabWidget), add:
        from dmelogic.ui.dashboard_widget import DashboardWidget
        self.dashboard_widget = DashboardWidget(folder_path=self.folder_path, parent=self)
        self.tabs.insertTab(0, self.dashboard_widget, "📊 Dashboard")
        self.tabs.setCurrentIndex(0)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, date
from typing import Optional, Dict, Any, List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGridLayout, QScrollArea, QPushButton, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush

from dmelogic.db.base import get_connection


# ═══════════════════════════════════════════════════════════════════
# Stat Card Widget
# ═══════════════════════════════════════════════════════════════════


class StatCard(QFrame):
    """Single KPI stat card with icon, value, and subtitle."""

    def __init__(
        self,
        title: str,
        value: str,
        subtitle: str = "",
        icon: str = "📦",
        accent_color: str = "#4F6EF7",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.setStyleSheet(f"""
            QFrame#StatCard {{
                background-color: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
                padding: 16px;
            }}
            QFrame#StatCard:hover {{
                border-color: {accent_color};
                box-shadow: 0 2px 8px rgba(0,0,0,0.06);
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        # Icon
        icon_label = QLabel(icon)
        icon_label.setFont(QFont("Segoe UI Emoji", 22))
        icon_label.setFixedSize(48, 48)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"""
            background-color: {accent_color}20;
            border-radius: 10px;
        """)
        layout.addWidget(icon_label)

        # Text column
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        title_label = QLabel(title.upper())
        title_label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        title_label.setStyleSheet("color: #6B7280; letter-spacing: 1px; border: none;")
        text_layout.addWidget(title_label)

        self.value_label = QLabel(value)
        self.value_label.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self.value_label.setStyleSheet(f"color: #111827; border: none;")
        text_layout.addWidget(self.value_label)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setFont(QFont("Segoe UI", 9))
        self.subtitle_label.setStyleSheet("color: #9CA3AF; border: none;")
        text_layout.addWidget(self.subtitle_label)

        layout.addLayout(text_layout, 1)

    def update_value(self, value: str, subtitle: str = ""):
        self.value_label.setText(value)
        if subtitle:
            self.subtitle_label.setText(subtitle)


# ═══════════════════════════════════════════════════════════════════
# Alert Row Widget
# ═══════════════════════════════════════════════════════════════════


class AlertRow(QFrame):
    """Single alert/notification row."""

    def __init__(self, icon: str, message: str, detail: str, severity: str = "warning", parent=None):
        super().__init__(parent)
        colors = {
            "warning": ("#FEF3C7", "#92400E", "#F59E0B"),
            "danger": ("#FEE2E2", "#991B1B", "#EF4444"),
            "info": ("#DBEAFE", "#1E40AF", "#3B82F6"),
            "success": ("#D1FAE5", "#065F46", "#10B981"),
        }
        bg, fg, border = colors.get(severity, colors["info"])

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg};
                border: 1px solid {border}40;
                border-left: 3px solid {border};
                border-radius: 6px;
                padding: 8px 12px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        lbl_icon = QLabel(icon)
        lbl_icon.setFont(QFont("Segoe UI Emoji", 13))
        lbl_icon.setFixedWidth(24)
        layout.addWidget(lbl_icon)

        lbl_msg = QLabel(message)
        lbl_msg.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        lbl_msg.setStyleSheet(f"color: {fg}; border: none;")
        layout.addWidget(lbl_msg, 1)

        lbl_detail = QLabel(detail)
        lbl_detail.setFont(QFont("Segoe UI", 9))
        lbl_detail.setStyleSheet(f"color: {fg}99; border: none;")
        layout.addWidget(lbl_detail)


# ═══════════════════════════════════════════════════════════════════
# Dashboard Widget
# ═══════════════════════════════════════════════════════════════════


class DashboardWidget(QWidget):
    """
    Full dashboard tab showing:
    - KPI stat cards (total inventory, order pipeline, alerts, revenue)
    - Stock alerts (low stock / out of stock)
    - Recent orders table
    - Order status breakdown
    """

    def __init__(self, folder_path: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self.main_window = parent

        self._setup_ui()

        # Auto-refresh every 60 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_data)
        self._refresh_timer.start(60_000)

        # Initial load
        QTimer.singleShot(100, self.refresh_data)

    def _setup_ui(self):
        # Scrollable container
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #F9FAFB; border: none; }")

        content = QWidget()
        content.setStyleSheet("background: #F9FAFB;")
        self.main_layout = QVBoxLayout(content)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("📊 Dashboard Overview")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #111827;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.last_updated_label = QLabel("")
        self.last_updated_label.setFont(QFont("Segoe UI", 9))
        self.last_updated_label.setStyleSheet("color: #9CA3AF;")
        header_layout.addWidget(self.last_updated_label)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background: #EFF6FF;
                border: 1px solid #BFDBFE;
                border-radius: 6px;
                padding: 5px 14px;
                color: #1D4ED8;
                font-weight: 500;
            }
            QPushButton:hover { background: #DBEAFE; }
        """)
        refresh_btn.clicked.connect(self.refresh_data)
        header_layout.addWidget(refresh_btn)

        self.main_layout.addLayout(header_layout)

        # Stat cards row
        self.cards_layout = QHBoxLayout()
        self.cards_layout.setSpacing(12)

        self.card_inventory = StatCard("Total Items", "—", "Loading…", "📦", "#4F6EF7")
        self.card_orders = StatCard("Open Orders", "—", "Loading…", "📋", "#F59E0B")
        self.card_alerts = StatCard("Alerts", "—", "Loading…", "⚠️", "#EF4444")
        self.card_delivered = StatCard("Delivered (MTD)", "—", "Loading…", "✅", "#10B981")

        self.cards_layout.addWidget(self.card_inventory)
        self.cards_layout.addWidget(self.card_orders)
        self.cards_layout.addWidget(self.card_alerts)
        self.cards_layout.addWidget(self.card_delivered)

        self.main_layout.addLayout(self.cards_layout)

        # Alerts section
        self.alerts_container = QVBoxLayout()
        self.alerts_container.setSpacing(6)

        alerts_header = QLabel("⚠️  Stock Alerts")
        alerts_header.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        alerts_header.setStyleSheet("color: #374151;")
        self.alerts_container.addWidget(alerts_header)

        self.main_layout.addLayout(self.alerts_container)

        # Bottom row: Recent Orders + Order Status
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(16)

        # Recent Orders
        recent_frame = QFrame()
        recent_frame.setStyleSheet("""
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
        """)
        recent_layout = QVBoxLayout(recent_frame)
        recent_layout.setContentsMargins(16, 16, 16, 16)

        recent_title = QLabel("📋  Recent Orders")
        recent_title.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        recent_title.setStyleSheet("color: #374151; border: none;")
        recent_layout.addWidget(recent_title)

        self.recent_orders_table = QTableWidget()
        self.recent_orders_table.setColumnCount(5)
        self.recent_orders_table.setHorizontalHeaderLabels(["Order #", "Patient", "Status", "Date", "Total"])
        self.recent_orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.recent_orders_table.verticalHeader().setVisible(False)
        self.recent_orders_table.setAlternatingRowColors(True)
        self.recent_orders_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recent_orders_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent_orders_table.setMaximumHeight(240)
        self.recent_orders_table.setStyleSheet("""
            QTableWidget { border: none; background: transparent; }
            QHeaderView::section { 
                background: #F3F4F6; border: none; padding: 6px;
                font-weight: 600; color: #374151;
            }
        """)
        recent_layout.addWidget(self.recent_orders_table)
        bottom_row.addWidget(recent_frame, 3)

        # Order Status Breakdown
        status_frame = QFrame()
        status_frame.setStyleSheet("""
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
        """)
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(16, 16, 16, 16)

        status_title = QLabel("📊  Order Pipeline")
        status_title.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        status_title.setStyleSheet("color: #374151; border: none;")
        status_layout.addWidget(status_title)

        self.status_bars_layout = QVBoxLayout()
        self.status_bars_layout.setSpacing(8)
        status_layout.addLayout(self.status_bars_layout)
        status_layout.addStretch()

        bottom_row.addWidget(status_frame, 2)
        self.main_layout.addLayout(bottom_row)

        self.main_layout.addStretch()

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------ Data

    def refresh_data(self):
        """Reload all dashboard data from DBs."""
        try:
            inv_stats = self._get_inventory_stats()
            order_stats = self._get_order_stats()
            recent_orders = self._get_recent_orders()
            alerts = self._get_stock_alerts()

            # Update stat cards
            self.card_inventory.update_value(
                f"{inv_stats['total_skus']:,}",
                f"{inv_stats['total_qty']:,} total units"
            )
            self.card_orders.update_value(
                str(order_stats.get("open", 0)),
                f"{order_stats.get('pending', 0)} pending, {order_stats.get('unbilled', 0)} unbilled"
            )
            alert_count = len(alerts)
            self.card_alerts.update_value(
                str(alert_count),
                f"{inv_stats['low_stock']} low stock, {inv_stats['out_of_stock']} out"
            )
            self.card_delivered.update_value(
                str(order_stats.get("delivered_mtd", 0)),
                f"This month"
            )

            # Update alerts
            self._update_alerts(alerts)

            # Update recent orders table
            self._update_recent_orders(recent_orders)

            # Update status breakdown
            self._update_status_bars(order_stats.get("by_status", {}))

            self.last_updated_label.setText(
                f"Updated: {datetime.now().strftime('%I:%M %p')}"
            )
        except Exception as e:
            print(f"Dashboard refresh error: {e}")

    def _get_inventory_stats(self) -> Dict[str, Any]:
        stats = {"total_skus": 0, "total_qty": 0, "low_stock": 0, "out_of_stock": 0, "total_value": 0}
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) as cnt FROM inventory")
            stats["total_skus"] = cur.fetchone()["cnt"]

            cur.execute("SELECT COALESCE(SUM(stock_quantity), 0) as total FROM inventory")
            stats["total_qty"] = cur.fetchone()["total"]

            cur.execute("""
                SELECT COUNT(*) as cnt FROM inventory
                WHERE stock_quantity > 0
                  AND reorder_level > 0
                  AND stock_quantity <= reorder_level
            """)
            stats["low_stock"] = cur.fetchone()["cnt"]

            cur.execute("""
                SELECT COUNT(*) as cnt FROM inventory
                WHERE stock_quantity IS NOT NULL AND stock_quantity <= 0
            """)
            stats["out_of_stock"] = cur.fetchone()["cnt"]

            conn.close()
        except Exception as e:
            print(f"Dashboard inventory stats error: {e}")
        return stats

    def _get_order_stats(self) -> Dict[str, Any]:
        stats = {"open": 0, "pending": 0, "unbilled": 0, "delivered_mtd": 0, "by_status": {}}
        try:
            conn = get_connection("orders.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Status counts
            cur.execute("""
                SELECT order_status, COUNT(*) as cnt
                FROM orders
                GROUP BY order_status
            """)
            for row in cur.fetchall():
                status = row["order_status"] or "Unknown"
                count = row["cnt"]
                stats["by_status"][status] = count

            stats["pending"] = stats["by_status"].get("Pending", 0)
            stats["unbilled"] = stats["by_status"].get("Unbilled", 0)
            stats["open"] = sum(
                stats["by_status"].get(s, 0)
                for s in ["Pending", "Unbilled", "Verified", "Approved", "Ready", "Docs Needed", "On Hold"]
            )

            # Delivered this month
            first_of_month = date.today().replace(day=1).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT COUNT(*) as cnt FROM orders
                WHERE order_status = 'Delivered'
                  AND delivery_date >= ?
            """, (first_of_month,))
            stats["delivered_mtd"] = cur.fetchone()["cnt"]

            conn.close()
        except Exception as e:
            print(f"Dashboard order stats error: {e}")
        return stats

    def _get_recent_orders(self, limit: int = 10) -> List[Dict[str, Any]]:
        orders = []
        try:
            conn = get_connection("orders.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT id, patient_name, order_status, order_date, billing_type
                FROM orders
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            orders = [dict(r) for r in cur.fetchall()]
            conn.close()
        except Exception:
            pass
        return orders

    def _get_stock_alerts(self) -> List[Dict[str, Any]]:
        alerts = []
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT hcpcs_code, description, stock_quantity, reorder_level
                FROM inventory
                WHERE (stock_quantity IS NOT NULL AND stock_quantity <= 0)
                   OR (reorder_level > 0 AND stock_quantity <= reorder_level)
                ORDER BY stock_quantity ASC
                LIMIT 15
            """)
            alerts = [dict(r) for r in cur.fetchall()]
            conn.close()
        except Exception:
            pass
        return alerts

    # ------------------------------------------------------------------ UI Updates

    def _update_alerts(self, alerts: List[Dict[str, Any]]):
        # Clear existing alerts (keep the header at index 0)
        while self.alerts_container.count() > 1:
            item = self.alerts_container.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        if not alerts:
            no_alert = QLabel("✅ All inventory levels are healthy")
            no_alert.setFont(QFont("Segoe UI", 10))
            no_alert.setStyleSheet("color: #059669; padding: 8px;")
            self.alerts_container.addWidget(no_alert)
            return

        for a in alerts[:8]:
            hcpcs = a.get("hcpcs_code", "")
            desc = a.get("description", "")
            stock = a.get("stock_quantity", 0)
            reorder = a.get("reorder_level", 0)

            if stock is not None and stock <= 0:
                severity = "danger"
                icon = "🔴"
                msg = f"{hcpcs} — {desc}"
                detail = "OUT OF STOCK"
            else:
                severity = "warning"
                icon = "🟡"
                msg = f"{hcpcs} — {desc}"
                detail = f"Stock: {stock} / Reorder at: {reorder}"

            row = AlertRow(icon, msg, detail, severity)
            self.alerts_container.addWidget(row)

    def _update_recent_orders(self, orders: List[Dict[str, Any]]):
        self.recent_orders_table.setRowCount(len(orders))
        status_colors = {
            "Pending": "#F59E0B",
            "Unbilled": "#EF4444",
            "Delivered": "#10B981",
            "Shipped": "#3B82F6",
            "Billed": "#8B5CF6",
            "Paid": "#059669",
            "Cancelled": "#6B7280",
            "On Hold": "#F97316",
        }

        for i, order in enumerate(orders):
            oid = order.get("id", "")
            name = order.get("patient_name", "Unknown")
            status = order.get("order_status", "")
            odate = order.get("order_date", "")
            billing = order.get("billing_type", "")

            self.recent_orders_table.setItem(i, 0, QTableWidgetItem(f"ORD-{oid:03d}" if isinstance(oid, int) else str(oid)))
            self.recent_orders_table.setItem(i, 1, QTableWidgetItem(name))

            status_item = QTableWidgetItem(status)
            color = status_colors.get(status, "#6B7280")
            status_item.setForeground(QBrush(QColor(color)))
            status_item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            self.recent_orders_table.setItem(i, 2, status_item)

            self.recent_orders_table.setItem(i, 3, QTableWidgetItem(odate))
            self.recent_orders_table.setItem(i, 4, QTableWidgetItem(billing))

    def _update_status_bars(self, by_status: Dict[str, int]):
        # Clear existing bars
        while self.status_bars_layout.count() > 0:
            item = self.status_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not by_status:
            return

        total = sum(by_status.values()) or 1
        colors = {
            "Pending": "#F59E0B", "Unbilled": "#EF4444", "Delivered": "#10B981",
            "Shipped": "#3B82F6", "Billed": "#8B5CF6", "Paid": "#059669",
            "On Hold": "#F97316", "Cancelled": "#9CA3AF", "Verified": "#06B6D4",
        }

        for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            label = QLabel(status)
            label.setFixedWidth(90)
            label.setFont(QFont("Segoe UI", 9))
            label.setStyleSheet("color: #374151; border: none;")
            row_layout.addWidget(label)

            # Bar
            bar_bg = QFrame()
            bar_bg.setFixedHeight(8)
            bar_bg.setStyleSheet("background: #E5E7EB; border-radius: 4px; border: none;")

            bar_fill = QFrame(bar_bg)
            pct = max(2, int((count / total) * 100))
            color = colors.get(status, "#6B7280")
            bar_fill.setStyleSheet(f"background: {color}; border-radius: 4px; border: none;")
            bar_fill.setFixedHeight(8)
            bar_fill.setFixedWidth(max(4, int((count / total) * 200)))

            row_layout.addWidget(bar_bg, 1)

            count_label = QLabel(str(count))
            count_label.setFixedWidth(36)
            count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            count_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            count_label.setStyleSheet(f"color: {color}; border: none;")
            row_layout.addWidget(count_label)

            self.status_bars_layout.addWidget(row)
