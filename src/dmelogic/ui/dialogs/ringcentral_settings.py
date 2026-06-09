"""
RingCentral Settings Dialog

Provides UI for configuring RingCentral integration:
- OAuth connection/disconnection
- Test connection
- View connection status
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QFormLayout, QMessageBox,
    QProgressDialog, QFrame, QComboBox, QCheckBox, QApplication
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont
import requests

from dmelogic.settings import load_settings, save_settings
from dmelogic.services.ringcentral_service import (
    RingCentralService, RingCentralConfig, get_ringcentral_service,
    reset_ringcentral_service
)
from dmelogic.config import debug_log


class RingCentralSettingsWidget(QFrame):
    """
    Widget for RingCentral settings, can be embedded in a larger settings dialog.
    """
    
    connection_changed = pyqtSignal(bool)  # Emitted when connection state changes
    _auth_finished = pyqtSignal(bool, str)  # Internal signal for thread-safe callback
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._service: RingCentralService = None
        self._progress_dialog = None
        self._setup_ui()
        self._load_current_settings()
        self._update_connection_status()
        
        # Connect internal signal for thread-safe auth callback
        self._auth_finished.connect(self._on_auth_finished)
    
    def _setup_ui(self):
        """Build the settings UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Title
        title_label = QLabel("RingCentral Integration")
        title_label.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        layout.addWidget(title_label)
        
        description = QLabel(
            "Connect to RingCentral for SMS, Fax, and Click-to-Call functionality. "
            "You will need RingCentral API credentials (Client ID and Secret)."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(description)
        
        # Connection Status
        self.status_group = QGroupBox("Connection Status")
        status_layout = QHBoxLayout(self.status_group)
        
        self.status_indicator = QLabel("●")
        self.status_indicator.setFont(QFont("Segoe UI", 14))
        status_layout.addWidget(self.status_indicator)
        
        self.status_label = QLabel("Not Connected")
        self.status_label.setFont(QFont("Segoe UI", 10))
        status_layout.addWidget(self.status_label, 1)
        
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        status_layout.addWidget(self.test_btn)
        
        layout.addWidget(self.status_group)
        
        # API Credentials
        creds_group = QGroupBox("API Credentials")
        creds_layout = QFormLayout(creds_group)
        
        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText("Enter your RingCentral Client ID")
        creds_layout.addRow("Client ID:", self.client_id_edit)
        
        self.client_secret_edit = QLineEdit()
        self.client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret_edit.setPlaceholderText("Enter your RingCentral Client Secret")
        creds_layout.addRow("Client Secret:", self.client_secret_edit)
        
        # Show/hide secret toggle
        self.show_secret_cb = QCheckBox("Show secret")
        self.show_secret_cb.toggled.connect(self._toggle_secret_visibility)
        creds_layout.addRow("", self.show_secret_cb)
        
        # Server URL
        self.server_combo = QComboBox()
        self.server_combo.addItem("Production (platform.ringcentral.com)", "https://platform.ringcentral.com")
        self.server_combo.addItem("Sandbox (platform.devtest.ringcentral.com)", "https://platform.devtest.ringcentral.com")
        creds_layout.addRow("Server:", self.server_combo)
        
        # Phone Number (manual fallback)
        self.phone_number_edit = QLineEdit()
        self.phone_number_edit.setPlaceholderText("e.g., +1 (555) 123-4567")
        creds_layout.addRow("Your Phone #:", self.phone_number_edit)
        
        phone_help = QLabel("<i>Enter your RingCentral phone number for SMS/Fax</i>")
        phone_help.setStyleSheet("color: #666; font-size: 10px;")
        creds_layout.addRow("", phone_help)
        
        layout.addWidget(creds_group)
        
        # Actions
        actions_layout = QHBoxLayout()
        
        self.save_btn = QPushButton("Save Credentials")
        self.save_btn.clicked.connect(lambda: self._save_credentials(show_confirmation=True))
        actions_layout.addWidget(self.save_btn)
        
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setStyleSheet(
            "QPushButton { background-color: #28a745; color: white; font-weight: bold; }"
            "QPushButton:disabled { background-color: #888; color: #ccc; }"
        )
        self.connect_btn.clicked.connect(self._connect)
        actions_layout.addWidget(self.connect_btn)
        
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setStyleSheet(
            "QPushButton { background-color: #dc3545; color: white; }"
            "QPushButton:disabled { background-color: #888; color: #ccc; }"
        )
        self.disconnect_btn.clicked.connect(self._disconnect)
        actions_layout.addWidget(self.disconnect_btn)
        
        layout.addLayout(actions_layout)
        
        # Help text
        help_label = QLabel(
            "<b>Setup Instructions:</b><br>"
            "1. Log in to <a href='https://developers.ringcentral.com'>developers.ringcentral.com</a><br>"
            "2. Create or select an application<br>"
            "3. Copy the Client ID and Secret<br>"
            "4. Set OAuth Redirect URI to: <code>http://127.0.0.1:8765/callback</code><br>"
            "5. Enable required permissions: SMS, Fax, Call Control, Read Messages"
        )
        help_label.setOpenExternalLinks(True)
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #666; font-size: 11px; margin-top: 10px;")
        layout.addWidget(help_label)
        
        layout.addStretch()
    
    def _toggle_secret_visibility(self, checked: bool):
        """Toggle password visibility for client secret."""
        if checked:
            self.client_secret_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
    
    def _load_current_settings(self):
        """Load current RingCentral settings."""
        settings = load_settings()
        rc_settings = settings.get('ringcentral', {})
        
        self.client_id_edit.setText(rc_settings.get('client_id', ''))
        self.client_secret_edit.setText(rc_settings.get('client_secret', ''))
        self.phone_number_edit.setText(rc_settings.get('phone_number', ''))
        
        server_url = rc_settings.get('server_url', 'https://platform.ringcentral.com')
        index = self.server_combo.findData(server_url)
        if index >= 0:
            self.server_combo.setCurrentIndex(index)
    
    def _save_credentials(self, show_confirmation: bool = True):
        """Save credentials to settings.
        
        Args:
            show_confirmation: Show a message box after saving (False when called from _connect).
        """
        settings = load_settings()
        
        if 'ringcentral' not in settings:
            settings['ringcentral'] = {}
        
        settings['ringcentral']['client_id'] = self.client_id_edit.text().strip()
        settings['ringcentral']['client_secret'] = self.client_secret_edit.text().strip()
        settings['ringcentral']['server_url'] = self.server_combo.currentData()
        settings['ringcentral']['phone_number'] = self.phone_number_edit.text().strip()
        settings['ringcentral']['redirect_uri'] = 'http://127.0.0.1:8765/callback'
        
        save_settings(settings)
        
        # Reset service to pick up new config
        reset_ringcentral_service()
        
        if show_confirmation:
            QMessageBox.information(
                self,
                "Saved",
                "RingCentral credentials saved. Click 'Connect' to authorize."
            )
    
    def _update_connection_status(self):
        """Update the connection status display."""
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if service and service.is_connected:
            self.status_indicator.setText("●")
            self.status_indicator.setStyleSheet("color: #28a745; font-size: 16px;")
            self.status_label.setText("Connected to RingCentral")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.test_btn.setEnabled(True)
        else:
            self.status_indicator.setText("●")
            self.status_indicator.setStyleSheet("color: #dc3545; font-size: 16px;")
            self.status_label.setText("Not Connected")
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.test_btn.setEnabled(False)
    
    def _connect(self):
        """Start OAuth authorization flow."""
        # Ensure credentials are saved first
        client_id = self.client_id_edit.text().strip()
        client_secret = self.client_secret_edit.text().strip()
        
        if not client_id or not client_secret:
            QMessageBox.warning(
                self,
                "Missing Credentials",
                "Please enter your RingCentral Client ID and Client Secret first."
            )
            return
        
        # Save credentials (silently — no confirmation dialog)
        self._save_credentials(show_confirmation=False)
        
        # Show progress dialog
        self._progress_dialog = QProgressDialog(
            "Opening browser for authorization...\n\nPlease complete the sign-in process.",
            "Cancel",
            0, 0,
            self
        )
        self._progress_dialog.setWindowTitle("Connecting to RingCentral")
        self._progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress_dialog.setMinimumDuration(0)
        self._progress_dialog.show()
        
        # Get service and start auth
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service:
            self._progress_dialog.close()
            self._progress_dialog = None
            QMessageBox.critical(
                self,
                "Configuration Error",
                "Failed to initialize RingCentral service. Check your credentials."
            )
            return
        
        def on_auth_complete(success: bool, error: str):
            # Emit signal to handle callback on main thread
            self._auth_finished.emit(success, error)
        
        # Start async authorization
        service.authorize_async(on_auth_complete, timeout=120)
    
    def _on_auth_finished(self, success: bool, error: str):
        """Handle auth completion on main thread (called via signal)."""
        if self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None
        
        if success:
            self._update_connection_status()
            self.connection_changed.emit(True)
            QMessageBox.information(
                self,
                "Connected",
                "Successfully connected to RingCentral!"
            )
        else:
            QMessageBox.warning(
                self,
                "Connection Failed",
                f"Failed to connect to RingCentral:\n{error}"
            )
    
    def _disconnect(self):
        """Disconnect from RingCentral."""
        reply = QMessageBox.question(
            self,
            "Disconnect",
            "Are you sure you want to disconnect from RingCentral?\n\n"
            "You will need to re-authorize to use SMS, Fax, and Call features.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if service:
            service.disconnect()
        
        reset_ringcentral_service()
        self._update_connection_status()
        self.connection_changed.emit(False)
        
        QMessageBox.information(
            self,
            "Disconnected",
            "Disconnected from RingCentral."
        )
    
    def _test_connection(self):
        """Test the RingCentral connection."""
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service:
            QMessageBox.warning(self, "Not Configured", "RingCentral is not configured.")
            return
        
        result = service.test_connection()

        # Backward-compatibility fallback: older service builds probe /account/~
        # which can return 403 even when extension-scoped access is valid.
        try:
            msg = str(result.get('message', ''))
            if (not result.get('success')) and ('/restapi/v1.0/account/~' in msg) and ('403' in msg):
                ext_url = f"{service.config.server_url}/restapi/v1.0/account/~/extension/~"
                ext_resp = requests.get(ext_url, headers=service._get_auth_header(), timeout=10)
                if ext_resp.ok:
                    ext = ext_resp.json() if ext_resp.content else {}
                    ext_name = ext.get('name') or ext.get('contact', {}).get('firstName') or 'RingCentral extension'
                    result = {
                        'success': True,
                        'message': f"Connected to {ext_name}",
                        'account_info': ext,
                    }
                elif ext_resp.status_code == 403:
                    result = {
                        'success': False,
                        'message': (
                            'Connected token exists, but API permissions are insufficient. '
                            'Enable read scopes (Accounts/Extensions) in RingCentral app settings, then reconnect.'
                        ),
                    }
        except Exception as e:
            debug_log(f"RingCentral UI fallback test failed: {e}")
        
        if result['success']:
            limited_note = ""
            if result.get('limited_permissions'):
                limited_note = (
                    "\n\nNote: Some optional RingCentral read endpoints are restricted for this account. "
                    "SMS/Fax/call features can still work with your current authorization."
                )
            QMessageBox.information(
                self,
                "Connection Successful",
                f"✓ {result['message']}\n\n"
                f"Account: {result.get('account_info', {}).get('name', 'Unknown')}"
                f"{limited_note}"
            )
        else:
            QMessageBox.warning(
                self,
                "Connection Failed",
                f"✗ {result['message']}"
            )


class RingCentralSettingsDialog(QDialog):
    """
    Standalone dialog for RingCentral settings.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RingCentral Settings")
        self.setMinimumSize(500, 450)
        
        layout = QVBoxLayout(self)
        
        self.settings_widget = RingCentralSettingsWidget(self)
        layout.addWidget(self.settings_widget)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
