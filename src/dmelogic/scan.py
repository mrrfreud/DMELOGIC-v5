"""
scan.py — Scanner integration for DMELogic.

Supports three scanner modes:
  1. WIA (Windows Image Acquisition) — for scanners with WIA drivers
  2. File Picker — user scans with their scanner software (e.g. ScanSnap),
     then selects the resulting file
  3. Auto — tries WIA first, falls back to File Picker

Provides a helper to scan/select a document, copy it to the OCR folder,
and return the saved filename.
"""

from __future__ import annotations

import os
import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# WIA image format constants
WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_TIFF = "{B96B3CB1-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_PDF = "{D3CB2BF4-11A9-43C4-9B62-04E1CE28E8B8}"
WIA_DPS_DOCUMENT_HANDLING_SELECT = 3088
WIA_FEEDER = 1

# Scanner mode constants
MODE_AUTO = "Auto"
MODE_WIA = "WIA Only"
MODE_FILE_PICKER = "File Picker"

# Sentinel for "not supplied" (distinct from None / empty string)
_NOT_SET = object()


def _load_scanner_settings() -> dict:
    """Read scanner-related keys from the app settings file."""
    try:
        import json
        settings_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "DMELogic"
        settings_file = settings_dir / "settings.json"
        if settings_file.exists():
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "scanner_device_id": data.get("scanner_device_id", ""),
                "scan_format": data.get("scan_format", "PDF"),
                "scan_folder": data.get("scan_folder", ""),
                "scanner_mode": data.get("scanner_mode", MODE_AUTO),
                "scanner_app_path": data.get("scanner_app_path", ""),
                "scanner_output_folder": data.get("scanner_output_folder", ""),
            }
    except Exception:
        pass
    return {
        "scanner_device_id": "", "scan_format": "PDF", "scan_folder": "",
        "scanner_mode": MODE_AUTO, "scanner_app_path": "", "scanner_output_folder": "",
    }


def _has_wia_runtime() -> bool:
    """Return True when WIA runtime dependencies are available."""
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
        return True
    except Exception:
        return False


def scan_document(
    parent_widget=None,
    suggested_name: str = "",
    save_folder: Path | str | None = _NOT_SET,
    as_pdf: bool | None = None,
    device_id: str | None = _NOT_SET,
) -> str | None:
    """Scan or select a document and save it to the OCR folder.

    Behaviour depends on the scanner mode in Settings:
      - **Auto**: tries WIA first; if no WIA scanner is found, falls
        back to a file-picker dialog.
      - **WIA Only**: uses WIA (shows error if no WIA scanner).
      - **File Picker**: always shows a file-picker so the user can
        scan with external software (e.g. ScanSnap) then select the
        resulting file.

    Args:
        parent_widget: Parent QWidget (for dialogs / message boxes).
        suggested_name: Suggested base filename (without extension).
        save_folder: Folder to save into. Defaults to the configured
                     scan folder or ``ocr_folder()``.
        as_pdf: If True save as PDF, False as PNG.
        device_id: WIA DeviceID to connect to directly.

    Returns:
        The saved filename (basename only), or ``None`` if cancelled.
    """
    # Load saved scanner settings for any parameter not explicitly supplied
    cfg = _load_scanner_settings()
    if device_id is _NOT_SET:
        device_id = cfg["scanner_device_id"] or None
    if save_folder is _NOT_SET:
        sf = cfg["scan_folder"]
        save_folder = sf if sf else None
    if as_pdf is None:
        as_pdf = cfg["scan_format"].upper() != "PNG"

    if save_folder is None:
        from dmelogic.paths import ocr_folder
        save_folder = ocr_folder()
    save_folder = Path(save_folder)
    save_folder.mkdir(parents=True, exist_ok=True)

    mode = cfg.get("scanner_mode", MODE_AUTO)

    if mode == MODE_FILE_PICKER:
        return _scan_via_file_picker(parent_widget, suggested_name, save_folder, as_pdf)

    if mode == MODE_WIA:
        return _scan_via_wia(parent_widget, suggested_name, save_folder, as_pdf, device_id)

    # Auto mode: try WIA first when available, but fall back to file-picker flow
    # if WIA fails (including false "busy" states on some drivers).
    if device_id:
        result = _scan_via_wia(parent_widget, suggested_name, save_folder, as_pdf,
                               device_id, quiet_no_scanner=True)
        if result is not None:
            return result
        return _scan_via_file_picker(parent_widget, suggested_name, save_folder, as_pdf)

    # No specific device — try WIA silently, fall back to file picker if absent.
    result = _scan_via_wia(parent_widget, suggested_name, save_folder, as_pdf,
                           device_id, quiet_no_scanner=True)
    if result is not None:
        return result

    # WIA found nothing — offer file picker
    return _scan_via_file_picker(parent_widget, suggested_name, save_folder, as_pdf)


# ---------------------------------------------------------------------------
#  WIA scanner verification (pre-scan check)
# ---------------------------------------------------------------------------

def verify_wia_scanner(device_id: str = "") -> tuple[bool, str]:
    """Check whether a WIA scanner is reachable without actually scanning.

    Args:
        device_id: WIA DeviceID to check.  Pass empty string to check whether
                   *any* WIA device is present.

    Returns:
        ``(True, description)`` if the scanner is reachable,
        ``(False, error_message)`` otherwise.
    """
    try:
        import win32com.client
        import pythoncom
    except ImportError:
        return False, "pywin32 is not installed."

    try:
        pythoncom.CoInitialize()
        manager = win32com.client.Dispatch("WIA.DeviceManager")
        count = manager.DeviceInfos.Count

        if not device_id:
            if count == 0:
                return False, "No WIA scanner detected.  Check that the scanner is on and the driver is installed."
            names = []
            for i in range(1, count + 1):
                try:
                    names.append(manager.DeviceInfos.Item(i).Properties("Name").Value)
                except Exception:
                    names.append(f"Device {i}")
            return True, f"{count} scanner(s) found: {', '.join(names)}"

        # Look for specific device
        for i in range(1, count + 1):
            info = manager.DeviceInfos.Item(i)
            if info.DeviceID == device_id:
                try:
                    name = info.Properties("Name").Value
                except Exception:
                    name = device_id
                # Try connecting to confirm it is not just listed but actually online
                try:
                    info.Connect()
                except Exception as ce:
                    return False, f"Scanner '{name}' found in WIA but failed to connect: {ce}"
                return True, f"Scanner '{name}' is ready."

        return False, (
            "Configured scanner is no longer listed in Windows WIA.\n\n"
            "Possible causes:\n"
            "  • Scanner is off or disconnected\n"
            "  • Driver was uninstalled\n"
            "  • USB cable unplugged\n\n"
            "Fix: turn on the scanner, then refresh the scanner list in Settings."
        )
    except Exception as e:
        return False, f"WIA check failed: {e}"
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  WIA scanning
# ---------------------------------------------------------------------------

def _scan_via_wia(
    parent_widget,
    suggested_name: str,
    save_folder: Path,
    as_pdf: bool,
    device_id: str | None,
    quiet_no_scanner: bool = False,
) -> str | None:
    """Acquire an image through WIA.

    If *quiet_no_scanner* is True and no scanner is detected, returns
    ``None`` silently (so the caller can fall back to file picker).
    """
    try:
        import win32com.client
        import pythoncom
    except ImportError:
        if not quiet_no_scanner:
            _show_error(parent_widget, "pywin32 is not installed.\nInstall with: pip install pywin32")
        return None

    try:
        pythoncom.CoInitialize()

        if device_id:
            manager = win32com.client.Dispatch("WIA.DeviceManager")
            device = None
            for i in range(1, manager.DeviceInfos.Count + 1):
                info = manager.DeviceInfos.Item(i)
                if info.DeviceID == device_id:
                    device = info.Connect()
                    break
            if device is None:
                if not quiet_no_scanner:
                    _show_error(parent_widget,
                                "Configured scanner not found.\n\n"
                                "Check Settings → Scanner or select a different device.")
                return None
            item = device.Items(1)
            image = item.Transfer(WIA_FORMAT_PNG)
        else:
            # Check whether any WIA device exists before showing the dialog
            manager = win32com.client.Dispatch("WIA.DeviceManager")
            if manager.DeviceInfos.Count == 0:
                if not quiet_no_scanner:
                    _show_error(parent_widget,
                                "No WIA scanner found.\n\n"
                                "If you use a ScanSnap or similar scanner, go to\n"
                                "Settings → Scanner and change mode to\n"
                                "\"File Picker\" or \"Auto\".")
                return None
            wia_dialog = win32com.client.Dispatch("WIA.CommonDialog")
            image = wia_dialog.ShowAcquireImage()

        if image is None:
            return None

        return _save_scanned_image(image, suggested_name, save_folder, as_pdf)

    except Exception as e:
        error_msg = str(e)
        error_lower = error_msg.lower()
        if "cancelled" in error_lower or "cancel" in error_lower:
            return None
        if "-2145320939" in error_msg or "No scanner" in error_msg.lower():
            if not quiet_no_scanner:
                _show_error(parent_widget,
                            "No scanner found.\n\n"
                            "If you use a ScanSnap or similar scanner, go to\n"
                            "Settings → Scanner and change mode to\n"
                            "\"File Picker\" or \"Auto\".")
        else:
            is_busy = (
                "0x80210006" in error_msg
                or "-2145320954" in error_msg
                or "-2147352567" in error_msg
                or "wia device is busy" in error_lower
                or "busy" in error_lower
            )
            if is_busy:
                if device_id:
                    try:
                        # Some scanners reject direct item.Transfer but still work
                        # through the common WIA acquisition dialog.
                        wia_dialog = win32com.client.Dispatch("WIA.CommonDialog")
                        image = wia_dialog.ShowAcquireImage()
                        if image is not None:
                            return _save_scanned_image(image, suggested_name, save_folder, as_pdf)
                    except Exception:
                        pass

                if not quiet_no_scanner:
                    _show_error(parent_widget, "Scanner is busy or offline.\n\nClose any other scan app using the device, then try again.")
                return None

            logger.error(f"Scan failed: {e}")
            if not quiet_no_scanner:
                _show_error(parent_widget, f"Scan failed:\n{e}")
            return None
        return None
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _save_scanned_image(image, suggested_name: str, save_folder: Path, as_pdf: bool) -> str:
    """Save a WIA ImageFile to disk, optionally converting to PDF."""
    from datetime import datetime

    timestamp = datetime.now().strftime("%m-%d-%Y_%H%M%S")
    base = suggested_name.rstrip(".") if suggested_name else f"Scan_{timestamp}"
    ext = ".png"

    filename = f"{base}{ext}"
    save_path = save_folder / filename

    counter = 1
    while save_path.exists():
        filename = f"{base}_{counter}{ext}"
        save_path = save_folder / filename
        counter += 1

    image.SaveFile(str(save_path))
    logger.info(f"Scanned document saved: {save_path}")

    if as_pdf:
        pdf_filename = _convert_image_to_pdf(save_path)
        if pdf_filename:
            try:
                save_path.unlink()
            except OSError:
                pass
            return pdf_filename

    return filename


def _save_wia_image_to_path(image, save_path: Path) -> bool:
    """Save a WIA ImageFile to an exact path."""
    try:
        if save_path.exists():
            save_path.unlink()
        image.SaveFile(str(save_path))
        return True
    except Exception as e:
        logger.warning(f"Failed to save WIA page {save_path}: {e}")
        return False


def _combine_images_to_pdf(image_paths: list[Path], pdf_path: Path) -> bool:
    """Combine scanned image pages into a single PDF."""
    if not image_paths:
        return False

    try:
        import fitz

        doc = fitz.open()
        for image_path in image_paths:
            img_doc = fitz.open(str(image_path))
            rect = img_doc[0].rect
            page = doc.new_page(width=rect.width, height=rect.height)
            page.insert_image(rect, filename=str(image_path))
            img_doc.close()
        doc.save(str(pdf_path))
        doc.close()
        return True
    except Exception as e:
        logger.warning(f"PyMuPDF image merge failed: {e}")

    try:
        from PIL import Image

        opened = []
        try:
            for image_path in image_paths:
                img = Image.open(str(image_path))
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                opened.append(img)

            if not opened:
                return False
            first, rest = opened[0], opened[1:]
            first.save(str(pdf_path), "PDF", save_all=True, append_images=rest)
            return True
        finally:
            for img in opened:
                try:
                    img.close()
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Pillow image merge failed: {e}")
        return False


def _set_wia_property_value(wia_object, property_id: int, value) -> bool:
    try:
        properties = getattr(wia_object, "Properties", None)
        if properties is None:
            return False
        for idx in range(1, properties.Count + 1):
            prop = properties.Item(idx)
            if int(getattr(prop, "PropertyID", 0)) == property_id:
                prop.Value = value
                return True
    except Exception as e:
        logger.debug(f"Unable to set WIA property {property_id}: {e}")
    return False


def _prefer_wia_feeder(device) -> None:
    _set_wia_property_value(device, WIA_DPS_DOCUMENT_HANDLING_SELECT, WIA_FEEDER)


# ---------------------------------------------------------------------------
#  File-picker scanning (for ScanSnap, etc.)
# ---------------------------------------------------------------------------

def _scan_via_file_picker(
    parent_widget,
    suggested_name: str,
    save_folder: Path,
    as_pdf: bool,
) -> str | None:
    """Launch scanner software, watch for new files, let user confirm.

    Flow:
    1. Snapshot the scanner output folder
    2. Launch the scanner application (ScanSnap Home, etc.)
    3. Wait for the user to scan — show a dialog with a "Done Scanning" button
         or choose "Scan with Phone (QR)"
    4. Detect new files in the output folder
    5. If new file(s) found, auto-select the most recent one
    6. If nothing detected, fall back to a file picker
    7. Copy to OCR folder and return filename
    """
    import subprocess
    import time

    try:
        from PyQt6.QtWidgets import (QFileDialog, QMessageBox, QDialog,
                                     QVBoxLayout, QLabel, QPushButton, QHBoxLayout)
        from PyQt6.QtCore import Qt
    except ImportError:
        _show_error(parent_widget, "PyQt6 is required.")
        return None

    cfg = _load_scanner_settings()
    scanner_app = cfg.get("scanner_app_path", "")
    output_folder = cfg.get("scanner_output_folder", "")

    # Determine the folder to watch for new scans. If no scanner-output folder
    # is configured, watch the save destination so Brother/iPrint & Scan can
    # still save directly into the order's document folder.
    watch_folder = Path(output_folder) if output_folder else save_folder
    if watch_folder and not watch_folder.exists():
        watch_folder = save_folder if save_folder.exists() else None

    # If no usable watch folder exists, go straight to file picker.
    if not scanner_app and not watch_folder:
        return _simple_file_picker(parent_widget, suggested_name, save_folder, as_pdf)

    # Snapshot existing files in the watch folder
    scan_extensions = {'.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}
    existing_files: set[str] = set()
    if watch_folder:
        try:
            for f in watch_folder.iterdir():
                if f.is_file() and f.suffix.lower() in scan_extensions:
                    existing_files.add(str(f))
        except Exception:
            pass

    # Launch scanner application
    app_launched = False
    if scanner_app and Path(scanner_app).exists():
        try:
            subprocess.Popen([scanner_app], shell=False)
            app_launched = True
            logger.info(f"Launched scanner app: {scanner_app}")
        except Exception as e:
            logger.warning(f"Failed to launch scanner app: {e}")

    # Show "Done Scanning" dialog
    dlg = QDialog(parent_widget)
    dlg.setWindowTitle("Scan Document")
    dlg.setMinimumWidth(380)
    dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    layout = QVBoxLayout(dlg)

    if app_launched:
        msg = (
            "Your scanner software has been launched.\n\n"
            "1. Scan your document using the scanner\n"
            f"2. Save the scan to:\n{watch_folder}\n"
            "3. Wait for scanning to complete\n"
            "4. Click 'Done Scanning' below"
        )
    else:
        msg = (
            "Scan with your scanner software.\n\n"
            f"Save the scan to:\n{watch_folder}\n\n"
            "When the scan is complete, click 'Done Scanning' below."
        )

    label = QLabel(msg)
    label.setStyleSheet("font-size: 13px; padding: 10px;")
    layout.addWidget(label)

    btn_layout = QHBoxLayout()
    done_btn = QPushButton("✅ Done Scanning")
    done_btn.setStyleSheet("""
        QPushButton {
            background-color: #0d9488; color: white;
            font-weight: bold; padding: 10px 24px;
            border-radius: 6px; font-size: 13px;
        }
        QPushButton:hover { background-color: #0f766e; }
    """)
    done_btn.clicked.connect(dlg.accept)

    cancel_btn = QPushButton("Cancel")
    cancel_btn.setStyleSheet("padding: 10px 16px; font-size: 13px;")
    cancel_btn.clicked.connect(dlg.reject)

    pick_btn = QPushButton("📂 Pick File Manually")
    pick_btn.setStyleSheet("padding: 10px 16px; font-size: 13px;")
    pick_btn.setToolTip("Skip auto-detection and pick the scanned file yourself")

    phone_btn = QPushButton("📱 Scan with Phone (QR)")
    phone_btn.setStyleSheet("padding: 10px 16px; font-size: 13px;")
    phone_btn.setToolTip("Upload a document from your phone camera over local network")

    # Use a flag to track manual pick
    manual_pick = [False]
    mobile_result = [None]

    def on_manual_pick():
        manual_pick[0] = True
        dlg.accept()

    def on_phone_scan():
        try:
            from dmelogic.ui.mobile_scan_dialog import MobileScanDialog
        except Exception as e:
            _show_error(parent_widget, f"Mobile scan dialog unavailable:\n{e}")
            return

        m = MobileScanDialog(suggested_name=suggested_name or "mobile_scan", parent=parent_widget)

        def _on_file_received(filename: str):
            mobile_result[0] = filename

        m.file_received.connect(_on_file_received)
        res = m.exec()
        if res == QDialog.DialogCode.Accepted and mobile_result[0]:
            dlg.accept()

    pick_btn.clicked.connect(on_manual_pick)
    phone_btn.clicked.connect(on_phone_scan)

    btn_layout.addWidget(pick_btn)
    btn_layout.addWidget(phone_btn)
    btn_layout.addStretch()
    btn_layout.addWidget(cancel_btn)
    btn_layout.addWidget(done_btn)
    layout.addLayout(btn_layout)

    result = dlg.exec()
    if result != QDialog.DialogCode.Accepted:
        return None

    # Mobile scan already saved the file into OCR folder.
    if mobile_result[0]:
        return mobile_result[0]

    # Manual pick requested
    if manual_pick[0]:
        start_dir = str(watch_folder) if watch_folder else ""
        return _simple_file_picker(parent_widget, suggested_name, save_folder, as_pdf,
                                   start_dir=start_dir)

    # Check for new files in watch folder
    new_file = None
    if watch_folder:
        try:
            new_files = []
            for f in watch_folder.iterdir():
                if f.is_file() and f.suffix.lower() in scan_extensions:
                    if str(f) not in existing_files:
                        new_files.append(f)
            if new_files:
                # Pick the most recently modified
                new_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                new_file = new_files[0]
                logger.info(f"Auto-detected new scan: {new_file}")
        except Exception as e:
            logger.warning(f"Error scanning output folder: {e}")

    if new_file is None:
        # Nothing auto-detected — fall back to file picker
        QMessageBox.information(
            parent_widget,
            "Scan",
            "No new scanned file was detected.\n\n"
            "Please select the scanned file manually.",
        )
        start_dir = str(watch_folder) if watch_folder else ""
        return _simple_file_picker(parent_widget, suggested_name, save_folder, as_pdf,
                                   start_dir=start_dir)

    # We found a new file — copy it to OCR folder
    return _copy_to_ocr_folder(new_file, suggested_name, save_folder, as_pdf, parent_widget)


def _simple_file_picker(
    parent_widget,
    suggested_name: str,
    save_folder: Path,
    as_pdf: bool,
    start_dir: str = "",
) -> str | None:
    """Plain file picker fallback."""
    from PyQt6.QtWidgets import QFileDialog

    file_filter = "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp);;All Files (*)"
    file_path, _ = QFileDialog.getOpenFileName(
        parent_widget,
        "Select Scanned Document",
        start_dir,
        file_filter,
    )
    if not file_path:
        return None

    src = Path(file_path)
    if not src.exists():
        _show_error(parent_widget, f"File not found:\n{file_path}")
        return None

    return _copy_to_ocr_folder(src, suggested_name, save_folder, as_pdf, parent_widget)


def _copy_to_ocr_folder(
    src: Path,
    suggested_name: str,
    save_folder: Path,
    as_pdf: bool,
    parent_widget=None,
) -> str | None:
    """Copy a scanned file to the OCR folder, optionally converting to PDF."""
    if not src.exists():
        _show_error(parent_widget, f"File not found:\n{src}")
        return None

    # Build target filename
    timestamp = datetime.now().strftime("%m-%d-%Y_%H%M%S")
    base = suggested_name.rstrip(".") if suggested_name else f"Scan_{timestamp}"

    # Keep original extension unless user wants PDF conversion
    src_ext = src.suffix.lower()
    target_ext = src_ext if src_ext else ".pdf"

    filename = f"{base}{target_ext}"
    dest_path = save_folder / filename

    counter = 1
    while dest_path.exists():
        filename = f"{base}_{counter}{target_ext}"
        dest_path = save_folder / filename
        counter += 1

    # Copy file to OCR folder
    try:
        shutil.copy2(str(src), str(dest_path))
        logger.info(f"Copied scanned file: {src} -> {dest_path}")
    except Exception as e:
        _show_error(parent_widget, f"Failed to copy file:\n{e}")
        return None

    # Convert to PDF if requested and source isn't already PDF
    if as_pdf and target_ext != ".pdf":
        pdf_filename = _convert_image_to_pdf(dest_path)
        if pdf_filename:
            try:
                dest_path.unlink()
            except OSError:
                pass
            return pdf_filename

    return filename


def _convert_image_to_pdf(image_path: Path) -> str | None:
    """Convert a scanned image (PNG/TIFF) to PDF using Pillow or PyMuPDF.

    Returns the PDF filename (basename) or None on failure.
    """
    pdf_path = image_path.with_suffix(".pdf")

    # Try a compact PDF path first to keep scan sizes manageable.
    # Typical fax/RX paperwork remains readable at 200 DPI with grayscale JPEG.
    try:
        import tempfile
        from PIL import Image
        from reportlab.pdfgen import canvas

        target_dpi = 200
        max_long_edge = 2800
        jpeg_quality = 50

        img = Image.open(str(image_path))

        # Normalize mode and favor grayscale for paperwork-size files.
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if img.mode != "L":
            img = img.convert("L")

        # Bound image dimensions to keep PDF size predictable.
        w, h = img.size
        long_edge = max(w, h)
        if long_edge > max_long_edge and long_edge > 0:
            scale = max_long_edge / float(long_edge)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            w, h = img.size

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_jpg = Path(tmp.name)

        try:
            img.save(
                str(tmp_jpg),
                "JPEG",
                quality=jpeg_quality,
                optimize=True,
                progressive=True,
            )

            page_w = (w * 72.0) / target_dpi
            page_h = (h * 72.0) / target_dpi
            c = canvas.Canvas(str(pdf_path), pagesize=(page_w, page_h))
            c.drawImage(str(tmp_jpg), 0, 0, width=page_w, height=page_h, preserveAspectRatio=True)
            c.showPage()
            c.save()
            return pdf_path.name
        finally:
            try:
                tmp_jpg.unlink()
            except OSError:
                pass
    except Exception:
        pass

    # Try PyMuPDF (fitz) first — already in the project
    try:
        import fitz  # PyMuPDF
        doc = fitz.open()
        img = fitz.open(str(image_path))
        # Get image dimensions
        page = img[0]
        rect = page.rect
        pdf_page = doc.new_page(width=rect.width, height=rect.height)
        pdf_page.insert_image(rect, filename=str(image_path))
        doc.save(str(pdf_path))
        doc.close()
        img.close()
        return pdf_path.name
    except Exception:
        pass

    # Fallback to Pillow
    try:
        from PIL import Image
        img = Image.open(str(image_path))
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(str(pdf_path), "PDF")
        img.close()
        return pdf_path.name
    except Exception:
        pass

    # Could not convert — return the image filename instead
    return None


def scan_batch_document(
    parent_widget=None,
    save_folder: "Path | str | None" = None,
    suggested_name: str = "Delivery Batch",
    max_wia_attempts: int = 3,
) -> "tuple[Path | None, str]":
    """Acquire a single multi-page document for batch delivery processing.

    This is the **scanner-mode-only** acquisition path.  It never falls back
    to a generic file-picker dialog.  Supports two scanner environments:

    * **WIA** — acquires directly through Windows Image Acquisition.  If the
      scanner is busy the user is offered up to *max_wia_attempts* retries.
        * **Output-folder watch** (Brother iPrint & Scan, ScanSnap, etc.) — shows a "press Scan, then
      Done" dialog and detects the newest file in the configured output folder.
      If nothing new is found the user is told to retry; no generic file
      picker is shown.

    Returns:
        ``(Path, "")`` on success, ``(None, error_message)`` on failure or
        cancel (``error_message`` is empty string when user cancelled).
    """
    from pathlib import Path as _Path

    cfg = _load_scanner_settings()
    mode = cfg.get("scanner_mode", MODE_AUTO)
    device_id = cfg.get("scanner_device_id", "") or ""
    scanner_app = cfg.get("scanner_app_path", "")
    output_folder_str = cfg.get("scanner_output_folder", "")

    if save_folder is None:
        from dmelogic.paths import delivery_tickets_folder
        save_folder = delivery_tickets_folder()
    save_folder = _Path(save_folder)
    save_folder.mkdir(parents=True, exist_ok=True)
    fallback_output_folder = output_folder_str or str(save_folder)

    # For batch jobs in Auto mode, prefer output-folder acquisition when it is
    # configured. This captures scanners that emit one file per page.
    use_wia = (
        mode == MODE_WIA
        or (mode == MODE_AUTO and not output_folder_str)
    )

    # Graceful handling for environments without pywin32/WIA runtime.
    if use_wia and not _has_wia_runtime():
        if output_folder_str:
            return _batch_acquire_via_output_folder(
                parent_widget, save_folder, suggested_name, scanner_app, output_folder_str
            )
        return None, (
            "WIA scanning is unavailable because pywin32 is not installed.\n\n"
            "Use \"Use existing files\" in Batch Delivery OCR, or configure "
            "Settings → Scanner → Output Folder for scanner-mode capture."
        )

    if use_wia:
        wia_attempts = 1 if mode == MODE_AUTO else max_wia_attempts
        scanned_path, error_msg = _batch_acquire_via_wia(
            parent_widget, save_folder, suggested_name, device_id, wia_attempts
        )
        if scanned_path is not None:
            return scanned_path, ""
        # Brother's WIA driver can report "device busy" even when the scanner is
        # idle. In Auto mode, fall back to the scanner-app folder workflow so the
        # user can scan from Brother iPrint & Scan and keep OCR auto-attach.
        if mode == MODE_AUTO:
            return _batch_acquire_via_output_folder(
                parent_widget,
                save_folder,
                suggested_name,
                scanner_app,
                fallback_output_folder,
                wia_error=error_msg,
            )
        return None, error_msg

    # Output-folder / scanner-app path (ScanSnap-style) — no generic picker.
    return _batch_acquire_via_output_folder(
        parent_widget, save_folder, suggested_name, scanner_app, fallback_output_folder
    )


def _batch_acquire_via_wia(
    parent_widget,
    save_folder: "Path",
    suggested_name: str,
    device_id: str,
    max_attempts: int,
) -> "tuple[Path | None, str]":
    """WIA acquisition with repeated ADF transfers for true batch scanning."""
    import tempfile
    from datetime import datetime as _dt
    from pathlib import Path as _Path

    try:
        import win32com.client
        import pythoncom
    except ImportError:
        return None, "pywin32 is not installed."

    def _connect_device():
        manager = win32com.client.Dispatch("WIA.DeviceManager")
        if device_id:
            for i in range(1, manager.DeviceInfos.Count + 1):
                info = manager.DeviceInfos.Item(i)
                if info.DeviceID == device_id:
                    return info.Connect()
            raise RuntimeError("Configured scanner not found. Refresh scanner list in Settings.")

        if manager.DeviceInfos.Count == 0:
            raise RuntimeError("No WIA scanner found.")
        if manager.DeviceInfos.Count == 1:
            return manager.DeviceInfos.Item(1).Connect()

        dialog = win32com.client.Dispatch("WIA.CommonDialog")
        selected = dialog.ShowSelectDevice(1, True, False)
        if selected is None:
            raise RuntimeError("Scanner selection cancelled.")
        return selected

    def _is_feeder_done(error_text: str) -> bool:
        lower = error_text.lower()
        return any(
            token in lower
            for token in (
                "paper",
                "feeder",
                "empty",
                "no documents",
                "document feeder",
                "0x80210003",
                "0x8021000c",
                "-2145320957",
                "-2145320948",
            )
        )

    def _is_wia_busy(error_text: str) -> bool:
        lower = error_text.lower()
        return any(
            token in lower
            for token in (
                "0x80210006",
                "-2145320954",
                "-2147352567",
                "wia device is busy",
                "busy",
            )
        )

    def _show_dialog_acquire_batch(temp_dir: _Path) -> list[_Path]:
        dialog = win32com.client.Dispatch("WIA.CommonDialog")
        dialog_pages: list[_Path] = []
        for page_no in range(1, 101):
            try:
                image = dialog.ShowAcquireImage()
            except Exception as dialog_error:
                error_text = str(dialog_error)
                if dialog_pages or _is_feeder_done(error_text) or "cancel" in error_text.lower():
                    break
                raise

            if image is None:
                break

            page_path = temp_dir / f"dialog_page_{page_no:03d}.png"
            if _save_wia_image_to_path(image, page_path):
                dialog_pages.append(page_path)
            else:
                break
        return dialog_pages

    last_error = ""
    for attempt in range(max_attempts):
        temp_dir = _Path(tempfile.mkdtemp(prefix="dme_wia_batch_"))
        page_paths: list[_Path] = []
        device = None
        item = None
        image = None

        try:
            pythoncom.CoInitialize()
            try:
                device = _connect_device()
            except Exception as connect_error:
                error_text = str(connect_error)
                if _is_wia_busy(error_text):
                    logger.info("WIA device connect reported busy; trying WIA acquisition dialog.")
                    page_paths = _show_dialog_acquire_batch(temp_dir)
                else:
                    raise

            if device is not None:
                _prefer_wia_feeder(device)
                item = device.Items(1)

                for page_no in range(1, 101):
                    try:
                        image = item.Transfer(WIA_FORMAT_PNG)
                    except Exception as page_error:
                        error_text = str(page_error)
                        if not page_paths and _is_wia_busy(error_text):
                            logger.info("Direct WIA batch transfer reported busy; trying WIA acquisition dialog.")
                            page_paths = _show_dialog_acquire_batch(temp_dir)
                            break
                        if page_paths or _is_feeder_done(error_text):
                            break
                        raise

                    if image is None:
                        break

                    page_path = temp_dir / f"page_{page_no:03d}.png"
                    if _save_wia_image_to_path(image, page_path):
                        page_paths.append(page_path)

            if not page_paths:
                last_error = "Scanner did not return any pages."
            else:
                timestamp = _dt.now().strftime("%m-%d-%Y_%H%M%S")
                base = suggested_name.rstrip(".") or f"Batch_{timestamp}"
                dest = save_folder / f"{base}.pdf"
                counter = 1
                while dest.exists():
                    dest = save_folder / f"{base}_{counter}.pdf"
                    counter += 1

                if _combine_images_to_pdf(page_paths, dest):
                    logger.info(f"WIA batch scan saved {len(page_paths)} page(s): {dest}")
                    return dest, ""
                last_error = "Scanned pages were captured but could not be combined into a PDF."
        except Exception as e:
            error_text = str(e)
            if "cancel" in error_text.lower():
                return None, ""
            last_error = error_text
        finally:
            image = None
            item = None
            device = None
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

        if attempt < max_attempts - 1:
            try:
                from PyQt6.QtWidgets import QMessageBox
                retry = QMessageBox.question(
                    parent_widget,
                    "Scanner Busy",
                    "Scanner did not return the batch.\n\n"
                    "Make sure pages are loaded in the feeder and no other scan app is using the device,\n"
                    f"then click Retry to try again. ({attempt + 1}/{max_attempts})\n\n"
                    f"Last error: {last_error}",
                    QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Retry,
                )
                if retry != QMessageBox.StandardButton.Retry:
                    return None, ""
            except Exception:
                break

    return None, last_error or "Scanner did not return a document after multiple attempts."


def _batch_acquire_via_output_folder(
    parent_widget,
    save_folder: "Path",
    suggested_name: str,
    scanner_app: str,
    output_folder_str: str,
    wia_error: str = "",
) -> "tuple[Path | None, str]":
    """Output-folder watch acquisition for ScanSnap-style scanners.

    Shows a "press Scan / Done Scanning" dialog, then detects new files.
    Never falls back to a generic file picker.
    """
    import subprocess
    import time
    from pathlib import Path as _Path

    try:
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QMessageBox
        )
        from PyQt6.QtCore import Qt
    except ImportError:
        return None, "PyQt6 is required."

    watch_folder = _Path(output_folder_str) if output_folder_str else None
    if watch_folder and not watch_folder.exists():
        return None, (
            f"Scanner output folder not found:\n{output_folder_str}\n\n"
            "Update the path in Settings → Scanner → Output Folder."
        )

    if not watch_folder:
        return None, (
            "No scanner output folder is configured.\n\n"
            "Go to Settings → Scanner and set the Output Folder to the folder\n"
            "where your scanner saves files (e.g. ScanSnap output folder)."
        )

    scan_extensions = {'.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}

    while True:
        # Snapshot existing files and baseline mtimes/sizes so we can detect
        # both newly created files and files overwritten in place.
        try:
            existing: set[str] = set()
            existing_meta: dict[str, tuple[int, int]] = {}
            for f in watch_folder.iterdir():
                if not (f.is_file() and f.suffix.lower() in scan_extensions):
                    continue
                p = str(f)
                st = f.stat()
                existing.add(p)
                existing_meta[p] = (int(st.st_mtime_ns), int(st.st_size))
        except Exception as e:
            return None, f"Cannot read scanner output folder:\n{e}"

        scan_start_ns = time.time_ns()

        # Optionally launch scanner app
        app_launched = False
        if scanner_app and _Path(scanner_app).exists():
            try:
                subprocess.Popen([scanner_app], shell=False)
                app_launched = True
            except Exception as e:
                logger.warning(f"Failed to launch scanner app: {e}")

        # Show "Done Scanning" dialog — no manual-pick option
        dlg = QDialog(parent_widget)
        dlg.setWindowTitle("Batch Scan — Ready")
        dlg.setMinimumWidth(420)
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        layout = QVBoxLayout(dlg)

        folder_text = str(watch_folder)
        wia_note = ""
        if wia_error:
            wia_note = (
                "WIA did not return a scan, so DMELogic is using scanner-app capture.\n\n"
            )

        if app_launched:
            msg_text = (
                f"{wia_note}Your scanner software has been launched.\n\n"
                "1. Place your delivery tickets face-down in the feeder\n"
                "2. Press Scan in the scanner software\n"
                f"3. Save the scan to:\n{folder_text}\n"
                "4. Wait for all pages to finish scanning\n"
                "5. Click Done Scanning below"
            )
        else:
            msg_text = (
                f"{wia_note}Ready to scan a batch of delivery tickets.\n\n"
                "1. Place your delivery tickets face-down in the feeder\n"
                "2. Scan with Brother iPrint & Scan\n"
                f"3. Save the scan to:\n{folder_text}\n"
                "4. Wait for all pages to finish scanning\n"
                "5. Click Done Scanning below"
            )

        lbl = QLabel(msg_text)
        lbl.setStyleSheet("font-size: 13px; padding: 10px;")
        layout.addWidget(lbl)

        note = QLabel(
            "DMELogic auto-detects new PDF/image files in this folder and sends them to Batch Delivery OCR."
        )
        note.setStyleSheet("font-size: 11px; color: #888; padding: 4px 10px;")
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        done_btn = QPushButton("✅ Done Scanning")
        done_btn.setStyleSheet("""
            QPushButton { background-color: #0d9488; color: white;
                font-weight: bold; padding: 10px 24px;
                border-radius: 6px; font-size: 13px; }
            QPushButton:hover { background-color: #0f766e; }
        """)
        done_btn.clicked.connect(dlg.accept)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("padding: 10px 16px; font-size: 13px;")
        cancel_btn.clicked.connect(dlg.reject)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(done_btn)
        layout.addLayout(btn_row)

        res = dlg.exec()
        if res != QDialog.DialogCode.Accepted:
            return None, ""  # user cancelled

        # Detect scanner outputs. Some scanner apps overwrite the same file
        # path for each batch, and some emit additional pages a few seconds
        # after the first file appears, so poll briefly until output is quiet.
        try:
            candidate_map: dict[str, _Path] = {}

            # Poll for up to ~14s total and stop early after ~3s of inactivity.
            # This captures pages that arrive shortly after the first output.
            poll_interval = 0.5
            max_poll_seconds = 14.0
            quiet_seconds = 3.0
            elapsed = 0.0
            quiet_elapsed = 0.0

            while elapsed < max_poll_seconds:
                changed_this_round = False
                for f in watch_folder.iterdir():
                    if not (f.is_file() and f.suffix.lower() in scan_extensions):
                        continue
                    p = str(f)
                    st = f.stat()
                    curr = (int(st.st_mtime_ns), int(st.st_size))
                    prev = existing_meta.get(p)

                    changed = prev is None or curr != prev or int(st.st_mtime_ns) >= scan_start_ns
                    if changed:
                        candidate_map[p] = f
                        changed_this_round = True
                        existing_meta[p] = curr

                if changed_this_round:
                    quiet_elapsed = 0.0
                else:
                    quiet_elapsed += poll_interval
                    if quiet_elapsed >= quiet_seconds and candidate_map:
                        break

                time.sleep(poll_interval)
                elapsed += poll_interval

            candidate_files = list(candidate_map.values())

            # Wait briefly for candidate files to finish writing.
            settle_rounds = 0
            while candidate_files and settle_rounds < 10:
                stable = True
                for f in list(candidate_files):
                    try:
                        s1 = f.stat()
                    except Exception:
                        stable = False
                        break
                    time.sleep(0.2)
                    try:
                        s2 = f.stat()
                    except Exception:
                        stable = False
                        break
                    if (int(s1.st_size), int(s1.st_mtime_ns)) != (int(s2.st_size), int(s2.st_mtime_ns)):
                        stable = False
                        break
                if stable:
                    break
                settle_rounds += 1
                time.sleep(0.25)

            new_files = candidate_files
        except Exception as e:
            return None, f"Cannot read scanner output folder:\n{e}"

        if not new_files:
            retry = QMessageBox.question(
                parent_widget,
                "No New Scan Detected",
                "No new scanned file was found in the scanner output folder.\n\n"
                f"Output folder: {watch_folder}\n\n"
                "Make sure the scanner finished and saved the file, then click Retry.",
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Retry,
            )
            if retry != QMessageBox.StandardButton.Retry:
                return None, ""
            continue  # loop back and try again

        # Build destination path once for either single-file copy or merge.
        from datetime import datetime as _dt
        timestamp = _dt.now().strftime("%m-%d-%Y_%H%M%S")
        base = suggested_name.rstrip(".") or f"Batch_{timestamp}"
        dest = save_folder / f"{base}.pdf"
        counter = 1
        while dest.exists():
            dest = save_folder / f"{base}_{counter}.pdf"
            counter += 1

        # Preserve scan order by file modification time.
        new_files.sort(key=lambda p: p.stat().st_mtime)

        # Most scanners emit either one multi-page PDF or many single-page
        # image/PDF files. If many files are emitted, merge everything so the
        # downstream splitter can process every page.
        try:
            if len(new_files) == 1 and new_files[0].suffix.lower() == ".pdf":
                src = new_files[0]
                logger.info(f"Batch scan detected new file: {src}")
                shutil.copy2(str(src), str(dest))
                return dest, ""

            import fitz

            merged = fitz.open()
            for src in new_files:
                suffix = src.suffix.lower()
                logger.info(f"Batch scan include file: {src}")
                if suffix == ".pdf":
                    part = fitz.open(str(src))
                    merged.insert_pdf(part)
                    part.close()
                    continue

                if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
                    img_doc = fitz.open(str(src))
                    rect = img_doc[0].rect
                    page = merged.new_page(width=rect.width, height=rect.height)
                    page.insert_image(rect, filename=str(src))
                    img_doc.close()
                    continue

                logger.warning(f"Skipping unsupported batch scan file type: {src}")

            if len(merged) == 0:
                merged.close()
                return None, "No supported scanned pages were detected in the output folder."

            merged.save(str(dest))
            merged.close()
        except Exception as e:
            return None, f"Failed to combine scanned pages:\n{e}"

        return dest, ""


def _show_error(parent_widget, message: str):
    """Show an error message box if we have a parent widget, otherwise print."""
    try:
        if parent_widget:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(parent_widget, "Scanner", message)
        else:
            print(f"Scanner error: {message}")
    except Exception:
        print(f"Scanner error: {message}")
