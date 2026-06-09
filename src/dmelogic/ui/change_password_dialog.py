"""
Change Password Dialog

Standalone module for password change functionality.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QFrame, QFormLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


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
