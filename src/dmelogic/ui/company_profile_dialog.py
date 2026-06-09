"""
company_profile_dialog.py — collect/edit the company profile.

Used in two places from one widget:
  * First-run onboarding (Skip allowed; logo optional).
  * Settings → Company Profile (edit anytime).

All fields feed dmelogic.company, which every form and fax reads from.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from dmelogic.company import (
    CompanyProfile, load_company_profile, save_company_profile, set_logo,
)


class CompanyProfileForm(QWidget):
    """Editable form for the company profile (embeddable in a dialog or wizard)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._logo_path = ""
        self._build()
        self.load(load_company_profile())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        intro = QLabel(
            "Tell us about your business. This name, contact information, and "
            "logo appear on your fax cover sheets, forms, and instructions. "
            "You can change any of it later in <b>Settings → Company Profile</b>."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#475569;")
        root.addWidget(intro)

        # ── Business identity ──
        ident = QGroupBox("Business")
        ident_l = QFormLayout(ident)
        self.name = QLineEdit(); self.name.setPlaceholderText("Acme DME Supplies, Inc.")
        self.subtitle = QLineEdit(); self.subtitle.setPlaceholderText("Durable Medical Equipment")
        ident_l.addRow("Business name *", self.name)
        ident_l.addRow("Subtitle / department", self.subtitle)
        root.addWidget(ident)

        # ── Address ──
        addr = QGroupBox("Address")
        addr_l = QFormLayout(addr)
        self.address1 = QLineEdit()
        self.address2 = QLineEdit()
        csz = QHBoxLayout()
        self.city = QLineEdit(); self.city.setPlaceholderText("City")
        self.state = QLineEdit(); self.state.setPlaceholderText("ST"); self.state.setMaximumWidth(60)
        self.zip = QLineEdit(); self.zip.setPlaceholderText("ZIP"); self.zip.setMaximumWidth(90)
        csz.addWidget(self.city); csz.addWidget(self.state); csz.addWidget(self.zip)
        addr_l.addRow("Address line 1", self.address1)
        addr_l.addRow("Address line 2", self.address2)
        addr_l.addRow("City / State / ZIP", self._wrap(csz))
        root.addWidget(addr)

        # ── Contact ──
        contact = QGroupBox("Contact")
        c_l = QFormLayout(contact)
        self.phone = QLineEdit()
        self.fax = QLineEdit()
        self.email = QLineEdit()
        self.website = QLineEdit()
        self.npi = QLineEdit()
        self.tax_id = QLineEdit()
        self.contact_name = QLineEdit(); self.contact_name.setPlaceholderText("Signs outbound faxes")
        self.contact_title = QLineEdit()
        c_l.addRow("Phone", self.phone)
        c_l.addRow("Fax", self.fax)
        c_l.addRow("Email", self.email)
        c_l.addRow("Website", self.website)
        c_l.addRow("NPI", self.npi)
        c_l.addRow("Tax ID", self.tax_id)
        c_l.addRow("Contact name", self.contact_name)
        c_l.addRow("Contact title", self.contact_title)
        root.addWidget(contact)

        # ── Logo ──
        logo_box = QGroupBox("Logo (optional)")
        lg = QHBoxLayout(logo_box)
        self.logo_preview = QLabel("No logo")
        self.logo_preview.setFixedSize(120, 70)
        self.logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_preview.setStyleSheet(
            "border:1px solid #cbd5e1; border-radius:8px; color:#94a3b8; background:#ffffff;")
        upload = QPushButton("Upload logo…"); upload.setProperty("flat", True)
        upload.clicked.connect(self._pick_logo)
        remove = QPushButton("Remove"); remove.setProperty("flat", True)
        remove.clicked.connect(self._clear_logo)
        lg.addWidget(self.logo_preview)
        lg.addWidget(upload)
        lg.addWidget(remove)
        lg.addStretch()
        root.addWidget(logo_box)

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget(); w.setLayout(layout); return w

    # ── logo ──
    def _pick_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a logo image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not path:
            return
        stored = set_logo(path)
        if stored:
            self._logo_path = stored
            self._show_logo(stored)

    def _clear_logo(self) -> None:
        self._logo_path = ""
        self.logo_preview.setPixmap(QPixmap())
        self.logo_preview.setText("No logo")

    def _show_logo(self, path: str) -> None:
        pix = QPixmap(path)
        if not pix.isNull():
            self.logo_preview.setPixmap(
                pix.scaled(116, 66, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation))
            self.logo_preview.setText("")

    # ── data binding ──
    def load(self, p: CompanyProfile) -> None:
        self.name.setText(p.name); self.subtitle.setText(p.subtitle)
        self.address1.setText(p.address_line1); self.address2.setText(p.address_line2)
        self.city.setText(p.city); self.state.setText(p.state); self.zip.setText(p.zip)
        self.phone.setText(p.phone); self.fax.setText(p.fax)
        self.email.setText(p.email); self.website.setText(p.website)
        self.npi.setText(p.npi); self.tax_id.setText(p.tax_id)
        self.contact_name.setText(p.contact_name); self.contact_title.setText(p.contact_title)
        self._logo_path = p.logo_path
        if p.has_logo():
            self._show_logo(p.logo_path)

    def to_profile(self) -> CompanyProfile:
        return CompanyProfile(
            name=self.name.text().strip(), subtitle=self.subtitle.text().strip(),
            address_line1=self.address1.text().strip(),
            address_line2=self.address2.text().strip(),
            city=self.city.text().strip(), state=self.state.text().strip(),
            zip=self.zip.text().strip(), phone=self.phone.text().strip(),
            fax=self.fax.text().strip(), email=self.email.text().strip(),
            website=self.website.text().strip(), npi=self.npi.text().strip(),
            tax_id=self.tax_id.text().strip(),
            contact_name=self.contact_name.text().strip(),
            contact_title=self.contact_title.text().strip(),
            logo_path=self._logo_path,
        )

    def save(self) -> CompanyProfile:
        prof = self.to_profile()
        save_company_profile(prof)
        return prof


class CompanyProfileDialog(QDialog):
    """Dialog wrapper — onboarding (with Skip) or plain edit."""

    def __init__(self, parent: QWidget | None = None, onboarding: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Welcome — Set Up Your Company" if onboarding
                            else "Company Profile")
        self.resize(620, 720)
        self.onboarding = onboarding

        root = QVBoxLayout(self)
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        self.form = CompanyProfileForm()
        scroll.setWidget(self.form)
        root.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        if onboarding:
            skip = QPushButton("Skip for now"); skip.setProperty("flat", True)
            skip.clicked.connect(self.reject)
            buttons.addWidget(skip)
        buttons.addStretch()
        cancel = QPushButton("Cancel"); cancel.setProperty("flat", True)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(self._save)
        if not onboarding:
            buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _save(self) -> None:
        self.form.save()
        self.accept()


def main() -> int:  # standalone preview
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    try:
        from dmelogic.ui.theme_modern import apply_modern_theme
        apply_modern_theme(app)
    except Exception:
        pass
    dlg = CompanyProfileDialog(onboarding=True)
    dlg.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
