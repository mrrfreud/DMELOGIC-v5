"""
Agent Order Watcher — file-based queue for cross-platform agent integration.

Problem: Cloney (and other AI agents) run inside a sandboxed Linux VM that
mounts the Windows filesystem.  They cannot call ``create_order_as_agent()``
directly because:
  1. SQLite databases use Windows paths that don't translate to Linux.
  2. The running DMELogic app holds WAL/SHM locks.

Solution: Agents write a small JSON file into a **drop folder**.  This watcher
(running inside the Windows DMELogic process) polls that folder and feeds
each JSON through ``create_order_as_agent()``.

Drop folder location (auto-created):
    <fax_root>/agent_orders/          — incoming JSON files
    <fax_root>/agent_orders/done/     — successfully processed
    <fax_root>/agent_orders/failed/   — could not be processed

JSON schema (one file per order):
    {
        "patient_last_name": "Gomez",
        "patient_first_name": "Alizabeth",
        "patient_dob": "2006-09-04",
        "rx_date": "2026-03-25",
        "prescriber_name": "Dr. Smith",
        "prescriber_npi": "1234567890",
        "primary_insurance": "Medicaid",
        "icd_codes": ["R32"],
        "items": [
            {"hcpcs": "T4522", "description": "Diapers, medium", "quantity": 30}
        ],
        "rx_origin": "Fax",
        "source_file_path": "C:/Users/pharmacy/Documents/FaxManagerData/Faxes OCR'd/rx.pdf",
        "notes": "Processed by Cloney v2.0"
    }

All fields except ``patient_last_name`` are optional (mirrors
``create_order_as_agent()`` signature).
"""

from __future__ import annotations

import json
import os
import shutil
import time
import traceback
from pathlib import Path
from typing import Optional, Callable, List

from dmelogic.config import debug_log


# ── Default drop-folder location ────────────────────────────────────
def _default_drop_folder() -> Path:
    """Return the default agent-order drop folder under the OCR'd folder."""
    try:
        from dmelogic.paths import ocr_folder
        return ocr_folder() / "agent_orders"
    except Exception:
        # Absolute fallback
        return Path(os.path.expanduser("~")) / "Documents" / "FaxManagerData" / "Faxes OCR'd" / "agent_orders"


# ── Public watcher class ────────────────────────────────────────────
class AgentOrderWatcher:
    """
    Watches a drop folder for ``.json`` order files and submits them
    through ``create_order_as_agent()``.

    Designed to be called periodically (e.g. from a QTimer).  Each call
    to :meth:`poll` processes at most ``batch_size`` files so the UI
    stays responsive.

    Attributes:
        drop_folder:  Path to the incoming-order folder.
        done_folder:  Successfully processed JSONs are moved here.
        failed_folder: Failed JSONs are moved here (with a ``.reason`` sidecar).
        on_order_created: Optional callback(order_id: int, filename: str).
        on_order_failed:  Optional callback(filename: str, reason: str).
    """

    def __init__(
        self,
        drop_folder: Optional[Path] = None,
        on_order_created: Optional[Callable[[int, str], None]] = None,
        on_order_failed: Optional[Callable[[str, str], None]] = None,
        batch_size: int = 5,
    ):
        self.drop_folder = Path(drop_folder) if drop_folder else _default_drop_folder()
        self.done_folder = self.drop_folder / "done"
        self.failed_folder = self.drop_folder / "failed"
        self.on_order_created = on_order_created
        self.on_order_failed = on_order_failed
        self.batch_size = batch_size

        # Ensure folders exist
        for d in (self.drop_folder, self.done_folder, self.failed_folder):
            d.mkdir(parents=True, exist_ok=True)

        debug_log(f"[agent-watcher] Watching {self.drop_folder}")

    # ----------------------------------------------------------------
    # Core poll
    # ----------------------------------------------------------------

    def poll(self) -> int:
        """
        Check for new ``.json`` files, process up to ``batch_size``.

        Returns the number of orders successfully created this cycle.
        """
        json_files = sorted(self.drop_folder.glob("*.json"))[:self.batch_size]
        created = 0

        for jf in json_files:
            try:
                ok = self._process_file(jf)
                if ok:
                    created += 1
            except Exception as exc:
                debug_log(f"[agent-watcher] Unexpected error processing {jf.name}: {exc}")
                self._move_to_failed(jf, f"Unexpected error: {exc}")

        return created

    # ----------------------------------------------------------------
    # File processing
    # ----------------------------------------------------------------

    def _process_file(self, json_path: Path) -> bool:
        """Parse a single JSON file and create an order.  Returns True on success."""
        debug_log(f"[agent-watcher] Processing {json_path.name}")

        # -- Read & parse --
        try:
            raw_text = json_path.read_text(encoding="utf-8-sig")
            data = json.loads(raw_text)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            reason = f"Invalid JSON: {e}"
            debug_log(f"[agent-watcher] {reason}")
            self._move_to_failed(json_path, reason)
            return False

        if not isinstance(data, dict):
            reason = "JSON root must be an object (dict)"
            self._move_to_failed(json_path, reason)
            return False

        # -- Map JSON keys to create_order_as_agent kwargs --
        # We lazily import to avoid circular imports at module load.
        from dmelogic.services.agent_order_service import create_order_as_agent

        kwargs = self._map_json_to_kwargs(data)

        try:
            order_id = create_order_as_agent(**kwargs)
        except ValueError as ve:
            # Duplicate detection or validation error
            reason = str(ve)
            debug_log(f"[agent-watcher] Order rejected: {reason}")
            self._move_to_failed(json_path, reason)
            return False
        except Exception as exc:
            reason = f"create_order_as_agent error: {exc}\n{traceback.format_exc()}"
            debug_log(f"[agent-watcher] {reason}")
            self._move_to_failed(json_path, reason)
            return False

        # -- Success --
        debug_log(f"[agent-watcher] Order {order_id} created from {json_path.name}")
        
        # Archive the source PDF to "Processesd By Cloney" folder automatically
        # This eliminates the need for the external agent to wait for confirmation
        self._archive_source_pdf(data, order_id)
        
        self._move_to_done(json_path, order_id)

        if self.on_order_created:
            try:
                self.on_order_created(order_id, json_path.name)
            except Exception:
                pass

        return True

    # ----------------------------------------------------------------
    # JSON → kwargs mapping
    # ----------------------------------------------------------------

    @staticmethod
    def _map_json_to_kwargs(data: dict) -> dict:
        """
        Translate a Cloney-style JSON dict into keyword arguments for
        ``create_order_as_agent()``.

        Accepts both the canonical field names (matching the Python function
        signature) AND some friendly aliases that an agent might produce.
        """
        kwargs: dict = {}

        # --- Patient ---
        kwargs["patient_last_name"] = data.get("patient_last_name", "")
        kwargs["patient_first_name"] = data.get("patient_first_name", "")
        kwargs["patient_id"] = data.get("patient_id")
        kwargs["patient_dob"] = data.get("patient_dob") or data.get("dob")
        kwargs["patient_phone"] = data.get("patient_phone") or data.get("phone")
        kwargs["patient_address"] = data.get("patient_address") or data.get("address")

        # --- Prescriber ---
        kwargs["prescriber_id"] = data.get("prescriber_id")
        kwargs["prescriber_name"] = data.get("prescriber_name")
        kwargs["prescriber_npi"] = data.get("prescriber_npi") or data.get("npi")

        # --- Dates ---
        kwargs["rx_date"] = data.get("rx_date")
        kwargs["order_date"] = data.get("order_date")

        # --- Insurance ---
        kwargs["primary_insurance"] = data.get("primary_insurance") or data.get("insurance")
        kwargs["primary_insurance_id"] = data.get("primary_insurance_id") or data.get("insurance_id")
        kwargs["billing_type"] = data.get("billing_type")
        kwargs["place_of_service"] = data.get("place_of_service") or data.get("placeOfService") or data.get("pos")

        # --- Diagnosis ---
        # Accept "icd_codes", "icd10_codes", or "icd_10_codes" (various agent formats)
        icd = data.get("icd_codes") or data.get("icd10_codes") or data.get("icd_10_codes")
        if icd and isinstance(icd, list):
            kwargs["icd_codes"] = icd

        # --- Items ---
        items = data.get("items")
        if items and isinstance(items, list):
            kwargs["items"] = items

        # --- Origin / Source ---
        kwargs["rx_origin"] = data.get("rx_origin")
        kwargs["source_file_path"] = data.get("source_file_path")

        # --- Misc ---
        kwargs["doctor_directions"] = data.get("doctor_directions")
        kwargs["notes"] = data.get("notes")

        # Don't pass folder_path — let the Windows app use its own settings
        # (this is the whole point: the agent can't resolve Windows DB paths)

        # Remove None values so create_order_as_agent uses its defaults
        return {k: v for k, v in kwargs.items() if v is not None}

    # ----------------------------------------------------------------
    # File movement helpers
    # ----------------------------------------------------------------

    def _move_to_done(self, json_path: Path, order_id: int) -> None:
        """Move a successfully processed JSON to the done folder."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = self.done_folder / f"{json_path.stem}__order{order_id}__{ts}.json"
        try:
            shutil.move(str(json_path), str(dest))
        except Exception as e:
            debug_log(f"[agent-watcher] Failed to move {json_path.name} to done: {e}")
            # Try to at least delete so we don't re-process
            try:
                json_path.unlink()
            except Exception:
                pass

    def _move_to_failed(self, json_path: Path, reason: str) -> None:
        """Move a failed JSON to the failed folder with a reason sidecar."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = self.failed_folder / f"{json_path.stem}__{ts}.json"
        reason_file = self.failed_folder / f"{json_path.stem}__{ts}.reason"
        try:
            shutil.move(str(json_path), str(dest))
        except Exception as e:
            debug_log(f"[agent-watcher] Failed to move {json_path.name} to failed: {e}")
            try:
                json_path.unlink()
            except Exception:
                pass

        try:
            reason_file.write_text(reason, encoding="utf-8")
        except Exception:
            pass

        if self.on_order_failed:
            try:
                self.on_order_failed(json_path.name, reason)
            except Exception:
                pass

    # ----------------------------------------------------------------
    # Convenience: list pending files (for UI status display)
    # ----------------------------------------------------------------

    def pending_count(self) -> int:
        """Return number of unprocessed JSON files in the drop folder."""
        try:
            return len(list(self.drop_folder.glob("*.json")))
        except Exception:
            return 0

    # ----------------------------------------------------------------
    # Automatic file archival
    # ----------------------------------------------------------------

    def _archive_source_pdf(self, data: dict, order_id: int) -> None:
        """
        Automatically move/rename the source PDF to 'Processesd By Cloney' folder.
        
        This eliminates the need for the external agent to wait for DMELogic
        confirmation before archiving - DMELogic handles it automatically.
        """
        from datetime import datetime
        from dmelogic.paths import ocr_folder
        
        try:
            source_path = data.get("source_file_path", "")
            if not source_path or not os.path.exists(source_path):
                debug_log(f"[agent-watcher] No source file to archive for order {order_id}")
                return
            
            source_path = Path(source_path)
            
            # Build destination folder: "Processesd By Cloney" under the OCR root folder
            # (not relative to where the source file is)
            try:
                ocr_root = ocr_folder()
            except Exception:
                ocr_root = Path(os.path.expanduser("~")) / "Documents" / "FaxManagerData" / "Faxes OCR'd"
            
            cloney_folder = ocr_root / "Processesd By Cloney"
            cloney_folder.mkdir(parents=True, exist_ok=True)
            
            # Build filename: LASTNAME, FIRSTNAME (DOB) DESCRIPTION RX (RX_DATE).pdf
            patient_last = (data.get("patient_last_name") or "").strip().upper()
            patient_first = (data.get("patient_first_name") or "").strip().upper()
            
            # Format DOB
            dob_str = data.get("patient_dob") or ""
            if dob_str:
                try:
                    dob_parsed = datetime.strptime(dob_str, "%Y-%m-%d")
                    dob_formatted = dob_parsed.strftime("%m-%d-%Y")
                except Exception:
                    dob_formatted = dob_str.replace("/", "-")
            else:
                dob_formatted = "UNKNOWN"
            
            # Format RX date
            rx_date_str = data.get("rx_date") or ""
            if rx_date_str:
                try:
                    rx_parsed = datetime.strptime(rx_date_str, "%Y-%m-%d")
                    rx_formatted = rx_parsed.strftime("%m-%d-%Y")
                except Exception:
                    rx_formatted = rx_date_str.replace("/", "-")
            else:
                rx_formatted = datetime.now().strftime("%m-%d-%Y")
            
            # Get item description(s)
            items = data.get("items") or []
            if items and isinstance(items, list) and len(items) > 0:
                first_item = items[0]
                if isinstance(first_item, dict):
                    description = first_item.get("description") or first_item.get("hcpcs") or "RX"
                else:
                    description = "RX"
            else:
                description = "RX"
            
            # Clean up description for filename (remove problematic chars)
            description = description.replace("/", "-").replace("\\", "-").replace(":", "-")
            description = description.replace("*", "").replace("?", "").replace('"', "")
            description = description.replace("<", "").replace(">", "").replace("|", "")
            # Truncate if too long
            if len(description) > 50:
                description = description[:50]
            
            # Build final filename
            new_filename = f"{patient_last}, {patient_first} ({dob_formatted}) {description} RX ({rx_formatted}){source_path.suffix}"
            dest_path = cloney_folder / new_filename
            
            # Handle duplicates
            counter = 1
            while dest_path.exists():
                new_filename = f"{patient_last}, {patient_first} ({dob_formatted}) {description} RX ({rx_formatted}) ({counter}){source_path.suffix}"
                dest_path = cloney_folder / new_filename
                counter += 1
            
            # Move the file
            shutil.move(str(source_path), str(dest_path))
            debug_log(f"[agent-watcher] Archived source PDF: {source_path.name} -> {dest_path.name}")
            
        except Exception as e:
            debug_log(f"[agent-watcher] Failed to archive source PDF: {e}")
            # Don't fail the order creation if archival fails

    # ----------------------------------------------------------------
    # Attachment handling
    # ----------------------------------------------------------------

    def _attach_source_pdf(self, order_id: int, data: dict, json_path: Path) -> None:
        """Copy source prescription PDF to the order's attachments directory."""
        from dmelogic.settings import load_settings
        
        try:
            # Determine the folder path for attachments
            folder_path = load_settings().get("db_folder") or str(Path.home() / "Documents" / "Dme_Solutions" / "Data")
            attachments_dir = Path(folder_path) / "attachments" / str(order_id)
            
            # First, try the source_file_path from the JSON
            source_path = data.get("source_file_path", "")
            if source_path and os.path.exists(source_path):
                self._copy_attachment(source_path, attachments_dir)
                debug_log(f"[agent-watcher] Attached source file: {source_path}")
                return
            
            # Second, check the attachments subfolder alongside the JSON
            json_attachment_dir = self.drop_folder / "attachments"
            if json_attachment_dir.is_dir():
                # Look for matching attachment by patient name from JSON filename
                json_stem = json_path.stem  # e.g., "GOMEZ_ALIZABETH_03252026"
                patient_prefix = json_stem.split("_")[0].upper() if "_" in json_stem else json_stem.upper()
                
                for f in json_attachment_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in (".pdf", ".tif", ".tiff"):
                        if patient_prefix in f.name.upper():
                            self._copy_attachment(str(f), attachments_dir)
                            debug_log(f"[agent-watcher] Attached from drop folder: {f.name}")
                            return
            
            debug_log(f"[agent-watcher] No source PDF found for order {order_id}")
            
        except Exception as e:
            debug_log(f"[agent-watcher] Error attaching source PDF: {e}")

    def _copy_attachment(self, source_path: str, attachments_dir: Path) -> None:
        """Copy a file to the attachments directory."""
        attachments_dir.mkdir(parents=True, exist_ok=True)
        dest = attachments_dir / os.path.basename(source_path)
        if not dest.exists():
            shutil.copy2(source_path, str(dest))
            debug_log(f"[agent-watcher] Copied attachment to {dest}")
