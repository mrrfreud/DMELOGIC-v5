"""
Login Dialog

Provides the login UI for user authentication.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QFrame, QCheckBox, QFormLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeyEvent

from ..db.users import initialize_auth_system
from ..security.auth import login, get_session


class LoginDialog(QDialog):
    """Login dialog for user authentication"""
    
    def __init__(self, parent=None, folder_path: str = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self._first_run = False
        
        self.setWindowTitle("DMELogic - Login")
        self.setFixedSize(400, 340)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        # Initialize auth system (creates DB, seeds defaults, ensures admin)
        self._first_run = initialize_auth_system(folder_path)
        
        self._setup_ui()
        
        # Show first-run message if admin was just created
        if self._first_run:
            QMessageBox.information(
                self,
                "First Run",
                "Welcome to DMELogic!\n\n"
                "A default admin account has been created:\n"
                "Username: admin\n"
                "Password: admin123\n\n"
                "Please log in and change your password immediately."
            )
    
    def _setup_ui(self):
        """Set up the login UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)
        
        # Header
        header = QLabel("🔐 Login")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        layout.addWidget(header)
        
        subtitle = QLabel("Enter your credentials to continue")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)
        
        layout.addSpacing(10)
        
        # Form
        form_frame = QFrame()
        form_layout = QFormLayout(form_frame)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)
        
        # Username
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Enter username")
        self.username_edit.setMinimumHeight(35)
        form_layout.addRow("Username:", self.username_edit)
        
        # Password
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Enter password")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMinimumHeight(35)
        form_layout.addRow("Password:", self.password_edit)
        
        layout.addWidget(form_frame)
        
        # Show password checkbox
        self.show_password_cb = QCheckBox("Show password")
        self.show_password_cb.toggled.connect(self._toggle_password_visibility)
        layout.addWidget(self.show_password_cb)
        
        layout.addStretch()
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        self.login_btn = QPushButton("Login")
        self.login_btn.setMinimumHeight(40)
        self.login_btn.setDefault(True)
        self.login_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078D4;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background-color: #1084D8;
            }
            QPushButton:pressed {
                background-color: #006CBD;
            }
        """)
        self.login_btn.clicked.connect(self._on_login)
        
        self.cancel_btn = QPushButton("Exit")
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.login_btn)
        
        layout.addLayout(btn_layout)
        
        # Focus username field
        self.username_edit.setFocus()
        
        # Enter key triggers login
        self.password_edit.returnPressed.connect(self._on_login)
        self.username_edit.returnPressed.connect(lambda: self.password_edit.setFocus())
    
    def _toggle_password_visibility(self, show: bool):
        """Toggle password visibility"""
        if show:
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
    
    def _on_login(self):
        """Handle login attempt"""
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        
        if not username:
            QMessageBox.warning(self, "Login", "Please enter your username.")
            self.username_edit.setFocus()
            return
        
        if not password:
            QMessageBox.warning(self, "Login", "Please enter your password.")
            self.password_edit.setFocus()
            return
        
        # Check failed-login lockout before attempting auth.
        _auth_db = None
        try:
            from dmelogic.security.lockout import check_lockout, record_attempt
            from dmelogic.core.config import get_config
            from dmelogic.paths import db_dir
            _auth_db = db_dir() / "auth.db"
            cfg = get_config().session
            status = check_lockout(
                _auth_db, username,
                max_attempts=cfg.failed_login_lockout_attempts,
                window_minutes=cfg.failed_login_lockout_minutes,
                lockout_minutes=cfg.failed_login_lockout_minutes,
            )
            if status.locked:
                until_str = status.locked_until.strftime("%H:%M") if status.locked_until else "soon"
                QMessageBox.warning(
                    self, "Account Locked",
                    f"Too many failed login attempts.\n\nThis account is locked until {until_str}.\n"
                    "Please wait and try again.",
                )
                return
        except Exception:
            pass  # fail open — a broken lockout DB must never block a valid user

        # Attempt login
        success, message = login(username, password, self.folder_path)

        # Record the attempt so future lockout checks have accurate data.
        try:
            if _auth_db is not None:
                record_attempt(_auth_db, username, success)
        except Exception:
            pass

        if success:
            session = get_session()
            
            # Check if password change is required
            if session.force_password_change:
                self._prompt_password_change()
            
            self.accept()
        else:
            QMessageBox.warning(self, "Login Failed", message)
            self.password_edit.clear()
            self.password_edit.setFocus()
    
    def _prompt_password_change(self):
        """Prompt user to change their password"""
        from .change_password_dialog import ChangePasswordDialog
        
        QMessageBox.information(
            self,
            "Password Change Required",
            "You must change your password before continuing."
        )
        
        dialog = ChangePasswordDialog(self, force_change=True, folder_path=self.folder_path)
        while True:
            result = dialog.exec()
            if result == QDialog.DialogCode.Accepted:
                break
            else:
                # User cancelled - warn them they must change password
                reply = QMessageBox.question(
                    self,
                    "Password Change Required",
                    "You must change your password to continue.\n\n"
                    "Do you want to try again?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    # Log them out and reject login
                    from ..security.auth import logout
                    logout(self.folder_path)
                    self.reject()
                    return
    
    def keyPressEvent(self, event: QKeyEvent):
        """Handle key press events"""
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class ChangePasswordDialog(QDialog):
    """Dialog for changing password"""
    
    def __init__(self, parent=None, force_change: bool = False, folder_path: str = None):
        super().__init__(parent)
        self.force_change = force_change
        self.folder_path = folder_path
        
        title = "Change Password" if not force_change else "Set New Password"
        self.setWindowTitle(f"DMELogic - {title}")
        self.setFixedSize(400, 300)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)
        
        # Header
        header = QLabel("🔑 Change Password")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        layout.addWidget(header)
        
        if self.force_change:
            notice = QLabel("You must set a new password to continue.")
            notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            notice.setStyleSheet("color: #E65100;")
            layout.addWidget(notice)
        
        layout.addSpacing(10)
        
        # Form
        form_frame = QFrame()
        form_layout = QFormLayout(form_frame)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)
        
        # Current password
        self.current_password_edit = QLineEdit()
        self.current_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.current_password_edit.setMinimumHeight(35)
        form_layout.addRow("Current:", self.current_password_edit)
        
        # New password
        self.new_password_edit = QLineEdit()
        self.new_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password_edit.setMinimumHeight(35)
        form_layout.addRow("New:", self.new_password_edit)
        
        # Confirm password
        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_edit.setMinimumHeight(35)
        form_layout.addRow("Confirm:", self.confirm_password_edit)
        
        layout.addWidget(form_frame)
        
        # Requirements
        req_label = QLabel("Password must be at least 6 characters")
        req_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(req_label)
        
        layout.addStretch()
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        if not self.force_change:
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setMinimumHeight(35)
            self.cancel_btn.clicked.connect(self.reject)
            btn_layout.addWidget(self.cancel_btn)
        
        self.save_btn = QPushButton("Save Password")
        self.save_btn.setMinimumHeight(35)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078D4;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background-color: #1084D8;
            }
        """)
        self.save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(self.save_btn)
        
        layout.addLayout(btn_layout)
        
        self.current_password_edit.setFocus()
    
    def _on_save(self):
        """Handle save password"""
        current = self.current_password_edit.text()
        new_pw = self.new_password_edit.text()
        confirm = self.confirm_password_edit.text()
        
        if not current:
            QMessageBox.warning(self, "Error", "Please enter your current password.")
            self.current_password_edit.setFocus()
            return
        
        if not new_pw:
            QMessageBox.warning(self, "Error", "Please enter a new password.")
            self.new_password_edit.setFocus()
            return
        
        if len(new_pw) < 6:
            QMessageBox.warning(self, "Error", "Password must be at least 6 characters.")
            self.new_password_edit.setFocus()
            return
        
        if new_pw != confirm:
            QMessageBox.warning(self, "Error", "Passwords do not match.")
            self.confirm_password_edit.clear()
            self.confirm_password_edit.setFocus()
            return
        
        # Attempt password change
        from ..security.auth import change_password
        
        success, message = change_password(current, new_pw, self.folder_path)
        
        if success:
            QMessageBox.information(self, "Success", "Password changed successfully!")
            self.accept()
        else:
            QMessageBox.warning(self, "Error", message)
            self.current_password_edit.clear()
            self.current_password_edit.setFocus()
