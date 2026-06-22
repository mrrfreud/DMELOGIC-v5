"""Split Brother batch-scanned delivery tickets into named PDFs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from dmelogic.db.base import resolve_db_path
from dmelogic.paths import delivery_ticket_split_folder, delivery_ticket_triage_folder


ORDER_RE = re.compile(
    r"\b(?:ORD|0RD|ORDER)\s*[-#:]?\s*(\d{1,6})(?:\s*[-/ ]\s*R\s*(\d{1,3}))?\b",
    re.IGNORECASE,
)

DATE_RE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})"
LABELED_DATE_RE = re.compile(
    rf"(?:date\s+of\s+service|service\s+date|dos|delivery\s+date|order\s+date)\s*[:#-]?\s*{DATE_RE}",
    re.IGNORECASE,
)


@dataclass
class DeliveryTicketTriageResult:
    triage_dir: Path
    output_dir: Path
    selected: int = 0
    pages: int = 0
    saved: int = 0
    attached: int = 0
    already_attached: int = 0
    no_order: int = 0
    ocr_fail: int = 0
    save_fail: int = 0
    archived: int = 0
    details: list[str] = field(default_factory=list)


def process_delivery_ticket_triage_batches(folder_path: str | None = None) -> DeliveryTicketTriageResult:
    """Process all PDF batch scans currently in the DT-TRIAGE folder."""
    triage_dir = delivery_ticket_triage_folder()
    output_dir = delivery_ticket_split_folder()
    triage_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = DeliveryTicketTriageResult(triage_dir=triage_dir, output_dir=output_dir)
    batch_pdfs = sorted(
        [p for p in triage_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"],
        key=lambda p: p.stat().st_mtime,
    )
    result.selected = len(batch_pdfs)
    if not batch_pdfs:
        result.details.append("No PDF batches found in triage folder.")
        return result

    orders_db_path = resolve_db_path("orders.db", folder_path=folder_path)
    conn = sqlite3.connect(orders_db_path)
    conn.row_factory = sqlite3.Row

    temp_root = Path(tempfile.mkdtemp(prefix="dme_dt_triage_"))
    try:
        for batch_path in batch_pdfs:
            batch_failed = False
            try:
                page_paths = _split_pdf_to_page_pdfs(batch_path, temp_root)
            except Exception as exc:
                result.save_fail += 1
                result.details.append(f"Could not split {batch_path.name}: {exc}")
                continue

            if not page_paths:
                result.save_fail += 1
                result.details.append(f"No pages found: {batch_path.name}")
                continue

            for page_index, page_path in enumerate(page_paths, start=1):
                result.pages += 1
                try:
                    text = _extract_page_text(page_path)
                except Exception as exc:
                    result.ocr_fail += 1
                    batch_failed = True
                    result.details.append(f"OCR failed {batch_path.name} page {page_index}: {exc}")
                    text = ""

                metadata = _metadata_from_text(conn, text, page_index)
                if not metadata["order_token"] or not metadata.get("order_id"):
                    result.no_order += 1
                    batch_failed = True

                filename = _build_ticket_filename(metadata, batch_path.stem, page_index)
                target = _unique_path(output_dir / filename)
                try:
                    shutil.copy2(str(page_path), str(target))
                    result.saved += 1
                    order_id = metadata.get("order_id")
                    if order_id:
                        attached = _attach_ticket_to_order_family(conn, int(order_id), target.name)
                        if attached:
                            result.attached += 1
                            conn.commit()
                            result.details.append(f"Saved and attached {target.name}")
                        else:
                            result.already_attached += 1
                            result.details.append(f"Saved; already attached {target.name}")
                    else:
                        result.details.append(f"Saved without order match {target.name}")
                except Exception as exc:
                    result.save_fail += 1
                    batch_failed = True
                    result.details.append(f"Save failed {batch_path.name} page {page_index}: {exc}")

            if not batch_failed:
                archived = _archive_batch(batch_path)
                if archived is not None:
                    result.archived += 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        conn.close()

    return result


def _split_pdf_to_page_pdfs(src: Path, temp_root: Path) -> list[Path]:
    import fitz

    doc = fitz.open(str(src))
    try:
        pages: list[Path] = []
        for index in range(len(doc)):
            out = temp_root / f"{src.stem}_p{index + 1}.pdf"
            one = fitz.open()
            try:
                one.insert_pdf(doc, from_page=index, to_page=index)
                one.save(str(out))
            finally:
                one.close()
            pages.append(out)
        return pages
    finally:
        doc.close()


def _extract_page_text(page_pdf: Path) -> str:
    from dmelogic.ocr_tools import extract_text_from_pdf

    return f"{page_pdf.name}\n{extract_text_from_pdf(str(page_pdf)) or ''}"


def _metadata_from_text(conn: sqlite3.Connection, text: str, page_index: int) -> dict[str, str]:
    order_token = ""
    root_id: int | None = None
    refill_number: int | None = None

    match = ORDER_RE.search(text or "")
    if match:
        root_id = int(match.group(1))
        refill_number = int(match.group(2)) if match.group(2) else None
        order_token = f"ORD-{root_id:03d}" + (f"-R{refill_number}" if refill_number is not None else "")

    row = _find_order_row(conn, root_id, refill_number) if root_id is not None else None
    last_name = ""
    first_name = ""
    service_date = _extract_labeled_date(text)

    if row is not None:
        last_name = str(row["patient_last_name"] or "").strip()
        first_name = str(row["patient_first_name"] or "").strip()
        service_date = service_date or _first_date_value(
            row["delivery_date"], row["order_date"], row["rx_date"]
        )

    if not last_name and not first_name:
        last_name, first_name = _extract_patient_name(text)

    return {
        "last_name": (last_name or "UNKNOWN").upper(),
        "first_name": (first_name or "UNKNOWN").upper(),
        "order_token": order_token,
        "order_id": str(row["id"]) if row is not None else "",
        "service_date": _format_date_for_filename(service_date) or "UNKNOWN DATE",
        "page_token": f"PAGE {page_index}",
    }


def _find_order_row(conn: sqlite3.Connection, root_id: int | None, refill_number: int | None):
    if root_id is None:
        return None

    if refill_number is not None:
        row = conn.execute(
            """
            SELECT id, parent_order_id, rx_date, order_date, delivery_date, patient_last_name, patient_first_name
            FROM orders
            WHERE parent_order_id = ? AND refill_number = ?
            ORDER BY id DESC LIMIT 1
            """,
            (root_id, refill_number),
        ).fetchone()
        if row is not None:
            return row

    return conn.execute(
        """
        SELECT id, parent_order_id, rx_date, order_date, delivery_date, patient_last_name, patient_first_name
        FROM orders
        WHERE id = ?
        """,
        (root_id,),
    ).fetchone()


def _attach_ticket_to_order_family(conn: sqlite3.Connection, order_id: int, filename: str) -> bool:
    row = conn.execute(
        "SELECT id, parent_order_id FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if row is None:
        return False

    root_family_id = int(row["parent_order_id"] or row["id"])
    family_rows = conn.execute(
        "SELECT id, attached_signed_ticket_files FROM orders WHERE id = ? OR parent_order_id = ? ORDER BY id",
        (root_family_id, root_family_id),
    ).fetchall()

    updated_any = False
    for family_row in family_rows:
        current_files = family_row["attached_signed_ticket_files"] or ""
        existing = [f.strip() for f in str(current_files).replace("\n", ";").split(";") if f.strip()]
        if filename in existing:
            continue
        updated = f"{current_files};{filename}" if current_files else filename
        conn.execute(
            "UPDATE orders SET attached_signed_ticket_files = ? WHERE id = ?",
            (updated, int(family_row["id"])),
        )
        updated_any = True

    return updated_any


def _extract_labeled_date(text: str) -> str:
    match = LABELED_DATE_RE.search(text or "")
    return match.group(1) if match else ""


def _first_date_value(*values) -> str:
    for value in values:
        raw = str(value or "").strip()
        if raw:
            return raw
    return ""


def _format_date_for_filename(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.split()[0]
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%m-%d-%Y")
        except ValueError:
            continue
    return _sanitize_filename_part(raw.replace("/", "-").replace("\\", "-"))


def _extract_patient_name(text: str) -> tuple[str, str]:
    compact = " ".join((text or "").split())
    patterns = (
        r"(?:Patient|Name)\s*[:#-]?\s*([A-Z][A-Z' .-]{1,40}),\s*([A-Z][A-Z' .-]{1,40})",
        r"([A-Z][A-Z' .-]{1,40}),\s*([A-Z][A-Z' .-]{1,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            last = _clean_name_part(match.group(1))
            first = _clean_name_part(match.group(2))
            if last and first:
                return last, first
    return "", ""


def _clean_name_part(value: str) -> str:
    value = re.sub(r"\b(?:DOB|PHONE|ADDRESS|ORDER|RX|DATE)\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[^A-Za-z' .-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip(" ,.-")


def _build_ticket_filename(metadata: dict[str, str], batch_stem: str, page_index: int) -> str:
    name_part = f"{metadata['last_name']}, {metadata['first_name']}".strip(", ")
    order_part = metadata["order_token"] or f"{_sanitize_filename_part(batch_stem)} PAGE {page_index}"
    date_part = metadata["service_date"]
    base = f"{name_part} - {order_part} - {date_part}"
    base = _sanitize_filename_part(base)
    return f"{base or f'DELIVERY TICKET PAGE {page_index}'} DT.pdf"


def _sanitize_filename_part(value: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*]+", " ", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip().strip(".")
    return value[:180]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix or ".pdf"
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _archive_batch(batch_path: Path) -> Path | None:
    processed_dir = batch_path.parent / "Processed"
    try:
        processed_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(processed_dir / batch_path.name)
        shutil.move(str(batch_path), str(target))
        return target
    except Exception:
        return None