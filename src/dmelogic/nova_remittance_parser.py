"""
NY MMIS Title XIX Remittance Parser
=====================================
Parses NY Medicaid DME Remittance Statement PDFs and matches
claims to DMELogic orders using CIN + HCPCS + Date of Service.

Columns in the remittance:
  LN NO | PROC CODE | QUANTITY | CLIENT NUMBER | CLIENT NAME |
  OFFICE ACCT NUMBER | SERVICE DATE | TCN | AMOUNT CHARGED |
  AMOUNT PAID | STATUS | ERRORS
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger("nova_remittance")


# ── Data models ────────────────────────────────────────────────────────
@dataclass
class RemittanceLine:
    ln_no: str = ""
    proc_code: str = ""  # HCPCS
    quantity: float = 0.0
    cin: str = ""  # Client/Medicaid ID
    client_name: str = ""
    office_acct: str = ""  # Order/account number
    service_date: str = ""  # MM/DD/YY from remittance
    service_date_iso: str = ""  # YYYY-MM-DD normalized
    tcn: str = ""  # Transaction Control Number
    amount_charged: float = 0.0
    amount_paid: float = 0.0
    status: str = ""  # PAID, DENY, PEND, VOID
    errors: List[str] = field(default_factory=list)
    is_void: bool = False
    is_adjustment: bool = False


@dataclass
class RemittanceSummary:
    remittance_no: str = ""
    cycle: str = ""
    date: str = ""
    provider_id: str = ""
    total_paid: float = 0.0
    total_denied: float = 0.0
    total_pending: float = 0.0
    total_voids: float = 0.0
    net_paid: float = 0.0
    claims_paid: int = 0
    claims_denied: int = 0
    claims_pending: int = 0
    lines: List[RemittanceLine] = field(default_factory=list)


@dataclass
class MatchResult:
    line: Optional[RemittanceLine] = None
    matched: bool = False
    order_id: Optional[int] = None
    order_display: str = ""
    patient_name: str = ""
    match_key: str = ""
    action_needed: str = ""
    error_meanings: List[str] = field(default_factory=list)


# ── Error code dictionary ──────────────────────────────────────────────
MMIS_ERROR_CODES = {
    "00131": "TPL — Third party insurance must be billed first (patient has other coverage)",
    "00702": "Invalid or missing modifier — check HCPCS modifier on claim line",
    "00186": "Invalid quantity — quantity billed exceeds allowed amount",
    "00938": "Prior authorization required or PA number invalid",
    "00903": "Invalid procedure code combination",
    "02304": "Claim pending — additional information required",
    "00043": "Service not covered for recipient age",
    "00056": "Duplicate claim — already paid or in process",
    "00076": "Provider not authorized to bill this service",
    "00104": "Recipient not eligible on date of service",
    "00119": "Claim exceeds frequency limit",
    "00124": "Missing or invalid diagnosis code",
    "00140": "Service requires prior approval",
    "00178": "Invalid place of service",
    "00182": "Recipient has Medicare — bill Medicare first",
    "00199": "Missing required information on claim",
    "00201": "Fee schedule maximum exceeded",
    "00559": "PA exhausted or expired",
    "00574": "Quantity exceeds monthly maximum",
    "00592": "Recipient not enrolled in managed care plan",
    "00637": "CMN required — Certificate of Medical Necessity missing",
}


# ── Parser ─────────────────────────────────────────────────────────────
class RemittanceParser:
    def parse_pdf(self, pdf_path: str) -> RemittanceSummary:
        """Parse a NY MMIS Title XIX remittance PDF."""
        text_pages: List[str] = []
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_pages.append(text)
        except ImportError:
            try:
                from pypdf import PdfReader

                reader = PdfReader(pdf_path)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        text_pages.append(text)
            except ImportError as exc:
                raise ImportError("Install pdfplumber: pip install pdfplumber") from exc

        # If direct text extraction failed (image-only PDF), fall back to OCR.
        if not text_pages:
            try:
                from dmelogic.config import configure_tesseract
                from dmelogic.ocr_tools import extract_text_from_pdf

                configure_tesseract()
                ocr_full_text = extract_text_from_pdf(pdf_path)

                # ocr_tools adds page markers like: --- Page 1 (OCR) ---
                parts = re.split(r"\n---\s*Page\s+\d+(?:\s*\(OCR\))?\s*---\n", ocr_full_text)
                text_pages = [p.strip() for p in parts if p and p.strip()]
            except Exception as exc:
                raise RuntimeError(f"OCR fallback failed: {exc}") from exc

        if not text_pages:
            raise RuntimeError(
                "No readable text extracted from PDF. File appears image-only and OCR returned no text."
            )

        full_text = "\n".join(text_pages)
        return self._parse_text(full_text, text_pages)

    def _parse_text(self, full_text: str, pages: List[str]) -> RemittanceSummary:
        summary = RemittanceSummary()

        m = re.search(r"REMITTANCE NO[:\s]+(\S+)", full_text)
        if m:
            summary.remittance_no = m.group(1).strip()

        m = re.search(r"CYCLE[:\s]+(\d+)", full_text)
        if m:
            summary.cycle = m.group(1).strip()

        m = re.search(r"DATE[:\s]+(\d{2}/\d{2}/\d{4})", full_text)
        if m:
            summary.date = m.group(1).strip()

        m = re.search(r"PROV ID[:\s]+(\S+)", full_text)
        if m:
            summary.provider_id = m.group(1).strip()

        lines: List[RemittanceLine] = []
        for page_text in pages:
            lines.extend(self._parse_page_lines(page_text))

        summary.lines = lines
        self._extract_totals(full_text, summary)
        return summary

    def _normalize_date(self, date_str: str) -> str:
        """Convert MM/DD/YY or MM/DD/YYYY to YYYY-MM-DD."""
        if not date_str:
            return ""
        try:
            if len(date_str) == 8:
                dt = datetime.strptime(date_str, "%m/%d/%y")
                if dt.year < 2000:
                    dt = dt.replace(year=dt.year + 100)
                return dt.strftime("%Y-%m-%d")
            if len(date_str) == 10:
                return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            pass
        return date_str

    def _parse_page_lines(self, page_text: str) -> List[RemittanceLine]:
        """
        Parse individual claim lines from a page.

        Format:
        LN PROC    QTY    CIN         NAME           OFFICE  SVC_DATE  TCN                  CHARGED  PAID  STATUS  ERRORS
        01 A4554   300.000 MN53650J   PORTESVOLQUEZ  090R4   04/28/26  26118-003471445-3-0  87.00   0.00  DENY    00702
        """
        lines: List[RemittanceLine] = []
        current_line: Optional[RemittanceLine] = None

        for raw in page_text.split("\n"):
            raw = raw.strip()
            if not raw:
                continue

            raw = (
                raw.replace("\u2014", " ")
                .replace("\u00a9", " ")
                .replace("\u00ab", " ")
                .replace("\u00bb", " ")
            )
            raw = re.sub(r"\s+", " ", raw).strip()

            skip_patterns = [
                "LN",
                "PROC",
                "CLIENT",
                "OFFICE ACCT",
                "SERVICE",
                "AMOUNT",
                "STATUS",
                "ERRORS",
                "EDICAID",
                "MEDICAL ASSISTANCE",
                "REMITTANCE",
                "TITLE XIX",
                "MANAGEMENT",
                "INFORMATION",
                "TO:",
                "CENTRAL PHARMACY",
                "FORDHAM",
                "BRONX",
                "ETIN",
                "PROV ID",
                "CYCLE",
                "PAGE",
                "DATE:",
                "TOTAL AMOUNT",
                "NET AMOUNT",
                "CLAIM TYPE",
                "MEMBER ID",
                "* =",
                "** =",
                "PREVIOUSLY",
                "NEW PEND",
                "TPL CARR NAME",
            ]
            if any(raw.upper().startswith(p) for p in skip_patterns):
                if raw.upper().startswith("TPL CARR NAME") and current_line:
                    current_line.errors.append(f"TPL: {raw}")
                continue

            m = re.match(
                r"^(\d+[A-Z]?)\s+"                          # LN NO
                r"([A-Z0-9]{4,12}(?:-[A-Z0-9]+)?)\s+"        # PROC CODE (OCR may drop leading letter)
                r"([\d,.:]+-?)\s+"                           # QTY
                r"([A-Z0-9\u00a5]{6,14})\s+"                 # CIN (OCR may emit Yen sign)
                r"(.+?)\s+"                                   # CLIENT NAME
                r"([A-Z0-9]+)\s+"                             # OFFICE ACCT
                r"(\d{2}/\d{2}/\d{2})\s+"                  # SERVICE DATE
                r"(\d{5}-\d{9}-\d-\d)\s+"                 # TCN
                r"([\d,.:]+-?)\s+"                           # AMOUNT CHARGED
                r"([\d,.:]+-?)\s+"                           # AMOUNT PAID
                r"(?:\*\*\s*)?"                             # optional '**' marker before status
                r"(PAID|DENY|PEND|VOID)\s*"                  # STATUS
                r"(.*)$",                                      # ERRORS
                raw,
                re.IGNORECASE,
            )

            if m:
                if current_line:
                    lines.append(current_line)

                def _norm_num(s: str) -> str:
                    s = (s or "").replace(",", "").replace(":", ".").strip()
                    return s

                charged_str = _norm_num(m.group(9))
                paid_str = _norm_num(m.group(10))
                qty_str = _norm_num(m.group(3))
                is_void = "-" in qty_str or "-" in charged_str or "-" in paid_str
                svc_date = m.group(7)

                cin = m.group(4).replace("\u00a5", "").replace("©", "").strip().upper()

                current_line = RemittanceLine(
                    ln_no=m.group(1),
                    proc_code=m.group(2).upper(),
                    quantity=abs(float((qty_str.replace("-", "") or "0"))),
                    cin=cin,
                    client_name=m.group(5).strip(),
                    office_acct=m.group(6).strip(),
                    service_date=svc_date,
                    service_date_iso=self._normalize_date(svc_date),
                    tcn=m.group(8),
                    amount_charged=abs(float((charged_str.replace("-", "") or "0"))),
                    amount_paid=abs(float((paid_str.replace("-", "") or "0"))),
                    status=m.group(11).upper(),
                    errors=[e.strip() for e in m.group(12).split() if e.strip()],
                    is_void=is_void or m.group(11).upper() == "VOID",
                )
            else:
                if current_line and re.match(r"^\d{5}(\s+\d{5})*$", raw):
                    current_line.errors.extend(raw.split())

        if current_line:
            lines.append(current_line)

        return lines

    def _extract_totals(self, full_text: str, summary: RemittanceSummary):
        """Extract summary totals from the last page."""
        m = re.search(r"TOTAL AMOUNT ORIGINAL CLAIMS\s+PAID\s+([\d,]+\.\d{2})\s+NUMBER OF CLAIMS\s+(\d+)", full_text)
        if m:
            summary.total_paid = float(m.group(1).replace(",", ""))
            summary.claims_paid = int(m.group(2))

        m = re.search(r"TOTAL AMOUNT ORIGINAL CLAIMS\s+DENIED?\s+([\d,]+\.\d{2})\s+NUMBER OF CLAIMS\s+(\d+)", full_text)
        if m:
            summary.total_denied = float(m.group(1).replace(",", ""))
            summary.claims_denied = int(m.group(2))

        m = re.search(r"TOTAL AMOUNT ORIGINAL CLAIMS\s+PEND\s+([\d,]+\.\d{2})\s+NUMBER OF CLAIMS\s+(\d+)", full_text)
        if m:
            summary.total_pending = float(m.group(1).replace(",", ""))
            summary.claims_pending = int(m.group(2))

        m = re.search(r"NET TOTAL PAID\s+([\d,]+\.\d{2})", full_text)
        if m:
            summary.net_paid = float(m.group(1).replace(",", ""))

        m = re.search(r"NET AMOUNT VOIDS\s+(?:PAID|DENIED?)?\s+([\d,]+\.\d{2}-?)", full_text)
        if m:
            summary.total_voids = float(m.group(1).replace(",", "").replace("-", ""))


# ── DMELogic matcher ───────────────────────────────────────────────────
class RemittanceMatcher:
    """Matches parsed remittance lines to DMELogic orders."""

    def __init__(self, db_folder: str):
        self.db_folder = db_folder
        self.orders_db = os.path.join(db_folder, "orders.db")
        self.billing_db = os.path.join(db_folder, "billing.db")

    def match_all(self, summary: RemittanceSummary) -> List[MatchResult]:
        results = []
        for line in summary.lines:
            results.append(self.match_line(line))
        return results

    def match_line(self, line: RemittanceLine) -> MatchResult:
        """
        Match a remittance line to a DMELogic order using:
        CIN (primary_insurance_id) + HCPCS (proc_code) + service_date (order_date)
        """
        result = MatchResult(line=line)
        result.match_key = f"{line.cin} / {line.proc_code} / {line.service_date_iso}"
        result.error_meanings = [
            f"{code}: {MMIS_ERROR_CODES.get(code, 'Unknown error code')}"
            for code in line.errors
            if re.match(r"^\d{5}$", code)
        ]

        try:
            conn = sqlite3.connect(self.orders_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            svc_date_dme = ""
            if line.service_date_iso:
                try:
                    dt = datetime.strptime(line.service_date_iso, "%Y-%m-%d")
                    svc_date_dme = dt.strftime("%m/%d/%Y")
                except Exception:
                    pass

            cur.execute(
                """
                SELECT o.id, o.patient_last_name, o.patient_first_name,
                       o.order_date, o.order_status, o.primary_insurance_id,
                       o.refill_number, o.parent_order_id
                FROM orders o
                WHERE UPPER(REPLACE(o.primary_insurance_id, ' ', '')) = UPPER(REPLACE(?, ' ', ''))
                  AND (
                    o.order_date = ?
                    OR o.order_date = ?
                    OR o.delivery_date = ?
                  )
                ORDER BY o.id DESC
                LIMIT 5
                """,
                (line.cin, svc_date_dme, line.service_date_iso, svc_date_dme),
            )
            orders = cur.fetchall()

            if orders:
                for order in orders:
                    cur.execute(
                        """
                        SELECT oi.hcpcs_code, oi.description, oi.qty, oi.refills
                        FROM order_items oi
                        WHERE oi.order_id = ?
                          AND UPPER(REPLACE(oi.hcpcs_code, '-', ''))
                              LIKE UPPER(REPLACE(?, '-', '') || '%')
                        """,
                        (order["id"], line.proc_code.split("-")[0]),
                    )
                    item = cur.fetchone()
                    if item:
                        result.matched = True
                        result.order_id = order["id"]
                        refill_no = order["refill_number"] or 0
                        base_id = order["parent_order_id"] or order["id"]
                        result.order_display = f"ORD-{base_id}" + (f"-R{refill_no}" if refill_no else "")
                        result.patient_name = f"{order['patient_last_name']}, {order['patient_first_name']}"
                        break

            conn.close()
        except Exception as e:
            log.error(f"Match error for {line.cin}: {e}")

        if line.status == "DENY":
            if result.matched:
                result.action_needed = f"Fix and rebill - {'; '.join(result.error_meanings) or 'check error codes'}"
            else:
                result.action_needed = "Manual review - order not found in DMELogic"
        elif line.status == "PEND":
            result.action_needed = "Follow up - claim is pending additional info"
        elif line.is_void:
            result.action_needed = "Void processed - verify replacement claim submitted"
        else:
            result.action_needed = ""

        return result


# ── Main reconciliation function ───────────────────────────────────────
def process_remittance(pdf_path: str, db_folder: str) -> Dict[str, Any]:
    """
    Full remittance processing pipeline:
    1. Parse PDF
    2. Match to DMELogic orders
    3. Return structured results for Nova
    """
    parser = RemittanceParser()
    summary = parser.parse_pdf(pdf_path)
    matcher = RemittanceMatcher(db_folder)
    matches = matcher.match_all(summary)

    paid = [m for m in matches if m.line and m.line.status == "PAID" and not m.line.is_void]
    denied = [m for m in matches if m.line and m.line.status == "DENY"]
    pending = [m for m in matches if m.line and m.line.status == "PEND"]
    voids = [m for m in matches if m.line and m.line.is_void]
    unmatched = [m for m in matches if not m.matched]

    return {
        "remittance_no": summary.remittance_no,
        "cycle": summary.cycle,
        "date": summary.date,
        "provider_id": summary.provider_id,
        "totals": {
            "paid": summary.total_paid,
            "denied": summary.total_denied,
            "pending": summary.total_pending,
            "voids": summary.total_voids,
            "net_paid": summary.net_paid,
            "claims_paid": summary.claims_paid,
            "claims_denied": summary.claims_denied,
            "claims_pending": summary.claims_pending,
        },
        "denied_lines": [
            {
                "patient": m.line.client_name,
                "cin": m.line.cin,
                "hcpcs": m.line.proc_code,
                "service_date": m.line.service_date,
                "service_date_iso": m.line.service_date_iso,
                "amount": m.line.amount_charged,
                "tcn": m.line.tcn,
                "errors": m.line.errors,
                "error_meanings": m.error_meanings,
                "matched": m.matched,
                "order_id": m.order_id,
                "order_display": m.order_display,
                "action": m.action_needed,
            }
            for m in denied
        ],
        "pending_lines": [
            {
                "patient": m.line.client_name,
                "cin": m.line.cin,
                "hcpcs": m.line.proc_code,
                "service_date": m.line.service_date,
                "service_date_iso": m.line.service_date_iso,
                "amount": m.line.amount_charged,
                "tcn": m.line.tcn,
                "errors": m.line.errors,
                "matched": m.matched,
                "order_id": m.order_id,
                "order_display": m.order_display,
                "action": m.action_needed,
            }
            for m in pending
        ],
        "paid_count": len(paid),
        "void_count": len(voids),
        "unmatched_count": len(unmatched),
        "unmatched": [
            {
                "patient": m.line.client_name,
                "cin": m.line.cin,
                "hcpcs": m.line.proc_code,
                "service_date": m.line.service_date,
                "status": m.line.status,
            }
            for m in unmatched
            if m.line
        ],
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("Usage: python nova_remittance_parser.py <pdf_path> <db_folder>")
        sys.exit(1)
    result = process_remittance(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2, default=str))