"""
update_dialog.py — Dialog for displaying update notifications
"""

import logging
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QProgressBar, QCheckBox, QFrame, QMessageBox,
    QApplication
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QIcon

from dmelogic.version import APP_VERSION
from dmelogic.update_checker import (
    UpdateInfo, download_update, install_update, 
    open_download_page, skip_version, set_last_update_check
)

logger = logging.getLogger(__name__)


class DownloadThread(QThread):
    """Background thread for downloading updates."""
    
    progress = pyqtSignal(int, int)  # downloaded, total
    finished = pyqtSignal(str)  # filepath or empty string on failure
    error = pyqtSignal(str)  # error message
    
    def __init__(self, update_info: UpdateInfo):
        super().__init__()
        self.update_info = update_info
    
    def run(self):
        try:
            def progress_callback(downloaded: int, total: int):
                self.progress.emit(downloaded, total)
            
            filepath = download_update(self.update_info, progress_callback)
            if filepath:
                self.finished.emit(filepath)
            else:
                self.error.emit("Download failed or was redirected to browser")
        except Exception as e:
            self.error.emit(str(e))


class UpdateDialog(QDialog):
    """Dialog to notify user of available updates."""
    
    def __init__(self, update_info: UpdateInfo, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.download_thread: Optional[DownloadThread] = None
        self.downloaded_file: Optional[str] = None
        
        self.setWindowTitle("Update Available")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.setModal(True)
        
        self._setup_ui()
        
        # Record that we checked for updates
        set_last_update_check(datetime.now().isoformat())
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Header with icon and title
        header_layout = QHBoxLayout()
        
        # Update icon (using emoji as fallback)
        icon_label = QLabel("🔄")
        icon_label.setFont(QFont("Segoe UI Emoji", 32))
        header_layout.addWidget(icon_label)
        
        # Title and version info
        title_layout = QVBoxLayout()
        title_label = QLabel("A New Version is Available!")
        title_label.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title_layout.addWidget(title_label)
        
        version_label = QLabel(f"Version {self.update_info.version} is available (you have {APP_VERSION})")
        version_label.setStyleSheet("color: #666;")
        title_layout.addWidget(version_label)
        
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #ccc;")
        layout.addWidget(separator)
        
        # Release notes
        notes_label = QLabel("What's New:")
        notes_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        layout.addWidget(notes_label)
        
        self.notes_text = QTextEdit()
        self.notes_text.setReadOnly(True)
        self.notes_text.setPlainText(self.update_info.release_notes or "No release notes available.")
        self.notes_text.setMaximumHeight(200)
        self.notes_text.setStyleSheet("""
            QTextEdit {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        layout.addWidget(self.notes_text)
        
        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Downloading... %p%")
        layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-style: italic;")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        
        # Skip this version checkbox
        self.skip_checkbox = QCheckBox("Don't remind me about this version")
        layout.addWidget(self.skip_checkbox)
        
        layout.addStretch()
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.remind_later_btn = QPushButton("Remind Me Later")
        self.remind_later_btn.clicked.connect(self.remind_later)
        button_layout.addWidget(self.remind_later_btn)
        
        self.view_online_btn = QPushButton("View on GitHub")
        self.view_online_btn.clicked.connect(self.view_online)
        button_layout.addWidget(self.view_online_btn)
        
        button_layout.addStretch()
        
        self.download_btn = QPushButton("Download && Install")
        self.download_btn.setDefault(True)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                padding: 8px 20px;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton:pressed {
                background-color: #005a9e;
            }
            QPushButton:disabled {
                background-color: #ccc;
            }
        """)
        self.download_btn.clicked.connect(self.start_download)
        button_layout.addWidget(self.download_btn)
        
        layout.addLayout(button_layout)
    
    def remind_later(self):
        """Close dialog, will check again later."""
        if self.skip_checkbox.isChecked():
            skip_version(self.update_info.version)
        self.reject()
    
    def view_online(self):
        """Open GitHub releases page in browser."""
        open_download_page(self.update_info)
    
    def start_download(self):
        """Start downloading the update."""
        # Check if it's a direct download link
        if not self.update_info.download_url.endswith('.exe'):
            # Just open the browser
            open_download_page(self.update_info)
            self.accept()
            return
        
        # Disable buttons during download
        self.download_btn.setEnabled(False)
        self.download_btn.setText("Downloading...")
        self.remind_later_btn.setEnabled(False)
        self.view_online_btn.setEnabled(False)
        self.skip_checkbox.setEnabled(False)
        
        # Show progress bar
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        # Start download in background
        self.download_thread = DownloadThread(self.update_info)
        self.download_thread.progress.connect(self._on_download_progress)
        self.download_thread.finished.connect(self._on_download_finished)
        self.download_thread.error.connect(self._on_download_error)
        self.download_thread.start()
    
    def _on_download_progress(self, downloaded: int, total: int):
        """Update progress bar."""
        if total > 0:
            percent = int((downloaded / total) * 100)
            self.progress_bar.setValue(percent)
            
            # Show size info
            downloaded_mb = downloaded / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            self.status_label.setText(f"Downloaded {downloaded_mb:.1f} MB of {total_mb:.1f} MB")
            self.status_label.setVisible(True)
    
    def _on_download_finished(self, filepath: str):
        """Handle successful download."""
        self.downloaded_file = filepath
        self.progress_bar.setValue(100)
        self.status_label.setText("Download complete!")
        
        # Change button to Install
        self.download_btn.setText("Install Now")
        self.download_btn.setEnabled(True)
        self.download_btn.clicked.disconnect()
        self.download_btn.clicked.connect(self._install_update)
        
        # Re-enable other buttons
        self.remind_later_btn.setEnabled(True)
        self.remind_later_btn.setText("Install Later")
    
    def _on_download_error(self, error: str):
        """Handle download error."""
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Download failed: {error}")
        self.status_label.setStyleSheet("color: #d32f2f;")
        self.status_label.setVisible(True)
        
        # Re-enable buttons
        self.download_btn.setEnabled(True)
        self.download_btn.setText("Download && Install")
        self.remind_later_btn.setEnabled(True)
        self.view_online_btn.setEnabled(True)
        self.skip_checkbox.setEnabled(True)
        
        # Offer to open browser instead
        QMessageBox.warning(
            self,
            "Download Failed",
            f"Could not download the update automatically.\n\n"
            f"Would you like to download it from the website instead?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
    
    def _install_update(self):
        """Launch the installer and close the app."""
        if not self.downloaded_file:
            return
        
        reply = QMessageBox.question(
            self,
            "Install Update",
            "The application will close to install the update.\n\n"
            "Make sure you have saved any work before continuing.\n\n"
            "Do you want to install now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if install_update(self.downloaded_file):
                # Close the application
                self.accept()
                QApplication.instance().quit()
            else:
                QMessageBox.critical(
                    self,
                    "Installation Failed",
                    "Could not launch the installer.\n\n"
                    f"The installer is saved at:\n{self.downloaded_file}\n\n"
                    "You can run it manually."
                )
    
    def closeEvent(self, event):
        """Handle dialog close."""
        if self.skip_checkbox.isChecked():
            skip_version(self.update_info.version)
        super().closeEvent(event)


def show_update_dialog(update_info: UpdateInfo, parent=None) -> bool:
    """
    Show the update dialog.
    
    Returns:
        True if user chose to update, False otherwise
    """
    dialog = UpdateDialog(update_info, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


def show_no_updates_dialog(parent=None) -> None:
    """Show a dialog indicating no updates are available."""
    QMessageBox.information(
        parent,
        "No Updates Available",
        f"You are running the latest version of DMELogic.\n\n"
        f"Current version: {APP_VERSION}",
        QMessageBox.StandardButton.Ok
    )
