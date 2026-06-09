"""
Message Notification Service

Background service that polls for new incoming SMS and Fax messages
and shows system notifications when new messages arrive.
"""

import os
import json
from typing import Optional, Set, Dict, Any, Callable
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon

from dmelogic.config import debug_log


class MessageCheckerThread(QThread):
    """Background thread that checks for new messages."""
    
    # Signals
    new_messages = pyqtSignal(dict)  # Emits {sms: [...], fax: [...]}
    unread_counts = pyqtSignal(dict)  # Emits {sms: int, fax: int, total: int}
    error = pyqtSignal(str)
    
    def __init__(self, service, seen_ids: Set[str]):
        super().__init__()
        self.service = service
        self.seen_ids = seen_ids
        self._stop = False
    
    def stop(self):
        self._stop = True
    
    def run(self):
        if self._stop:
            return
            
        try:
            if not self.service or not self.service.is_connected:
                return
            
            new_messages = {'sms': [], 'fax': []}
            
            # Check for unread SMS
            sms_result = self.service.get_messages(
                message_type='SMS',
                direction='Inbound',
                read_status='Unread',
                per_page=50
            )
            
            if sms_result.get('success'):
                for msg in sms_result.get('messages', []):
                    msg_id = str(msg.get('id', ''))
                    if msg_id and msg_id not in self.seen_ids:
                        new_messages['sms'].append(msg)
                        self.seen_ids.add(msg_id)
            
            # Check for unread Fax
            fax_result = self.service.get_messages(
                message_type='Fax',
                direction='Inbound',
                read_status='Unread',
                per_page=50
            )
            
            if fax_result.get('success'):
                for msg in fax_result.get('messages', []):
                    msg_id = str(msg.get('id', ''))
                    if msg_id and msg_id not in self.seen_ids:
                        new_messages['fax'].append(msg)
                        self.seen_ids.add(msg_id)
            
            # Emit new messages if any
            if new_messages['sms'] or new_messages['fax']:
                self.new_messages.emit(new_messages)
            
            # Emit current unread counts
            counts = {
                'sms': sms_result.get('total', 0) if sms_result.get('success') else 0,
                'fax': fax_result.get('total', 0) if fax_result.get('success') else 0,
                'total': 0
            }
            counts['total'] = counts['sms'] + counts['fax']
            self.unread_counts.emit(counts)
            
        except Exception as e:
            self.error.emit(str(e))


class MessageNotifier(QObject):
    """
    Manages background message checking and notifications.
    
    Features:
    - Polls RingCentral every N minutes for new messages
    - Shows system tray notifications for new SMS/Fax
    - Tracks seen message IDs to avoid duplicate notifications
    - Provides unread count for menu badges
    """
    
    # Signals
    unread_count_changed = pyqtSignal(dict)  # {sms: int, fax: int, total: int}
    new_sms_received = pyqtSignal(list)  # List of new SMS messages
    new_fax_received = pyqtSignal(list)  # List of new fax messages
    
    # Default check interval (3 minutes)
    DEFAULT_INTERVAL_MS = 180000
    
    def __init__(self, parent=None, check_interval_ms: int = None):
        super().__init__(parent)
        
        self._service = None
        self._check_timer = QTimer(self)
        self._check_timer.timeout.connect(self._check_messages)
        self._checker_thread: Optional[MessageCheckerThread] = None
        self._interval = check_interval_ms or self.DEFAULT_INTERVAL_MS
        
        # Track seen message IDs to avoid duplicate notifications
        self._seen_message_ids: Set[str] = set()
        
        # Current unread counts
        self._unread_counts = {'sms': 0, 'fax': 0, 'total': 0}
        
        # System tray icon
        self._tray_icon: Optional[QSystemTrayIcon] = None
        
        # Callback for opening inbox
        self._inbox_callback: Optional[Callable] = None
        
        # Load previously seen IDs
        self._load_seen_ids()
    
    def _get_seen_ids_path(self) -> str:
        """Get path to store seen message IDs."""
        data_dir = os.path.join(os.environ.get('PROGRAMDATA', 'C:\\ProgramData'), 'DMELogic')
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, 'seen_message_ids.json')
    
    def _load_seen_ids(self):
        """Load previously seen message IDs from disk."""
        try:
            path = self._get_seen_ids_path()
            if os.path.exists(path):
                with open(path, 'r') as f:
                    data = json.load(f)
                    # Only keep IDs from the last 7 days worth of data
                    # But we'll just keep the most recent 500 IDs to limit size
                    ids = data.get('ids', [])
                    self._seen_message_ids = set(ids[-500:])
                    debug_log(f"MessageNotifier: Loaded {len(self._seen_message_ids)} seen message IDs")
        except Exception as e:
            debug_log(f"MessageNotifier: Failed to load seen IDs: {e}")
            self._seen_message_ids = set()
    
    def _save_seen_ids(self):
        """Save seen message IDs to disk."""
        try:
            path = self._get_seen_ids_path()
            # Only save most recent 500
            ids = list(self._seen_message_ids)[-500:]
            with open(path, 'w') as f:
                json.dump({'ids': ids, 'saved_at': datetime.now().isoformat()}, f)
        except Exception as e:
            debug_log(f"MessageNotifier: Failed to save seen IDs: {e}")
    
    def set_service(self, service):
        """Set the RingCentral service instance."""
        self._service = service
    
    def set_inbox_callback(self, callback: Callable):
        """Set callback to open inbox when tray notification clicked."""
        self._inbox_callback = callback
    
    def setup_tray_icon(self, icon_path: str = None):
        """Set up system tray icon for notifications."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            debug_log("MessageNotifier: System tray not available")
            return
        
        self._tray_icon = QSystemTrayIcon(self.parent())
        
        # Try to load icon
        if icon_path and os.path.exists(icon_path):
            self._tray_icon.setIcon(QIcon(icon_path))
        else:
            # Use app icon if available
            app = QApplication.instance()
            if app and app.windowIcon():
                self._tray_icon.setIcon(app.windowIcon())
        
        self._tray_icon.setToolTip("DMELogic - Message Notifications")
        
        # Create context menu
        menu = QMenu()
        open_inbox_action = menu.addAction("📬 Open Inbox")
        open_inbox_action.triggered.connect(self._on_tray_open_inbox)
        menu.addSeparator()
        check_now_action = menu.addAction("🔄 Check Now")
        check_now_action.triggered.connect(self._check_messages)
        
        self._tray_icon.setContextMenu(menu)
        
        # Connect click to open inbox
        self._tray_icon.activated.connect(self._on_tray_activated)
        
        self._tray_icon.show()
        debug_log("MessageNotifier: System tray icon set up")
    
    def _on_tray_activated(self, reason):
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Single click
            self._on_tray_open_inbox()
    
    def _on_tray_open_inbox(self):
        """Open inbox when tray clicked."""
        if self._inbox_callback:
            self._inbox_callback()
    
    def start(self):
        """Start background message checking."""
        if not self._service:
            debug_log("MessageNotifier: No service configured, not starting")
            return
        
        # Do an initial check
        self._check_messages()
        
        # Start periodic timer
        self._check_timer.start(self._interval)
        debug_log(f"MessageNotifier: Started with {self._interval // 1000}s interval")
    
    def stop(self):
        """Stop background message checking."""
        self._check_timer.stop()
        if self._checker_thread and self._checker_thread.isRunning():
            self._checker_thread.stop()
            self._checker_thread.wait(2000)
        self._save_seen_ids()
        debug_log("MessageNotifier: Stopped")
    
    def set_interval(self, interval_ms: int):
        """Change the check interval."""
        self._interval = interval_ms
        if self._check_timer.isActive():
            self._check_timer.setInterval(interval_ms)
    
    def _check_messages(self):
        """Start a background check for new messages."""
        if not self._service or not self._service.is_connected:
            return
        
        # Don't start if already checking
        if self._checker_thread and self._checker_thread.isRunning():
            return
        
        self._checker_thread = MessageCheckerThread(self._service, self._seen_message_ids)
        self._checker_thread.new_messages.connect(self._on_new_messages)
        self._checker_thread.unread_counts.connect(self._on_unread_counts)
        self._checker_thread.error.connect(self._on_check_error)
        self._checker_thread.finished.connect(self._on_check_finished)
        self._checker_thread.start()
    
    def _on_new_messages(self, messages: Dict[str, list]):
        """Handle new messages detected."""
        sms_list = messages.get('sms', [])
        fax_list = messages.get('fax', [])
        
        # Emit signals for listeners
        if sms_list:
            self.new_sms_received.emit(sms_list)
        if fax_list:
            self.new_fax_received.emit(fax_list)
        
        # Show notifications
        self._show_notifications(sms_list, fax_list)
        
        # Save updated seen IDs
        self._save_seen_ids()
    
    def _on_unread_counts(self, counts: Dict[str, int]):
        """Handle updated unread counts."""
        self._unread_counts = counts
        self.unread_count_changed.emit(counts)
        
        # Update tray tooltip
        if self._tray_icon:
            total = counts.get('total', 0)
            if total > 0:
                self._tray_icon.setToolTip(f"DMELogic - {total} unread message(s)")
            else:
                self._tray_icon.setToolTip("DMELogic - No unread messages")
    
    def _on_check_error(self, error: str):
        """Handle check error."""
        debug_log(f"MessageNotifier: Check error: {error}")
    
    def _on_check_finished(self):
        """Clean up after check completes."""
        self._checker_thread = None
    
    def _show_notifications(self, sms_list: list, fax_list: list):
        """Show system tray notifications for new messages."""
        if not self._tray_icon:
            return
        
        # Show SMS notification
        if sms_list:
            count = len(sms_list)
            if count == 1:
                msg = sms_list[0]
                # Messages from RingCentral service are normalized, but fall back if needed
                from_num = msg.get('from_number') or msg.get('from', {}).get('phoneNumber', 'Unknown')
                subject = (msg.get('subject', '') or '').strip()[:50]  # First 50 chars of message
                attachments = msg.get('attachments', []) or []
                if not subject:
                    has_image = any('image' in (att.get('content_type') or '').lower() for att in attachments)
                    subject = "Image message" if has_image else "Image/attachment message"
                title = f"📱 New SMS from {self._format_phone(from_num)}"
                body = subject or "(No text)"
            else:
                title = f"📱 {count} New SMS Messages"
                body = "Click to view inbox"
            
            self._tray_icon.showMessage(
                title,
                body,
                QSystemTrayIcon.MessageIcon.Information,
                5000  # Show for 5 seconds
            )
        
        # Show Fax notification (separate if also SMS)
        if fax_list:
            count = len(fax_list)
            if count == 1:
                msg = fax_list[0]
                from_num = msg.get('from_number') or msg.get('from', {}).get('phoneNumber', 'Unknown')
                pages = msg.get('fax_page_count') or 0
                title = f"📠 New Fax from {self._format_phone(from_num)}"
                body = f"{pages} page(s)" if pages else "New fax"
            else:
                title = f"📠 {count} New Faxes"
                body = "Click to view inbox"
            
            # Small delay if we also showed SMS notification
            if sms_list:
                QTimer.singleShot(5500, lambda: self._tray_icon.showMessage(
                    title, body, QSystemTrayIcon.MessageIcon.Information, 5000
                ))
            else:
                self._tray_icon.showMessage(
                    title,
                    body,
                    QSystemTrayIcon.MessageIcon.Information,
                    5000
                )
    
    def _format_phone(self, phone: str) -> str:
        """Format phone number for display."""
        if not phone:
            return "Unknown"
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        elif len(digits) == 11 and digits[0] == '1':
            return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        return phone
    
    @property
    def unread_counts(self) -> Dict[str, int]:
        """Get current unread counts."""
        return self._unread_counts.copy()
    
    def check_now(self):
        """Force an immediate check for new messages."""
        self._check_messages()
