"""
Users Administration Dialog

Provides UI for managing users, roles, and permissions.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QListWidget, QListWidgetItem, QTabWidget, QWidget, QSplitter,
    QTextEdit, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from typing import Optional, List

from ...db.users import (
    get_all_users, get_all_roles, get_user_roles, get_user_by_id,
    create_user, update_user, set_user_password, set_user_active,
    set_user_roles, get_audit_logs, log_audit,
)
from ...security.auth import get_session, is_current_user_agent
from ...security.permissions import require_perm, has_permission


class UsersAdminDialog(QDialog):
    """Main dialog for user administration"""
    
    user_updated = pyqtSignal()  # Signal emitted when users are modified
    
    def __init__(self, parent=None, folder_path: str = None):
        super().__init__(parent)
        self.folder_path = folder_path
        
        self.setWindowTitle("User Administration")
        self.setMinimumSize(900, 600)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        self._setup_ui()
        self._load_users()
    
    def _setup_ui(self):
        """Set up the admin UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # Header
        header_layout = QHBoxLayout()
        header = QLabel("👤 User Administration")
        header.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        header_layout.addWidget(header)
        header_layout.addStretch()
        
        # Current user info
        session = get_session()
        user_label = QLabel(f"Logged in as: {session.display_name}")
        user_label.setStyleSheet("color: #666;")
        header_layout.addWidget(user_label)
        
        layout.addLayout(header_layout)
        
        # Tab widget
        tabs = QTabWidget()
        
        # Users tab
        users_tab = self._create_users_tab()
        tabs.addTab(users_tab, "👥 Users")
        
        # Audit Log tab
        audit_tab = self._create_audit_tab()
        tabs.addTab(audit_tab, "📋 Audit Log")
        
        layout.addWidget(tabs)
        
        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
    
    def _create_users_tab(self) -> QWidget:
        """Create the users management tab"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Left side - user list
        left_panel = QFrame()
        left_layout = QVBoxLayout(left_panel)
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self.add_user_btn = QPushButton("➕ Add User")
        self.add_user_btn.clicked.connect(self._on_add_user)
        toolbar.addWidget(self.add_user_btn)
        
        toolbar.addStretch()
        
        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setToolTip("Refresh")
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.clicked.connect(self._load_users)
        toolbar.addWidget(self.refresh_btn)
        
        left_layout.addLayout(toolbar)
        
        # Users table
        self.users_table = QTableWidget()
        self.users_table.setColumnCount(6)
        self.users_table.setHorizontalHeaderLabels(["ID", "Username", "Display Name", "Roles", "Agent", "Active"])
        self.users_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.users_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.users_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.users_table.verticalHeader().setVisible(False)
        self.users_table.setAlternatingRowColors(True)
        
        header = self.users_table.horizontalHeader()
        header.resizeSection(0, 50)   # ID
        header.resizeSection(1, 120)  # Username
        header.resizeSection(2, 150)  # Display Name
        header.resizeSection(3, 150)  # Roles
        header.resizeSection(4, 60)   # Agent
        header.resizeSection(5, 60)   # Active
        header.setStretchLastSection(True)
        
        self.users_table.itemSelectionChanged.connect(self._on_user_selected)
        
        left_layout.addWidget(self.users_table)
        
        # Right side - user details/edit
        right_panel = QFrame()
        right_panel.setFrameStyle(QFrame.Shape.StyledPanel)
        right_layout = QVBoxLayout(right_panel)
        
        # User details form
        details_group = QGroupBox("User Details")
        details_layout = QFormLayout(details_group)
        
        self.detail_username = QLineEdit()
        self.detail_username.setPlaceholderText("Required")
        details_layout.addRow("Username:", self.detail_username)
        
        self.detail_display_name = QLineEdit()
        self.detail_display_name.setPlaceholderText("Full name")
        details_layout.addRow("Display Name:", self.detail_display_name)
        
        self.detail_active = QCheckBox("Account Active")
        self.detail_active.setChecked(True)
        details_layout.addRow("", self.detail_active)
        
        self.detail_is_agent = QCheckBox("Agent Account")
        self.detail_is_agent.setToolTip(
            "Agent accounts are used by AI assistants.\n"
            "Orders created by agents go to Pending Approval\n"
            "and must be reviewed before entering the orders list."
        )
        self.detail_is_agent.setStyleSheet("""
            QCheckBox {
                color: #E65100;
                font-weight: bold;
            }
        """)
        details_layout.addRow("", self.detail_is_agent)
        
        right_layout.addWidget(details_group)
        
        # Roles selection
        roles_group = QGroupBox("Roles")
        roles_layout = QVBoxLayout(roles_group)
        
        self.roles_list = QListWidget()
        self.roles_list.setMaximumHeight(120)
        roles_layout.addWidget(self.roles_list)
        
        right_layout.addWidget(roles_group)
        
        # Password section
        password_group = QGroupBox("Password")
        password_layout = QVBoxLayout(password_group)
        
        self.new_password = QLineEdit()
        self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password.setPlaceholderText("Leave blank to keep current")
        password_layout.addWidget(QLabel("New Password:"))
        password_layout.addWidget(self.new_password)
        
        self.reset_password_btn = QPushButton("Reset to Temp Password")
        self.reset_password_btn.clicked.connect(self._on_reset_password)
        password_layout.addWidget(self.reset_password_btn)
        
        right_layout.addWidget(password_group)
        
        right_layout.addStretch()
        
        # Action buttons
        action_layout = QHBoxLayout()
        
        self.save_btn = QPushButton("💾 Save")
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078D4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #1084D8; }
        """)
        self.save_btn.clicked.connect(self._on_save_user)
        
        self.delete_btn = QPushButton("🗑️ Delete")
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #D32F2F;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #E53935; }
        """)
        self.delete_btn.clicked.connect(self._on_delete_user)
        
        action_layout.addWidget(self.delete_btn)
        action_layout.addStretch()
        action_layout.addWidget(self.save_btn)
        
        right_layout.addLayout(action_layout)
        
        # Add panels to splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([400, 350])
        
        layout.addWidget(splitter)
        
        # Load roles into list
        self._load_roles()
        
        # Initial state
        self._set_edit_mode(False)
        
        return widget
    
    def _create_audit_tab(self) -> QWidget:
        """Create the audit log tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Filters
        filter_layout = QHBoxLayout()
        
        filter_layout.addWidget(QLabel("Action:"))
        self.audit_action_filter = QComboBox()
        self.audit_action_filter.addItems([
            "All", "auth.login", "auth.logout", "auth.password_changed",
            "inventory.delete", "financial.edit", "orders.unlock_override"
        ])
        self.audit_action_filter.currentTextChanged.connect(self._load_audit_logs)
        filter_layout.addWidget(self.audit_action_filter)
        
        filter_layout.addStretch()
        
        refresh_audit_btn = QPushButton("🔄 Refresh")
        refresh_audit_btn.clicked.connect(self._load_audit_logs)
        filter_layout.addWidget(refresh_audit_btn)
        
        layout.addLayout(filter_layout)
        
        # Audit table
        self.audit_table = QTableWidget()
        self.audit_table.setColumnCount(6)
        self.audit_table.setHorizontalHeaderLabels([
            "Timestamp", "User", "Action", "Entity Type", "Entity ID", "Details"
        ])
        self.audit_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.audit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.audit_table.verticalHeader().setVisible(False)
        self.audit_table.setAlternatingRowColors(True)
        
        header = self.audit_table.horizontalHeader()
        header.resizeSection(0, 150)  # Timestamp
        header.resizeSection(1, 100)  # User
        header.resizeSection(2, 150)  # Action
        header.resizeSection(3, 100)  # Entity Type
        header.resizeSection(4, 80)   # Entity ID
        header.setStretchLastSection(True)
        
        layout.addWidget(self.audit_table)
        
        # Load initial data
        self._load_audit_logs()
        
        return widget
    
    def _load_roles(self):
        """Load roles into the roles list"""
        self.roles_list.clear()
        roles = get_all_roles(self.folder_path)
        
        for role in roles:
            item = QListWidgetItem(role['name'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.roles_list.addItem(item)
    
    def _load_users(self):
        """Load users into the table"""
        self.users_table.setRowCount(0)
        users = get_all_users(self.folder_path)
        
        for user in users:
            row = self.users_table.rowCount()
            self.users_table.insertRow(row)
            
            # ID
            id_item = QTableWidgetItem(str(user['id']))
            id_item.setData(Qt.ItemDataRole.UserRole, user['id'])
            self.users_table.setItem(row, 0, id_item)
            
            # Username
            self.users_table.setItem(row, 1, QTableWidgetItem(user['username']))
            
            # Display Name
            self.users_table.setItem(row, 2, QTableWidgetItem(user.get('display_name', '')))
            
            # Roles
            roles = get_user_roles(user['id'], self.folder_path)
            self.users_table.setItem(row, 3, QTableWidgetItem(", ".join(roles)))
            
            # Agent
            is_agent = bool(user.get('is_agent', 0))
            agent_item = QTableWidgetItem("🤖" if is_agent else "")
            if is_agent:
                agent_item.setForeground(QColor("#E65100"))
            self.users_table.setItem(row, 4, agent_item)
            
            # Active
            active_item = QTableWidgetItem("✓" if user.get('is_active', 0) else "✗")
            active_item.setForeground(QColor("#4CAF50") if user.get('is_active', 0) else QColor("#F44336"))
            self.users_table.setItem(row, 5, active_item)
        
        self._set_edit_mode(False)
    
    def _load_audit_logs(self):
        """Load audit logs into the table"""
        self.audit_table.setRowCount(0)
        
        action_filter = self.audit_action_filter.currentText()
        if action_filter == "All":
            action_filter = None
        
        logs = get_audit_logs(
            limit=200,
            action_filter=action_filter,
            folder_path=self.folder_path
        )
        
        for log in logs:
            row = self.audit_table.rowCount()
            self.audit_table.insertRow(row)
            
            self.audit_table.setItem(row, 0, QTableWidgetItem(log.get('at', '')))
            self.audit_table.setItem(row, 1, QTableWidgetItem(log.get('username', '')))
            self.audit_table.setItem(row, 2, QTableWidgetItem(log.get('action', '')))
            self.audit_table.setItem(row, 3, QTableWidgetItem(log.get('entity_type', '')))
            self.audit_table.setItem(row, 4, QTableWidgetItem(str(log.get('entity_id', ''))))
            self.audit_table.setItem(row, 5, QTableWidgetItem(log.get('details', '')))
    
    def _on_user_selected(self):
        """Handle user selection"""
        selected = self.users_table.selectedItems()
        if not selected:
            self._set_edit_mode(False)
            return
        
        row = selected[0].row()
        user_id = self.users_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        
        self._current_user_id = user_id
        self._load_user_details(user_id)
        self._set_edit_mode(True)
    
    def _load_user_details(self, user_id: int):
        """Load user details into the form"""
        user = get_user_by_id(user_id, self.folder_path)
        if not user:
            return
        
        self.detail_username.setText(user['username'])
        self.detail_display_name.setText(user.get('display_name', ''))
        self.detail_active.setChecked(bool(user.get('is_active', 0)))
        self.detail_is_agent.setChecked(bool(user.get('is_agent', 0)))
        
        # Load roles
        user_roles = set(get_user_roles(user_id, self.folder_path))
        for i in range(self.roles_list.count()):
            item = self.roles_list.item(i)
            if item.text() in user_roles:
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
        
        self.new_password.clear()
    
    def _set_edit_mode(self, editing: bool):
        """Set whether we're editing an existing user or creating new"""
        self._is_new_user = not editing
        self._current_user_id = None if not editing else getattr(self, '_current_user_id', None)
        
        if not editing:
            self.detail_username.clear()
            self.detail_display_name.clear()
            self.detail_active.setChecked(True)
            self.detail_is_agent.setChecked(False)
            self.new_password.clear()
            
            for i in range(self.roles_list.count()):
                self.roles_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        
        self.detail_username.setReadOnly(editing)  # Can't change username
        self.delete_btn.setEnabled(editing)
        self.reset_password_btn.setEnabled(editing)
    
    def _on_add_user(self):
        """Handle add new user"""
        self.users_table.clearSelection()
        self._set_edit_mode(False)
        self._is_new_user = True
        self.detail_username.setReadOnly(False)
        self.detail_username.setFocus()
    
    def _on_save_user(self):
        """Handle save user"""
        username = self.detail_username.text().strip()
        display_name = self.detail_display_name.text().strip()
        is_active = self.detail_active.isChecked()
        is_agent = self.detail_is_agent.isChecked()
        password = self.new_password.text()
        
        # Get selected roles
        selected_roles = []
        for i in range(self.roles_list.count()):
            item = self.roles_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected_roles.append(item.text())
        
        if not username:
            QMessageBox.warning(self, "Error", "Username is required.")
            return
        
        try:
            if self._is_new_user:
                # Creating new user
                if not password:
                    QMessageBox.warning(self, "Error", "Password is required for new users.")
                    return
                
                if len(password) < 6:
                    QMessageBox.warning(self, "Error", "Password must be at least 6 characters.")
                    return
                
                user_id = create_user(
                    username=username,
                    display_name=display_name,
                    password=password,
                    roles=selected_roles,
                    is_active=is_active,
                    is_agent=is_agent,
                    force_password_change=True,
                    folder_path=self.folder_path
                )
                
                # Log audit
                session = get_session()
                log_audit(
                    session.user_id,
                    "users.created",
                    entity_type="user",
                    entity_id=str(user_id),
                    username=session.username,
                    details=f"Created user: {username}",
                    folder_path=self.folder_path
                )
                
                QMessageBox.information(self, "Success", f"User '{username}' created successfully!")
            else:
                # Updating existing user
                update_user(
                    self._current_user_id,
                    display_name=display_name,
                    is_active=is_active,
                    is_agent=is_agent,
                    folder_path=self.folder_path
                )
                
                set_user_roles(self._current_user_id, selected_roles, self.folder_path)
                
                if password:
                    if len(password) < 6:
                        QMessageBox.warning(self, "Error", "Password must be at least 6 characters.")
                        return
                    set_user_password(self._current_user_id, password, self.folder_path)
                
                # Log audit
                session = get_session()
                log_audit(
                    session.user_id,
                    "users.updated",
                    entity_type="user",
                    entity_id=str(self._current_user_id),
                    username=session.username,
                    details=f"Updated user: {username}",
                    folder_path=self.folder_path
                )
                
                QMessageBox.information(self, "Success", "User updated successfully!")
            
            self._load_users()
            self.user_updated.emit()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save user: {e}")
    
    def _on_delete_user(self):
        """Handle delete user"""
        if not self._current_user_id:
            return
        
        # Prevent self-deletion
        session = get_session()
        if self._current_user_id == session.user_id:
            QMessageBox.warning(self, "Error", "You cannot delete your own account.")
            return
        
        user = get_user_by_id(self._current_user_id, self.folder_path)
        if not user:
            return
        
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete user '{user['username']}'?\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # Prefer deactivating over deleting
            set_user_active(self._current_user_id, False, self.folder_path)
            
            # Log audit
            log_audit(
                session.user_id,
                "users.deleted",
                entity_type="user",
                entity_id=str(self._current_user_id),
                username=session.username,
                details=f"Deactivated user: {user['username']}",
                folder_path=self.folder_path
            )
            
            QMessageBox.information(self, "Success", "User has been deactivated.")
            self._load_users()
            self.user_updated.emit()
    
    def _on_reset_password(self):
        """Reset user password to temporary"""
        if not self._current_user_id:
            return
        
        user = get_user_by_id(self._current_user_id, self.folder_path)
        if not user:
            return
        
        reply = QMessageBox.question(
            self,
            "Reset Password",
            f"Reset password for '{user['username']}' to 'temp123'?\n\n"
            "They will be required to change it on next login.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            from ...db.users import hash_password
            import sqlite3
            from ...db.users import get_connection
            
            conn = get_connection(self.folder_path)
            cursor = conn.cursor()
            
            password_hash = hash_password("temp123")
            cursor.execute("""
                UPDATE users 
                SET password_hash = ?, force_password_change = 1, updated_at = datetime('now')
                WHERE id = ?
            """, (password_hash, self._current_user_id))
            
            conn.commit()
            conn.close()
            
            # Log audit
            session = get_session()
            log_audit(
                session.user_id,
                "users.password_reset",
                entity_type="user",
                entity_id=str(self._current_user_id),
                username=session.username,
                details=f"Reset password for: {user['username']}",
                folder_path=self.folder_path
            )
            
            QMessageBox.information(
                self,
                "Password Reset",
                f"Password has been reset to 'temp123'.\n\n"
                f"User will be required to change it on next login."
            )
