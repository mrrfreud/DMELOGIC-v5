"""
Communication Dialogs for SMS, Fax, and Call

Provides UI dialogs for:
- SendSMSDialog: Compose and send SMS messages
- SendFaxDialog: Send fax with document attachment
- InitiateCallDialog: Click-to-call functionality
- CommunicationLogPanel: View communication history
"""

import os
import tempfile
from typing import Optional, Dict, Any, List
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QFormLayout, QMessageBox,
    QTextEdit, QComboBox, QFileDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame, QSplitter,
    QWidget, QProgressDialog, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from dmelogic.settings import load_settings
from dmelogic.services.ringcentral_service import get_ringcentral_service
from dmelogic.db.communications import CommunicationsRepository, log_communication
from dmelogic.config import debug_log
from dmelogic.printing.fax_cover import generate_fax_cover_page


def format_phone_number(phone: str) -> str:
    """Format phone number for display."""
    # Remove non-digits
    digits = ''.join(c for c in phone if c.isdigit())
    
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    
    return phone


def normalize_phone_number(phone: str) -> str:
    """Normalize phone number to E.164 format for API."""
    digits = ''.join(c for c in phone if c.isdigit())
    
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"+{digits}"
    
    return phone


class SendSMSDialog(QDialog):
    """
    Dialog for composing and sending SMS messages.
    """
    
    sms_sent = pyqtSignal(dict)  # Emitted with result when SMS is sent
    
    def __init__(
        self,
        parent=None,
        patient_id: Optional[int] = None,
        order_id: Optional[str] = None,
        to_number: str = "",
        patient_name: str = "",
        username: str = ""
    ):
        super().__init__(parent)
        self.patient_id = patient_id
        self.order_id = order_id
        self.username = username
        self.patient_name = patient_name
        
        self.setWindowTitle("Send SMS")
        self.setMinimumSize(450, 350)
        self._setup_ui()
        
        if to_number:
            self.to_edit.setText(to_number)
    
    def _setup_ui(self):
        """Build the dialog UI."""
        layout = QVBoxLayout(self)
        
        # Header with patient context
        if self.patient_name:
            header = QLabel(f"<b>Patient:</b> {self.patient_name}")
            header.setStyleSheet("background-color: #e8f4fd; padding: 8px; border-radius: 4px;")
            layout.addWidget(header)
        
        # Form
        form_layout = QFormLayout()
        
        # From number (dropdown of available numbers)
        self.from_combo = QComboBox()
        self.from_combo.addItem("Loading...", "")
        form_layout.addRow("From:", self.from_combo)
        
        # To number
        self.to_edit = QLineEdit()
        self.to_edit.setPlaceholderText("Enter recipient phone number")
        form_layout.addRow("To:", self.to_edit)
        
        layout.addLayout(form_layout)
        
        # Message
        msg_label = QLabel("Message:")
        layout.addWidget(msg_label)
        
        self.message_edit = QTextEdit()
        self.message_edit.setPlaceholderText("Type your message here...")
        self.message_edit.setMaximumHeight(150)
        layout.addWidget(self.message_edit)
        
        # Character count
        self.char_count_label = QLabel("0 / 1000 characters")
        self.char_count_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.char_count_label)
        
        self.message_edit.textChanged.connect(self._update_char_count)
        
        # Quick message templates
        templates_group = QGroupBox("Quick Templates")
        templates_layout = QHBoxLayout(templates_group)
        
        templates = [
            ("Appointment", "Your appointment is scheduled. Please call if you need to reschedule."),
            ("Delivery", "Your DME order is ready for delivery. Please confirm a convenient time."),
            ("Refill", "It's time to refill your supplies. Please contact us to place your order."),
        ]
        
        for name, text in templates:
            btn = QPushButton(name)
            btn.clicked.connect(lambda checked, t=text: self.message_edit.setPlainText(t))
            templates_layout.addWidget(btn)
        
        layout.addWidget(templates_group)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        btn_layout.addStretch()
        
        self.send_btn = QPushButton("Send SMS")
        self.send_btn.setStyleSheet("background-color: #007bff; color: white; font-weight: bold;")
        self.send_btn.clicked.connect(self._send_sms)
        btn_layout.addWidget(self.send_btn)
        
        layout.addLayout(btn_layout)
        
        # Load available phone numbers
        QTimer.singleShot(100, self._load_from_numbers)
    
    def _load_from_numbers(self):
        """Load SMS-capable phone numbers from RingCentral."""
        self.from_combo.clear()
        
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service or not service.is_connected:
            self.from_combo.addItem("Not connected to RingCentral", "")
            self.send_btn.setEnabled(False)
            return
        
        numbers = service.get_sms_capable_numbers(settings)
        
        if not numbers:
            self.from_combo.addItem("No SMS numbers available", "")
            self.send_btn.setEnabled(False)
            return
        
        for num in numbers:
            display = format_phone_number(num['number'])
            if num.get('label'):
                display = f"{num['label']} - {display}"
            self.from_combo.addItem(display, num['number'])
    
    def _update_char_count(self):
        """Update character count display."""
        count = len(self.message_edit.toPlainText())
        self.char_count_label.setText(f"{count} / 1000 characters")
        
        if count > 1000:
            self.char_count_label.setStyleSheet("color: #dc3545; font-size: 11px;")
        else:
            self.char_count_label.setStyleSheet("color: #666; font-size: 11px;")
    
    def _send_sms(self):
        """Send the SMS message."""
        to_number = normalize_phone_number(self.to_edit.text().strip())
        from_number = self.from_combo.currentData()
        message = self.message_edit.toPlainText().strip()
        
        # Validation
        if not to_number:
            QMessageBox.warning(self, "Missing Information", "Please enter a recipient phone number.")
            return
        
        if not message:
            QMessageBox.warning(self, "Missing Information", "Please enter a message.")
            return
        
        if len(message) > 1000:
            QMessageBox.warning(self, "Message Too Long", "Message must be 1000 characters or less.")
            return
        
        if not from_number:
            QMessageBox.warning(self, "No From Number", "No SMS-capable number available.")
            return
        
        # Send via RingCentral
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service:
            QMessageBox.critical(self, "Error", "RingCentral service not available.")
            return
        
        self.send_btn.setEnabled(False)
        self.send_btn.setText("Sending...")
        
        result = service.send_sms(to_number, message, from_number)
        
        # Log to database
        log_id = log_communication(
            channel='sms',
            to_number=to_number,
            from_number=from_number,
            status=result.get('status', 'sent') if result['success'] else 'failed',
            direction='outbound',
            patient_id=self.patient_id,
            order_id=self.order_id,
            remote_id=result.get('message_id'),
            body=message,
            error_message=result.get('error'),
            created_by=self.username,
            metadata={'ringcentral_response': result}
        )
        
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Send SMS")
        
        if result['success']:
            self.sms_sent.emit(result)
            QMessageBox.information(
                self,
                "SMS Sent",
                f"SMS sent successfully to {format_phone_number(to_number)}"
            )
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "SMS Failed",
                f"Failed to send SMS:\n{result.get('error', 'Unknown error')}"
            )


class SendFaxDialog(QDialog):
    """
    Dialog for sending fax documents.
    """
    
    fax_sent = pyqtSignal(dict)  # Emitted with result when fax is sent
    
    def __init__(
        self,
        parent=None,
        patient_id: Optional[int] = None,
        order_id: Optional[str] = None,
        to_number: str = "",
        patient_name: str = "",
        username: str = "",
        initial_file: str = "",
        recipient_name: str = ""
    ):
        super().__init__(parent)
        self.patient_id = patient_id
        self.order_id = order_id
        self.username = username
        self.patient_name = patient_name
        self.selected_file = initial_file
        self.recipient_name = recipient_name
        
        self.setWindowTitle("Send Fax")
        self.setMinimumSize(500, 400)
        self._setup_ui()
        
        if to_number:
            self.to_edit.setText(to_number)
    
    def _setup_ui(self):
        """Build the dialog UI."""
        layout = QVBoxLayout(self)
        
        # Header with patient context
        if self.patient_name:
            header = QLabel(f"<b>Patient:</b> {self.patient_name}")
            header.setStyleSheet("background-color: #e8f4fd; padding: 8px; border-radius: 4px;")
            layout.addWidget(header)
        
        # Form
        form_layout = QFormLayout()
        
        # To number
        self.to_edit = QLineEdit()
        self.to_edit.setPlaceholderText("Enter recipient fax number")
        form_layout.addRow("Fax Number:", self.to_edit)
        
        layout.addLayout(form_layout)
        
        # Document selection
        doc_group = QGroupBox("Document to Fax")
        doc_layout = QVBoxLayout(doc_group)
        
        file_row = QHBoxLayout()
        self.file_label = QLabel("No file selected")
        self.file_label.setStyleSheet("padding: 8px; background-color: #f8f9fa; border-radius: 4px;")
        file_row.addWidget(self.file_label, 1)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(browse_btn)
        
        doc_layout.addLayout(file_row)
        
        # Supported formats
        formats_label = QLabel("Supported formats: PDF, TIFF, DOC, DOCX")
        formats_label.setStyleSheet("color: #666; font-size: 11px;")
        doc_layout.addWidget(formats_label)
        
        layout.addWidget(doc_group)
        
        # Cover page
        cover_group = QGroupBox("Fax Cover Page")
        cover_layout = QVBoxLayout(cover_group)

        self.include_cover_checkbox = QCheckBox("Include cover page")
        self.include_cover_checkbox.setTristate(False)
        self.include_cover_checkbox.setChecked(True)
        cover_layout.addWidget(self.include_cover_checkbox)

        self.cover_fields_container = QWidget()
        cover_fields = QFormLayout(self.cover_fields_container)

        self.attention_edit = QLineEdit()
        self.attention_edit.setPlaceholderText("e.g., Billing Department")
        cover_fields.addRow("Attention:", self.attention_edit)

        self.cover_body_edit = QTextEdit()
        self.cover_body_edit.setPlaceholderText("Message body shown on the cover page")
        self.cover_body_edit.setMaximumHeight(120)
        cover_fields.addRow("Message:", self.cover_body_edit)

        cover_layout.addWidget(self.cover_fields_container)
        layout.addWidget(cover_group)

        self.include_cover_checkbox.toggled.connect(self.cover_fields_container.setEnabled)
        self.cover_fields_container.setEnabled(True)
        self.cover_body_edit.setPlainText(self._default_cover_body())
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        btn_layout.addStretch()
        
        self.send_btn = QPushButton("Send Fax")
        self.send_btn.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        self.send_btn.clicked.connect(self._send_fax)
        btn_layout.addWidget(self.send_btn)
        
        layout.addLayout(btn_layout)
        
        # Update file display if initial file provided
        if self.selected_file:
            self._update_file_display()
    
    def _browse_file(self):
        """Browse for a file to fax."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Document to Fax",
            "",
            "Documents (*.pdf *.tif *.tiff *.doc *.docx);;PDF Files (*.pdf);;All Files (*.*)"
        )
        
        if file_path:
            self.selected_file = file_path
            self._update_file_display()
    
    def _update_file_display(self):
        """Update the file label with selected file info."""
        if self.selected_file:
            path = Path(self.selected_file)
            size = path.stat().st_size if path.exists() else 0
            size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
            self.file_label.setText(f"{path.name} ({size_str})")
        else:
            self.file_label.setText("No file selected")

    def _default_cover_body(self) -> str:
        if self.patient_name:
            return f"Please review the enclosed documentation regarding patient {self.patient_name}."
        return "Please review the enclosed documentation."
    
    def _send_fax(self):
        """Send the fax."""
        raw_to = self.to_edit.text().strip()
        to_number = normalize_phone_number(raw_to)
        include_cover = self.include_cover_checkbox.isChecked()
        attention = self.attention_edit.text().strip()
        cover_message = self.cover_body_edit.toPlainText().strip()

        # Validation
        if not to_number:
            QMessageBox.warning(self, "Missing Information", "Please enter a fax number.")
            return

        attachment_path: Optional[Path] = None
        if self.selected_file:
            attachment_path = Path(self.selected_file)
            if not attachment_path.exists():
                QMessageBox.warning(self, "File Not Found", "The selected file no longer exists.")
                return

        if not include_cover and attachment_path is None:
            QMessageBox.warning(
                self,
                "Missing Document",
                "Select a document to fax or enable the cover page option."
            )
            return

        # Ensure service connection before generating files
        settings = load_settings()
        service = get_ringcentral_service(settings)
        if not service or not service.is_connected:
            QMessageBox.critical(self, "Not Connected", "Please connect to RingCentral first.")
            return

        cover_pdf_path: Optional[Path] = None
        temp_cover_path: Optional[Path] = None
        primary_path: Optional[Path] = None
        extra_attachments: List[Path] = []
        result: Dict[str, Any] = {'success': False, 'error': 'Fax send was not completed.'}

        try:
            if include_cover:
                try:
                    temp_file = tempfile.NamedTemporaryFile(prefix="dmelogic_cover_", suffix=".pdf", delete=False)
                    temp_cover_path = Path(temp_file.name)
                    temp_file.close()
                    display_number = raw_to or format_phone_number(to_number)
                    cover_pdf_path = generate_fax_cover_page(
                        temp_cover_path,
                        to_number=display_number,
                        attention=attention or None,
                        body=cover_message or None,
                        patient_name=self.patient_name or None,
                        recipient_name=self.recipient_name or None,
                    )
                except RuntimeError as exc:
                    if temp_cover_path and temp_cover_path.exists():
                        temp_cover_path.unlink(missing_ok=True)
                    QMessageBox.critical(
                        self,
                        "ReportLab Not Available",
                        f"Unable to generate cover page: {exc}"
                    )
                    return
                except Exception as exc:
                    if temp_cover_path and temp_cover_path.exists():
                        temp_cover_path.unlink(missing_ok=True)
                    QMessageBox.critical(
                        self,
                        "Cover Page Error",
                        f"Failed to generate cover page: {exc}"
                    )
                    return

                primary_path = cover_pdf_path
                if attachment_path:
                    extra_attachments.append(attachment_path)
            else:
                primary_path = attachment_path

            if primary_path is None:
                QMessageBox.warning(self, "Missing Document", "No file available to fax.")
                return

            self.send_btn.setEnabled(False)
            self.send_btn.setText("Sending...")

            attachments_arg = extra_attachments or None
            result = service.send_fax(
                to_number,
                primary_path,
                attachments=attachments_arg,
                to_name=self.recipient_name or None
            )

        except RuntimeError as auth_exc:
            debug_log(f"SendFaxDialog: Auth/runtime error during fax send: {auth_exc}")
            QMessageBox.critical(
                self,
                "Fax Error",
                f"Authentication error — please reconnect to RingCentral and try again.\n\n"
                f"Details: {auth_exc}"
            )
            result = {'success': False, 'error': str(auth_exc)}
        except Exception as exc:
            debug_log(f"SendFaxDialog: Unexpected fax send error: {exc}")
            QMessageBox.critical(
                self,
                "Fax Error",
                f"An unexpected error occurred while sending the fax. Please try again.\n\n"
                f"Details: {exc}"
            )
            result = {'success': False, 'error': str(exc)}

        finally:
            self.send_btn.setEnabled(True)
            self.send_btn.setText("Send Fax")
            if cover_pdf_path and cover_pdf_path.exists():
                try:
                    cover_pdf_path.unlink()
                except Exception:
                    debug_log("SendFaxDialog: Failed to remove temporary cover page")
            elif temp_cover_path and temp_cover_path.exists():
                try:
                    temp_cover_path.unlink()
                except Exception:
                    debug_log("SendFaxDialog: Failed to clean up temporary cover placeholder")

        # Log to database
        subject_text = attention or (cover_message if include_cover and cover_message else None)
        metadata = {
            'ringcentral_response': result,
            'cover_page': include_cover,
            'cover_attention': (attention or None) if include_cover else None,
            'cover_message': (cover_message or None) if include_cover else None,
            'attachment_sent': bool(attachment_path)
        }
        log_communication(
            channel='fax',
            to_number=to_number,
            status=result.get('status', 'queued') if result['success'] else 'failed',
            direction='outbound',
            patient_id=self.patient_id,
            order_id=self.order_id,
            remote_id=result.get('message_id'),
            subject=subject_text,
            attachment_path=str(attachment_path) if attachment_path else None,
            error_message=result.get('error'),
            created_by=self.username,
            metadata=metadata
        )

        if result['success']:
            self.fax_sent.emit(result)
            QMessageBox.information(
                self,
                "Fax Queued",
                f"Fax queued successfully to {format_phone_number(to_number)}\n\n"
                "The fax will be sent in the background. Check the communication log for status."
            )
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "Fax Failed",
                f"Failed to send fax:\n{result.get('error', 'Unknown error')}"
            )


class InitiateCallDialog(QDialog):
    """
    Dialog for click-to-call functionality.
    """
    
    call_initiated = pyqtSignal(dict)
    
    def __init__(
        self,
        parent=None,
        patient_id: Optional[int] = None,
        order_id: Optional[str] = None,
        to_number: str = "",
        patient_name: str = "",
        username: str = ""
    ):
        super().__init__(parent)
        self.patient_id = patient_id
        self.order_id = order_id
        self.username = username
        self.patient_name = patient_name
        self.call_id = None
        
        self.setWindowTitle("Make Call")
        self.setMinimumSize(400, 300)
        self._setup_ui()
        
        if to_number:
            self.to_edit.setText(to_number)
    
    def _setup_ui(self):
        """Build the dialog UI."""
        layout = QVBoxLayout(self)
        
        # Header with patient context
        if self.patient_name:
            header = QLabel(f"<b>Patient:</b> {self.patient_name}")
            header.setStyleSheet("background-color: #e8f4fd; padding: 8px; border-radius: 4px;")
            layout.addWidget(header)
        
        # Info
        info_label = QLabel(
            "<b>How Click-to-Call Works:</b><br>"
            "1. Your phone will ring first<br>"
            "2. When you answer, the call to the recipient is connected<br>"
            "3. You can cancel the call before it connects"
        )
        info_label.setStyleSheet("background-color: #f8f9fa; padding: 10px; border-radius: 4px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Form
        form_layout = QFormLayout()
        
        # Your phone number
        self.from_edit = QLineEdit()
        self.from_edit.setPlaceholderText("Your phone number (will ring first)")
        form_layout.addRow("Your Phone:", self.from_edit)
        
        # Recipient number
        self.to_edit = QLineEdit()
        self.to_edit.setPlaceholderText("Number to call")
        form_layout.addRow("Call:", self.to_edit)
        
        layout.addLayout(form_layout)
        
        # Options
        self.prompt_cb = QCheckBox("Play connecting prompt")
        self.prompt_cb.setChecked(True)
        layout.addWidget(self.prompt_cb)
        
        layout.addStretch()
        
        # Status area
        self.status_frame = QFrame()
        self.status_frame.setStyleSheet("background-color: #e8f4fd; padding: 10px; border-radius: 4px;")
        self.status_frame.setVisible(False)
        status_layout = QVBoxLayout(self.status_frame)
        
        self.status_label = QLabel("Initiating call...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.status_label)
        
        self.cancel_call_btn = QPushButton("Cancel Call")
        self.cancel_call_btn.setStyleSheet("background-color: #dc3545; color: white;")
        self.cancel_call_btn.clicked.connect(self._cancel_call)
        status_layout.addWidget(self.cancel_call_btn)
        
        layout.addWidget(self.status_frame)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        cancel_btn = QPushButton("Close")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        btn_layout.addStretch()
        
        self.call_btn = QPushButton("Start Call")
        self.call_btn.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        self.call_btn.clicked.connect(self._initiate_call)
        btn_layout.addWidget(self.call_btn)
        
        layout.addLayout(btn_layout)
    
    def _initiate_call(self):
        """Start the click-to-call."""
        from_number = normalize_phone_number(self.from_edit.text().strip())
        to_number = normalize_phone_number(self.to_edit.text().strip())
        
        # Validation
        if not from_number:
            QMessageBox.warning(self, "Missing Information", "Please enter your phone number.")
            return
        
        if not to_number:
            QMessageBox.warning(self, "Missing Information", "Please enter the number to call.")
            return
        
        # Initiate via RingCentral
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if not service or not service.is_connected:
            QMessageBox.critical(self, "Not Connected", "Please connect to RingCentral first.")
            return
        
        self.call_btn.setEnabled(False)
        self.status_frame.setVisible(True)
        self.status_label.setText("📞 Initiating call...\nYour phone will ring shortly.")
        
        result = service.initiate_call(
            to_number=to_number,
            from_number=from_number,
            play_prompt=self.prompt_cb.isChecked()
        )
        
        # Log to database
        log_id = log_communication(
            channel='call',
            to_number=to_number,
            from_number=from_number,
            status=result.get('status', 'initiated') if result['success'] else 'failed',
            direction='outbound',
            patient_id=self.patient_id,
            order_id=self.order_id,
            remote_id=result.get('call_id'),
            error_message=result.get('error'),
            created_by=self.username,
            metadata={'ringcentral_response': result}
        )
        
        if result['success']:
            self.call_id = result.get('call_id')
            self.status_label.setText(
                "📞 Call in progress\n\n"
                f"Your phone: {format_phone_number(from_number)}\n"
                f"Calling: {format_phone_number(to_number)}"
            )
            self.call_initiated.emit(result)
        else:
            self.status_frame.setVisible(False)
            self.call_btn.setEnabled(True)
            QMessageBox.warning(
                self,
                "Call Failed",
                f"Failed to initiate call:\n{result.get('error', 'Unknown error')}"
            )
    
    def _cancel_call(self):
        """Cancel the in-progress call."""
        if not self.call_id:
            return
        
        settings = load_settings()
        service = get_ringcentral_service(settings)
        
        if service:
            result = service.cancel_call(self.call_id)
            
            if result.get('success'):
                self.status_label.setText("Call cancelled")
            else:
                self.status_label.setText(f"Cancel failed: {result.get('error', 'Unknown')}")
        
        self.call_id = None
        self.cancel_call_btn.setEnabled(False)
        QTimer.singleShot(1500, self.accept)


class CommunicationLogPanel(QWidget):
    """
    Widget showing communication history for a patient or order.
    """
    
    def __init__(
        self,
        parent=None,
        patient_id: Optional[int] = None,
        order_id: Optional[str] = None
    ):
        super().__init__(parent)
        self.patient_id = patient_id
        self.order_id = order_id
        self._setup_ui()
        self.refresh()
    
    def _setup_ui(self):
        """Build the panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Header with filter
        header_layout = QHBoxLayout()
        
        title = QLabel("Communication Log")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("All", None)
        self.filter_combo.addItem("SMS", "sms")
        self.filter_combo.addItem("Fax", "fax")
        self.filter_combo.addItem("Calls", "call")
        self.filter_combo.currentIndexChanged.connect(self.refresh)
        header_layout.addWidget(self.filter_combo)
        
        refresh_btn = QPushButton("↻")
        refresh_btn.setMaximumWidth(30)
        refresh_btn.clicked.connect(self.refresh)
        header_layout.addWidget(refresh_btn)
        
        layout.addLayout(header_layout)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Date/Time", "Type", "To/From", "Status", "User", "Details"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
    
    def refresh(self):
        """Refresh the communication log."""
        try:
            repo = CommunicationsRepository()
            channel = self.filter_combo.currentData()
            
            if self.patient_id:
                logs = repo.get_for_patient(self.patient_id, channel=channel)
            elif self.order_id:
                logs = repo.get_for_order(self.order_id, channel=channel)
            else:
                logs = repo.search(channel=channel, limit=50)
            
            self.table.setRowCount(len(logs))
            
            for row, log in enumerate(logs):
                try:
                    # Date/Time
                    created = log.get('created_at', '') or ''
                    if created:
                        # Format datetime
                        try:
                            dt = str(created)[:16].replace('T', ' ')
                        except Exception:
                            dt = str(created)
                    else:
                        dt = ''
                    self.table.setItem(row, 0, QTableWidgetItem(dt))
                    
                    # Type (with icon)
                    channel_icons = {'sms': '💬', 'fax': '📠', 'call': '📞'}
                    channel_name = log.get('channel', '') or ''
                    icon = channel_icons.get(channel_name, '')
                    self.table.setItem(row, 1, QTableWidgetItem(f"{icon} {channel_name.upper()}"))
                    
                    # To/From
                    direction = log.get('direction', 'outbound') or 'outbound'
                    number = log.get('to_number', '') if direction == 'outbound' else log.get('from_number', '')
                    number = number or ''
                    self.table.setItem(row, 2, QTableWidgetItem(format_phone_number(number)))
                    
                    # Status
                    status = log.get('status', '') or ''
                    status_item = QTableWidgetItem(status.title() if status else '')
                    if status in ('delivered', 'sent', 'completed'):
                        status_item.setForeground(Qt.GlobalColor.darkGreen)
                    elif status == 'failed':
                        status_item.setForeground(Qt.GlobalColor.red)
                    self.table.setItem(row, 3, status_item)
                    
                    # User
                    self.table.setItem(row, 4, QTableWidgetItem(log.get('created_by', '') or ''))
                    
                    # Details
                    details = log.get('body', '') or log.get('subject', '') or log.get('error_message', '') or ''
                    if len(details) > 50:
                        details = details[:50] + '...'
                    self.table.setItem(row, 5, QTableWidgetItem(details))
                except Exception as row_err:
                    debug_log(f"CommunicationLogPanel: Error rendering row {row}: {row_err}")
                    # Fill with placeholders
                    for col in range(6):
                        self.table.setItem(row, col, QTableWidgetItem(""))
        except Exception as e:
            debug_log(f"CommunicationLogPanel: refresh error: {e}")
            import traceback
            traceback.print_exc()
            self.table.setRowCount(0)
    
    def set_patient(self, patient_id: Optional[int]):
        """Update for a new patient."""
        self.patient_id = patient_id
        self.order_id = None
        self.refresh()
    
    def set_order(self, order_id: Optional[str]):
        """Update for a new order."""
        self.order_id = order_id
        self.patient_id = None
        self.refresh()
