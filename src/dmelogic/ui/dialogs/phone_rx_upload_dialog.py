"""
phone_rx_upload_dialog.py — DMELogic

Shows a QR code that a phone can scan to upload prescription photos directly
into New Orders. Works over local Wi-Fi or Tailscale — no internet relay needed.
"""
from __future__ import annotations

import io
import socket
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _best_local_ip() -> str:
    """Return the machine's most likely LAN IP (not 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # Fallback: iterate adapters
        try:
            import socket as _s
            for info in _s.getaddrinfo(_s.gethostname(), None):
                addr = info[4][0]
                if addr.startswith("192.168.") or addr.startswith("10.") or addr.startswith("172."):
                    return addr
        except Exception:
            pass
        return "127.0.0.1"


def _tailscale_ip() -> Optional[str]:
    """Return the machine's Tailscale IP (100.64.0.0/10 CGNAT range) if present.

    A phone running Tailscale on the same tailnet can reach this address from
    anywhere, so it is preferred over the LAN IP for remote captures.
    """
    try:
        import socket as _s
        for info in _s.getaddrinfo(_s.gethostname(), None, _s.AF_INET):
            addr = info[4][0]
            try:
                first, second = (int(p) for p in addr.split(".")[:2])
            except ValueError:
                continue
            # Tailscale CGNAT range: 100.64.0.0 – 100.127.255.255
            if first == 100 and 64 <= second <= 127:
                return addr
    except Exception:
        pass
    return None


def _preferred_host_ip() -> tuple[str, bool]:
    """Return (ip, is_tailscale). Prefer Tailscale for remote reachability."""
    ts = _tailscale_ip()
    if ts:
        return ts, True
    return _best_local_ip(), False


def _make_qr_pixmap(url: str, size: int = 260) -> Optional[QPixmap]:
    try:
        import qrcode
        from qrcode.image.pil import PilImage  # type: ignore

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=7,
            border=3,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(image_factory=PilImage)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        qimg = QImage.fromData(buf.read())
        pix = QPixmap.fromImage(qimg).scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return pix
    except Exception:
        return None


# ── Dialog ────────────────────────────────────────────────────────────────────

class PhoneRxUploadDialog(QDialog):
    """Display a QR code that lets a phone upload Rx photos to New Orders."""

    SESSION_TTL = 600  # seconds (must match server)

    def __init__(self, save_folder: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Capture Rx from Phone")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setMaximumWidth(440)
        self.setSizeGripEnabled(False)

        self._save_folder = save_folder
        self._upload_done = False
        self._token: Optional[str] = None
        self._started_at = time.time()
        self._poll_timer: Optional[QTimer] = None
        self._countdown_timer: Optional[QTimer] = None

        self._build_ui()
        # Defer server start so dialog appears immediately
        QTimer.singleShot(100, self._start_session)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 18)

        # Title
        lbl_title = QLabel("📱  Scan QR to upload an Rx")
        lbl_title.setStyleSheet("font-size:16px; font-weight:800; color:#0f172a;")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_title)

        self._sub_label = QLabel("Phone must be on the same Wi-Fi as this PC")
        self._sub_label.setStyleSheet("font-size:11px; color:#64748b;")
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_label.setWordWrap(True)
        layout.addWidget(self._sub_label)

        # QR code area
        self._qr_label = QLabel("Starting upload server…")
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setMinimumHeight(270)
        self._qr_label.setStyleSheet(
            "border:1px solid #e2e8f0; border-radius:12px; background:#fafafa; color:#64748b; font-size:13px;"
        )
        layout.addWidget(self._qr_label)

        # URL text (for manual entry / copy)
        self._url_label = QLabel()
        self._url_label.setStyleSheet(
            "font-size:10px; color:#475569; font-family:Consolas,monospace; padding:4px 0;"
        )
        self._url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_label.setWordWrap(True)
        self._url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._url_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#e2e8f0;")
        layout.addWidget(sep)

        # Status
        self._status_label = QLabel("Waiting for phone upload…")
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        self._status_label.setFont(f)
        self._status_label.setStyleSheet("color:#0f172a;")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        self._countdown_label = QLabel()
        self._countdown_label.setStyleSheet("font-size:11px; color:#94a3b8;")
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._countdown_label)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setStyleSheet(
            "QPushButton{background:#fff;color:#0f172a;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:8px 22px;font-weight:600;}"
            "QPushButton:hover{background:#f1f5f9;}"
        )
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    # ── Session lifecycle ─────────────────────────────────────────────────

    def _start_session(self) -> None:
        try:
            from dmelogic.services.rx_upload_server import (
                create_session, ensure_server_running, get_upload_url,
            )

            ensure_server_running()
            ip, is_tailscale = _preferred_host_ip()
            self._token = create_session(
                save_folder=self._save_folder,
                callback=self._on_upload_complete_threadsafe,
            )
            url = get_upload_url(self._token, ip)

            if is_tailscale:
                self._sub_label.setText(
                    "Remote-ready: phone must have Tailscale running on the same account"
                )
                lan_ip = _best_local_ip()
                if lan_ip and lan_ip != ip and lan_ip != "127.0.0.1":
                    lan_url = get_upload_url(self._token, lan_ip)
                    self._url_label.setText(f"{url}\n(on office Wi-Fi: {lan_url})")
                else:
                    self._url_label.setText(url)
            else:
                self._sub_label.setText("Phone must be on the same Wi-Fi as this PC")
                self._url_label.setText(url)

            pix = _make_qr_pixmap(url)
            if pix:
                self._qr_label.setPixmap(pix)
                self._qr_label.setStyleSheet(
                    "border:1px solid #e2e8f0; border-radius:12px; background:#fff; padding:6px;"
                )
            else:
                self._qr_label.setText(
                    f"QR generation failed.\nOpen URL manually:\n\n{url}"
                )
                self._qr_label.setStyleSheet(
                    "border:1px solid #fecaca; border-radius:12px; background:#fef2f2; "
                    "color:#dc2626; font-size:11px; padding:12px;"
                )

            # Start polling and countdown
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(2000)
            self._poll_timer.timeout.connect(self._poll_status)
            self._poll_timer.start()

            self._countdown_timer = QTimer(self)
            self._countdown_timer.setInterval(1000)
            self._countdown_timer.timeout.connect(self._update_countdown)
            self._countdown_timer.start()

        except Exception as exc:
            self._status_label.setText(f"Error: {exc}")
            self._status_label.setStyleSheet("color:#dc2626; font-size:12px; font-weight:600;")

    def _poll_status(self) -> None:
        if self._upload_done or not self._token:
            return
        try:
            import requests
            r = requests.get(
                f"http://127.0.0.1:8402/rx-upload/{self._token}/status",
                timeout=2,
            )
            data = r.json()
            st = data.get("status")
            if st == "done":
                self._mark_done()
            elif st == "expired":
                self._status_label.setText("Link expired. Close and try again.")
                self._status_label.setStyleSheet("color:#dc2626; font-weight:700; font-size:12px;")
                self._stop_timers()
        except Exception:
            pass  # transient — keep polling

    def _update_countdown(self) -> None:
        if self._upload_done:
            return
        elapsed = time.time() - self._started_at
        remaining = max(0, self.SESSION_TTL - int(elapsed))
        m, s = divmod(remaining, 60)
        self._countdown_label.setText(f"Link expires in {m}:{s:02d}")
        if remaining == 0:
            self._stop_timers()
            self._status_label.setText("Link expired — close and try again.")
            self._status_label.setStyleSheet("color:#dc2626; font-weight:700; font-size:12px;")

    def _on_upload_complete_threadsafe(self, saved_path: str) -> None:
        """Called from background thread by server — re-enter Qt event loop safely."""
        QTimer.singleShot(0, self._mark_done)

    def _mark_done(self) -> None:
        if self._upload_done:
            return
        self._upload_done = True
        self._stop_timers()

        self._status_label.setText("✅  Rx added to New Orders!")
        self._status_label.setStyleSheet(
            "color:#16a34a; font-size:14px; font-weight:800;"
        )
        self._countdown_label.setText("Closing in 2 seconds…")
        self._cancel_btn.setText("Close")
        self._qr_label.clear()
        self._qr_label.setText("Upload complete")
        self._qr_label.setStyleSheet(
            "border:1px solid #bbf7d0; border-radius:12px; background:#f0fdf4; "
            "color:#16a34a; font-size:16px; font-weight:700;"
        )

        QTimer.singleShot(2000, self.accept)

    def _stop_timers(self) -> None:
        for t in (self._poll_timer, self._countdown_timer):
            try:
                if t:
                    t.stop()
            except Exception:
                pass

    def _on_cancel(self) -> None:
        self._stop_timers()
        self.reject()

    def closeEvent(self, event):
        self._stop_timers()
        super().closeEvent(event)
