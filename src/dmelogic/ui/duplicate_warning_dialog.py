"""
Duplicate Order Warning Dialog
================================
Shows a warning when a potential duplicate order is detected.
Gives the user options: Continue Anyway, View Existing Order, or Cancel.

Usage:
    from dmelogic.ui.duplicate_warning_dialog import DuplicateWarningDialog

    warnings = detector.check(...)
    if warnings:
        dlg = DuplicateWarningDialog(warnings, parent=self)
        result = dlg.exec()
        if result == DuplicateWarningDialog.ACTION_CONTINUE:
            # proceed with order creation
        elif result == DuplicateWarningDialog.ACTION_VIEW:
            order_id = dlg.selected_order_id
            # open that order
        else:
            # cancelled
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from dmelogic.services.duplicate_detector import DuplicateWarning


class DuplicateWarningDialog(QDialog):
    """
    Warning dialog shown when duplicate orders are detected.
    
    Returns:
        ACTION_CONTINUE (1): User chose to create the order anyway
        ACTION_VIEW (2): User wants to view an existing order (check selected_order_id)
        ACTION_CANCEL (0): User cancelled
    """

    ACTION_CANCEL = 0
    ACTION_CONTINUE = 1
    ACTION_VIEW = 2

    def __init__(self, warnings: List[DuplicateWarning], parent=None):
        super().__init__(parent)
        self.warnings = warnings
        self.selected_order_id: Optional[int] = None
        self._result_action = self.ACTION_CANCEL

        self.setWindowTitle("⚠️ Potential Duplicate Order Detected")
        self.setMinimumSize(580, 350)
        self.resize(640, 420)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Determine if critical
        has_critical = any(w.severity == "critical" for w in self.warnings)

        # Header
        if has_critical:
            icon_text = "⛔"
            header_text = "Duplicate Order Detected!"
            header_color = "#DC2626"
            banner_bg = "#FEF2F2"
            banner_border = "#FECACA"
        else:
            icon_text = "⚠️"
            header_text = "Similar Order Found"
            header_color = "#D97706"
            banner_bg = "#FFFBEB"
            banner_border = "#FDE68A"

        # Banner
        banner = QFrame()
        banner.setStyleSheet(f"""
            QFrame {{
                background: {banner_bg};
                border: 2px solid {banner_border};
                border-radius: 10px;
            }}
        """)
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)

        icon = QLabel(icon_text)
        icon.setFont(QFont("Segoe UI Emoji", 24))
        icon.setStyleSheet("border: none;")
        banner_layout.addWidget(icon)

        header_label = QLabel(header_text)
        header_label.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        header_label.setStyleSheet(f"border: none; color: {header_color};")
        banner_layout.addWidget(header_label, 1)

        layout.addWidget(banner)

        # Explanation
        n = len(self.warnings)
        explanation = QLabel(
            f"Found {n} existing order{'s' if n > 1 else ''} for this patient "
            f"with overlapping items. Creating a new order may result in a duplicate."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #4B5563; font-size: 11px;")
        layout.addWidget(explanation)

        # Warnings list (scrollable)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(200)

        warnings_widget = QWidget()
        warnings_layout = QVBoxLayout(warnings_widget)
        warnings_layout.setContentsMargins(0, 0, 0, 0)
        warnings_layout.setSpacing(8)

        for w in self.warnings:
            card = self._build_warning_card(w)
            warnings_layout.addWidget(card)

        warnings_layout.addStretch()
        scroll.setWidget(warnings_widget)
        layout.addWidget(scroll, 1)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        # View Existing button
        if self.warnings:
            self.btn_view = QPushButton(f"🔍 View {self.warnings[0].display_order_number}")
            self.btn_view.setStyleSheet(
                "background: #3B82F6; color: white; border-radius: 6px; "
                "padding: 10px 20px; font-weight: 600; font-size: 12px;"
            )
            self.btn_view.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_view.clicked.connect(self._on_view)
            btn_layout.addWidget(self.btn_view)

        btn_layout.addStretch()

        # Cancel
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(
            "color: #6B7280; border: 1px solid #D1D5DB; border-radius: 6px; "
            "padding: 10px 20px; font-size: 12px;"
        )
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        # Continue anyway
        self.btn_continue = QPushButton("Create Anyway →")
        if has_critical:
            self.btn_continue.setStyleSheet(
                "background: #EF4444; color: white; border-radius: 6px; "
                "padding: 10px 20px; font-weight: 600; font-size: 12px;"
            )
        else:
            self.btn_continue.setStyleSheet(
                "background: #F59E0B; color: white; border-radius: 6px; "
                "padding: 10px 20px; font-weight: 600; font-size: 12px;"
            )
        self.btn_continue.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_continue.clicked.connect(self._on_continue)
        btn_layout.addWidget(self.btn_continue)

        layout.addLayout(btn_layout)

    def _build_warning_card(self, w: DuplicateWarning) -> QFrame:
        """Build a card for a single warning."""
        card = QFrame()
        if w.severity == "critical":
            card.setStyleSheet("""
                QFrame { background: #FEF2F2; border: 1px solid #FECACA; border-radius: 8px; }
            """)
        else:
            card.setStyleSheet("""
                QFrame { background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 8px; }
            """)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        # Order info line
        order_line = QLabel(
            f"<b>{w.display_order_number}</b> — "
            f"<span style='color: #6B7280;'>{w.status}</span> — "
            f"<span style='color: #6B7280;'>Date: {w.order_date}</span>"
            f"{'  <span style=\"color:#DC2626;\">('+str(w.days_ago)+' days ago)</span>' if w.days_ago > 0 else ''}"
        )
        order_line.setStyleSheet("border: none; font-size: 11px;")
        card_layout.addWidget(order_line)

        # Prescriber
        if w.prescriber_name:
            pres_label = QLabel(f"Prescriber: {w.prescriber_name}")
            pres_label.setStyleSheet("border: none; color: #6B7280; font-size: 10px;")
            card_layout.addWidget(pres_label)

        # Overlapping items
        items_text = ""
        for code, desc in zip(w.overlap_codes, w.overlap_descriptions):
            items_text += f"<b>{code}</b> — {desc}<br>"
        if items_text:
            items_label = QLabel(f"<span style='color:#DC2626;'>Overlapping items:</span><br>{items_text}")
            items_label.setStyleSheet("border: none; font-size: 10px;")
            items_label.setWordWrap(True)
            card_layout.addWidget(items_label)

        return card

    def _on_continue(self):
        self._result_action = self.ACTION_CONTINUE
        self.accept()

    def _on_view(self):
        if self.warnings:
            self.selected_order_id = self.warnings[0].order_id
        self._result_action = self.ACTION_VIEW
        self.accept()

    def exec(self) -> int:
        """Override to return the action taken."""
        super().exec()
        return self._result_action
