"""
AI-Powered Rx Parser Fallback

Uses OpenAI GPT to extract structured prescription data from OCR text
when all regex-based parsers fail.  This is the last-resort fallback —
it handles ANY Rx format without needing custom parser code.

Usage:
    from dmelogic.services.ai_rx_parser import ai_parse_rx_text
    results = ai_parse_rx_text(ocr_text)   # returns list[ParsedRx]
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Module-level config ─────────────────────────────────────────────
_openai_api_key: str = ""
_openai_model: str = "gpt-4o-mini"   # fast + cheap default


def configure_openai(api_key: str, model: str = "gpt-4o-mini"):
    """Set the OpenAI API key and model at module level."""
    global _openai_api_key, _openai_model
    _openai_api_key = api_key
    _openai_model = model or "gpt-4o-mini"


def is_configured() -> bool:
    return bool(_openai_api_key)


# ── System prompt ────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are a medical document data-extraction assistant for a DME (Durable Medical Equipment) company.
You will receive raw OCR text from a scanned prescription or written order PDF.
Extract ALL of the following fields. If a field is not found, use null.
OCR text may contain errors — use your best judgment to correct obvious OCR mistakes
(e.g. "Luls" → "Luis", "1861443350" is an NPI, "183.893" may be ICD "I83.893").

Return ONLY valid JSON (no markdown, no explanation), matching this exact schema:

{
  "patient": {
    "first_name": "string or null",
    "last_name": "string or null",
    "dob": "MM/DD/YYYY or null",
    "gender": "M or F or null",
    "phone": "(999)999-9999 or null",
    "address": "street or null",
    "city": "string or null",
    "state": "XX or null",
    "zip": "string or null"
  },
  "prescriber": {
    "first_name": "string or null",
    "last_name": "string or null",
    "title": "MD/DO/NP/PA/etc or null",
    "npi": "10-digit string or null",
    "phone": "(999)999-9999 or null",
    "fax": "string or null",
    "practice_name": "string or null"
  },
  "items": [
    {
      "drug_name": "item/product description",
      "quantity": number_or_null,
      "refills": number_or_null,
      "directions": "string or null"
    }
  ],
  "icd_codes": ["X99.99", ...],
  "rx_date": "MM/DD/YYYY or null",
  "insurance_name": "string or null",
  "insurance_plan": "string or null",
  "rx_number": "string or null"
}
"""


def ai_parse_rx_text(ocr_text: str) -> list:
    """
    Send OCR text to OpenAI GPT and return list[ParsedRx].
    Returns empty list if not configured or on error.
    """
    if not _openai_api_key:
        logger.debug("AI Rx parser not configured (no API key)")
        return []

    if not ocr_text or not ocr_text.strip():
        return []

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed — AI parser unavailable")
        return []

    try:
        client = OpenAI(api_key=_openai_api_key)

        response = client.chat.completions.create(
            model=_openai_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract prescription data from this OCR text:\n\n{ocr_text[:4000]}"},
            ],
            temperature=0.0,
            max_tokens=1500,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```\s*$", "", raw)

        data = json.loads(raw)
        print(f"✅ AI parser extracted: {json.dumps(data, indent=2)[:500]}")

        return _convert_to_parsed_rx(data, ocr_text)

    except json.JSONDecodeError as e:
        logger.error("AI parser returned invalid JSON: %s", e)
        print(f"⚠️ AI parser JSON error: {e}")
        return []
    except Exception as e:
        logger.error("AI parser error: %s", e)
        print(f"⚠️ AI parser error: {e}")
        return []


def _convert_to_parsed_rx(data: dict, source_text: str) -> list:
    """Convert the AI JSON response into ParsedRx objects."""
    from dmelogic.services.rx_parser import ParsedRx, ParsedRxItem

    patient_data = data.get("patient") or {}
    prescriber_data = data.get("prescriber") or {}
    items_data = data.get("items") or []
    icd_codes = data.get("icd_codes") or []
    rx_date = data.get("rx_date")

    # Build base ParsedRx
    rx_base = ParsedRx(source_text=source_text)

    # Patient
    rx_base.patient.first_name = patient_data.get("first_name") or ""
    rx_base.patient.last_name = patient_data.get("last_name") or ""
    rx_base.patient.full_name = f"{rx_base.patient.first_name} {rx_base.patient.last_name}".strip()
    rx_base.patient.dob = patient_data.get("dob") or ""
    rx_base.patient.gender = patient_data.get("gender") or ""
    rx_base.patient.phone = patient_data.get("phone") or ""
    rx_base.patient.address = patient_data.get("address") or ""
    rx_base.patient.city = patient_data.get("city") or ""
    rx_base.patient.state = patient_data.get("state") or ""
    rx_base.patient.zip_code = patient_data.get("zip") or ""

    # Prescriber
    rx_base.prescriber.first_name = prescriber_data.get("first_name") or ""
    rx_base.prescriber.last_name = prescriber_data.get("last_name") or ""
    full = f"{rx_base.prescriber.first_name} {rx_base.prescriber.last_name}".strip()
    rx_base.prescriber.full_name = full
    rx_base.prescriber.title = prescriber_data.get("title") or ""
    rx_base.prescriber.npi = prescriber_data.get("npi") or ""
    rx_base.prescriber.phone = prescriber_data.get("phone") or ""
    rx_base.prescriber.practice_name = prescriber_data.get("practice_name") or ""

    # ICD codes
    rx_base.icd_codes = [str(c).strip() for c in icd_codes if c]

    # Rx date
    rx_base.rx_date = rx_date or ""

    # Items
    results = []
    if items_data:
        for item_d in items_data:
            drug = item_d.get("drug_name") or ""
            if not drug:
                continue
            item = ParsedRxItem(drug_name=drug)
            qty = item_d.get("quantity")
            if qty is not None:
                try:
                    item.quantity = float(qty)
                except (ValueError, TypeError):
                    pass
            refills = item_d.get("refills")
            if refills is not None:
                try:
                    item.refills = int(refills)
                except (ValueError, TypeError):
                    pass
            item.directions = item_d.get("directions") or ""

            rx = ParsedRx(
                patient=rx_base.patient,
                prescriber=rx_base.prescriber,
                item=item,
                icd_codes=rx_base.icd_codes,
                rx_date=rx_base.rx_date,
                source_text=source_text,
            )
            results.append(rx)
    elif rx_base.patient.last_name:
        results.append(rx_base)

    return results
