"""
Command Bar / Quick Launcher (Ctrl+K)
=====================================
Global search palette that finds inventory items, orders, patients,
and navigates to any tab — inspired by VS Code / Spotlight.

Feature #4: Command Bar with Global Search

Integration:
    In MainWindow.__init__(), add:
        from dmelogic.ui.command_bar import CommandBar
        self.command_bar = CommandBar(self)
"""

from __future__ import annotations

import sqlite3
from typing import Optional, List, Dict, Any, Callable

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QListWidget, QListWidgetItem, QWidget, QApplication,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QShortcut, QKeySequence, QKeyEvent

from dmelogic.db.base import get_connection


class CommandBarResult:
    """Single search result item."""

    def __init__(
        self,
        icon: str,
        title: str,
        subtitle: str,
        category: str,
        action: Optional[Callable] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        self.icon = icon
        self.title = title
        self.subtitle = subtitle
        self.category = category
        self.action = action
        self.data = data


class CommandBar(QDialog):
    """
    Quick-launch command bar accessible via Ctrl+K.

    Searches across:
    - Navigation (tabs)
    - Inventory items (HCPCS, description)
    - Orders (order #, patient name)
    - Patients (name, DOB)
    - Actions (New Order, Add Item, etc.)

    Usage:
        bar = CommandBar(main_window)
        # Ctrl+K shortcut is auto-registered on the parent.
    """

    result_activated = pyqtSignal(object)  # emits CommandBarResult

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.main_window = parent

        self.setWindowTitle("Quick Search")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setModal(True)
        self.resize(620, 460)

        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)
        self._debounce_timer.timeout.connect(self._do_search)

        self._setup_ui()
        self._register_shortcut()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container with border
        container = QWidget()
        container.setObjectName("CommandBarContainer")
        container.setStyleSheet("""
            QWidget#CommandBarContainer {
                background-color: #1E1E2E;
                border: 1px solid #45475A;
                border-radius: 12px;
            }
        """)
        clayout = QVBoxLayout(container)
        clayout.setContentsMargins(12, 12, 12, 12)
        clayout.setSpacing(8)

        # Search input
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        icon_label = QLabel("\U0001f50d")
        icon_label.setStyleSheet("font-size: 18px;")
        icon_label.setFixedWidth(28)
        search_row.addWidget(icon_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search patients, orders, inventory, or type a command\u2026")
        self.search_input.setFont(QFont("Segoe UI", 13))
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: #CDD6F4;
                padding: 6px 0px;
                font-size: 14px;
            }
        """)
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.installEventFilter(self)
        search_row.addWidget(self.search_input, 1)

        shortcut_label = QLabel("Ctrl+K")
        shortcut_label.setStyleSheet("""
            QLabel {
                background: #313244;
                color: #6C7086;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-family: Consolas;
            }
        """)
        search_row.addWidget(shortcut_label)

        clayout.addLayout(search_row)

        # Separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #313244;")
        clayout.addWidget(sep)

        # Results list
        self.results_list = QListWidget()
        self.results_list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                color: #CDD6F4;
                outline: none;
                font-size: 11pt;
            }
            QListWidget::item {
                padding: 10px 12px;
                border-radius: 6px;
                margin: 1px 0px;
                color: #CDD6F4;
            }
            QListWidget::item:selected {
                background-color: #313244;
                color: #FFFFFF;
            }
            QListWidget::item:hover {
                background-color: #2A2A3C;
                color: #FFFFFF;
            }
        """)
        self.results_list.setFont(QFont("Segoe UI", 11))
        self.results_list.itemActivated.connect(self._on_item_activated)
        self.results_list.itemDoubleClicked.connect(self._on_item_activated)
        clayout.addWidget(self.results_list, 1)

        # Hint bar
        hint = QLabel("↑↓ Navigate  •  Enter Select  •  Esc Close")
        hint.setStyleSheet("color: #585B70; font-size: 10px; padding: 4px 0;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clayout.addWidget(hint)

        layout.addWidget(container)

        # Show default items
        self._show_defaults()

    def _register_shortcut(self):
        """Register Ctrl+K on the parent window to open the bar."""
        try:
            shortcut = QShortcut(QKeySequence("Ctrl+K"), self.main_window)
            shortcut.activated.connect(self.toggle)
        except Exception:
            pass

    # ------------------------------------------------------------------ Show / Hide

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_bar()

    def show_bar(self):
        """Open the command bar centered over the parent."""
        self.search_input.clear()
        self._show_defaults()

        parent = self.main_window
        if parent:
            geo = parent.geometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + int(geo.height() * 0.12)
            self.move(x, y)

        self.show()
        self.raise_()
        self.search_input.setFocus()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        elif event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.results_list.setFocus()
            self.results_list.keyPressEvent(event)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current = self.results_list.currentItem()
            if current:
                self._on_item_activated(current)
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj == self.search_input and hasattr(event, 'key'):
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.results_list.setFocus()
                if key == Qt.Key.Key_Down and self.results_list.count() > 0:
                    row = self.results_list.currentRow()
                    self.results_list.setCurrentRow(min(row + 1, self.results_list.count() - 1))
                elif key == Qt.Key.Key_Up and self.results_list.count() > 0:
                    row = self.results_list.currentRow()
                    self.results_list.setCurrentRow(max(row - 1, 0))
                return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                current = self.results_list.currentItem()
                if current:
                    self._on_item_activated(current)
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ Search

    def _on_text_changed(self, text: str):
        self._debounce_timer.start()

    def _do_search(self):
        query = self.search_input.text().strip()
        if not query:
            self._show_defaults()
            return

        results: List[CommandBarResult] = []

        # 1) Navigation commands
        results.extend(self._search_navigation(query))

        # 2) Actions
        results.extend(self._search_actions(query))

        # 3) Patients (most common search target)
        results.extend(self._search_patients(query))

        # 4) Orders
        results.extend(self._search_orders(query))

        # 5) Inventory
        results.extend(self._search_inventory(query))

        # 6) Prescribers
        results.extend(self._search_prescribers(query))

        self._display_results(results)

    def _show_defaults(self):
        """Show default navigation + recent actions."""
        defaults = self._get_navigation_items() + self._get_action_items()
        self._display_results(defaults)

    def _display_results(self, results: List[CommandBarResult]):
        self.results_list.clear()
        if not results:
            item = QListWidgetItem("    No results found")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setForeground(QColor("#585B70"))
            self.results_list.addItem(item)
            return

        last_category = ""
        for r in results[:25]:  # Cap at 25
            # Category header
            if r.category != last_category:
                last_category = r.category
                header = QListWidgetItem(f"  {r.category.upper()}")
                header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                header.setForeground(QColor("#6C7086"))
                hfont = QFont("Segoe UI", 8)
                hfont.setBold(True)
                header.setFont(hfont)
                self.results_list.addItem(header)

            text = f"  {r.icon}  {r.title}"
            if r.subtitle:
                text += f"   \u2014  {r.subtitle}"

            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setForeground(QColor("#CDD6F4"))
            self.results_list.addItem(item)

        # Select first selectable item
        for i in range(self.results_list.count()):
            itm = self.results_list.item(i)
            if itm and itm.flags() & Qt.ItemFlag.ItemIsSelectable:
                self.results_list.setCurrentRow(i)
                break

    # ------------------------------------------------------------------ Sources

    def _get_navigation_items(self) -> List[CommandBarResult]:
        """Tab navigation entries matching actual main_tabs."""
        tabs = [
            ("\U0001f4c4", "Document Viewer", "View & index documents"),
            ("\U0001f465", "Patients", "Patient management"),
            ("\U0001f3e5", "Prescribers", "Prescriber lookup"),
            ("\U0001f3e2", "Clinics", "Clinic management"),
            ("\U0001f4e6", "DME Inventory", "Quick inventory view"),
            ("\U0001f4cb", "Orders", "View & track orders"),
            ("\u26a0\ufe0f", "Must Go Out", "Urgent deliveries"),
            ("\U0001f4e6", "Inventory", "Full inventory management"),
            ("\U0001f4b3", "Billing", "Billing & claims"),
            ("\U0001f4ca", "Reports", "Analytics & reports"),
            ("\u267b\ufe0f", "Process Refills", "Refill processing"),
            ("\U0001f4c5", "Fee Schedule", "Fee schedule lookup"),
            ("\U0001f4cb", "Queues", "Work queues"),
            ("\u2705", "Tasks", "Task management"),
            ("\U0001f3e5", "ICD-10", "Diagnosis codes"),
        ]
        results = []
        for icon, name, desc in tabs:
            results.append(CommandBarResult(
                icon=icon, title=f"Go to {name}", subtitle=desc,
                category="Navigation",
                action=lambda t=name: self._navigate_to_tab(t),
            ))
        return results

    def _get_action_items(self) -> List[CommandBarResult]:
        """Quick action entries."""
        actions = [
            ("\U0001f6d2", "New Order", "Create a new DME order", "new_order"),
            ("\u2795", "Add Patient", "Add a new patient", "new_patient"),
            ("\u2795", "Add Inventory Item", "Add item to inventory", "add_inventory"),
            ("\U0001f4dd", "Sticky Notes", "Open sticky notes board", "sticky_notes"),
            ("\u2699\ufe0f", "Settings", "Open application settings", "settings"),
        ]
        results = []
        for icon, name, desc, key in actions:
            results.append(CommandBarResult(
                icon=icon, title=name, subtitle=desc,
                category="Actions",
                action=lambda k=key: self._run_action(k),
            ))
        return results

    def _search_navigation(self, query: str) -> List[CommandBarResult]:
        q = query.lower()
        return [r for r in self._get_navigation_items()
                if q in r.title.lower() or q in r.subtitle.lower()]

    def _search_actions(self, query: str) -> List[CommandBarResult]:
        q = query.lower()
        return [r for r in self._get_action_items()
                if q in r.title.lower() or q in r.subtitle.lower()]

    def _search_inventory(self, query: str) -> List[CommandBarResult]:
        """Search inventory.db for matching items."""
        results = []
        try:
            folder_path = getattr(self.main_window, "folder_path", None)
            conn = get_connection("inventory.db", folder_path=folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT hcpcs_code, description, category, retail_price, stock_quantity
                FROM inventory
                WHERE hcpcs_code LIKE ? OR description LIKE ? OR category LIKE ?
                ORDER BY hcpcs_code
                LIMIT 8
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%"),
            )
            for row in cur.fetchall():
                hcpcs = row["hcpcs_code"] or ""
                desc = row["description"] or ""
                cat = row["category"] or ""
                price = row["retail_price"] or ""
                stock = row["stock_quantity"] if row["stock_quantity"] is not None else "?"

                results.append(CommandBarResult(
                    icon="📦",
                    title=f"{hcpcs} — {desc}",
                    subtitle=f"{cat} • ${price} • Stock: {stock}",
                    category="Inventory",
                    action=lambda h=hcpcs: self._navigate_to_inventory(h),
                    data=dict(row),
                ))
            conn.close()
        except Exception as e:
            print(f"Command bar inventory search error: {e}")
        return results

    def _search_orders(self, query: str) -> List[CommandBarResult]:
        """Search orders.db for matching orders."""
        results = []
        try:
            folder_path = getattr(self.main_window, "folder_path", None)
            conn = get_connection("orders.db", folder_path=folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, patient_last_name, patient_first_name,
                       order_status, order_date, primary_insurance
                FROM orders
                WHERE CAST(id AS TEXT) LIKE ?
                   OR patient_last_name LIKE ?
                   OR patient_first_name LIKE ?
                   OR order_status LIKE ?
                   OR primary_insurance LIKE ?
                ORDER BY id DESC
                LIMIT 8
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%",
                 f"%{query}%", f"%{query}%"),
            )
            for row in cur.fetchall():
                oid = row["id"]
                last = row["patient_last_name"] or ""
                first = row["patient_first_name"] or ""
                name = f"{last}, {first}".strip(", ") or "Unknown"
                status = row["order_status"] or ""
                odate = row["order_date"] or ""
                ins = row["primary_insurance"] or ""
                subtitle_parts = []
                if status:
                    subtitle_parts.append(status)
                if odate:
                    subtitle_parts.append(odate)
                if ins:
                    subtitle_parts.append(ins)

                results.append(CommandBarResult(
                    icon="\U0001f4cb",
                    title=f"ORD-{oid:03d} \u2014 {name}",
                    subtitle=" \u2022 ".join(subtitle_parts),
                    category="Orders",
                    action=lambda o=oid: self._open_order(o),
                    data=dict(row),
                ))
            conn.close()
        except Exception as e:
            print(f"Command bar order search error: {e}")
        return results

    def _search_patients(self, query: str) -> List[CommandBarResult]:
        """Search patients.db for matching patients."""
        results = []
        try:
            folder_path = getattr(self.main_window, "folder_path", None)
            conn = get_connection("patients.db", folder_path=folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, last_name, first_name, dob, phone,
                       primary_insurance
                FROM patients
                WHERE last_name LIKE ? OR first_name LIKE ?
                   OR phone LIKE ? OR dob LIKE ?
                ORDER BY last_name, first_name
                LIMIT 8
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"),
            )
            for row in cur.fetchall():
                pid = row["id"]
                last = row["last_name"] or ""
                first = row["first_name"] or ""
                name = f"{last}, {first}".strip(", ")
                dob = row["dob"] or ""
                phone = row["phone"] or ""
                ins = row["primary_insurance"] or ""
                subtitle_parts = []
                if dob:
                    subtitle_parts.append(f"DOB: {dob}")
                if phone:
                    subtitle_parts.append(phone)
                if ins:
                    subtitle_parts.append(ins)

                results.append(CommandBarResult(
                    icon="\U0001f464",
                    title=name,
                    subtitle=" \u2022 ".join(subtitle_parts),
                    category="Patients",
                    action=lambda p=pid, ln=last: self._navigate_to_patient(p, ln),
                    data=dict(row),
                ))
            conn.close()
        except Exception as e:
            print(f"Command bar patient search error: {e}")
        return results

    def _search_prescribers(self, query: str) -> List[CommandBarResult]:
        """Search prescribers.db for matching prescribers."""
        results = []
        try:
            folder_path = getattr(self.main_window, "folder_path", None)
            conn = get_connection("prescribers.db", folder_path=folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, last_name, first_name, npi_number, specialty
                FROM prescribers
                WHERE last_name LIKE ? OR first_name LIKE ?
                   OR npi_number LIKE ? OR specialty LIKE ?
                ORDER BY last_name, first_name
                LIMIT 6
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"),
            )
            for row in cur.fetchall():
                last = row["last_name"] or ""
                first = row["first_name"] or ""
                name = f"Dr. {first} {last}".strip()
                npi = row["npi_number"] or ""
                spec = row["specialty"] or ""
                subtitle_parts = []
                if npi:
                    subtitle_parts.append(f"NPI: {npi}")
                if spec:
                    subtitle_parts.append(spec)

                results.append(CommandBarResult(
                    icon="\U0001f3e5",
                    title=name,
                    subtitle=" \u2022 ".join(subtitle_parts),
                    category="Prescribers",
                    action=lambda: self._navigate_to_tab("Prescribers"),
                    data=dict(row),
                ))
            conn.close()
        except Exception as e:
            print(f"Command bar prescriber search error: {e}")
        return results

    # ------------------------------------------------------------------ Actions

    def _on_item_activated(self, item: QListWidgetItem):
        result: CommandBarResult = item.data(Qt.ItemDataRole.UserRole)
        if result and result.action:
            self.hide()
            try:
                result.action()
            except Exception as e:
                print(f"Command bar action error: {e}")

    def _navigate_to_tab(self, tab_text: str):
        """Switch to a specific tab by matching tab label text."""
        win = self.main_window
        tabs = getattr(win, "main_tabs", None)
        if not tabs:
            return
        target = tab_text.lower()
        for i in range(tabs.count()):
            if target in tabs.tabText(i).lower():
                tabs.setCurrentIndex(i)
                return

    def _navigate_to_inventory(self, hcpcs: str):
        """Switch to inventory tab and search for the HCPCS code."""
        self._navigate_to_tab("Inventory")
        win = self.main_window
        # Try multiple possible search widget names
        for attr in ("inventory_search_edit", "inv_search", "inventory_search"):
            search_widget = getattr(win, attr, None)
            if search_widget and hasattr(search_widget, "setText"):
                search_widget.setText(hcpcs)
                return
        # Fallback: try calling a search method
        if hasattr(win, "search_inventory"):
            try:
                win.search_inventory(hcpcs)
            except Exception:
                pass

    def _navigate_to_patient(self, patient_id: int, last_name: str = ""):
        """Switch to patients tab and search for the patient."""
        self._navigate_to_tab("Patients")
        win = self.main_window
        search_widget = getattr(win, "patient_search", None)
        if search_widget and hasattr(search_widget, "setText") and last_name:
            search_widget.setText(last_name)
            if hasattr(win, "search_patients"):
                try:
                    win.search_patients()
                except Exception:
                    pass

    def _open_order(self, order_id: int):
        """Open the order editor for the given order."""
        win = self.main_window
        if hasattr(win, "open_order_editor"):
            try:
                win.open_order_editor(order_id)
            except Exception:
                pass
        elif hasattr(win, "edit_order_by_id"):
            try:
                win.edit_order_by_id(order_id)
            except Exception:
                pass

    def _run_action(self, action_key: str):
        """Run a named action on the main window."""
        win = self.main_window
        actions = {
            "new_order": lambda: (
                win.open_new_order_wizard()
                if hasattr(win, "open_new_order_wizard") else None
            ),
            "new_patient": lambda: (
                win.add_new_patient()
                if hasattr(win, "add_new_patient") else None
            ),
            "add_inventory": lambda: (
                win.add_inventory_item()
                if hasattr(win, "add_inventory_item") else None
            ),
            "sticky_notes": lambda: (
                win._open_sticky_notes_manager()
                if hasattr(win, "_open_sticky_notes_manager") else None
            ),
            "settings": lambda: (
                win.open_settings()
                if hasattr(win, "open_settings") else None
            ),
        }
        action = actions.get(action_key)
        if action:
            try:
                action()
            except Exception as e:
                print(f"Command bar action error: {e}")
