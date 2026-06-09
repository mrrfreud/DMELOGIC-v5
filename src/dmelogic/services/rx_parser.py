"""
Rx PDF Parser
=============
Extracts structured prescription data from pharmacy Rx PDFs.

Handles the common e-Rx format your pharmacy system generates:
  - Pres: Name (Title)
  - Patient: Last, First
  - DOB: MM/DD/YYYY
  - Drug: DRUG NAME
  - Qty: N
  - Refills: N
  - ICD10 CODE
  - NPI# XXXXXXXXXX
  - Rx date

Supports multiple prescriptions in a single PDF.

Usage:
    from dmelogic.services.rx_parser import RxParser, ParsedRx

    parser = RxParser()
    results: list[ParsedRx] = parser.parse_pdf("/path/to/file.pdf")
    # or parse raw text:
    results = parser.parse_text(raw_text)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

# Set up file-based debug logging for Rx parsing
_rx_log = logging.getLogger("rx_parser")
if not _rx_log.handlers:
    _rx_log.setLevel(logging.DEBUG)
    try:
        _log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "DMELogic", "Logs")
        os.makedirs(_log_dir, exist_ok=True)
        _fh = logging.FileHandler(os.path.join(_log_dir, "rx_parser_debug.log"), encoding="utf-8")
        _fh.setLevel(logging.DEBUG)
        _fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))
        _rx_log.addHandler(_fh)
    except Exception:
        pass
    # Also log to stderr so output is visible in the terminal
    _sh = logging.StreamHandler()
    _sh.setLevel(logging.DEBUG)
    _sh.setFormatter(logging.Formatter("%(name)s %(levelname)s: %(message)s"))
    _rx_log.addHandler(_sh)


@dataclass
class ParsedRxItem:
    """Single item/drug extracted from an Rx block."""
    drug_name: str = ""
    quantity: float = 0
    refills: int = 0
    days_supply: int = 0
    directions: str = ""
    unit: str = "Each"


@dataclass
class ParsedPrescriber:
    """Prescriber info extracted from Rx."""
    full_name: str = ""          # e.g. "Kalpana Pethe"
    title: str = ""              # e.g. "MD"
    first_name: str = ""
    last_name: str = ""
    npi: str = ""
    phone: str = ""
    fax: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    practice_name: str = ""
    license_number: str = ""
    dea_number: str = ""


@dataclass
class ParsedPatient:
    """Patient info extracted from Rx."""
    full_name: str = ""          # e.g. "Bah, Mariama"
    first_name: str = ""
    last_name: str = ""
    dob: str = ""                # MM/DD/YYYY
    gender: str = ""
    phone: str = ""
    fax: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""


@dataclass
class ParsedRx:
    """Complete parsed prescription — one per Rx block in the PDF."""
    patient: ParsedPatient = field(default_factory=ParsedPatient)
    prescriber: ParsedPrescriber = field(default_factory=ParsedPrescriber)
    item: ParsedRxItem = field(default_factory=ParsedRxItem)
    icd_codes: List[str] = field(default_factory=list)
    rx_date: str = ""
    rx_number: str = ""
    source_text: str = ""        # raw text of this block for debugging


class RxParser:
    """
    Parses prescription PDF text into structured ParsedRx objects.
    
    The parser is designed to handle the common e-Rx format from
    pharmacy systems (SureScripts, etc.). It splits multi-Rx PDFs
    into individual blocks and extracts each one.
    """

    # Regex patterns for data extraction
    _PAT_PRESCRIBER = re.compile(
        r"Pres:\s*(.+?)(?:\s*\((\w+)\))?$", re.MULTILINE
    )
    _PAT_NPI = re.compile(r"NPI#?\s*(\d{10})")
    _PAT_PATIENT = re.compile(
        r"Patient:[ \t]*(.+?)$", re.MULTILINE
    )
    _PAT_DOB = re.compile(r"DOB:[ \t]*(\d{1,2}/\d{1,2}/\d{4})")
    _PAT_GENDER = re.compile(r"Gender:[ \t]*([MF])")
    _PAT_ADDRESS = re.compile(
        r"Address:[ \t]*(.+?)$", re.MULTILINE
    )
    _PAT_DRUG = re.compile(r"Drug:[ \t]*(.+?)$", re.MULTILINE)
    _PAT_QTY = re.compile(r"Qty:[ \t]*([\d,.]+)")
    _PAT_REFILLS = re.compile(r"Refills:[ \t]*(\d+)")
    _PAT_DAYS = re.compile(r"Days:[ \t]*(\d+)")
    _PAT_SIG = re.compile(r"Sig:[ \t]*(.+?)$", re.MULTILINE)
    _PAT_ICD = re.compile(r"ICD-?10[:\s]*([A-Z]\d{2,3}\.?\d{0,4})", re.IGNORECASE)
    _PAT_DIAG = re.compile(r"Diag:\s*([A-Z]\d{2,4}\.?\d{0,4})", re.IGNORECASE)
    _PAT_RX_DATE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s*$", re.MULTILINE)
    _PAT_PHONE = re.compile(r"(?:Phone|Phn):\s*\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
    _PAT_FAX = re.compile(r"Fax:\s*\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
    _PAT_PRESCRIBER_PHONE = re.compile(r"\((\d{3})\)(\d{3})-(\d{4})")
    _PAT_EFF_DATE = re.compile(r"Eff\.Date:\s*(\d{1,2}/\d{1,2}/\d{4})")
    _PAT_RX_NUM = re.compile(r"Rx#:\s*(\S+)")

    # Block splitter — each "Pres:" starts a new Rx block
    _PAT_BLOCK_SPLIT = re.compile(r"(?=Pres:\s)")

    # Month name → number mapping for text-format DOBs
    _MONTH_MAP = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        "january": "01", "february": "02", "march": "03", "april": "04",
        "june": "06", "july": "07", "august": "08", "september": "09",
        "october": "10", "november": "11", "december": "12",
    }

    def _parse_dob_value(self, dob_val: str) -> str:
        """Parse a DOB value in various formats and return MM-DD-YYYY."""
        if not dob_val:
            return ""
        # Standard numeric: MM/DD/YYYY or M/D/YYYY (also 2-3 digit years from OCR)
        m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", dob_val)
        if m:
            mm, dd, yy = m.group(1), m.group(2), m.group(3)
            return f"{int(mm):02d}-{int(dd):02d}-{yy}"
        # Text format: "Dec 24, 2019" or "December 24, 2019"
        m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", dob_val)
        if m:
            month_str = m.group(1).lower()
            month_num = self._MONTH_MAP.get(month_str)
            if month_num:
                return f"{month_num}-{int(m.group(2)):02d}-{m.group(3)}"
        # ISO-ish: YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", dob_val)
        if m:
            return f"{m.group(2)}-{m.group(3)}-{m.group(1)}"
        return ""

    def parse_pdf(self, pdf_path: str) -> List[ParsedRx]:
        """
        Extract text from a PDF file and parse all Rx blocks.
        
        Uses PyMuPDF (fitz) first.  If the extracted text is short /
        sparse (typical of scanned forms where fitz only captures typed
        letterhead), automatically calls Azure Document Intelligence OCR
        to read handwritten content before parsing.
        """
        import logging
        _log = logging.getLogger("rx_parser.parse_pdf")

        native_text = self._extract_pdf_text(pdf_path)
        _log.info("Native extraction: %d chars from %s", len(native_text), pdf_path)

        if not native_text.strip():
            _log.info("No text extracted at all — returning empty")
            return []

        # Decide whether to try Azure OCR.
        # Scanned Rx forms with typed headers typically yield < 500 chars
        # of form-label text from fitz.  Real e-Rx PDFs yield 1000+ chars.
        text_to_parse = native_text
        sparse_text = len(native_text.strip()) < 600

        if sparse_text:
            _log.info("Sparse native text (%d chars) — likely scanned, trying Azure OCR first",
                       len(native_text.strip()))
            ocr_text = self._try_azure_ocr(pdf_path, _log)
            if ocr_text:
                text_to_parse = ocr_text

        # Parse whatever text we have (OCR or native)
        results = self.parse_text(text_to_parse)

        if results:
            # Quality check: if every result has no patient last_name,
            # the parse likely grabbed form labels.  Try OCR if we haven't.
            has_real_name = any(
                r.patient.last_name and len(r.patient.last_name) > 1
                and r.patient.last_name.lower() not in ("name", "name,", "patient")
                for r in results
            )
            if has_real_name:
                _log.info("Found %d good results", len(results))
                return results
            else:
                _log.info("Results look like form labels — trying Azure OCR")

        # Either no results or garbage results — try Azure OCR (if not already tried)
        if not sparse_text:
            ocr_text = self._try_azure_ocr(pdf_path, _log)
            if ocr_text:
                results = self.parse_text(ocr_text)
                if results:
                    has_real_name = any(
                        r.patient.last_name and len(r.patient.last_name) > 1
                        and r.patient.last_name.lower() not in ("name", "name,", "patient")
                        for r in results
                    )
                    if has_real_name:
                        _log.info("Azure OCR re-parse found %d good results", len(results))
                        return results

        _log.warning("All parse attempts failed for %s", pdf_path)
        return []

    def _try_azure_ocr(self, pdf_path: str, _log) -> Optional[str]:
        """Try Azure Document Intelligence OCR.  Returns OCR text or None."""
        try:
            from dmelogic.services.azure_ocr import get_azure_ocr, configure_azure_ocr
            azure = get_azure_ocr()

            # If the singleton is not configured, try loading creds from settings.json
            if not azure.is_configured:
                azure = self._configure_azure_from_settings()

            if not azure or not azure.is_configured:
                _log.info("Azure OCR not configured — skipping")
                return None

            _log.info("Calling Azure OCR on %s …", pdf_path)
            ocr_text = azure.extract_text_from_pdf(pdf_path)
            if ocr_text and ocr_text.strip():
                _log.info("Azure OCR returned %d chars:\n%s", len(ocr_text), ocr_text[:2000])
                return ocr_text
            else:
                _log.info("Azure OCR returned empty text")
                return None
        except Exception as e:
            _log.error("Azure OCR failed: %s", e, exc_info=True)
            return None

    @staticmethod
    def _configure_azure_from_settings():
        """Load Azure OCR creds from settings.json if the singleton is empty."""
        import json
        try:
            # Try the app's settings.json (CWD or known paths)
            settings_paths = [
                "settings.json",  # CWD (dev mode)
            ]
            import os
            # Also check standard installed location
            prog_data = os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"),
                                     "DMELogic", "settings.json")
            settings_paths.append(prog_data)
            dme_sol = os.path.join("C:\\Users\\pharmacy\\Documents\\DmeSolutionsV1", "settings.json")
            settings_paths.append(dme_sol)

            for sp in settings_paths:
                if os.path.exists(sp):
                    with open(sp, "r", encoding="utf-8") as f:
                        s = json.load(f)
                    ep = s.get("azure_di_endpoint", "")
                    ak = s.get("azure_di_key", "")
                    if ep and ak:
                        from dmelogic.services.azure_ocr import configure_azure_ocr
                        return configure_azure_ocr(ep, ak)
        except Exception:
            pass
        return None

    def parse_text(self, text: str) -> List[ParsedRx]:
        """
        Parse raw text containing one or more Rx blocks.
        Returns a list of ParsedRx objects.
        Supports both e-Rx (SureScripts) and pharmacy fax request formats.
        """
        import logging
        _log = logging.getLogger("rx_parser.parse_text")

        # Try e-Rx format first: split on "Pres:" boundaries
        blocks = self._PAT_BLOCK_SPLIT.split(text)
        blocks = [b.strip() for b in blocks if b.strip() and "Pres:" in b]

        results: List[ParsedRx] = []
        for block in blocks:
            parsed = self._parse_block(block)
            if parsed and (parsed.item.drug_name or parsed.patient.last_name):
                results.append(parsed)

        if results:
            _log.debug("e-Rx parser matched %d results", len(results))
            return results

        # Fallback: try pharmacy fax/request format
        results = self._parse_fax_format(text)
        if results:
            _log.debug("fax parser matched %d results", len(results))
            return results

        # Fallback: try "Rx:" label format (faxed prescriptions from providers)
        results = self._parse_rx_label_format(text)
        if results:
            _log.debug("Rx label parser matched %d results", len(results))
            return results

        # Fallback: try structured "Prescription" form format (e-Rx portals)
        results = self._parse_prescription_form_format(text)
        if results:
            _log.debug("prescription form parser matched %d results", len(results))
            return results

        # Fallback: try handwritten/DME written order format
        results = self._parse_written_order_format(text)
        if results:
            _log.debug("written order parser matched %d results", len(results))
            return results

        # Fallback: try generic doctor Rx pad (loose matcher)
        results = self._parse_generic_rx_form(text)
        if results:
            _log.debug("generic Rx form parser matched %d results", len(results))
            return results

        # Final fallback: AI-powered parser (OpenAI GPT)
        try:
            from dmelogic.services.ai_rx_parser import ai_parse_rx_text, is_configured
            if is_configured():
                _log.info("All regex parsers failed — trying AI parser…")
                results = ai_parse_rx_text(text)
                if results:
                    _log.info("AI parser found %d prescription(s)", len(results))
                    return results
                else:
                    _log.info("AI parser returned no results")
            else:
                _log.debug("AI parser not configured — skipping")
        except Exception as e:
            _log.warning("AI parser unavailable: %s", e)

        return []

    def _parse_block(self, block: str) -> Optional[ParsedRx]:
        """Parse a single Rx block into a ParsedRx."""
        rx = ParsedRx(source_text=block)
        lines = block.split("\n")
        lines_stripped = [l.strip() for l in lines]

        # Build a lookup: for any label line, the value may be on the
        # PREVIOUS line (PDF column extraction) or on the SAME line.
        def _value_before_or_after(label: str) -> str:
            """Find the value for a label that may be on the line before or after."""
            for i, line in enumerate(lines_stripped):
                if line.startswith(label):
                    inline = line[len(label):].strip().strip(":")
                    if inline:
                        return inline
                    # Value on previous line
                    if i > 0 and lines_stripped[i - 1]:
                        return lines_stripped[i - 1]
                    # Value on next line
                    if i + 1 < len(lines_stripped) and lines_stripped[i + 1]:
                        return lines_stripped[i + 1]
            return ""

        # ---- Prescriber ----
        pres_match = self._PAT_PRESCRIBER.search(block)
        if pres_match:
            full_name = pres_match.group(1).strip()
            title = pres_match.group(2) or ""

            # With sort=True, the Pres: line may include horizontal
            # neighbours like NPI value, "Notes", "NPI#", "Ord:" etc.
            # Truncate at the first 10-digit number or known token.
            trunc = re.split(
                r"\s{3,}|\b\d{10}\b|(?:Notes|NPI#|Ord:|LIC#|DEA#)",
                full_name, maxsplit=1,
            )
            full_name = trunc[0].strip()

            # Extract title from parens, e.g. "Christina Labarbera (PA)"
            title_m = re.search(r"\s*\((MD|PA|DO|NP|DPM|OD|DDS|PhD|RN|APRN|FNP)\)\s*$",
                                full_name, re.IGNORECASE)
            if title_m:
                title = title_m.group(1)
                full_name = full_name[:title_m.start()].strip()

            rx.prescriber.full_name = full_name
            rx.prescriber.title = title
            # Split name: "Kalpana Pethe" → first=Kalpana, last=Pethe
            parts = full_name.split()
            if len(parts) >= 2:
                rx.prescriber.first_name = parts[0]
                rx.prescriber.last_name = " ".join(parts[1:])
            elif parts:
                rx.prescriber.last_name = parts[0]

        # NPI — try inline regex first, then look for any standalone
        # 10-digit number on the Pres: line (sort=True often places it
        # before the "NPI#" label, not after).
        npi_match = self._PAT_NPI.search(block)
        if npi_match:
            rx.prescriber.npi = npi_match.group(1)
        else:
            # Look for 10-digit number on the Pres: line itself
            pres_line_match = re.search(r"^Pres:.*$", block, re.MULTILINE)
            if pres_line_match:
                npi_on_line = re.search(r"\b(\d{10})\b", pres_line_match.group(0))
                if npi_on_line:
                    rx.prescriber.npi = npi_on_line.group(1)
            # Fallback: value-before-label
            if not rx.prescriber.npi:
                npi_val = _value_before_or_after("NPI#")
                if npi_val:
                    npi_digits = re.search(r"\d{10}", npi_val)
                    if npi_digits:
                        rx.prescriber.npi = npi_digits.group(0)

        # Prescriber phone/fax — look only in the prescriber section
        # (between Pres: and Patient:) to avoid capturing patient phone.
        pres_idx = block.find("Pres:")
        pat_idx = block.find("Patient:")
        pres_section = block[pres_idx:pat_idx] if pres_idx >= 0 and pat_idx > pres_idx else block
        phones = self._PAT_PRESCRIBER_PHONE.findall(pres_section)
        if phones:
            rx.prescriber.phone = f"({phones[0][0]}){phones[0][1]}-{phones[0][2]}"
            if len(phones) > 1:
                rx.prescriber.fax = f"({phones[1][0]}){phones[1][1]}-{phones[1][2]}"

        # Prescriber address — lines between Pres: and Patient:
        if pres_idx >= 0 and pat_idx > pres_idx:
            addr_lines = block[pres_idx:pat_idx].strip().split("\n")
            # Skip first line (Pres: name) and filter out label noise
            addr_parts = []
            for line in addr_lines[1:]:
                line = line.strip()
                if not line:
                    continue
                # Remove inline label fragments from sorted extraction
                line = re.sub(
                    r"\s*(LIC#\s*\S*|DEA#\S*|Rx#:?\s*\S*|Phn:\s*\S*|"
                    r"NPI#?\s*\S*|SPI#\s*\S*|Notes|Rx Notes|ERx Notes|"
                    r"Fax:\s*\S*|Ord:\s*\S*)\s*", " ", line
                ).strip()
                # Skip lines that are entirely phone/fax or empty after cleanup
                if not line or re.match(r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$", line):
                    continue
                addr_parts.append(line)
            if addr_parts:
                full_addr = ", ".join(addr_parts)
                # Collapse multiple spaces / commas
                full_addr = re.sub(r"\s{2,}", " ", full_addr).strip()
                full_addr = re.sub(r",\s*,", ",", full_addr).strip(", ")
                rx.prescriber.address = full_addr
                # Extract city/state/zip
                csz = re.search(r"([A-Za-z\s]+),\s*([A-Z]{2})\s*(\d{5,9})", full_addr)
                if csz:
                    rx.prescriber.city = csz.group(1).strip()
                    rx.prescriber.state = csz.group(2)
                    rx.prescriber.zip_code = csz.group(3)

        # ---- Patient ----
        pat_match = self._PAT_PATIENT.search(block)
        if pat_match:
            name_raw = pat_match.group(1).strip()
            if not name_raw:
                # Value may be on the line before "Patient:"
                name_raw = _value_before_or_after("Patient:")
            rx.patient.full_name = name_raw
            if "," in name_raw:
                parts = [p.strip() for p in name_raw.split(",", 1)]
                rx.patient.last_name = parts[0]
                rx.patient.first_name = parts[1] if len(parts) > 1 else ""
            else:
                parts = name_raw.split()
                if len(parts) >= 2:
                    rx.patient.first_name = parts[0]
                    rx.patient.last_name = " ".join(parts[1:])
        else:
            # Try line-before-label fallback
            name_raw = _value_before_or_after("Patient:")
            if name_raw:
                rx.patient.full_name = name_raw
                if "," in name_raw:
                    parts = [p.strip() for p in name_raw.split(",", 1)]
                    rx.patient.last_name = parts[0]
                    rx.patient.first_name = parts[1] if len(parts) > 1 else ""

        dob_match = self._PAT_DOB.search(block)
        if dob_match:
            rx.patient.dob = dob_match.group(1)
        else:
            # DOB may be on line before "DOB:"
            dob_raw = _value_before_or_after("DOB:")
            dob_m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", dob_raw)
            if dob_m:
                rx.patient.dob = dob_m.group(1)

        gender_match = self._PAT_GENDER.search(block)
        if gender_match:
            rx.patient.gender = gender_match.group(1)
        else:
            # Gender may be on line before "Gender"
            g_raw = _value_before_or_after("Gender")
            gm = re.match(r"^([MF])$", g_raw.strip())
            if gm:
                rx.patient.gender = gm.group(1)

        # Patient address
        addr_match = self._PAT_ADDRESS.search(block)
        addr_raw = ""
        csz_line = ""
        if addr_match:
            addr_raw = addr_match.group(1).strip()
        if not addr_raw:
            # Value may be on line before "Address:" with city/state on next line
            street = ""
            csz_line = ""
            for i, line in enumerate(lines_stripped):
                if line.startswith("Address:"):
                    # Street on previous line
                    if i > 0 and lines_stripped[i - 1]:
                        street = lines_stripped[i - 1]
                    # City/state/zip on next line
                    if i + 1 < len(lines_stripped) and lines_stripped[i + 1]:
                        csz_line = lines_stripped[i + 1]
                    # Check if street actually looks like city/state/zip, swap
                    if street and re.search(r"[A-Z]{2}\s+\d{5}", street):
                        csz_line, street = street, csz_line
                    if street and csz_line:
                        addr_raw = street + ", " + csz_line
                    elif street:
                        addr_raw = street
                    elif csz_line:
                        addr_raw = csz_line
                    break
        if addr_raw:
            rx.patient.address = addr_raw
            # Try to extract city/state/zip from the csz_line directly
            csz = None
            if csz_line:
                csz = re.search(r"([A-Za-z][A-Za-z\s]+),\s*([A-Z]{2})\s*(\d{5,9})", csz_line)
            if not csz:
                # Fallback: try on full addr_raw but require city >= 2 chars
                csz = re.search(r"([A-Za-z]{2}[A-Za-z\s]*),\s*([A-Z]{2})\s*(\d{5,9})", addr_raw)
            if csz:
                rx.patient.city = csz.group(1).strip()
                rx.patient.state = csz.group(2)
                rx.patient.zip_code = csz.group(3)
                # Address is everything before city
                city_idx = addr_raw.find(csz.group(1))
                if city_idx > 0:
                    rx.patient.address = addr_raw[:city_idx].strip().rstrip(",")

        # Patient phone — look for phone near patient section
        # Look after "Patient:" block for phone patterns
        pat_section_start = block.find("Patient:")
        drug_start = block.find("Drug:")
        if pat_section_start >= 0:
            end = drug_start if drug_start > pat_section_start else len(block)
            pat_section = block[pat_section_start:end]
            phone_m = re.search(r"Phone:\s*\((\d{3})\)(\d{3})-(\d{4})", pat_section)
            if phone_m:
                rx.patient.phone = f"({phone_m.group(1)}){phone_m.group(2)}-{phone_m.group(3)}"

        # Pharmacy-section patient phone (bottom of block)
        bottom_phone = re.search(r"Phone:\s*\((\d{3})\)(\d{3})-(\d{4})\s*Fax:", block[max(0, len(block)-400):])
        if bottom_phone:
            rx.patient.phone = f"({bottom_phone.group(1)}){bottom_phone.group(2)}-{bottom_phone.group(3)}"

        # Value-before-label fallback for phone  
        if not rx.patient.phone:
            # Search bottom portion for "phone-number\nPhone" pattern
            phone_pattern = re.search(
                r"\((\d{3})\)(\d{3})-(\d{4})\s*\n\s*Phone\b",
                block[max(0, len(block) - 500):]
            )
            if phone_pattern:
                rx.patient.phone = f"({phone_pattern.group(1)}){phone_pattern.group(2)}-{phone_pattern.group(3)}"

        # ---- Drug/Item ----
        drug_match = self._PAT_DRUG.search(block)
        if drug_match:
            drug_raw = drug_match.group(1).strip()
            # Verify it's actually a drug name (not a phone number)
            if drug_raw and not re.match(r"^\(?\d{3}\)?[-.\s]?\d{3}", drug_raw):
                rx.item.drug_name = drug_raw
            else:
                # Value on previous line
                drug_raw = _value_before_or_after("Drug:")
                if drug_raw and not re.match(r"^\(?\d{3}\)?[-.\s]?\d{3}", drug_raw):
                    rx.item.drug_name = drug_raw
        else:
            drug_raw = _value_before_or_after("Drug:")
            if drug_raw and not re.match(r"^\(?\d{3}\)?[-.\s]?\d{3}", drug_raw):
                rx.item.drug_name = drug_raw

        qty_match = self._PAT_QTY.search(block)
        if qty_match:
            try:
                rx.item.quantity = float(qty_match.group(1).replace(",", ""))
            except ValueError:
                rx.item.quantity = 0
        else:
            # For Qty, try value on the line after the label first (most
            # reliable when PDF extraction splits label/value across lines),
            # then fall back to the line before.
            qty_val = 0
            for i, line in enumerate(lines_stripped):
                if line.startswith("Qty:"):
                    # Try after first — most reliable
                    if i + 1 < len(lines_stripped):
                        qm2 = re.search(r"([\d,.]+)", lines_stripped[i + 1])
                        if qm2:
                            try:
                                qty_val = float(qm2.group(1).replace(",", ""))
                            except ValueError:
                                pass
                    # Fall back to before only if after didn't find anything
                    if not qty_val and i > 0:
                        qm = re.search(r"([\d,.]+)", lines_stripped[i - 1])
                        if qm:
                            try:
                                qty_val = float(qm.group(1).replace(",", ""))
                            except ValueError:
                                pass
                    break
            if qty_val > 0:
                rx.item.quantity = qty_val

        refill_match = self._PAT_REFILLS.search(block)
        if refill_match:
            rx.item.refills = int(refill_match.group(1))
        else:
            # PDF text extraction often puts "Refills:" on its own line
            # with the value on the next (or previous) line.
            # Check AFTER first (more reliable), then BEFORE.
            # Limit to 1-2 digits to avoid capturing NPI/IDs.
            for i, line in enumerate(lines_stripped):
                if line.startswith("Refills:"):
                    # Inline value after colon on same line
                    rest = line[len("Refills:"):].strip()
                    if rest and re.match(r"^\d{1,2}$", rest):
                        rx.item.refills = int(rest)
                        break
                    # Try after first (value below label)
                    if i + 1 < len(lines_stripped):
                        rm = re.match(r"^(\d{1,2})$", lines_stripped[i + 1])
                        if rm:
                            rx.item.refills = int(rm.group(1))
                            break
                    # Try before (value above label)
                    if i > 0:
                        rm = re.match(r"^(\d{1,2})$", lines_stripped[i - 1])
                        if rm:
                            rx.item.refills = int(rm.group(1))
                    break

        days_match = self._PAT_DAYS.search(block)
        if days_match:
            try:
                rx.item.days_supply = int(days_match.group(1))
            except ValueError:
                pass
        else:
            # Value-before-or-after fallback for Days
            for i, line in enumerate(lines_stripped):
                if line.startswith("Days:"):
                    # Try after first
                    if i + 1 < len(lines_stripped):
                        dm = re.search(r"^(\d{1,4})$", lines_stripped[i + 1])
                        if dm:
                            try:
                                rx.item.days_supply = int(dm.group(1))
                            except ValueError:
                                pass
                            break
                    # Then try before
                    if i > 0:
                        dm = re.search(r"([\d,.]+)", lines_stripped[i - 1])
                        if dm:
                            try:
                                rx.item.days_supply = int(float(dm.group(1).replace(",", "")))
                            except ValueError:
                                pass
                    if not rx.item.days_supply and i + 1 < len(lines_stripped):
                        dm = re.search(r"([\d,.]+)", lines_stripped[i + 1])
                        if dm:
                            try:
                                rx.item.days_supply = int(float(dm.group(1).replace(",", "")))
                            except ValueError:
                                pass
                    break

        sig_match = self._PAT_SIG.search(block)
        if sig_match:
            rx.item.directions = sig_match.group(1).strip()
        if not rx.item.directions or rx.item.directions in ("Rx", "Sig"):
            # For Sig, prefer line AFTER label (directions are usually after)
            for i, line in enumerate(lines_stripped):
                if line.startswith("Sig:"):
                    # Check line after first (preferred for directions)
                    if i + 1 < len(lines_stripped) and lines_stripped[i + 1]:
                        candidate = lines_stripped[i + 1]
                        # Skip if it looks like boilerplate
                        if not candidate.startswith("This Prescription"):
                            rx.item.directions = candidate
                            break
                    # Fallback to line before
                    if i > 0 and lines_stripped[i - 1] and lines_stripped[i - 1] not in ("Rx", "Sig"):
                        rx.item.directions = lines_stripped[i - 1]
                    break

        # ---- ICD codes ----
        icd_codes = set()
        for m in self._PAT_ICD.finditer(block):
            code = m.group(1).upper()
            # Normalize: ensure dot format (N3941 → N39.41)
            if "." not in code and len(code) > 3:
                code = code[:3] + "." + code[3:]
            icd_codes.add(code)
        for m in self._PAT_DIAG.finditer(block):
            code = m.group(1).upper()
            if "." not in code and len(code) > 3:
                code = code[:3] + "." + code[3:]
            icd_codes.add(code)
        # Also extract DX: codes from Sig line or anywhere in block
        for m in re.finditer(r"DX:\s*([A-Z]\d{2,3}\.?\d{0,4}(?:\s*,\s*[A-Z]\d{2,3}\.?\d{0,4})*)", block, re.IGNORECASE):
            for code_str in m.group(1).split(","):
                code = code_str.strip().upper()
                if code and re.match(r"[A-Z]\d{2,3}\.?\d{0,4}$", code):
                    if "." not in code and len(code) > 3:
                        code = code[:3] + "." + code[3:]
                    icd_codes.add(code)
        # Also try value-before-label for Diag:
        if not icd_codes:
            diag_val = _value_before_or_after("Diag:")
            if diag_val:
                code = diag_val.strip().upper()
                if re.match(r"[A-Z]\d{2,4}\.?\d{0,4}$", code):
                    if "." not in code and len(code) > 3:
                        code = code[:3] + "." + code[3:]
                    icd_codes.add(code)
        rx.icd_codes = sorted(icd_codes)

        # ---- Rx date ----
        eff_match = self._PAT_EFF_DATE.search(block)
        if eff_match:
            rx.rx_date = eff_match.group(1)
        else:
            # Look for Signature Date line with date
            sig_date = re.search(r"Signature\s+Date\s*\n?\s*.*?\n?\s*(\d{1,2}/\d{1,2}/\d{4})", block)
            if sig_date:
                rx.rx_date = sig_date.group(1)
            else:
                # Last resort: find date patterns near "02/16/2026"
                dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", block)
                if dates:
                    rx.rx_date = dates[-1]  # Usually the Rx date is last

        # Rx number
        rxnum_match = self._PAT_RX_NUM.search(block)
        if rxnum_match:
            rx.rx_number = rxnum_match.group(1)

        # Practice name — look for it in bottom section
        practice = re.search(r"^([A-Z][A-Za-z\s]+(?:Practice|Clinic|Medical|Health|Associates).*?)$", block, re.MULTILINE)
        if practice:
            rx.prescriber.practice_name = practice.group(1).strip()

        return rx

    # ------------------------------------------------------------------ Fax format parser

    def _parse_fax_format(self, text: str) -> List[ParsedRx]:
        """
        Parse a pharmacy fax/request letter format.
        
        Format:
            TO:
            PRESCRIBER_NAME, TITLE
            ...
            Patient: LAST, FIRST
            DOB:
            MM/DD/YYYY
            Address: STREET
            CITY, STATE, ZIP
            Phone:
            PHONE_NUMBER
            ...
            REQUESTED ITEMS (Monthly Usage):
            • ITEM NAME
            Qty/Month: N
        """
        lines = text.split("\n")
        lines_stripped = [l.strip() for l in lines]

        rx_base = ParsedRx(source_text=text)

        # ---- Prescriber from "TO:" section ----
        for i, line in enumerate(lines_stripped):
            if line == "TO:" and i + 1 < len(lines_stripped):
                pres_line = lines_stripped[i + 1].strip()
                if pres_line:
                    rx_base.prescriber.full_name = pres_line
                    # Parse "KRATZ, NATHANIEL, MD" or "GABRIELA KREBS, NP"
                    # Try LAST, FIRST, TITLE
                    parts = [p.strip() for p in pres_line.split(",")]
                    if len(parts) >= 3:
                        rx_base.prescriber.last_name = parts[0]
                        rx_base.prescriber.first_name = parts[1]
                        rx_base.prescriber.title = parts[2]
                    elif len(parts) == 2:
                        # Could be "LAST, FIRST" or "FIRST LAST, TITLE"
                        maybe_title = parts[1].strip()
                        if maybe_title in ("MD", "DO", "NP", "PA", "DPM", "OD", "DDS", "PhD", "RN", "APRN", "PA-C"):
                            # "GABRIELA KREBS, NP" → first=GABRIELA, last=KREBS
                            name_parts = parts[0].split()
                            if len(name_parts) >= 2:
                                rx_base.prescriber.first_name = name_parts[0]
                                rx_base.prescriber.last_name = " ".join(name_parts[1:])
                            rx_base.prescriber.title = maybe_title
                        else:
                            rx_base.prescriber.last_name = parts[0]
                            rx_base.prescriber.first_name = parts[1]
                    else:
                        name_parts = pres_line.split()
                        if len(name_parts) >= 2:
                            rx_base.prescriber.first_name = name_parts[0]
                            rx_base.prescriber.last_name = " ".join(name_parts[1:])
                break

        # ---- Patient ----
        for i, line in enumerate(lines_stripped):
            if line.startswith("Patient:"):
                name_part = line[len("Patient:"):].strip()
                # Strip inline DOB if present: "LAST, FIRST DOB: ..."
                if "DOB:" in name_part:
                    name_part, dob_inline = name_part.split("DOB:", 1)
                    name_part = name_part.strip()
                    # Parse inline DOB
                    rx_base.patient.dob = self._parse_dob_value(dob_inline.strip())
                if name_part:
                    rx_base.patient.full_name = name_part
                    if "," in name_part:
                        parts = [p.strip() for p in name_part.split(",", 1)]
                        rx_base.patient.last_name = parts[0]
                        rx_base.patient.first_name = parts[1] if len(parts) > 1 else ""
                    else:
                        name_parts = name_part.split()
                        if len(name_parts) >= 2:
                            rx_base.patient.first_name = name_parts[0]
                            rx_base.patient.last_name = " ".join(name_parts[1:])
                break

        # DOB — may be on same line or next line
        if not rx_base.patient.dob:
            for i, line in enumerate(lines_stripped):
                if line.startswith("DOB:"):
                    dob_val = line[len("DOB:"):].strip()
                    if not dob_val and i + 1 < len(lines_stripped):
                        dob_val = lines_stripped[i + 1].strip()
                    rx_base.patient.dob = self._parse_dob_value(dob_val)
                    break

        # Gender — may be "Gender: M" or "Gender M" on same line or separate
        for i, line in enumerate(lines_stripped):
            gm = re.match(r"Gender[:\s]+([MF])", line)
            if gm:
                rx_base.patient.gender = gm.group(1)
                break

        # Address — value may be on same line or next lines
        for i, line in enumerate(lines_stripped):
            if line.startswith("Address:"):
                addr_val = line[len("Address:"):].strip()
                # Grab continuation lines (city/state/zip)
                addr_lines = [addr_val] if addr_val else []
                for j in range(i + 1, min(i + 4, len(lines_stripped))):
                    nxt = lines_stripped[j]
                    # Stop at next field label or empty context
                    if not nxt or nxt.startswith(("Phone", "DOB", "Patient", "The patient", "If you")):
                        break
                    addr_lines.append(nxt)
                full_addr = " ".join(addr_lines).strip()
                rx_base.patient.address = full_addr
                # Extract city/state/zip
                csz = re.search(r"([A-Za-z\s]+),\s*([A-Z]{2}),?\s*(\d{5,9})", full_addr)
                if csz:
                    rx_base.patient.city = csz.group(1).strip()
                    rx_base.patient.state = csz.group(2)
                    rx_base.patient.zip_code = csz.group(3)
                    city_idx = full_addr.find(csz.group(1))
                    if city_idx > 0:
                        rx_base.patient.address = full_addr[:city_idx].strip().rstrip(",")
                break

        # Phone — value may be on same line or next line
        for i, line in enumerate(lines_stripped):
            if line.startswith("Phone:") or line.startswith("Phone "):
                phone_val = re.sub(r"^Phone[:\s]*", "", line).strip()
                if not phone_val and i + 1 < len(lines_stripped):
                    phone_val = lines_stripped[i + 1].strip()
                # Clean up phone
                digits = re.sub(r"[^0-9]", "", phone_val)
                if len(digits) >= 10:
                    rx_base.patient.phone = f"({digits[:3]}){digits[3:6]}-{digits[6:10]}"
                elif digits:
                    rx_base.patient.phone = phone_val
                break

        # ---- ICD/Diagnosis codes ----
        icd_codes = set()
        # Common false positives to skip
        _ICD_SKIP = {"NY", "NP", "MD", "DO", "FL", "PA", "NJ", "CT", "VA", "OR", "IN", "OH", "OK"}
        for idx, line in enumerate(lines_stripped):
            # Check for "Current Diagnosis Codes on File:" header — grab codes from following lines
            if "diagnosis codes on file" in line.lower():
                for k in range(idx + 1, min(idx + 6, len(lines_stripped))):
                    nxt = lines_stripped[k]
                    if not nxt or nxt.startswith(("Thank", "DME", "****", "Please")):
                        break
                    for cm in re.finditer(r"\b([A-Z]\d{2,3}\.?\d{1,4})\b", nxt):
                        code = cm.group(1).upper()
                        if code not in _ICD_SKIP:
                            if "." not in code and len(code) > 3:
                                code = code[:3] + "." + code[3:]
                            icd_codes.add(code)
                continue
            # Look for standalone ICD codes like "R39.81" or after "ATTENTION:" or "Diag:"
            for m in re.finditer(r"\b([A-Z]\d{2,3}\.?\d{0,4})\b", line):
                code = m.group(1).upper()
                # Must look like a real ICD code (letter + 2-3 digits + optional decimal)
                if re.match(r"^[A-Z]\d{2,3}(\.\d{1,4})?$", code) and code not in _ICD_SKIP:
                    if "." not in code and len(code) > 3:
                        code = code[:3] + "." + code[3:]
                    icd_codes.add(code)
        rx_base.icd_codes = sorted(icd_codes)

        # ---- Rx date ----
        eff_match = re.search(r"Eff\.?\s*Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
        if eff_match:
            rx_base.rx_date = eff_match.group(1)
        else:
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", text)
            # Use the last date that isn't the DOB
            for d in reversed(dates):
                if d != rx_base.patient.dob:
                    rx_base.rx_date = d
                    break

        # ---- Items from bullet points ----
        # Look for "• ITEM NAME" followed by "Qty/Month: N" or "Qty: N (Refills: R)"
        items: List[ParsedRxItem] = []
        for i, line in enumerate(lines_stripped):
            if line.startswith("•") or line.startswith("·") or line.startswith("-  "):
                item_name = re.sub(r"^[•·\-]\s*", "", line).strip()
                if not item_name:
                    continue
                item = ParsedRxItem(drug_name=item_name)
                # Look ahead for Qty/Month or Qty: N (Refills: R)
                for j in range(i + 1, min(i + 3, len(lines_stripped))):
                    qty_m = re.match(r"Qty(?:/Month)?:\s*([\d,.]+)", lines_stripped[j])
                    if qty_m:
                        try:
                            item.quantity = float(qty_m.group(1).replace(",", ""))
                        except ValueError:
                            item.quantity = 0
                        # Extract refills from same line: (Refills: N)
                        refill_m = re.search(r"\(Refills:\s*(\d+)\)", lines_stripped[j])
                        if refill_m:
                            item.refills = int(refill_m.group(1))
                        elif "OUT OF REFILLS" in lines_stripped[j].upper():
                            item.refills = 0
                        break
                items.append(item)

        # Also try "Drug:" format as fallback within fax text
        if not items:
            for m in self._PAT_DRUG.finditer(text):
                item = ParsedRxItem(drug_name=m.group(1).strip())
                items.append(item)

        # Create one ParsedRx per item (all share same patient/prescriber)
        results: List[ParsedRx] = []
        if items:
            for item in items:
                rx = ParsedRx(
                    patient=rx_base.patient,
                    prescriber=rx_base.prescriber,
                    item=item,
                    icd_codes=rx_base.icd_codes,
                    rx_date=rx_base.rx_date,
                    source_text=text,
                )
                results.append(rx)
        elif rx_base.patient.last_name:
            # No items found but we have patient data — return what we have
            results.append(rx_base)

        return results

    # ------------------------------------------------------------------ Rx: label format parser

    def _parse_rx_label_format(self, text: str) -> List[ParsedRx]:
        """
        Parse faxed prescriptions that use "Rx:" item labels.

        Format (per page):
            MEDICAL PC
            Kathleen BELTRAN NP
            1030 Sheridan Avenue
            Bronx NY 10456-6100
            718-410-3400
            02/19/2026
            Augusto Arvelo
            DOB: 11/11/1965
            ...
            Health First - Medicaid ZQ63649A
            Rx: DISPOSABLE UNDERPADS QTY-150 Dispense: 150
            Diagnosis: Functional urinary incontinence - R39.81
            Refills: 5

        Each page may contain a separate Rx item. OCR text pages are
        delimited by "--- Page N ---" or "--- Page N (OCR) ---" markers.
        """
        # Detect this format by finding "Rx:" lines that aren't "Rx#:"
        rx_lines = [l for l in text.split("\n")
                    if re.match(r"^\s*Rx:\s", l)]
        if not rx_lines:
            return []

        # Split into per-page blocks using the OCR page markers
        page_blocks = re.split(r"---\s*Page\s+\d+.*?---", text)
        # If no page markers, treat whole text as one block
        if len(page_blocks) <= 1:
            page_blocks = [text]

        results: List[ParsedRx] = []

        for block in page_blocks:
            block = block.strip()
            if not block:
                continue

            # Only process blocks that have an "Rx:" line
            rx_match = re.search(r"^\s*Rx:\s+(.+)", block, re.MULTILINE)
            if not rx_match:
                continue

            lines = block.split("\n")
            lines_stripped = [l.strip() for l in lines]

            rx = ParsedRx(source_text=block)

            # ---- Item from "Rx:" line ----
            rx_line_text = rx_match.group(1).strip()
            # Extract QTY-N from item line (e.g. "DISPOSABLE UNDERPADS QTY-150")
            qty_inline = re.search(r"QTY[-\s]*(\d+)", rx_line_text, re.IGNORECASE)
            # Extract Dispense: N from same or the rest of the line
            dispense_m = re.search(r"Dispense:\s*(\d+)", rx_line_text, re.IGNORECASE)
            # Drug name = everything before QTY- or Dispense:
            drug_name = re.split(r"\s+QTY[-\s]|\s+Dispense:", rx_line_text, flags=re.IGNORECASE)[0].strip()

            item = ParsedRxItem(drug_name=drug_name)
            if qty_inline:
                try:
                    item.quantity = float(qty_inline.group(1))
                except ValueError:
                    pass
            if not item.quantity and dispense_m:
                try:
                    item.quantity = float(dispense_m.group(1))
                except ValueError:
                    pass
            # Also check for standalone "Dispense:" on a separate line
            if not item.quantity:
                for line in lines_stripped:
                    dm = re.match(r"Dispense:\s*(\d+)", line, re.IGNORECASE)
                    if dm:
                        try:
                            item.quantity = float(dm.group(1))
                        except ValueError:
                            pass
                        break

            # ---- Refills ----
            for line in lines_stripped:
                rm = re.match(r"Refills:\s*(\d+)", line)
                if rm:
                    item.refills = int(rm.group(1))
                    break

            rx.item = item

            # ---- Prescriber ----
            # Look for a name with title suffix (NP, MD, DO, PA, etc.)
            _TITLES = {"MD", "DO", "NP", "PA", "DPM", "OD", "DDS",
                       "PhD", "RN", "APRN", "PA-C", "FNP", "DNP", "FNP-BC"}
            for i, line in enumerate(lines_stripped):
                if not line:
                    continue
                # Skip fax header lines, dates, page markers
                if re.match(r"^(To:|From:|Date:|Time:|Page:|Fax |Name:|Company:|Subject:|This Fax)", line, re.IGNORECASE):
                    continue
                # Check if line ends with a known title
                words = line.split()
                if len(words) >= 2 and words[-1].rstrip(",.") in _TITLES:
                    title = words[-1].rstrip(",.")
                    name = " ".join(words[:-1]).strip().rstrip(",")
                    rx.prescriber.full_name = name
                    rx.prescriber.title = title
                    # Split first/last — if "Kathleen BELTRAN"
                    name_parts = name.split()
                    if len(name_parts) >= 2:
                        rx.prescriber.first_name = name_parts[0]
                        rx.prescriber.last_name = " ".join(name_parts[1:])

                    # Practice name = line before prescriber name
                    if i > 0 and lines_stripped[i - 1]:
                        prev = lines_stripped[i - 1]
                        if not re.match(r"^(To:|From:|Date:|Time:|Page:|Fax |---)", prev, re.IGNORECASE):
                            rx.prescriber.practice_name = prev

                    # Address = next lines after prescriber name
                    addr_parts = []
                    for j in range(i + 1, min(i + 3, len(lines_stripped))):
                        nxt = lines_stripped[j]
                        if not nxt:
                            break
                        # Stop at phone, date, or patient info
                        if re.match(r"^(\d{1,2}/\d{1,2}/\d{4}|DOB:|\d{3}[-.]\d{3}[-.]\d{4})", nxt):
                            # If it looks like a phone number, capture it
                            phone_m = re.match(r"(\d{3})[-.]?(\d{3})[-.]?(\d{4})", nxt)
                            if phone_m:
                                rx.prescriber.phone = f"({phone_m.group(1)}){phone_m.group(2)}-{phone_m.group(3)}"
                            break
                        addr_parts.append(nxt)
                    if addr_parts:
                        rx.prescriber.address = ", ".join(addr_parts)
                        # Parse city/state/zip from last addr part
                        csz = re.search(r"([A-Za-z]{2,}(?:\s+[A-Za-z]+)*)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", addr_parts[-1])
                        if csz:
                            rx.prescriber.city = csz.group(1).strip()
                            rx.prescriber.state = csz.group(2)
                            rx.prescriber.zip_code = csz.group(3)
                    # Capture phone on line after address
                    if not rx.prescriber.phone:
                        phone_idx = i + 1 + len(addr_parts)
                        if phone_idx < len(lines_stripped):
                            phone_m = re.match(r"(\d{3})[-.]?(\d{3})[-.]?(\d{4})", lines_stripped[phone_idx])
                            if phone_m:
                                rx.prescriber.phone = f"({phone_m.group(1)}){phone_m.group(2)}-{phone_m.group(3)}"
                    break

            # ---- Patient ----
            # Find DOB line, patient name is on the line before it
            for i, line in enumerate(lines_stripped):
                if line.startswith("DOB:"):
                    dob_val = line[len("DOB:"):].strip()
                    rx.patient.dob = self._parse_dob_value(dob_val)
                    # Patient name is the line before DOB
                    if i > 0 and lines_stripped[i - 1]:
                        name_line = lines_stripped[i - 1]
                        # Skip if it looks like a date
                        if not re.match(r"^\d{1,2}/\d{1,2}/\d{4}", name_line):
                            rx.patient.full_name = name_line
                            name_parts = name_line.split()
                            if "," in name_line:
                                parts = [p.strip() for p in name_line.split(",", 1)]
                                rx.patient.last_name = parts[0]
                                rx.patient.first_name = parts[1] if len(parts) > 1 else ""
                            elif len(name_parts) >= 2:
                                rx.patient.first_name = name_parts[0]
                                rx.patient.last_name = " ".join(name_parts[1:])
                    # Patient address = lines after DOB until phone/insurance/Rx
                    pat_addr_parts = []
                    for j in range(i + 1, min(i + 4, len(lines_stripped))):
                        nxt = lines_stripped[j]
                        if not nxt:
                            break
                        if re.match(r"^(Rx:|Diagnosis:|Health|Medicaid|Medicare|MEDICAID|Insurance|Refills:)", nxt, re.IGNORECASE):
                            break
                        # Phone number line
                        phone_m = re.match(r"(\d{3})[-.]?(\d{3})[-.]?(\d{4})", nxt)
                        if phone_m and not pat_addr_parts:
                            # Probably still address
                            pat_addr_parts.append(nxt)
                            continue
                        if phone_m:
                            rx.patient.phone = f"({phone_m.group(1)}){phone_m.group(2)}-{phone_m.group(3)}"
                            continue
                        pat_addr_parts.append(nxt)
                    if pat_addr_parts:
                        # First line is street address
                        rx.patient.address = pat_addr_parts[0]
                        # Look for city/state/zip
                        for ap in pat_addr_parts:
                            csz = re.search(r"([A-Za-z]{2,}(?:\s+[A-Za-z]+)*)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", ap)
                            if csz:
                                rx.patient.city = csz.group(1).strip()
                                rx.patient.state = csz.group(2)
                                rx.patient.zip_code = csz.group(3)
                    break

            # ---- Insurance ----
            # Look for lines mentioning Medicaid/Medicare/insurance carrier
            for line in lines_stripped:
                ins_m = re.match(
                    r"(Health\s+First|Fidelis|Healthfirst|Aetna|United|Cigna|Humana|Molina|Anthem|Empire|Oscar|MetroPlus)"
                    r"[\s\-]*(.+)", line, re.IGNORECASE
                )
                if ins_m:
                    # Store insurance info in source text (no dedicated field yet)
                    break

            # ---- Diagnosis / ICD codes ----
            _ICD_SKIP = {"NY", "NP", "MD", "DO", "FL", "PA", "NJ", "CT", "VA", "OR", "IN", "OH", "OK"}
            icd_codes = set()
            for line in lines_stripped:
                diag_m = re.match(r"Diagnosis:\s*(.+)", line, re.IGNORECASE)
                if diag_m:
                    diag_text = diag_m.group(1)
                    # Extract ICD code from "... - R39.81" or "... R39.81"
                    # Also handle OCR comma: R39,81 → R39.81
                    for cm in re.finditer(r"\b([A-Z]\d{2,3}[.,]?\d{0,4})\b", diag_text):
                        code = cm.group(1).upper()
                        if code not in _ICD_SKIP:
                            # Handle OCR comma→period: R39,81 → R39.81
                            code = code.replace(",", ".")
                            if "." not in code and len(code) > 3:
                                code = code[:3] + "." + code[3:]
                            icd_codes.add(code)
            # Also scan for standalone ICD-like codes
            for line in lines_stripped:
                for m in re.finditer(r"\b([A-Z]\d{2,3}\.\d{1,4})\b", line):
                    code = m.group(1).upper()
                    if code not in _ICD_SKIP:
                        icd_codes.add(code)
            rx.icd_codes = sorted(icd_codes)

            # ---- Rx date ----
            # Look for a standalone date line (not DOB)
            for line in lines_stripped:
                date_m = re.match(r"^(\d{1,2}/\d{1,2}/\d{4})\s*$", line)
                if date_m and date_m.group(1) != rx.patient.dob:
                    rx.rx_date = date_m.group(1)
                    break

            # ---- NPI (if present) ----
            npi_m = self._PAT_NPI.search(block)
            if npi_m:
                rx.prescriber.npi = npi_m.group(1)

            results.append(rx)

        return results

    # ------------------------------------------------------------------ Prescription form format parser

    def _parse_prescription_form_format(self, text: str) -> List[ParsedRx]:
        """
        Parse structured "Prescription" form format from e-Rx portals.

        Common format from sites like emr.saveinclinics.net:
            Prescription  Practice Name
            384 E. 149th St
            ...
            Written by :
            Rx Date  02/12/2026
            Jeffrey Hellinger, MD
            DEA#  NPI  State License
            FH5158630  1861443350
            Patient
            Luis Perez  Patient #
            1331708
            Gender  DOB  Insurance Plan  Fidelis  Phone
            M  08/29/1963  Medicaid HMO Plan  3474812643
            Address 1305
            SHERIDAN AVE  BRONX  NY  10456
            Prescription
            Compression Stockings 20-30 mmHg Knee High External Therapy Pack
            ...
            Diag 183.893
        """
        # Detect: "Prescription" header + "Written by" somewhere
        has_prescription = bool(re.search(r"^Prescription\b", text, re.MULTILINE | re.IGNORECASE))
        has_written_by = bool(re.search(r"Written\s+by", text, re.IGNORECASE))
        if not (has_prescription and has_written_by):
            return []

        lines = text.split("\n")
        lines_stripped = [l.strip() for l in lines]

        rx = ParsedRx(source_text=text)

        _TITLES = {"MD", "DO", "NP", "PA", "DPM", "OD", "DDS",
                    "PhD", "RN", "APRN", "PA-C", "FNP", "DNP", "FNP-BC"}

        # ---- Prescriber ----
        # Find "Written by" line, then look for a name with title (e.g. "Jeffrey Hellinger, MD")
        written_by_idx = None
        for i, line in enumerate(lines_stripped):
            if re.match(r"Written\s+by", line, re.IGNORECASE):
                written_by_idx = i
                break

        if written_by_idx is not None:
            for j in range(written_by_idx + 1, min(written_by_idx + 6, len(lines_stripped))):
                line = lines_stripped[j]
                if not line:
                    continue
                # Look for name with title: "Jeffrey Hellinger, MD" or "Jeffrey Hellinger MD"
                title_m = re.search(
                    r",?\s*(MD|DO|NP|PA|DPM|OD|DDS|PhD|RN|APRN|PA-C|FNP|DNP|FNP-BC)\s*$",
                    line, re.IGNORECASE
                )
                if title_m:
                    name = line[:title_m.start()].strip().rstrip(",")
                    rx.prescriber.full_name = name
                    rx.prescriber.title = title_m.group(1).upper()
                    parts = name.split()
                    if len(parts) >= 2:
                        rx.prescriber.first_name = parts[0]
                        rx.prescriber.last_name = " ".join(parts[1:])
                    elif parts:
                        rx.prescriber.last_name = parts[0]
                    break

        # ---- NPI ----
        # Look for 10-digit number near "NPI" label
        npi_m = re.search(r"NPI[#:\s]*(\d{10})", text)
        if npi_m:
            rx.prescriber.npi = npi_m.group(1)
        else:
            # NPI may be on the line AFTER the label line (e.g. "DEA# NPI ..." then "FH5158630 1861443350")
            for i, line in enumerate(lines_stripped):
                if re.search(r"\bNPI\b", line, re.IGNORECASE):
                    # Check same line for 10-digit number
                    npi_inline = re.search(r"\b(\d{10})\b", line)
                    if npi_inline:
                        rx.prescriber.npi = npi_inline.group(1)
                        break
                    # Check next line
                    if i + 1 < len(lines_stripped):
                        npi_next = re.search(r"\b(\d{10})\b", lines_stripped[i + 1])
                        if npi_next:
                            rx.prescriber.npi = npi_next.group(1)
                    break

        # ---- DEA ----
        dea_m = re.search(r"DEA[#:\s]*([A-Z]{2}\d{7})", text, re.IGNORECASE)
        if not dea_m:
            # DEA may be on line after label
            for i, line in enumerate(lines_stripped):
                if re.search(r"\bDEA\b", line, re.IGNORECASE):
                    if i + 1 < len(lines_stripped):
                        dea_next = re.search(r"\b([A-Z]{2}\d{7})\b", lines_stripped[i + 1])
                        if dea_next:
                            dea_m = dea_next
                    break

        # ---- Patient ----
        # Find "Patient" header line (standalone or "Patient\n")
        patient_idx = None
        for i, line in enumerate(lines_stripped):
            # Match standalone "Patient" header, not "Patient #" or "Patient Instructio"
            if re.match(r"^Patient\s*$", line, re.IGNORECASE):
                patient_idx = i
                break

        if patient_idx is not None:
            # Next non-empty line has the patient name (may have "Patient #" or "Patient &" appended by OCR)
            for j in range(patient_idx + 1, min(patient_idx + 3, len(lines_stripped))):
                line = lines_stripped[j]
                if not line:
                    continue
                # Strip trailing "Patient #" or "Patient &" from OCR noise
                name_part = re.sub(r"\s+Patient\s*[#&]?\s*$", "", line, flags=re.IGNORECASE).strip()
                if name_part:
                    rx.patient.full_name = name_part
                    if "," in name_part:
                        parts = [p.strip() for p in name_part.split(",", 1)]
                        rx.patient.last_name = parts[0]
                        rx.patient.first_name = parts[1] if len(parts) > 1 else ""
                    else:
                        name_parts = name_part.split()
                        if len(name_parts) >= 2:
                            rx.patient.first_name = name_parts[0]
                            rx.patient.last_name = " ".join(name_parts[1:])
                        elif name_parts:
                            rx.patient.last_name = name_parts[0]
                break

        # ---- DOB ----
        # Look for DOB in format MM/DD/YYYY on lines near Gender/DOB labels
        # OCR may produce "M 08/29/1963_|" or "DOB 08/29/1963" or "dob\n08/29/1963"
        for i, line in enumerate(lines_stripped):
            # Try "DOB" label on same line
            dob_m = re.search(r"(?:DOB|bos|dos)[:\s]+(\d{1,2}/\d{1,2}/\d{2,4})", line, re.IGNORECASE)
            if dob_m:
                rx.patient.dob = self._parse_dob_value(dob_m.group(1))
                break
            # Try "Gender ... DOB ... date" pattern (the DOB and date may be on same line as gender)
            if re.search(r"Gender|^[MF]\s+\d{1,2}/", line, re.IGNORECASE):
                dates = re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", line)
                if dates:
                    rx.patient.dob = self._parse_dob_value(dates[0])
                    break
        # Fallback: look for any date pattern near a gender marker
        if not rx.patient.dob:
            gender_dob_m = re.search(r"[MF]\s+(\d{1,2}/\d{1,2}/\d{4})", text)
            if gender_dob_m:
                rx.patient.dob = self._parse_dob_value(gender_dob_m.group(1))

        # ---- Gender ----
        gender_m = re.search(r"(?:Gender[:\s]*)?([MF])\s+\d{1,2}/\d{1,2}/\d{4}", text)
        if gender_m:
            rx.patient.gender = gender_m.group(1)

        # ---- Phone ----
        # Look for 10-digit phone near "Phone" label
        for line in lines_stripped:
            if re.search(r"Phone", line, re.IGNORECASE):
                phone_m = re.search(r"\b(\d{10})\b", line)
                if phone_m:
                    digits = phone_m.group(1)
                    rx.patient.phone = f"({digits[:3]}){digits[3:6]}-{digits[6:10]}"
                    break
                # Also try formatted phone
                phone_m2 = re.search(r"\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})", line)
                if phone_m2:
                    rx.patient.phone = f"({phone_m2.group(1)}){phone_m2.group(2)}-{phone_m2.group(3)}"
                    break

        # ---- Address ----
        for i, line in enumerate(lines_stripped):
            if re.match(r"Address\s+\d", line, re.IGNORECASE):
                # "Address 1305" — the number is part of the address
                addr_num = re.sub(r"^Address\s+", "", line, flags=re.IGNORECASE).strip()
                # Next line has street name + city + state + zip
                addr_parts = [addr_num]
                for j in range(i + 1, min(i + 3, len(lines_stripped))):
                    nxt = lines_stripped[j]
                    if not nxt or re.match(r"^(Prescription|Written|Patient|Gender)", nxt, re.IGNORECASE):
                        break
                    # Clean OCR artifacts like "_|"
                    nxt = re.sub(r"[_|]+", "", nxt).strip()
                    addr_parts.append(nxt)
                full_addr = " ".join(addr_parts)
                # Try to extract city/state/zip
                csz = re.search(r"([A-Z][A-Za-z\s]+?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", full_addr)
                if csz:
                    street = full_addr[:csz.start()].strip()
                    rx.patient.address = street if street else full_addr
                    rx.patient.city = csz.group(1).strip()
                    rx.patient.state = csz.group(2)
                    rx.patient.zip_code = csz.group(3)
                else:
                    rx.patient.address = full_addr
                break

        # ---- Rx Date ----
        rx_date_m = re.search(r"Rx\s+Date\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
        if rx_date_m:
            rx.rx_date = rx_date_m.group(1)
        else:
            # Fallback: first date that isn't DOB
            dates = re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
            for d in dates:
                if d != rx.patient.dob:
                    rx.rx_date = d
                    break

        # ---- Prescription / Item ----
        # Find the SECOND "Prescription" occurrence (first is the header/practice name)
        prescription_indices = []
        for i, line in enumerate(lines_stripped):
            if re.match(r"^Prescription\b", line, re.IGNORECASE):
                prescription_indices.append(i)

        items: List[ParsedRxItem] = []
        item_start_idx = prescription_indices[1] if len(prescription_indices) >= 2 else (prescription_indices[0] if prescription_indices else None)

        if item_start_idx is not None:
            # Lines after "Prescription" header until "Patient Instruction" or "Diag" or "Notes"
            for j in range(item_start_idx + 1, min(item_start_idx + 6, len(lines_stripped))):
                line = lines_stripped[j]
                if not line:
                    continue
                # Stop at known section breaks
                if re.match(r"^(Patient\s+Instruct|Diag|Notes|Written|Quantity|Unit|Rx#|Refill)", line, re.IGNORECASE):
                    break
                # This is an item line
                item = ParsedRxItem(drug_name=line.strip())
                # Look for Qty/Quantity on nearby lines
                for k in range(j + 1, min(j + 5, len(lines_stripped))):
                    qty_m = re.search(r"(?:Qty|Quantity)[:\s]*(\d+)", lines_stripped[k], re.IGNORECASE)
                    if qty_m:
                        try:
                            item.quantity = float(qty_m.group(1))
                        except ValueError:
                            pass
                        break
                items.append(item)
                # Typically one item per prescription form, but continue scanning
                break

        # ---- Rx# (prescription number) ----
        rx_num_m = re.search(r"Rx#\s*(\d+)", text)
        if rx_num_m:
            # Store as reference (no dedicated field; could put in notes)
            pass

        # ---- ICD / Diagnosis codes ----
        _ICD_SKIP = {"NY", "NP", "MD", "DO", "FL", "PA", "NJ", "CT", "VA",
                      "OR", "IN", "OH", "OK", "DE"}
        icd_codes = set()
        for line in lines_stripped:
            # "Diag 183.893" or "Diag: I83.893" or "Diagnosis: ..."
            diag_m = re.match(r"Diag(?:nosis)?[:\s]+(.+)", line, re.IGNORECASE)
            if diag_m:
                diag_text = diag_m.group(1)
                # OCR may produce "183.893" instead of "I83.893" — fix leading digit
                for cm in re.finditer(r"\b([A-Z1I]\d{2,3}[.,]?\d{0,4})\b", diag_text):
                    code = cm.group(1).upper().replace(",", ".")
                    # Fix OCR: leading "1" should be "I" for ICD codes starting with I
                    if code[0] == "1" and len(code) >= 3:
                        maybe_i = "I" + code[1:]
                        # I83.893 is a valid ICD pattern; 183.893 is not a standard prefix
                        if re.match(r"^I\d{2}\.\d{1,4}$", maybe_i):
                            code = maybe_i
                    if code not in _ICD_SKIP:
                        if "." not in code and len(code) > 3:
                            code = code[:3] + "." + code[3:]
                        # Ensure trailing period is removed
                        code = code.rstrip(".")
                        icd_codes.add(code)
            else:
                # Scan for standalone ICD-like codes
                for m in re.finditer(r"\b([A-Z]\d{2,3}\.\d{1,4})\b", line):
                    code = m.group(1).upper()
                    if code not in _ICD_SKIP:
                        icd_codes.add(code)
        rx.icd_codes = sorted(icd_codes)

        # ---- Refills ----
        refill_m = re.search(r"Refills?[:\s]*(\d+)", text, re.IGNORECASE)
        if refill_m and items:
            items[0].refills = int(refill_m.group(1))

        # ---- Build results ----
        results: List[ParsedRx] = []
        if items:
            for item in items:
                result = ParsedRx(
                    patient=rx.patient,
                    prescriber=rx.prescriber,
                    item=item,
                    icd_codes=rx.icd_codes,
                    rx_date=rx.rx_date,
                    source_text=text,
                )
                results.append(result)
        elif rx.patient.last_name:
            # No items found but we have patient data
            results.append(rx)

        return results

    # ------------------------------------------------------------------ Written Order format parser

    def _parse_written_order_format(self, text: str) -> List[ParsedRx]:
        """
        Parse handwritten DME written order forms (typically Azure OCR output).

        Format (from faxed handwritten order forms):
            Patient Name
            Jonathan Hernandez DOB 1/30/2018
            ...
            Diagno:
            R 39.81
            ...
            HCPCS / QTY
            Product Description - Brand / Model
            disposable pull ups extra large
            240
            180
            disposable underpads
            ...
            Refills 6
            ...
            Practitioner Name:
            Simonelle Sambataro MD
            Practitioner Address: 1225 Gerard Avenue Bronx. NY 10452
            1376564708  (NPI)
            Practitioner Phone
            (718) 960-2891
        """
        # Detect this format by looking for "Practitioner Name" or
        # "Patient Name" combined with "HCPCS" or "Equipment Ordered"
        has_practitioner = bool(re.search(r"Practitioner\s+Name", text, re.IGNORECASE))
        has_patient_name = bool(re.search(r"Patient\s+Name", text, re.IGNORECASE))
        has_hcpcs_header = bool(re.search(r"HCPCS|Equipment\s+Ordered|Product\s+Desc", text, re.IGNORECASE))
        has_dob = bool(re.search(r"\bDOB\b|Date\s+of\s+Birth", text, re.IGNORECASE))

        if not (has_practitioner or (has_patient_name and has_hcpcs_header) or (has_patient_name and has_dob)):
            return []

        lines = text.split("\n")
        lines_stripped = [l.strip() for l in lines]

        rx_base = ParsedRx(source_text=text)

        # ---- Patient Name & DOB ----
        for i, line in enumerate(lines_stripped):
            if re.match(r"Patient\s+Name", line, re.IGNORECASE):
                # Patient info is on the next non-empty line
                for j in range(i + 1, min(i + 4, len(lines_stripped))):
                    nxt = lines_stripped[j]
                    if not nxt:
                        continue
                    # "Jonathan Hernandez DOB 1/30/2018" or
                    # "Jonathan Hernandez DOB 1/30/201" (OCR truncation)
                    dob_m = re.search(r"DOB\s+(\d{1,2}/\d{1,2}/\d{2,4})", nxt, re.IGNORECASE)
                    if dob_m:
                        name_part = nxt[:dob_m.start()].strip()
                        dob_val = dob_m.group(1)
                        # Handle truncated year from OCR: "1/30/201" → keep raw
                        # _parse_dob_value handles 2-4 digit years
                        rx_base.patient.dob = self._parse_dob_value(dob_val)
                    else:
                        name_part = nxt.strip()

                    if name_part:
                        rx_base.patient.full_name = name_part
                        # "Jonathan Hernandez" → first=Jonathan, last=Hernandez
                        # "HERNANDEZ, JONATHAN" → last=HERNANDEZ, first=JONATHAN
                        if "," in name_part:
                            parts = [p.strip() for p in name_part.split(",", 1)]
                            rx_base.patient.last_name = parts[0]
                            rx_base.patient.first_name = parts[1] if len(parts) > 1 else ""
                        else:
                            name_parts = name_part.split()
                            if len(name_parts) >= 2:
                                rx_base.patient.first_name = name_parts[0]
                                rx_base.patient.last_name = " ".join(name_parts[1:])
                            elif name_parts:
                                rx_base.patient.last_name = name_parts[0]
                    break
                break

        # ---- DOB fallback (if not inline with patient name) ----
        if not rx_base.patient.dob:
            for line in lines_stripped:
                m = re.search(r"DOB[:\s]+(\d{1,2}/\d{1,2}/\d{2,4})", line, re.IGNORECASE)
                if m:
                    rx_base.patient.dob = self._parse_dob_value(m.group(1))
                    break

        # ---- Order Date ----
        # Look for "Date" field or "Date Signed"
        for i, line in enumerate(lines_stripped):
            dm = re.search(r"(?:Date\s+Signed|Order\s+Date|Jeder\s+Date)[:\s]*(\d{1,2}/\d{1,2}/\d{2,4})", line, re.IGNORECASE)
            if dm:
                rx_base.rx_date = dm.group(1)
                break
        # Fallback: date patterns not matching DOB
        if not rx_base.rx_date:
            dates = re.findall(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", text)
            for d in dates:
                if d != rx_base.patient.dob:
                    rx_base.rx_date = d
                    break

        # ---- Diagnosis / ICD codes ----
        _ICD_SKIP = {"NY", "NP", "MD", "DO", "FL", "PA", "NJ", "CT", "VA",
                      "OR", "IN", "OH", "OK", "DE"}
        icd_codes = set()
        for i, line in enumerate(lines_stripped):
            # "Diagno:" or "Diagnosis:" header — check following lines
            if re.match(r"Diagno(?:sis|s)?[:\s]*$", line, re.IGNORECASE):
                for j in range(i + 1, min(i + 4, len(lines_stripped))):
                    nxt = lines_stripped[j]
                    if not nxt:
                        continue
                    # Handle OCR spaces: "R 39.81" → "R39.81"
                    nxt_collapsed = re.sub(r"([A-Z])\s+(\d)", r"\1\2", nxt)
                    for cm in re.finditer(r"\b([A-Z]\d{2,3}[.,]?\d{0,4})\b", nxt_collapsed):
                        code = cm.group(1).upper().replace(",", ".")
                        if code not in _ICD_SKIP:
                            if "." not in code and len(code) > 3:
                                code = code[:3] + "." + code[3:]
                            icd_codes.add(code)
                    if icd_codes:
                        break
            # "Diagnosis: R39.81" or "Diagnosis: R 39.81" inline
            diag_line = re.match(r"Diagno(?:sis|s)?[:\s]+(.+)", line, re.IGNORECASE)
            if diag_line:
                diag_text = re.sub(r"([A-Z])\s+(\d)", r"\1\2", diag_line.group(1))
                for cm in re.finditer(r"\b([A-Z]\d{2,3}[.,]?\d{0,4})\b", diag_text):
                    code = cm.group(1).upper().replace(",", ".")
                    if code not in _ICD_SKIP:
                        if "." not in code and len(code) > 3:
                            code = code[:3] + "." + code[3:]
                        icd_codes.add(code)
        # Also scan for standalone ICD codes (with/without space)
        for line in lines_stripped:
            # Collapse letter-space-digit for ICD matching
            line_collapsed = re.sub(r"([A-Z])\s+(\d)", r"\1\2", line)
            for m in re.finditer(r"\b([A-Z]\d{2,3}\.\d{1,4})\b", line_collapsed):
                code = m.group(1).upper()
                if code not in _ICD_SKIP:
                    icd_codes.add(code)
        rx_base.icd_codes = sorted(icd_codes)

        # ---- Prescriber ----
        for i, line in enumerate(lines_stripped):
            if re.match(r"Practitioner\s+Name[:\s]*$", line, re.IGNORECASE):
                # Next non-empty line is the prescriber name
                for j in range(i + 1, min(i + 3, len(lines_stripped))):
                    nxt = lines_stripped[j]
                    if not nxt:
                        continue
                    rx_base.prescriber.full_name = nxt
                    # "Simonelle Sambataro MD"
                    _TITLES = {"MD", "DO", "NP", "PA", "DPM", "OD", "DDS",
                               "PhD", "RN", "APRN", "PA-C", "FNP", "DNP"}
                    words = nxt.split()
                    if len(words) >= 2 and words[-1].rstrip(",.") in _TITLES:
                        rx_base.prescriber.title = words[-1].rstrip(",.")
                        name_only = " ".join(words[:-1]).strip()
                        rx_base.prescriber.full_name = name_only
                        parts2 = name_only.split()
                        if len(parts2) >= 2:
                            rx_base.prescriber.first_name = parts2[0]
                            rx_base.prescriber.last_name = " ".join(parts2[1:])
                    else:
                        parts2 = nxt.split()
                        if len(parts2) >= 2:
                            rx_base.prescriber.first_name = parts2[0]
                            rx_base.prescriber.last_name = " ".join(parts2[1:])
                    break
                break

        # Prescriber Address
        for line in lines_stripped:
            addr_m = re.match(r"Practitioner\s+Address[:\s]+(.+)", line, re.IGNORECASE)
            if addr_m:
                addr_text = addr_m.group(1).strip()
                rx_base.prescriber.address = addr_text
                # Extract city/state/zip: "1225 Gerard Avenue Bronx. NY 10452"
                # Handle OCR period instead of comma: "Bronx. NY" or "Bronx, NY"
                csz = re.search(r"([A-Za-z]{2,}(?:\s+[A-Za-z]+)*)[.,]\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", addr_text)
                if csz:
                    rx_base.prescriber.city = csz.group(1).strip()
                    rx_base.prescriber.state = csz.group(2)
                    rx_base.prescriber.zip_code = csz.group(3)
                break

        # Prescriber NPI — look for a standalone 10-digit number
        for i, line in enumerate(lines_stripped):
            npi_m = re.match(r"^(\d{10})\s*$", line)
            if npi_m:
                rx_base.prescriber.npi = npi_m.group(1)
                break
        if not rx_base.prescriber.npi:
            npi_m2 = re.search(r"NPI[#:\s]*(\d{10})", text)
            if npi_m2:
                rx_base.prescriber.npi = npi_m2.group(1)

        # Prescriber Phone
        for i, line in enumerate(lines_stripped):
            if re.match(r"Practitioner\s+Phone", line, re.IGNORECASE):
                for j in range(i + 1, min(i + 3, len(lines_stripped))):
                    nxt = lines_stripped[j]
                    # Clean up OCR artifacts: (718) 960-289] → (718) 960-2891
                    nxt_clean = re.sub(r"[\[\]{}|]", "", nxt)
                    phone_m = re.search(r"\((\d{3})\)\s*(\d{3})-?(\d{4})", nxt_clean)
                    if phone_m:
                        rx_base.prescriber.phone = f"({phone_m.group(1)}){phone_m.group(2)}-{phone_m.group(3)}"
                        break
                    phone_m2 = re.search(r"(\d{3})[-.](\d{3})[-.](\d{4})", nxt_clean)
                    if phone_m2:
                        rx_base.prescriber.phone = f"({phone_m2.group(1)}){phone_m2.group(2)}-{phone_m2.group(3)}"
                        break
                break

        # ---- Refills ----
        global_refills = 0
        for line in lines_stripped:
            rm = re.match(r"Refills?\s*[:\s]*(\d+)", line, re.IGNORECASE)
            if rm:
                global_refills = int(rm.group(1))
                break

        # ---- Items from the equipment table ----
        # The table area starts after "HCPCS / QTY" or "Product Description"
        # and ends before "Refills" or "Ordering Prec" or "Practitioner"
        items: List[ParsedRxItem] = []

        # Find the start of the items section
        items_start = -1
        for i, line in enumerate(lines_stripped):
            if re.match(r"(HCPCS|Product\s+Desc|Equipment\s+Ordered)", line, re.IGNORECASE):
                items_start = i + 1
                # Skip one more line if it's a sub-header
                if items_start < len(lines_stripped):
                    nxt = lines_stripped[items_start]
                    if re.match(r"Product\s+Desc|Brand\s*/\s*Mod", nxt, re.IGNORECASE):
                        items_start += 1

        # Find the end of items section
        items_end = len(lines_stripped)
        if items_start > 0:
            for i in range(items_start, len(lines_stripped)):
                line = lines_stripped[i]
                if re.match(r"(Refill|Ordering\s+Prec|Practitioner|Provider|Date\s+Signed|Signature)", line, re.IGNORECASE):
                    items_end = i
                    break

        if items_start > 0 and items_start < items_end:
            # Parse items: lines alternate between description and quantity
            # Pattern: qty (number) on one line, description on adjacent line
            item_lines = lines_stripped[items_start:items_end]
            item_lines = [l for l in item_lines if l]  # Remove empties

            i = 0
            while i < len(item_lines):
                line = item_lines[i]

                # Check if this line is a quantity (pure number)
                qty_m = re.match(r"^(\d+)\s*$", line)
                if qty_m:
                    qty = float(qty_m.group(1))
                    # Description is on the NEXT line
                    if i + 1 < len(item_lines):
                        desc = item_lines[i + 1]
                        # Make sure desc is not another number
                        if not re.match(r"^\d+\s*$", desc):
                            items.append(ParsedRxItem(drug_name=desc, quantity=qty, refills=global_refills))
                            i += 2
                            continue
                    # Description might be on the PREVIOUS line
                    if i > 0 and items:
                        # Already parsed as description — update qty
                        pass
                    i += 1
                    continue

                # This line is a description — look for qty number on next line
                desc = line
                # Skip if it looks like a header echo
                if re.match(r"(HCPCS|Product|Brand|Description)", desc, re.IGNORECASE):
                    i += 1
                    continue

                qty = 0
                if i + 1 < len(item_lines):
                    nxt = item_lines[i + 1]
                    qty_m2 = re.match(r"^(\d+)\s*$", nxt)
                    if qty_m2:
                        qty = float(qty_m2.group(1))
                        i += 2
                    else:
                        i += 1
                else:
                    i += 1

                items.append(ParsedRxItem(drug_name=desc, quantity=qty, refills=global_refills))

        # ---- Build results: one ParsedRx per item ----
        results: List[ParsedRx] = []
        if items:
            for item in items:
                rx = ParsedRx(
                    patient=rx_base.patient,
                    prescriber=rx_base.prescriber,
                    item=item,
                    icd_codes=rx_base.icd_codes,
                    rx_date=rx_base.rx_date,
                    source_text=text,
                )
                results.append(rx)
        elif rx_base.patient.last_name or rx_base.prescriber.full_name:
            # No items but we have patient/prescriber data
            results.append(rx_base)

        return results

    # ------------------------------------------------------------------ generic Rx form parser

    def _parse_generic_rx_form(self, text: str) -> List[ParsedRx]:
        """
        Very flexible catch-all parser for doctor Rx pads, handwritten
        prescriptions, and other forms that don't match the structured
        parsers above.

        Extracts whatever it can find:
        - Patient name (from "Patient Name:", "Patient:", "Name:" labels
          or from adjacent name-like text near DOB)
        - DOB (many formats)
        - Prescriber name & NPI (from header or labels)
        - Diagnosis / ICD codes
        - Items (lines that look like medical equipment/supplies)
        """
        lines = text.split("\n")
        lines_stripped = [l.strip() for l in lines]

        rx = ParsedRx(source_text=text)

        # ---- Patient Name ----
        # Reject names that are clearly just form labels
        _LABEL_JUNK = {"name", "name,", "patient", "patient name", "patient name:",
                        "first", "last", "address", "phone", "dob", "date",
                        "n/a", "none", ",", ":", ""}

        # Try label patterns first
        for i, line in enumerate(lines_stripped):
            # "Patient Name: Maria Toribio" or "Patient Name\n Maria Toribio"
            pm = re.match(
                r"(?:Patient\s*(?:Name)?|Name\s*of\s*Patient)[:\s]*(.+)",
                line, re.IGNORECASE
            )
            if pm:
                name_text = pm.group(1).strip()
                # Reject form label junk
                if name_text.lower().rstrip(",:. ") in _LABEL_JUNK:
                    name_text = ""
                if name_text and len(name_text) > 1:
                    # Strip DOB if inline: "Maria Toribio DOB 04/05/1963"
                    dob_m = re.search(r"\b(?:DOB|D\.O\.B\.?|Date\s+of\s+Birth)[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", name_text, re.IGNORECASE)
                    if dob_m:
                        rx.patient.dob = self._parse_dob_value(dob_m.group(1))
                        name_text = name_text[:dob_m.start()].strip()
                    self._set_patient_name(rx, name_text)
                elif not name_text:
                    # Name on next line
                    for j in range(i + 1, min(i + 3, len(lines_stripped))):
                        nxt = lines_stripped[j]
                        if nxt and len(nxt) > 1 and not re.match(r"(DOB|Date|Address|Phone|Ins)", nxt, re.IGNORECASE):
                            dob_m = re.search(r"\b(?:DOB|D\.O\.B\.?)[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", nxt, re.IGNORECASE)
                            if dob_m:
                                rx.patient.dob = self._parse_dob_value(dob_m.group(1))
                                nxt = nxt[:dob_m.start()].strip()
                            self._set_patient_name(rx, nxt)
                            break
                break

        # ---- DOB (if not found inline) ----
        if not rx.patient.dob:
            for line in lines_stripped:
                dob_m = re.search(
                    r"(?:DOB|D\.O\.B\.?|Date\s+of\s+Birth)\s*[:\s]\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
                    line, re.IGNORECASE
                )
                if dob_m:
                    rx.patient.dob = self._parse_dob_value(dob_m.group(1))
                    break

        # ---- Prescriber / NPI ----
        # NPI from "NPI:" or "NPI#" or standalone 10-digit on a line
        npi_m = re.search(r"NPI[#:\s]*(\d{10})", text)
        if npi_m:
            rx.prescriber.npi = npi_m.group(1)

        # Prescriber name from Doctor/Prescriber/Physician/Provider labels
        for i, line in enumerate(lines_stripped):
            doc_m = re.match(
                r"(?:Prescriber|Physician|Provider|Doctor|Practitioner|Ordering\s+Physician)[:\s]+(.+)",
                line, re.IGNORECASE
            )
            if doc_m:
                name = doc_m.group(1).strip()
                if name:
                    self._set_prescriber_name(rx, name)
                    break

        # If no explicit prescriber label, try the first line that looks like
        # a doctor name (Name MD/DO at the very top of the form — letterhead)
        if not rx.prescriber.full_name:
            _TITLES_RE = r"\b(MD|DO|NP|PA|DPM|OD|DDS|PhD|APRN|PA-C|FNP|DNP)\b"
            for line in lines_stripped[:10]:
                if re.search(_TITLES_RE, line, re.IGNORECASE):
                    # Clean up: "SACHIN KUMAR AMRUTHLAL JAIN MD,PC" → "SACHIN KUMAR AMRUTHLAL JAIN"
                    clean = re.sub(r",?\s*(?:MD|DO|NP|PA|DPM|OD|DDS|PhD|APRN|PA-C|FNP|DNP)\b.*$", "", line, flags=re.IGNORECASE).strip()
                    if clean and len(clean) > 3:
                        self._set_prescriber_name(rx, clean)
                    break

        # ---- ICD / Diagnosis ----
        _ICD_SKIP = {"NY", "NP", "MD", "DO", "FL", "PA", "NJ", "CT", "VA",
                      "OR", "IN", "OH", "OK", "DE"}
        icd_codes = set()
        for line in lines_stripped:
            diag_m = re.match(r"(?:Diagno(?:sis|s)?|Dx|ICD)[:\s]*(.+)", line, re.IGNORECASE)
            if diag_m:
                diag_text = re.sub(r"([A-Z])\s+(\d)", r"\1\2", diag_m.group(1))
                for cm in re.finditer(r"\b([A-Z]\d{2,3}[.,]?\d{0,4})\b", diag_text):
                    code = cm.group(1).upper().replace(",", ".")
                    if code not in _ICD_SKIP:
                        if "." not in code and len(code) > 3:
                            code = code[:3] + "." + code[3:]
                        icd_codes.add(code)
            # Also look for standalone ICD-10 codes
            for cm in re.finditer(r"\b([A-Z]\d{2,3}\.\d{1,4})\b", line):
                code = cm.group(1).upper()
                if code not in _ICD_SKIP:
                    icd_codes.add(code)
        rx.icd_codes = sorted(icd_codes)

        # ---- Rx Date ----
        for line in lines_stripped:
            dm = re.search(
                r"(?:Rx\s*Date|Date\s+(?:Written|Signed|of\s+Order))[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
                line, re.IGNORECASE
            )
            if dm:
                rx.rx_date = dm.group(1)
                break
        if not rx.rx_date:
            # Grab first date that isn't the DOB
            dates = re.findall(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", text)
            for d in dates:
                if d != rx.patient.dob:
                    rx.rx_date = d
                    break

        # ---- Items ----
        # Look for DME/medical supply keywords anywhere in the text
        _DME_KEYWORDS = [
            r"compression\s*stockings?", r"jobst", r"sigvaris",
            r"knee\s*high", r"thigh\s*high", r"waist\s*high",
            r"wheelchair", r"walker", r"rollator", r"crutch",
            r"cpap", r"bipap", r"nebulizer", r"oxygen",
            r"hospital\s*bed", r"mattress", r"cushion",
            r"brace", r"splint", r"orthotic", r"prosthetic",
            r"diabetic\s*(?:shoes?|supplies?|test|strips?|lancets?)",
            r"gluco(?:meter|se\s*monitor)",
            r"catheter", r"ostomy", r"wound\s*care",
            r"enteral\s*(?:formula|nutrition|feeding)",
            r"suction", r"tens\s*unit",
            r"underpads?", r"pull[\s-]*ups?", r"diapers?|briefs?",
            r"commode", r"shower\s*chair", r"bath\s*bench",
            r"blood\s*pressure\s*monitor", r"pulse\s*oximeter",
            r"20[\s-]*30\s*mmhg|30[\s-]*40\s*mmhg|15[\s-]*20\s*mmhg",
            r"HCPCS|A\d{4}|E\d{4}|K\d{4}|L\d{4}|T\d{4}",
        ]
        _dme_re = re.compile("|".join(_DME_KEYWORDS), re.IGNORECASE)

        items: List[ParsedRxItem] = []
        for line in lines_stripped:
            if _dme_re.search(line):
                # Don't treat header/label lines as items
                if re.match(r"(HCPCS|Equipment|Product\s+Desc|Patient|Practitioner|Prescriber|Doctor|NPI|DOB|Date|Phone|Address|Fax|Signature|Refill)", line, re.IGNORECASE):
                    continue
                # Skip if it's just an HCPCS code with nothing else useful
                clean_line = line.strip()
                if clean_line and len(clean_line) > 2:
                    # Check for qty pattern: "ITEM x 2" or "ITEM QTY 2"
                    qty = 0
                    qty_m = re.search(r"(?:\bx\s*|\bqty[:\s]*|\bquantity[:\s]*)(\d+)", clean_line, re.IGNORECASE)
                    if qty_m:
                        qty = int(qty_m.group(1))
                    items.append(ParsedRxItem(drug_name=clean_line, quantity=qty))

        # ---- Refills ----
        for line in lines_stripped:
            rm = re.search(r"Refills?\s*[:\s]*(\d+)", line, re.IGNORECASE)
            if rm:
                refills = int(rm.group(1))
                for item in items:
                    item.refills = refills
                break

        # ---- Build results ----
        # We need a real patient name (not a form label) or an item
        real_name = (rx.patient.last_name
                     and len(rx.patient.last_name) > 1
                     and rx.patient.last_name.lower().rstrip(",:. ") not in _LABEL_JUNK)
        if not (real_name or items):
            return []

        results: List[ParsedRx] = []
        if items:
            for item in items:
                rx_item = ParsedRx(
                    patient=rx.patient,
                    prescriber=rx.prescriber,
                    item=item,
                    icd_codes=rx.icd_codes,
                    rx_date=rx.rx_date,
                    source_text=text,
                )
                results.append(rx_item)
        else:
            # Have patient info but no specific items — return as-is
            results.append(rx)

        return results

    def _set_patient_name(self, rx: ParsedRx, name_text: str):
        """Set patient name from a name string (handles 'Last, First' or 'First Last')."""
        name_text = name_text.strip()
        if not name_text:
            return
        rx.patient.full_name = name_text
        if "," in name_text:
            parts = [p.strip() for p in name_text.split(",", 1)]
            rx.patient.last_name = parts[0]
            rx.patient.first_name = parts[1] if len(parts) > 1 else ""
        else:
            words = name_text.split()
            if len(words) >= 2:
                rx.patient.first_name = words[0]
                rx.patient.last_name = " ".join(words[1:])
            elif words:
                rx.patient.last_name = words[0]

    def _set_prescriber_name(self, rx: ParsedRx, name_text: str):
        """Set prescriber name from a name string, stripping titles."""
        name_text = name_text.strip()
        if not name_text:
            return
        # Strip trailing titles: "JAIN MD,PC" → "JAIN"
        clean = re.sub(
            r"[,\s]*\b(?:MD|DO|NP|PA|DPM|OD|DDS|PhD|APRN|PA-C|FNP|DNP|PC|LLC|PLLC|INC)\b.*$",
            "", name_text, flags=re.IGNORECASE
        ).strip()
        if not clean:
            clean = name_text
        rx.prescriber.full_name = clean
        words = clean.split()
        if len(words) >= 2:
            rx.prescriber.first_name = words[0]
            rx.prescriber.last_name = " ".join(words[1:])
        elif words:
            rx.prescriber.last_name = words[0]

    # ------------------------------------------------------------------ PDF text extraction

    @staticmethod
    def _extract_pdf_text(pdf_path: str) -> str:
        """Extract text from PDF using available library.
        
        Falls back to OCR (pytesseract) for scanned/image-only PDFs.
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # Try PyMuPDF (fitz) first — fastest and most reliable
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(path))
            text = ""
            for page in doc:
                # sort=True gives proper reading order for tabular e-Rx forms
                text += page.get_text(sort=True)
            doc.close()
            if text.strip():
                return text
        except ImportError:
            pass

        # Try pdfplumber
        try:
            import pdfplumber
            text = ""
            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            if text.strip():
                return text
        except ImportError:
            pass

        # Try pdfminer
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(str(path))
            if text.strip():
                return text
        except ImportError:
            pass

        # Try PyPDF2 as last resort
        try:
            import PyPDF2
            with open(str(path), "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            if text.strip():
                return text
        except ImportError:
            pass

        # All native text extraction returned empty — try OCR fallbacks
        # This handles scanned/image-only PDFs (faxed prescriptions)

        # 1) Try Azure Document Intelligence first (best quality, handles handwriting)
        try:
            from dmelogic.services.azure_ocr import get_azure_ocr
            azure = get_azure_ocr()
            if azure.is_configured:
                text = azure.extract_text_from_pdf(str(path))
                if text.strip():
                    return text
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Azure OCR fallback failed: %s", e)

        # 2) Fall back to Tesseract OCR (local, no handwriting support)
        try:
            from dmelogic.config import configure_tesseract
            configure_tesseract()
            from dmelogic.ocr_tools import extract_text_from_pdf as ocr_extract
            text = ocr_extract(str(path))
            if text.strip():
                return text
        except Exception:
            pass

        raise ImportError(
            "No PDF library available. Install one of: "
            "PyMuPDF (pip install PyMuPDF), "
            "pdfplumber (pip install pdfplumber), "
            "pdfminer (pip install pdfminer.six), "
            "or PyPDF2 (pip install PyPDF2)"
        )


# ═══════════════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════════════

def parse_rx_file(pdf_path: str) -> List[ParsedRx]:
    """Parse an Rx PDF and return structured data."""
    return RxParser().parse_pdf(pdf_path)
