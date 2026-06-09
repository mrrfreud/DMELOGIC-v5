"""
MobileScanDialog — QR-code-based document upload from a phone camera.

Shows a QR code linking to a temporary local HTTP endpoint on the LAN.
The user scans the QR with their phone, takes a photo in the browser,
and submits it.  The server receives the image, converts it to PDF,
saves it to the OCR folder, and emits file_received(filename).

No cloud, no internet, no PHI leaves the network.

Requirements:
  - Phone and desktop on the same WiFi/LAN.
  - qrcode[pil] installed (pip install "qrcode[pil]") for QR rendering.
    If missing the URL is shown as text instead.
"""
from __future__ import annotations

import io
import logging
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

logger = logging.getLogger("mobile_scan")

_TIMEOUT_SECONDS = 300  # 5-minute upload window


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _local_ip() -> str:
    """Return the machine's LAN IP (avoids 127.0.0.1)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# QR code rendering
# ---------------------------------------------------------------------------

def _make_qr_pixmap(url: str, size: int = 280) -> Optional[QPixmap]:
    """Render URL as a QR code QPixmap. Returns None if qrcode not installed."""
    try:
        import qrcode  # type: ignore
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=3,
        )
        qr.add_data(url)
        qr.make(fit=True)
        pil_img = qr.make_image(fill_color="black", back_color="white")
        pil_img = pil_img.resize((size, size))

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        return QPixmap.fromImage(QImage.fromData(buf.read()))
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Image → PDF conversion
# ---------------------------------------------------------------------------

def _image_to_pdf(src: Path, dest: Path) -> bool:
    """Convert an image file to PDF. Returns True on success."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open()
        img_doc = fitz.open(str(src))
        page_rect = img_doc[0].rect
        new_page = doc.new_page(width=page_rect.width, height=page_rect.height)
        new_page.insert_image(page_rect, filename=str(src))
        doc.save(str(dest))
        doc.close()
        img_doc.close()
        return True
    except Exception:
        pass
    try:
        from PIL import Image
        img = Image.open(src)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(dest, "PDF")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Upload HTML
# ---------------------------------------------------------------------------

_UPLOAD_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DMELogic Upload</title>
<style>
  body {{font-family:-apple-system,sans-serif;max-width:480px;
         margin:40px auto;padding:16px;background:#f9f9f9}}
  h2 {{color:#333}}
  input[type=file] {{display:block;margin:16px 0;font-size:16px}}
  button {{background:#F97316;color:#fff;border:none;padding:14px 28px;
           font-size:16px;border-radius:8px;cursor:pointer;width:100%}}
  button:active {{opacity:.8}}
  p.hint {{color:#666;font-size:13px}}
</style>
</head>
<body>
<h2>&#128222; Upload Document</h2>
<p>Take a photo of the prescription or document.</p>
<form method="POST" enctype="multipart/form-data">
  <input type="file" name="file" accept="image/*" capture="environment" required>
  <button type="submit">Upload</button>
</form>
<p class="hint">You can close this page once uploaded.</p>
</body>
</html>"""

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Uploaded</title>
<style>
  body {{font-family:-apple-system,sans-serif;max-width:480px;
         margin:40px auto;padding:16px;text-align:center}}
  h2 {{color:#22c55e}}
</style>
</head>
<body>
<h2>&#10004; Uploaded!</h2>
<p>Document received and attached to the order.</p>
<p>You can close this page.</p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _UploadServer(HTTPServer):
    def __init__(self, *args, token: str, **kwargs):
        self.token = token
        self.received = False
        self.received_bytes: Optional[bytes] = None
        super().__init__(*args, **kwargs)


class _UploadHandler(BaseHTTPRequestHandler):
    server: _UploadServer

    def log_message(self, fmt, *args):  # suppress default stderr logging
        logger.debug("[upload] " + fmt % args)

    def do_GET(self):
        if not self._check_token():
            return
        self._send_html(200, _UPLOAD_HTML)

    def do_POST(self):
        if not self._check_token():
            return
        if self.server.received:
            self._send_html(200, _SUCCESS_HTML)
            return
        try:
            ct = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ct:
                self._send_html(400, _UPLOAD_HTML)
                return
            boundary = ct.split("boundary=")[-1].encode()
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = _extract_file(body, boundary)
            if not data:
                self._send_html(400, _UPLOAD_HTML)
                return
            self.server.received_bytes = data
            self.server.received = True
            self._send_html(200, _SUCCESS_HTML)
        except Exception as exc:
            logger.exception(f"Upload handler error: {exc}")
            self._send_html(500, _UPLOAD_HTML)

    def _check_token(self) -> bool:
        from urllib.parse import parse_qs, urlparse
        token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if token != self.server.token:
            self._send_html(403, "<h1>403</h1>")
            return False
        return True

    def _send_html(self, code: int, body: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _extract_file(body: bytes, boundary: bytes) -> Optional[bytes]:
    """Pull the first file field from a multipart body."""
    sep = b"--" + boundary
    for part in body.split(sep):
        if b'name="file"' not in part:
            continue
        if b"\r\n\r\n" not in part:
            continue
        _, payload = part.split(b"\r\n\r\n", 1)
        payload = payload.rstrip(b"\r\n--")
        if payload:
            return payload
    return None


# ---------------------------------------------------------------------------
# Extension sniffing
# ---------------------------------------------------------------------------

_MAGIC: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG",      ".png"),
    (b"GIF8",         ".gif"),
    (b"RIFF",         ".webp"),
    (b"%PDF",         ".pdf"),
]


def _sniff_ext(data: bytes) -> str:
    for magic, ext in _MAGIC:
        if data[: len(magic)] == magic:
            return ext
    return ".jpg"


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class MobileScanDialog(QDialog):
    """
    Show a QR code; phone scans and uploads a photo; emit file_received
    with the saved filename (basename only, inside the OCR folder).
    """

    file_received = pyqtSignal(str)  # basename of the saved file

    def __init__(self, suggested_name: str = "mobile_scan", parent=None):
        super().__init__(parent)
        self.suggested_name = suggested_name
        self.setWindowTitle("Scan with Phone")
        self.setModal(True)
        self.setMinimumWidth(400)

        self._token = secrets.token_urlsafe(16)
        self._server: Optional[_UploadServer] = None
        self._start_time = time.monotonic()

        self._build_ui()
        self._start_server()

        self._poll = QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._check_received)
        self._poll.start()

        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

    # -- UI ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._info = QLabel("Point your phone camera at the QR code below.")
        self._info.setWordWrap(True)
        self._info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info)

        self._qr_lbl = QLabel()
        self._qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_lbl.setMinimumSize(290, 290)
        layout.addWidget(self._qr_lbl)

        self._url_lbl = QLabel()
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setWordWrap(True)
        self._url_lbl.setStyleSheet("color:#888;font-size:10px;")
        layout.addWidget(self._url_lbl)

        self._countdown = QLabel()
        self._countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._countdown)

        self._bar = QProgressBar()
        self._bar.setRange(0, _TIMEOUT_SECONDS)
        self._bar.setValue(_TIMEOUT_SECONDS)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        layout.addWidget(self._bar)

        row = QHBoxLayout()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(self._cancel_btn)
        layout.addLayout(row)

    # -- Server --------------------------------------------------------------

    def _start_server(self):
        try:
            self._server = _UploadServer(
                ("0.0.0.0", 0), _UploadHandler, token=self._token
            )
            port = self._server.server_address[1]
            ip = _local_ip()
            url = f"http://{ip}:{port}/upload?token={self._token}"

            threading.Thread(
                target=self._server.serve_forever, daemon=True
            ).start()

            pix = _make_qr_pixmap(url, 280)
            if pix:
                self._qr_lbl.setPixmap(pix)
            else:
                self._qr_lbl.setText(
                    "Install 'qrcode[pil]' for QR display.\n\nOpen URL on phone:"
                )

            self._url_lbl.setText(url)

        except Exception as exc:
            logger.exception(f"Server start failed: {exc}")
            self._info.setText(f"Could not start upload server:\n{exc}")

    # -- Polling / countdown -------------------------------------------------

    def _check_received(self):
        if self._server and self._server.received and self._server.received_bytes:
            self._poll.stop()
            self._ticker.stop()
            self._process(self._server.received_bytes)

    def _tick(self):
        elapsed = int(time.monotonic() - self._start_time)
        remaining = max(0, _TIMEOUT_SECONDS - elapsed)
        m, s = divmod(remaining, 60)
        self._countdown.setText(f"Link expires in {m}:{s:02d}")
        self._bar.setValue(remaining)
        if remaining == 0:
            self._poll.stop()
            self._ticker.stop()
            self._info.setText("Upload link expired. Close and try again.")
            self._cancel_btn.setText("Close")

    # -- File processing -----------------------------------------------------

    def _process(self, data: bytes):
        self._info.setText("Received! Saving…")
        self._qr_lbl.clear()
        self._url_lbl.clear()
        try:
            from dmelogic.paths import ocr_folder

            dest_dir = ocr_folder()
            dest_dir.mkdir(parents=True, exist_ok=True)

            ext = _sniff_ext(data)
            base = self.suggested_name or "mobile_scan"

            # Save raw image
            img_path = _unique_path(dest_dir, base + "_raw", ext)
            img_path.write_bytes(data)

            _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
            if ext.lower() in _IMAGE_EXTS:
                pdf_path = _unique_path(dest_dir, base, ".pdf")
                if _image_to_pdf(img_path, pdf_path):
                    img_path.unlink(missing_ok=True)
                    final_name = pdf_path.name
                else:
                    final_name = img_path.name
            else:
                final_name = img_path.name

            self._info.setText(f"Saved: {final_name}")
            self.file_received.emit(final_name)
            QTimer.singleShot(1000, self.accept)

        except Exception as exc:
            logger.exception(f"File processing error: {exc}")
            self._info.setText(f"Error saving file:\n{exc}")

    # -- Cleanup -------------------------------------------------------------

    def closeEvent(self, event):
        self._poll.stop()
        self._ticker.stop()
        if self._server:
            threading.Thread(target=self._server.shutdown, daemon=True).start()
        super().closeEvent(event)


def _unique_path(folder: Path, stem: str, ext: str) -> Path:
    """Return a path that doesn't exist yet, appending _2, _3, … as needed."""
    p = folder / (stem + ext)
    n = 2
    while p.exists():
        p = folder / f"{stem}_{n}{ext}"
        n += 1
    return p
