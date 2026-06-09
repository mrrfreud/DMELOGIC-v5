"""
Inbox Dialog for Incoming SMS and Fax Messages

Shows all incoming communications from RingCentral with:
- Filter by type (SMS, Fax, All)
- Filter by read status
- Patient matching by phone number
- Quick reply/view actions
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QWidget, QGroupBox, QMessageBox, QSplitter, QTextEdit,
    QProgressDialog, QCheckBox, QFrame, QFileDialog, QScrollArea,
    QStackedWidget, QApplication, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread, QByteArray, QBuffer
from PyQt6.QtGui import QFont, QColor, QPixmap, QImage

from dmelogic.config import debug_log
from dmelogic.settings import load_settings
from dmelogic.services.ringcentral_service import get_ringcentral_service


def format_phone_number(phone: str) -> str:
    """Format phone number for display."""
    if not phone:
        return ""
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return phone


def normalize_phone(phone: str) -> str:
    """Normalize phone to digits only for matching."""
    if not phone:
        return ""
    digits = ''.join(c for c in phone if c.isdigit())
    # Remove leading 1 for US numbers
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
    return digits


def format_message_datetime(iso_str: str) -> str:
    """Convert ISO timestamp from RingCentral to local display string."""
    if not iso_str:
        return ""
    try:
        # Normalize Z suffix to explicit UTC offset
        iso_norm = iso_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(iso_norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return local_dt.strftime("%m/%d/%Y %I:%M %p")
    except Exception:
        # Fallback: trim and replace T if parsing fails
        return iso_str[:16].replace('T', ' ')


class FetchMessagesThread(QThread):
    """Background thread to fetch messages from RingCentral."""
    
    finished = pyqtSignal(dict)
    
    def __init__(self, service, message_type: Optional[str], direction: Optional[str], date_from: str, unread_only: bool):
        super().__init__()
        self.service = service
        self.message_type = message_type
        self.direction = direction
        self.date_from = date_from
        self.unread_only = unread_only
    
    def run(self):
        try:
            result = self.service.get_messages(
                message_type=self.message_type,
                direction=self.direction,
                date_from=self.date_from,
                read_status='Unread' if self.unread_only else None,
                per_page=250
            )
            self.finished.emit(result)
        except Exception as e:
            self.finished.emit({'success': False, 'error': str(e), 'messages': []})


class InboxDialog(QDialog):
    """
    Dialog showing incoming SMS and Fax messages.
    """
    
    # Signal emitted when user wants to open a patient
    open_patient = pyqtSignal(int)  # patient_id
    
    def __init__(self, parent=None, patient_repo=None, folder_path: Optional[str] = None):
        super().__init__(parent)
        self.patient_repo = patient_repo
        self.folder_path = folder_path
        self.messages = []
        self.patient_cache = {}  # phone -> patient info
        self._fetch_thread = None
        self._downloaded_tracker_path = self._get_downloaded_tracker_path()
        self._downloaded_metadata: Dict[str, Dict[str, Any]] = self._load_downloaded_metadata()
        
        self.setWindowTitle("📬 Inbox - Incoming Messages")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setMinimumSize(900, 600)
        self.resize(1100, 750)
        
        self._setup_ui()
        
        # Auto-refresh timer (every 60 seconds)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._auto_refresh)
        self.refresh_timer.start(60000)
        
        # Initial load
        QTimer.singleShot(100, self.refresh)
    
    def _get_downloaded_tracker_path(self) -> Path:
        base = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / "DMELogic"
        base.mkdir(parents=True, exist_ok=True)
        return base / "downloaded_faxes.json"

    def _load_downloaded_metadata(self) -> Dict[str, Dict[str, Any]]:
        try:
            if self._downloaded_tracker_path.exists():
                data = json.loads(self._downloaded_tracker_path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    # Ensure keys are strings for reliable lookups
                    return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception as exc:
            debug_log(f"Inbox: Failed to load downloaded tracker: {exc}")
        return {}

    def _save_downloaded_metadata(self):
        try:
            if len(self._downloaded_metadata) > 500:
                sorted_items = sorted(
                    self._downloaded_metadata.items(),
                    key=lambda kv: kv[1].get('saved_at', '')
                )
                self._downloaded_metadata = dict(sorted_items[-500:])
            self._downloaded_tracker_path.write_text(
                json.dumps(self._downloaded_metadata, indent=2),
                encoding='utf-8'
            )
        except Exception as exc:
            debug_log(f"Inbox: Failed to save downloaded tracker: {exc}")

    def _is_message_downloaded(self, message_id: str) -> bool:
        return bool(message_id and message_id in self._downloaded_metadata)

    def _mark_message_downloaded(self, message_id: str, file_path: str):
        if not message_id:
            return
        self._downloaded_metadata[message_id] = {
            'saved_at': datetime.now().isoformat(),
            'path': file_path
        }
        self._save_downloaded_metadata()
        self._update_download_indicator_for_message(message_id)

    def _create_download_cell(self, download_info: Optional[Dict[str, Any]]):
        item = QTableWidgetItem("✓" if download_info else "")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if download_info:
            saved_at = download_info.get('saved_at', 'unknown time')
            saved_path = download_info.get('path', '')
            tooltip_lines = [f"Saved at: {saved_at}"]
            if saved_path:
                tooltip_lines.append(f"Path: {saved_path}")
            item.setToolTip("\n".join(tooltip_lines))
        else:
            item.setToolTip("Not downloaded yet")
        return item

    def _update_download_indicator_for_message(self, message_id: str):
        if not message_id:
            return
        download_info = self._downloaded_metadata.get(message_id)
        for row in range(self.table.rowCount()):
            status_item = self.table.item(row, 0)
            if not status_item:
                continue
            msg = status_item.data(Qt.ItemDataRole.UserRole)
            if msg and str(msg.get('id', '')) == message_id:
                self.table.setItem(row, 3, self._create_download_cell(download_info))
                break

    def _setup_ui(self):
        """Build the dialog UI."""
        layout = QVBoxLayout(self)
        
        # Header
        header_layout = QHBoxLayout()
        
        title = QLabel("📬 Inbox")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        # Connection status
        self.status_label = QLabel("Checking connection...")
        self.status_label.setStyleSheet("color: #666;")
        header_layout.addWidget(self.status_label)
        
        layout.addLayout(header_layout)
        
        # Filters
        filter_frame = QFrame()
        filter_frame.setStyleSheet("QFrame { background-color: #f5f5f5; border-radius: 4px; padding: 8px; }")
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(8, 8, 8, 8)
        
        filter_layout.addWidget(QLabel("Type:"))
        self.type_filter = QComboBox()
        self.type_filter.addItem("All Messages", None)
        self.type_filter.addItem("💬 SMS Only", "SMS")
        self.type_filter.addItem("📠 Fax Only", "Fax")
        self.type_filter.addItem("🎤 Voicemail Only", "VoiceMail")
        self.type_filter.currentIndexChanged.connect(self.refresh)
        filter_layout.addWidget(self.type_filter)

        filter_layout.addSpacing(20)

        filter_layout.addWidget(QLabel("Direction:"))
        self.direction_filter = QComboBox()
        self.direction_filter.addItem("Inbound Only", "Inbound")
        self.direction_filter.addItem("Outbound Only", "Outbound")
        self.direction_filter.addItem("All", None)
        self.direction_filter.currentIndexChanged.connect(self.refresh)
        filter_layout.addWidget(self.direction_filter)

        filter_layout.addSpacing(20)
        
        filter_layout.addWidget(QLabel("Date Range:"))
        self.date_filter = QComboBox()
        self.date_filter.addItem("Last 7 Days", 7)
        self.date_filter.addItem("Last 14 Days", 14)
        self.date_filter.addItem("Last 30 Days", 30)
        self.date_filter.addItem("Last 90 Days", 90)
        self.date_filter.currentIndexChanged.connect(self.refresh)
        filter_layout.addWidget(self.date_filter)
        
        filter_layout.addSpacing(20)
        
        self.unread_only_cb = QCheckBox("Unread Only")
        self.unread_only_cb.stateChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.unread_only_cb)
        
        filter_layout.addSpacing(20)
        
        # Search box
        filter_layout.addWidget(QLabel("🔍 Search:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by phone, patient, or content...")
        self.search_box.setMinimumWidth(200)
        self.search_box.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.search_box)
        
        filter_layout.addStretch()
        
        # Refresh button
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        filter_layout.addWidget(refresh_btn)
        
        layout.addWidget(filter_frame)
        
        # Splitter for list and preview
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Message table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Status", "Type", "Contact", "Downloaded", "Patient", "Subject/Preview", "Date"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        splitter.addWidget(self.table)
        
        # Preview panel
        preview_group = QGroupBox("Message Preview")
        preview_layout = QVBoxLayout(preview_group)
        
        self.preview_header = QLabel("Select a message to preview")
        self.preview_header.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        preview_layout.addWidget(self.preview_header)
        
        # Stacked widget for text preview vs fax preview
        self.preview_stack = QStackedWidget()
        
        # Text preview (for SMS)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_stack.addWidget(self.preview_text)
        
        # Fax preview (scrollable image area)
        self.fax_preview_scroll = QScrollArea()
        self.fax_preview_scroll.setWidgetResizable(True)
        self.fax_preview_scroll.setMinimumHeight(200)
        
        self.fax_preview_container = QWidget()
        self.fax_preview_layout = QVBoxLayout(self.fax_preview_container)
        self.fax_preview_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.fax_preview_scroll.setWidget(self.fax_preview_container)
        self.preview_stack.addWidget(self.fax_preview_scroll)
        
        preview_layout.addWidget(self.preview_stack)
        
        # Preview action buttons
        preview_btn_layout = QHBoxLayout()
        
        self.reply_btn = QPushButton("💬 Reply SMS")
        self.reply_btn.setEnabled(False)
        self.reply_btn.clicked.connect(self._reply_to_message)
        preview_btn_layout.addWidget(self.reply_btn)
        
        self.view_patient_btn = QPushButton("👤 View Patient")
        self.view_patient_btn.setEnabled(False)
        self.view_patient_btn.clicked.connect(self._view_patient)
        preview_btn_layout.addWidget(self.view_patient_btn)
        
        self.play_voicemail_btn = QPushButton("🔊 Play Voicemail")
        self.play_voicemail_btn.setEnabled(False)
        self.play_voicemail_btn.clicked.connect(self._play_voicemail)
        preview_btn_layout.addWidget(self.play_voicemail_btn)
        
        self.preview_fax_btn = QPushButton("👁️ Preview Attachment")
        self.preview_fax_btn.setEnabled(False)
        self.preview_fax_btn.clicked.connect(self._on_preview_attachment_clicked)
        preview_btn_layout.addWidget(self.preview_fax_btn)
        
        self.download_fax_btn = QPushButton("📥 Download Attachment")
        self.download_fax_btn.setEnabled(False)
        self.download_fax_btn.clicked.connect(self._download_fax)
        preview_btn_layout.addWidget(self.download_fax_btn)
        
        self.mark_read_btn = QPushButton("✓ Mark as Read")
        self.mark_read_btn.setEnabled(False)
        self.mark_read_btn.clicked.connect(self._mark_as_read)
        preview_btn_layout.addWidget(self.mark_read_btn)

        self.delete_btn = QPushButton("🗑️ Delete Message")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_message)
        preview_btn_layout.addWidget(self.delete_btn)
        
        preview_btn_layout.addStretch()
        preview_layout.addLayout(preview_btn_layout)
        
        splitter.addWidget(preview_group)
        splitter.setSizes([400, 300])
        
        layout.addWidget(splitter)
        
        # Bottom bar
        bottom_layout = QHBoxLayout()
        
        self.count_label = QLabel("0 messages")
        bottom_layout.addWidget(self.count_label)
        
        bottom_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom_layout.addWidget(close_btn)
        
        layout.addLayout(bottom_layout)
    
    def refresh(self):
        """Refresh the message list from RingCentral."""
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service or not service.is_connected:
            self.status_label.setText("⚠️ Not connected to RingCentral")
            self.status_label.setStyleSheet("color: #dc3545;")
            self.table.setRowCount(0)
            self.count_label.setText("0 messages")
            return
        
        self.status_label.setText("🔄 Loading...")
        self.status_label.setStyleSheet("color: #007bff;")
        
        # Get filter values
        message_type = self.type_filter.currentData()
        direction = self.direction_filter.currentData()
        days = self.date_filter.currentData() or 7
        date_from = (datetime.now() - timedelta(days=days)).isoformat()
        unread_only = self.unread_only_cb.isChecked()
        
        # Fetch in background thread
        self._fetch_thread = FetchMessagesThread(service, message_type, direction, date_from, unread_only)
        self._fetch_thread.finished.connect(self._on_messages_fetched)
        self._fetch_thread.start()
    
    def _on_messages_fetched(self, result: Dict[str, Any]):
        """Handle fetched messages."""
        if not result.get('success'):
            self.status_label.setText(f"⚠️ Error: {result.get('error', 'Unknown')}")
            self.status_label.setStyleSheet("color: #dc3545;")
            return
        
        self.status_label.setText("✓ Connected")
        self.status_label.setStyleSheet("color: #28a745;")
        
        self.messages = result.get('messages', [])
        self._populate_table()
    
    def _populate_table(self):
        """Populate the table with messages."""
        self.table.setRowCount(len(self.messages))
        
        for row, msg in enumerate(self.messages):
            try:
                message_id = str(msg.get('id', ''))

                # Status (read/unread)
                read_status = msg.get('read_status', 'Read')
                status_item = QTableWidgetItem("●" if read_status == 'Unread' else "○")
                status_item.setForeground(QColor("#007bff") if read_status == 'Unread' else QColor("#ccc"))
                status_item.setToolTip("Unread" if read_status == 'Unread' else "Read")
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 0, status_item)
                
                direction_label, is_inbound, contact_name, contact_number = self._get_counterparty(msg)
                
                # Type (with direction hint)
                msg_type = msg.get('type', '')
                if msg_type == 'SMS':
                    type_icon = '💬'
                elif msg_type == 'Fax':
                    type_icon = '📠'
                elif msg_type == 'VoiceMail':
                    type_icon = '🎤'
                else:
                    type_icon = '📧'
                direction_icon = '⬇️' if is_inbound else '⬆️'
                type_item = QTableWidgetItem(f"{direction_icon} {type_icon} {msg_type}")
                type_item.setToolTip(f"{direction_label} {msg_type}")
                self.table.setItem(row, 1, type_item)
                
                # Contact column
                contact_display = contact_name if contact_name else format_phone_number(contact_number)
                if not contact_display:
                    contact_display = "(Unknown)"
                contact_item = QTableWidgetItem(contact_display)
                tooltip_number = contact_number or contact_display
                contact_item.setToolTip(tooltip_number)
                self.table.setItem(row, 2, contact_item)
                
                # Patient match uses counterparty number
                patient_info = self._match_patient(contact_number)
                if patient_info:
                    patient_item = QTableWidgetItem(f"👤 {patient_info['name']}")
                    patient_item.setForeground(QColor("#28a745"))
                    patient_item.setData(Qt.ItemDataRole.UserRole, patient_info['id'])
                else:
                    patient_item = QTableWidgetItem("—")
                    patient_item.setForeground(QColor("#999"))
                self.table.setItem(row, 4, patient_item)
                
                # Subject/Preview
                raw_subject = msg.get('subject', '') or ""
                subject = raw_subject.strip()
                attachments = msg.get('attachments', []) or []
                if not subject and msg_type == 'Fax':
                    pages = msg.get('fax_page_count', 0)
                    subject = f"Fax ({pages} pages)" if pages else "Fax"
                elif not subject and msg_type == 'SMS':
                    # Inbound image/MMS messages usually have no text subject
                    has_image = any('image' in (att.get('content_type') or '').lower() for att in attachments)
                    subject = "📷 Image message" if has_image else "(Image/attachment message)"
                elif not subject:
                    subject = "(No content)"
                if len(subject) > 60:
                    subject = subject[:60] + "..."
                self.table.setItem(row, 5, QTableWidgetItem(subject))
                
                # Date
                created = msg.get('created_at', '')
                date_str = format_message_datetime(created) if created else ""
                self.table.setItem(row, 6, QTableWidgetItem(date_str))

                # Download indicator
                download_info = self._downloaded_metadata.get(message_id)
                self.table.setItem(row, 3, self._create_download_cell(download_info))
                
                # Store message data for later use
                self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, msg)
                
                # Make unread rows bold
                if read_status == 'Unread':
                    for col in range(self.table.columnCount()):
                        item = self.table.item(row, col)
                        if item:
                            font = item.font()
                            font.setBold(True)
                            item.setFont(font)
                
            except Exception as e:
                debug_log(f"Inbox: Error rendering row {row}: {e}")
        
        # Update count
        unread = sum(1 for m in self.messages if m.get('read_status') == 'Unread')
        self.count_label.setText(f"{len(self.messages)} messages ({unread} unread)")
        
        # Apply any existing filter
        self._apply_filter()
    
    def _apply_filter(self):
        """Apply search filter and unread-only filter to visible rows."""
        search_text = self.search_box.text().strip().lower() if hasattr(self, 'search_box') else ""
        unread_only = self.unread_only_cb.isChecked()
        
        visible_count = 0
        unread_visible = 0
        
        for row in range(self.table.rowCount()):
            # Get message data
            status_item = self.table.item(row, 0)
            if not status_item:
                continue
            
            msg = status_item.data(Qt.ItemDataRole.UserRole)
            if not msg:
                continue
            
            # Check unread filter
            is_unread = msg.get('read_status') == 'Unread'
            if unread_only and not is_unread:
                self.table.setRowHidden(row, True)
                continue
            
            # Check search filter
            if search_text:
                # Build searchable text from various fields
                _, _, contact_name, contact_number = self._get_counterparty(msg)
                contact_number = (contact_number or '').lower()
                contact_name = (contact_name or '').lower()
                subject = (msg.get('subject', '') or '').strip().lower()
                direction_text = (msg.get('direction', '') or '').lower()
                
                # Get patient name from table cell
                patient_item = self.table.item(row, 4)
                patient_name = patient_item.text().lower() if patient_item else ""
                
                # Check if search term matches any field
                searchable = f"{contact_number} {contact_name} {subject} {patient_name} {direction_text}"
                if search_text not in searchable:
                    self.table.setRowHidden(row, True)
                    continue
            
            # Row passes all filters - show it
            self.table.setRowHidden(row, False)
            visible_count += 1
            if is_unread:
                unread_visible += 1
        
        # Update count label to show filtered count
        total = len(self.messages)
        if search_text or unread_only:
            self.count_label.setText(f"Showing {visible_count} of {total} ({unread_visible} unread)")
        else:
            unread = sum(1 for m in self.messages if m.get('read_status') == 'Unread')
            self.count_label.setText(f"{total} messages ({unread} unread)")
    
    def _match_patient(self, phone: str) -> Optional[Dict[str, Any]]:
        """Try to match a phone number to a patient."""
        if not phone or not self.patient_repo:
            return None
        
        normalized = normalize_phone(phone)
        if not normalized:
            return None
        
        # Check cache
        if normalized in self.patient_cache:
            return self.patient_cache[normalized]
        
        # Search database
        try:
            # Try to find by phone number
            patients = self.patient_repo.search(phone)
            for p in patients:
                patient_phone = normalize_phone(p.get('phone', '') or '')
                patient_secondary = normalize_phone(p.get('secondary_contact', '') or '')
                
                if patient_phone == normalized or patient_secondary == normalized:
                    result = {
                        'id': p.get('id'),
                        'name': f"{p.get('last_name', '')}, {p.get('first_name', '')}".strip(', ')
                    }
                    self.patient_cache[normalized] = result
                    return result
            
            # Not found
            self.patient_cache[normalized] = None
            return None
            
        except Exception as e:
            debug_log(f"Inbox: Patient match error: {e}")
            return None

    def _get_counterparty(self, msg: Dict[str, Any]) -> tuple:
        """Return direction flag plus counterparty name/number for a message."""
        direction_raw = msg.get('direction', 'Inbound') or 'Inbound'
        direction_norm = str(direction_raw).capitalize()
        if direction_norm not in ('Inbound', 'Outbound'):
            direction_norm = 'Inbound'
        is_inbound = direction_norm == 'Inbound'
        name_key = 'from_name' if is_inbound else 'to_name'
        number_key = 'from_number' if is_inbound else 'to_number'
        name = msg.get(name_key, '') or ''
        number = msg.get(number_key, '') or ''
        return direction_norm, is_inbound, name, number
    
    def _on_selection_changed(self):
        """Handle table selection change."""
        selected = self.table.selectedItems()
        if not selected:
            self.preview_header.setText("Select a message to preview")
            self.preview_text.clear()
            self._clear_fax_preview()
            self.preview_stack.setCurrentIndex(0)
            self.reply_btn.setEnabled(False)
            self.view_patient_btn.setEnabled(False)
            self.play_voicemail_btn.setEnabled(False)
            self.preview_fax_btn.setEnabled(False)
            self.download_fax_btn.setEnabled(False)
            self.mark_read_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            return
        
        row = self.table.currentRow()
        if row < 0:
            return
        
        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return
        
        # Update preview
        msg_type = msg.get('type', '')
        direction_label, is_inbound, contact_name, contact_number = self._get_counterparty(msg)
        partner_display = contact_name if contact_name else format_phone_number(contact_number)
        if not partner_display:
            partner_display = "Unknown"
        created_iso = msg.get('created_at', '') or ''
        created_display = format_message_datetime(created_iso) if created_iso else ''
        preposition = "from" if is_inbound else "to"
        self.preview_header.setText(f"{msg_type} {preposition} {partner_display} - {created_display}")
        
        subject = (msg.get('subject', '') or '').strip()
        attachments = msg.get('attachments', []) or []
        # Determine attachment info up front
        attachments = msg.get('attachments', []) or []
        has_attachments = len(attachments) > 0
        has_image_attachment = any(
            'image' in (att.get('content_type') or '').lower() for att in attachments
        )

        if msg_type == 'Fax':
            pages = msg.get('fax_page_count', 0)
            self.preview_text.setPlainText(f"📠 Loading fax preview ({pages} page{'s' if pages != 1 else ''})...")
            self.preview_stack.setCurrentIndex(0)  # Show loading text
            self._clear_fax_preview()
            
            # Auto-load fax preview
            if has_attachments:
                QTimer.singleShot(100, self._preview_fax)  # Load preview after UI updates
        elif msg_type == 'VoiceMail':
            duration = ''
            for att in attachments:
                vm_dur = att.get('vmDuration')
                if vm_dur:
                    mins, secs = divmod(int(vm_dur), 60)
                    duration = f" ({mins}:{secs:02d})"
                    break
            self.preview_text.setPlainText(
                f"🎤 Voicemail{duration}\n\n"
                "Click 'Play Voicemail' to listen."
            )
            self.preview_stack.setCurrentIndex(0)
        else:
            if msg_type == 'SMS':
                if not subject and has_image_attachment:
                    # Image/MMS-only message: show hint and auto-load preview
                    self.preview_text.setPlainText(
                        "Loading image preview...\n\n"
                        "If the image does not appear, use 'Preview Attachment' or 'Download Attachment'."
                    )
                    if has_attachments:
                        QTimer.singleShot(100, self._preview_message_attachment)
                elif not subject:
                    # Non-text SMS with non-image attachment
                    self.preview_text.setPlainText(
                        "This message appears to contain only attachments.\n"
                        "Use 'Preview Attachment' or 'Download Attachment' to view."
                    )
                else:
                    # Text SMS (with or without attachments)
                    extra = "\n\n(Attachment preview will appear below.)" if has_attachments else ""
                    self.preview_text.setPlainText(subject + extra)
            else:
                self.preview_text.setPlainText(subject if subject else "(No content)")
            self.preview_stack.setCurrentIndex(0)  # Show text for SMS and other types
        
        # Enable/disable buttons
        message_id = str(msg.get('id', ''))
        self.reply_btn.setEnabled(msg_type == 'SMS' and bool(contact_number))
        self.play_voicemail_btn.setEnabled(msg_type == 'VoiceMail' and has_attachments)
        self.preview_fax_btn.setEnabled(has_attachments and msg_type != 'VoiceMail')
        self.download_fax_btn.setEnabled(has_attachments)
        self.mark_read_btn.setEnabled(msg.get('read_status') == 'Unread')
        self.delete_btn.setEnabled(True)

        if has_attachments:
            if self._is_message_downloaded(message_id):
                self.download_fax_btn.setText("📥 Download Again")
            else:
                self.download_fax_btn.setText("📥 Download Attachment")
        else:
            self.download_fax_btn.setText("📥 Download Attachment")
        
        # Check if patient matched
        patient_item = self.table.item(row, 4)
        patient_id = patient_item.data(Qt.ItemDataRole.UserRole) if patient_item else None
        self.view_patient_btn.setEnabled(patient_id is not None)
    
    def _on_double_click(self, row: int, col: int):
        """Handle double-click on a row."""
        # Check if patient column and has patient
        if col == 4:
            patient_item = self.table.item(row, 4)
            patient_id = patient_item.data(Qt.ItemDataRole.UserRole) if patient_item else None
            if patient_id:
                self.open_patient.emit(patient_id)
    
    def _reply_to_message(self):
        """Open SMS dialog to reply to selected message."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return
        
        _, _, _, contact_number = self._get_counterparty(msg)
        if not contact_number:
            QMessageBox.warning(self, "Error", "No phone number is associated with this message.")
            return
        
        try:
            from dmelogic.ui.dialogs.communications import SendSMSDialog
            
            # Get patient info if matched
            patient_item = self.table.item(row, 4)
            patient_id = patient_item.data(Qt.ItemDataRole.UserRole) if patient_item else None
            patient_name = patient_item.text().replace("👤 ", "") if patient_id else ""
            
            dialog = SendSMSDialog(
                parent=self,
                patient_id=patient_id,
                to_number=contact_number,
                patient_name=patient_name
            )
            dialog.exec()
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open SMS dialog: {e}")
    
    def _view_patient(self):
        """Open the matched patient's record."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        patient_item = self.table.item(row, 4)
        patient_id = patient_item.data(Qt.ItemDataRole.UserRole) if patient_item else None
        
        if patient_id:
            self.open_patient.emit(patient_id)

    def _play_voicemail(self):
        """Download and play the voicemail audio."""
        row = self.table.currentRow()
        if row < 0:
            return

        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return

        attachments = msg.get('attachments', []) or []
        if not attachments:
            QMessageBox.warning(self, "No Audio", "This voicemail has no audio attachment.")
            return

        # Find audio attachment
        audio_att = None
        for att in attachments:
            content_type = (att.get('content_type') or '').lower()
            if 'audio' in content_type or 'wav' in content_type or 'mp3' in content_type or 'mpeg' in content_type:
                audio_att = att
                break

        if not audio_att:
            # Fallback: try the first attachment
            audio_att = attachments[0]

        uri = audio_att.get('uri', '')

        if not uri:
            QMessageBox.warning(self, "Error", "Audio attachment URI is missing.")
            return

        settings = load_settings()
        service = get_ringcentral_service(settings)
        if not service or not service.is_connected:
            QMessageBox.warning(self, "Error", "RingCentral service not available")
            return

        self.preview_text.setPlainText("⏳ Downloading voicemail...")
        QApplication.processEvents()

        try:
            # Download the audio
            result = service.download_message_attachment(uri)
            if not result:
                QMessageBox.warning(self, "Error", "Failed to download voicemail audio.")
                self.preview_text.setPlainText("❌ Download failed. Try again.")
                return

            audio_data, content_type = result

            # Save to a temp file
            import tempfile
            import subprocess
            import platform

            ext = '.mp3'
            if 'wav' in content_type:
                ext = '.wav'
            elif 'ogg' in content_type:
                ext = '.ogg'

            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name

            self.preview_text.setPlainText(f"🔊 Playing voicemail...\n\nFile: {tmp_path}")

            # Open with default audio player
            if platform.system() == 'Windows':
                os.startfile(tmp_path)
            elif platform.system() == 'Darwin':
                subprocess.run(['open', tmp_path], check=False)
            else:
                subprocess.run(['xdg-open', tmp_path], check=False)

        except Exception as e:
            debug_log(f"Inbox: Voicemail playback error: {e}")
            QMessageBox.warning(self, "Error", f"Failed to play voicemail: {e}")

    def _on_preview_attachment_clicked(self):
        """Preview attachment using the appropriate handler for the message type."""
        row = self.table.currentRow()
        if row < 0:
            return

        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return

        msg_type = (msg.get('type', '') or '').upper()
        attachments = msg.get('attachments', []) or []
        has_image = any('image' in (att.get('content_type') or '').lower() for att in attachments)

        if msg_type == 'SMS' and has_image:
            self._preview_message_attachment()
        else:
            self._preview_fax()
    
    def _download_fax(self):
        """Download the first attachment from the selected message."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return
        
        attachments = msg.get('attachments', [])
        if not attachments:
            QMessageBox.warning(self, "No Attachment", "This message has no downloadable attachment.")
            return
        
        # Get first attachment
        attachment = attachments[0]
        uri = attachment.get('uri', '')
        content_type = (attachment.get('content_type') or 'application/octet-stream').lower()
        attachment_id = attachment.get('id', '')
        message_id = str(msg.get('id', ''))
        msg_type = (msg.get('type', '') or '').lower()
        
        # Determine file extension based on content type
        if 'pdf' in content_type:
            ext = '.pdf'
        elif 'tiff' in content_type or 'tif' in content_type:
            ext = '.tiff'
        elif 'jpeg' in content_type or 'jpg' in content_type:
            ext = '.jpg'
        elif 'png' in content_type:
            ext = '.png'
        elif 'gif' in content_type:
            ext = '.gif'
        else:
            ext = ''
        
        # Ask for save location - default to FaxManagerData folder
        from_number = msg.get('from_number', 'unknown').replace('+', '')
        msg_type = (msg.get('type', '') or '').lower()
        prefix = 'fax' if msg_type == 'fax' else 'message'
        default_name = f"{prefix}_{from_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        
        # Use default inbox download folder (configured OCR folder)
        from dmelogic.paths import ocr_folder
        default_folder = str(ocr_folder())
        os.makedirs(default_folder, exist_ok=True)
        default_path = os.path.join(default_folder, default_name)
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Attachment",
            default_path,
            "PDF Files (*.pdf);;Image Files (*.png *.jpg *.jpeg *.gif *.tiff *.tif);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        # Download
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service:
            QMessageBox.warning(self, "Error", "RingCentral service not available")
            return
        
        progress = QProgressDialog("Downloading attachment...", "Cancel", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        
        try:
            if msg_type == 'fax':
                content = self._download_fax_bytes(
                    service,
                    uri,
                    message_id,
                    attachment_id
                )
            else:
                download_result = service.download_message_attachment(uri)
                if download_result:
                    content, detected_type = download_result
                    if detected_type:
                        content_type = detected_type.lower()
                else:
                    content = None

            if content:
                with open(file_path, 'wb') as f:
                    f.write(content)
                self._mark_message_downloaded(message_id, file_path)
                self.download_fax_btn.setText("📥 Download Again")
                QMessageBox.information(self, "Success", f"Attachment saved to:\n{file_path}")
            else:
                QMessageBox.warning(self, "Error", "Failed to download attachment")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Download failed: {e}")
        finally:
            progress.close()

    def _preview_message_attachment(self):
        """Preview an SMS/MMS attachment (typically an image)."""
        row = self.table.currentRow()
        if row < 0:
            return

        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return

        attachments = msg.get('attachments', []) or []
        if not attachments:
            QMessageBox.warning(self, "No Attachment", "This message has no viewable attachment.")
            return

        image_attachment = next(
            (att for att in attachments if 'image' in (att.get('content_type') or '').lower()),
            attachments[0]
        )
        uri = image_attachment.get('uri', '')
        if not uri:
            QMessageBox.warning(self, "Error", "Attachment URI is missing.")
            return

        settings = load_settings()
        service = get_ringcentral_service(settings)

        if not service:
            QMessageBox.warning(self, "Error", "RingCentral service not available")
            return

        self.preview_fax_btn.setEnabled(False)
        self.preview_fax_btn.setText("Loading...")
        QApplication.processEvents()

        try:
            debug_log(
                f"Inbox: Fetching message attachment for preview: {uri[:80] if uri else '(empty)'}"
            )
            download_result = service.download_message_attachment(uri)
            if not download_result:
                raise RuntimeError("Failed to download attachment bytes")

            content, detected_type = download_result
            content_type = detected_type or image_attachment.get('content_type') or 'application/octet-stream'
            debug_log(
                f"Inbox: Downloaded {len(content)} bytes for message attachment, content_type={content_type}"
            )

            images = self._convert_fax_to_images(content, content_type)
            if not images:
                raise RuntimeError("Could not render attachment preview")

            self._display_preview_images(images)

        except Exception as e:
            debug_log(f"Inbox: Message attachment preview failed: {e}")
            QMessageBox.warning(
                self,
                "Error",
                "Failed to preview this attachment.\n"
                "Use 'Download Attachment' to save and view it externally."
            )
        finally:
            self.preview_fax_btn.setEnabled(True)
            self.preview_fax_btn.setText("👁️ Preview Attachment")
    
    def _clear_fax_preview(self):
        """Clear any existing fax preview images."""
        # Remove all widgets from fax preview layout
        while self.fax_preview_layout.count():
            item = self.fax_preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _display_preview_images(self, images: list):
        """Render a list of image byte blobs into the preview area."""
        self._clear_fax_preview()

        for i, img_data in enumerate(images):
            page_frame = QFrame()
            page_frame.setStyleSheet("background-color: white; border: 1px solid #ccc; margin: 5px;")
            page_layout = QVBoxLayout(page_frame)
            page_layout.setContentsMargins(5, 5, 5, 5)

            page_label = QLabel(f"Page {i + 1}")
            page_label.setStyleSheet("font-weight: bold; color: #333;")
            page_layout.addWidget(page_label)

            img_label = QLabel()
            pixmap = QPixmap()
            pixmap.loadFromData(img_data)

            if pixmap.width() > 700:
                pixmap = pixmap.scaledToWidth(700, Qt.TransformationMode.SmoothTransformation)

            img_label.setPixmap(pixmap)
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            page_layout.addWidget(img_label)

            self.fax_preview_layout.addWidget(page_frame)

        self.fax_preview_layout.addStretch()
        self.preview_stack.setCurrentIndex(1)
    
    def _preview_fax(self):
        """Preview the first attachment inline without saving to file."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return
        
        attachments = msg.get('attachments', [])
        if not attachments:
            QMessageBox.warning(self, "No Attachment", "This message has no viewable attachment.")
            return
        
        # Get first attachment
        attachment = attachments[0]
        uri = attachment.get('uri', '')
        content_type = attachment.get('content_type', 'application/octet-stream')
        attachment_id = attachment.get('id', '')
        message_id = str(msg.get('id', ''))
        
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service:
            QMessageBox.warning(self, "Error", "RingCentral service not available")
            return
        
        # Show progress
        self.preview_fax_btn.setEnabled(False)
        self.preview_fax_btn.setText("Loading...")
        QApplication.processEvents()
        
        try:
            debug_log(f"Inbox: Fetching attachment from URI: {uri[:80] if uri else '(empty)'}...")
            debug_log(f"Inbox: message_id={message_id}, attachment_id={attachment_id}")
            debug_log(f"Inbox: Full attachment dict: {attachment}")
            content = self._download_fax_bytes(
                service,
                uri,
                message_id,
                attachment_id
            )
            if not content:
                debug_log("Inbox: download_fax_attachment returned None/empty")
                # Show more details in the error
                QMessageBox.warning(
                    self, 
                    "Error", 
                    f"Failed to fetch attachment for preview.\n\n"
                    f"URI: {uri[:60] if uri else '(empty)'}...\n"
                    f"Message ID: {message_id}\n"
                    f"Attachment ID: {attachment_id}"
                )
                return
            
            debug_log(f"Inbox: Downloaded {len(content)} bytes, content_type={content_type}")
            
            # Convert to images based on content type
            images = self._convert_fax_to_images(content, content_type)
            
            if not images:
                debug_log("Inbox: _convert_fax_to_images returned empty list")
                QMessageBox.warning(self, "Error", "Could not render attachment preview")
                return
            
            debug_log(f"Inbox: Got {len(images)} image(s) to display")
            self._display_preview_images(images)
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Preview failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.preview_fax_btn.setEnabled(True)
            self.preview_fax_btn.setText("👁️ Preview Attachment")
    
    def _download_fax_bytes(self, service, uri: str, message_id: str, attachment_id: str) -> Optional[bytes]:
        """Download fax attachment, handling older service signatures."""
        try:
            return service.download_fax_attachment(
                uri,
                message_id=message_id,
                attachment_id=attachment_id
            )
        except TypeError as exc:
            # Older builds of RingCentralService did not accept these keywords.
            if "unexpected keyword argument" in str(exc):
                debug_log(
                    "Inbox: download_fax_attachment signature mismatch; falling back to legacy call"
                )
                return service.download_fax_attachment(uri)
            raise

    def _convert_fax_to_images(self, content: bytes, content_type: str) -> list:
        """Convert attachment content to list of PNG image bytes for display."""
        images = []
        content_type_lower = (content_type or '').lower()
        
        debug_log(f"Inbox: Converting attachment, content_type={content_type}, size={len(content)} bytes")
        
        try:
            if 'pdf' in content_type_lower:
                # Use PyMuPDF to render PDF pages
                import fitz  # PyMuPDF
                
                doc = fitz.open(stream=content, filetype="pdf")
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    # Render at 150 DPI for good quality
                    mat = fitz.Matrix(150/72, 150/72)
                    pix = page.get_pixmap(matrix=mat)
                    images.append(pix.tobytes("png"))
                doc.close()
                
            elif 'tiff' in content_type_lower or 'tif' in content_type_lower:
                # Use PIL to handle TIFF (potentially multi-page)
                from PIL import Image
                import io
                
                img = Image.open(io.BytesIO(content))
                
                # Handle multi-page TIFF
                page_num = 0
                while True:
                    try:
                        img.seek(page_num)
                        # Convert to RGB if needed
                        if img.mode != 'RGB':
                            page_img = img.convert('RGB')
                        else:
                            page_img = img.copy()
                        
                        # Save as PNG bytes
                        buffer = io.BytesIO()
                        page_img.save(buffer, format='PNG')
                        images.append(buffer.getvalue())
                        page_num += 1
                    except EOFError:
                        break
            else:
                # For JPEG, PNG, GIF, etc. – convert through PIL to ensure
                # we have valid PNG bytes that Qt can display reliably
                from PIL import Image
                import io
                
                try:
                    img = Image.open(io.BytesIO(content))
                    # Convert to RGB if necessary (e.g., RGBA or palette modes)
                    if img.mode not in ('RGB', 'RGBA'):
                        img = img.convert('RGB')
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    images.append(buffer.getvalue())
                    debug_log(f"Inbox: Converted image via PIL, output size={len(images[-1])} bytes")
                except Exception as pil_err:
                    debug_log(f"Inbox: PIL failed ({pil_err}), using raw bytes")
                    # Last resort: pass raw bytes and hope Qt can handle it
                    images.append(content)
                
        except Exception as e:
            debug_log(f"Inbox: Fax/image conversion error: {e}")
            import traceback
            traceback.print_exc()
        
        return images

    def _mark_as_read(self):
        """Mark selected message as read."""
        row = self.table.currentRow()
        if row < 0:
            return
        
        msg = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not msg:
            return
        
        message_id = msg.get('id')
        if not message_id:
            return
        
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service:
            return
        
        result = service.mark_as_read(str(message_id))
        if result.get('success'):
            # Update local state
            msg['read_status'] = 'Read'
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, msg)
            
            # Update display
            self.table.item(row, 0).setText("○")
            self.table.item(row, 0).setForeground(QColor("#ccc"))
            
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    font = item.font()
                    font.setBold(False)
                    item.setFont(font)
            
            self.mark_read_btn.setEnabled(False)
            
            # Update count
            unread = sum(1 for m in self.messages if m.get('read_status') == 'Unread')
            self.count_label.setText(f"{len(self.messages)} messages ({unread} unread)")
        else:
            QMessageBox.warning(self, "Error", f"Failed to mark as read: {result.get('error', 'Unknown')}")

    def _delete_message(self):
        """Delete the currently selected message via RingCentral."""
        row = self.table.currentRow()
        if row < 0:
            return
        msg_item = self.table.item(row, 0)
        if not msg_item:
            return
        msg = msg_item.data(Qt.ItemDataRole.UserRole)
        if not msg:
            return

        message_id = msg.get('id')
        if not message_id:
            QMessageBox.warning(self, "Error", "This message is missing its identifier and cannot be deleted.")
            return

        msg_type = (msg.get('type') or 'Message').lower()
        subject = (msg.get('subject') or '').strip()
        preview = subject or msg.get('from_number') or msg.get('to_number') or ''
        preview_line = f"\n\nPreview: {preview[:80]}" if preview else ""
        message_text = (
            f"Are you sure you want to delete this {msg_type} from RingCentral?\n"
            "This action cannot be undone."
        )
        if preview_line:
            message_text += preview_line
        confirm = QMessageBox.question(
            self,
            "Delete Message",
            message_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        settings = load_settings()
        service = get_ringcentral_service(settings)
        if not service or not service.is_connected:
            QMessageBox.warning(self, "Error", "RingCentral service not available")
            return

        result = service.delete_message(str(message_id))
        if result.get('success'):
            self._downloaded_metadata.pop(str(message_id), None)
            self._save_downloaded_metadata()
            QMessageBox.information(self, "Deleted", "Message deleted successfully.")
            self.refresh()
        else:
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to delete message: {result.get('error', 'Unknown error')}"
            )
    
    def _auto_refresh(self):
        """Auto-refresh if dialog is visible."""
        if self.isVisible():
            self.refresh()
    
    def closeEvent(self, event):
        """Clean up on close."""
        self.refresh_timer.stop()
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.wait(1000)
        super().closeEvent(event)
