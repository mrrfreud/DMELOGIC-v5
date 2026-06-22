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

from ..db.users import (
    initialize_auth_system,
    get_user_by_username,
    get_user_count,
    reset_or_create_admin_user,
)
from ..security.auth import login, get_session


class LoginDialog(QDialog):
    """Login dialog for user authentication"""
    
    def __init__(self, parent=None, folder_path: str = None):
        super().__init__(parent)
        self.folder_path = folder_path
        self._first_run = False
        self._init_error = None
        
        self.setWindowTitle("DMELogic - Login")
        self.setFixedSize(400, 340)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        # Initialize auth system (creates DB, seeds defaults, ensures admin)
        try:
            self._first_run = initialize_auth_system(folder_path)
        except Exception as exc:
            self._init_error = str(exc)
            self._first_run = False
        
        self._setup_ui()

        if self._init_error:
            QMessageBox.critical(
                self,
                "Authentication Setup Error",
                "Login initialization failed.\n\n"
                f"{self._init_error}\n\n"
                "You can try the Reset Login button to recreate admin access.",
            )
        
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

        # Recovery action for account issues (forgotten credentials, missing user)
        self.reset_btn = QPushButton("Reset Login")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setStyleSheet(
            "QPushButton { border: none; color: #0B57D0; text-decoration: underline; }"
            "QPushButton:hover { color: #0948AA; }"
        )
        self.reset_btn.clicked.connect(self._on_reset_login)
        recovery_layout = QHBoxLayout()
        recovery_layout.addStretch()
        recovery_layout.addWidget(self.reset_btn)
        layout.addLayout(recovery_layout)
        
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
        record_attempt_fn = None
        try:
            from dmelogic.security.lockout import check_lockout, record_attempt
            from dmelogic.core.config import get_config
            from dmelogic.paths import db_dir
            _auth_db = db_dir() / "auth.db"
            record_attempt_fn = record_attempt
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

        user_exists = False
        try:
            user_exists = get_user_by_username(username, self.folder_path) is not None
        except Exception:
            pass

        # Attempt login
        try:
            success, message = login(username, password, self.folder_path)
        except Exception as exc:
            # Keep login failures user-visible and actionable instead of crashing the app.
            if _auth_db is not None and record_attempt_fn is not None:
                try:
                    record_attempt_fn(_auth_db, username, False)
                except Exception:
                    pass
            QMessageBox.critical(
                self,
                "Login Error",
                "Authentication failed due to an internal error.\n\n"
                f"{exc}\n\n"
                "Use Reset Login to recover admin access if this keeps happening.",
            )
            self.password_edit.clear()
            self.password_edit.setFocus()
            return

        # Record the attempt so future lockout checks have accurate data.
        try:
            if _auth_db is not None and record_attempt_fn is not None:
                record_attempt_fn(_auth_db, username, success)
        except Exception:
            pass

        if success:
            session = get_session()
            
            # Check if password change is required
            if session.force_password_change:
                self._prompt_password_change()
            
            self.accept()
        else:
            failure_message = message
            if not user_exists and message == "Invalid username or password":
                failure_message = (
                    f"No account was found for username '{username}'.\n\n"
                    "Try the admin account, or click Reset Login to recover access."
                )
                try:
                    if get_user_count(self.folder_path) == 1:
                        failure_message += "\n\nHint: this install currently has only one account."
                except Exception:
                    pass

            QMessageBox.warning(self, "Login Failed", failure_message)
            self.password_edit.clear()
            self.password_edit.setFocus()

    def _on_reset_login(self):
        """Reset admin access for local recovery on the login screen."""
        reply = QMessageBox.question(
            self,
            "Reset Login",
            "This will reset the admin account password to the default value.\n\n"
            "Username: admin\n"
            "Password: admin123\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            created, user = reset_or_create_admin_user(
                self.folder_path,
                username="admin",
                display_name="Administrator",
                new_password="admin123",
                force_password_change=True,
            )

            try:
                from dmelogic.paths import db_dir
                from dmelogic.security.lockout import clear_attempts
                clear_attempts(db_dir() / "auth.db", user.get("username", "admin"))
            except Exception:
                pass

            self.username_edit.setText(str(user.get("username", "admin")))
            self.password_edit.clear()
            self.password_edit.setFocus()

            QMessageBox.information(
                self,
                "Login Reset Complete",
                (
                    "Admin access has been reset.\n\n"
                    f"Account {'created' if created else 'updated'}: {user.get('username', 'admin')}\n"
                    "Password: admin123\n\n"
                    "Sign in now and change the password immediately."
                ),
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Reset Failed",
                "Could not reset login credentials.\n\n"
                f"{exc}",
            )
    
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
