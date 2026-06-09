"""
Rx Matcher Service
==================
Orchestrates matching parsed Rx data against your databases.
Connects the parser output to patient, prescriber, and inventory lookups.

Usage:
    from dmelogic.services.rx_matcher import RxMatcher, MatchResult

    matcher = RxMatcher(folder_path=...)
    
    # Match a parsed Rx against DBs
    result = matcher.match_patient(parsed_rx.patient)
    # → MatchResult(found=True, record={...}, confidence=0.95)
    
    result = matcher.match_prescriber(parsed_rx.prescriber)
    result = matcher.match_item(parsed_rx.item)
"""

from __future__ import annotations

import sqlite3
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from dmelogic.db.base import get_connection
from dmelogic.db.patients import find_patient_by_name_and_dob, search_patients
from dmelogic.db.prescribers import fetch_prescriber_by_npi, search_prescribers
from dmelogic.db.drug_mappings import DrugMapper, MappingResult
from dmelogic.services.rx_parser import ParsedPatient, ParsedPrescriber, ParsedRxItem


@dataclass
class MatchResult:
    """Result of a DB lookup attempt."""
    found: bool = False
    confidence: float = 0.0      # 0.0 to 1.0
    record: Optional[Dict[str, Any]] = None   # DB row as dict
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    match_type: str = ""         # "exact", "fuzzy", "npi", "none"
    message: str = ""            # Human-readable status


@dataclass
class ItemMatchResult:
    """Result of matching a drug name to inventory HCPCS."""
    found: bool = False
    confidence: float = 0.0
    hcpcs: str = ""
    description: str = ""
    drug_name: str = ""
    candidates: List[MappingResult] = field(default_factory=list)
    match_type: str = ""         # "learned", "fuzzy", "inventory", "none"
    message: str = ""
    retail_price: float = 0.0    # Per-unit price from inventory
    item_number: str = ""
    # Fee schedule fields
    fee: float = 0.0             # Medicaid fee schedule price
    rental_fee: float = 0.0      # Rental fee if applicable
    max_units: int = 0           # Medicaid max units
    pa_required: str = ""        # Prior auth code


class RxMatcher:
    """
    Matches parsed Rx data against existing database records.
    
    For patients: searches by name + DOB (exact), then fuzzy name.
    For prescribers: searches by NPI (exact), then name.
    For items: uses DrugMapper (learned → fuzzy → inventory).
    """

    def __init__(self, folder_path: Optional[str] = None):
        self.folder_path = folder_path
        self.drug_mapper = DrugMapper(folder_path=folder_path)

    # ------------------------------------------------------------------ Patient

    def match_patient(self, patient: ParsedPatient) -> MatchResult:
        """
        Search for a matching patient in patients.db.
        
        Strategy:
        1. Exact match: last_name + first_name + DOB
        2. Name-only match: last_name + first_name (no DOB)
        3. Fuzzy search: partial name
        """
        result = MatchResult()

        if not patient.last_name:
            result.message = "No patient name found in Rx"
            return result

        # 1) Exact match with DOB
        if patient.dob:
            # Normalize DOB to match DB format — try multiple formats
            dob_variants = self._dob_variants(patient.dob)
            for dob_fmt in dob_variants:
                row = find_patient_by_name_and_dob(
                    patient.last_name, patient.first_name,
                    dob=dob_fmt, folder_path=self.folder_path
                )
                if row:
                    result.found = True
                    result.confidence = 0.98
                    result.record = dict(row)
                    result.match_type = "exact"
                    result.message = f"✅ Exact match: {patient.last_name}, {patient.first_name} (DOB: {patient.dob})"
                    return result

        # 2) Name-only match (no DOB)
        row = find_patient_by_name_and_dob(
            patient.last_name, patient.first_name,
            dob=None, folder_path=self.folder_path
        )
        if row:
            result.found = True
            result.confidence = 0.85
            result.record = dict(row)
            result.match_type = "exact"
            result.message = f"✅ Name match: {patient.last_name}, {patient.first_name} (verify DOB)"
            return result

        # 3) Fuzzy search
        candidates = search_patients(patient.last_name, folder_path=self.folder_path)
        if not candidates and patient.first_name:
            candidates = search_patients(patient.first_name, folder_path=self.folder_path)

        if candidates:
            # Score each candidate
            scored = []
            for c in candidates:
                score = self._patient_score(patient, c)
                scored.append((score, c))
            scored.sort(key=lambda x: -x[0])

            best_score, best = scored[0]
            if best_score >= 0.7:
                result.found = True
                result.confidence = best_score
                result.record = best
                result.match_type = "fuzzy"
                result.message = f"🔍 Likely match: {best.get('last_name', '')}, {best.get('first_name', '')} ({best_score:.0%} confidence)"
            
            result.candidates = [c for _, c in scored[:5]]
        else:
            result.message = f"❌ No match found for: {patient.last_name}, {patient.first_name}"

        return result

    # ------------------------------------------------------------------ Prescriber

    def match_prescriber(self, prescriber: ParsedPrescriber) -> MatchResult:
        """
        Search for a matching prescriber in prescribers.db.
        
        Strategy:
        1. NPI match (most reliable)
        2. Name match
        3. Fuzzy search
        """
        result = MatchResult()

        if not prescriber.last_name and not prescriber.npi:
            result.message = "No prescriber info found in Rx"
            return result

        # 1) NPI match — gold standard
        if prescriber.npi:
            row = fetch_prescriber_by_npi(prescriber.npi, folder_path=self.folder_path)
            if row:
                result.found = True
                result.confidence = 0.99
                result.record = dict(row)
                result.match_type = "npi"
                result.message = f"✅ NPI match: {prescriber.full_name} (NPI: {prescriber.npi})"
                return result

        # 2) Name search
        candidates = search_prescribers(prescriber.last_name, folder_path=self.folder_path)
        if not candidates and prescriber.first_name:
            candidates = search_prescribers(prescriber.first_name, folder_path=self.folder_path)

        if candidates:
            scored = []
            for c in candidates:
                score = self._prescriber_score(prescriber, c)
                scored.append((score, c))
            scored.sort(key=lambda x: -x[0])

            best_score, best = scored[0]
            if best_score >= 0.7:
                result.found = True
                result.confidence = best_score
                result.record = best
                result.match_type = "fuzzy"
                result.message = f"🔍 Likely match: {best.get('last_name', '')}, {best.get('first_name', '')} ({best_score:.0%})"

            result.candidates = [c for _, c in scored[:5]]
        else:
            result.message = f"❌ No match found for: {prescriber.full_name} (NPI: {prescriber.npi})"

        return result

    # ------------------------------------------------------------------ Item

    def match_item(self, item: ParsedRxItem) -> ItemMatchResult:
        """
        Match a drug/product name to an inventory HCPCS code.
        Uses the learning DrugMapper.
        """
        result = ItemMatchResult(drug_name=item.drug_name)

        if not item.drug_name:
            result.message = "No drug name found"
            return result

        # Try the learning mapper
        mapping = self.drug_mapper.find_hcpcs(item.drug_name)
        if mapping and mapping.confidence >= 0.6:
            result.found = True
            result.confidence = mapping.confidence
            result.hcpcs = mapping.hcpcs
            result.description = mapping.description
            result.match_type = mapping.source

            if mapping.source == "learned" and mapping.confidence >= 0.8:
                result.message = f"✅ Learned: {mapping.hcpcs} — {mapping.description} ({mapping.use_count} uses)"
            else:
                result.message = f"🔍 Suggested: {mapping.hcpcs} — {mapping.description} ({mapping.confidence:.0%})"

        # Always get candidates for the UI
        result.candidates = self.drug_mapper.suggest(item.drug_name, limit=8)
        
        if not result.found and result.candidates:
            result.message = f"⚠️ No confident match — {len(result.candidates)} suggestions available"
        elif not result.found:
            result.message = f"❌ No inventory match for: {item.drug_name}"

        # Look up pricing from fee schedule (primary) and inventory (fallback)
        if result.hcpcs:
            self._apply_pricing(result)

        return result

    def _apply_pricing(self, result: ItemMatchResult) -> None:
        """Apply pricing from fee schedule (primary) + inventory (secondary)."""
        from dmelogic.db.fee_schedule import lookup_fee

        # 1) Fee schedule — authoritative Medicaid pricing
        fee_info = lookup_fee(result.hcpcs, folder_path=self.folder_path)
        if fee_info:
            result.fee = fee_info.get("fee", 0.0)
            result.rental_fee = fee_info.get("rental_fee", 0.0)
            result.max_units = fee_info.get("max_units", 0)
            result.pa_required = fee_info.get("pa", "")
            # Use fee schedule price as the billing price
            result.retail_price = result.fee

        # 2) Inventory — get item_number, and fallback price if no fee schedule
        inv = self._lookup_inventory_price(result.hcpcs)
        if inv:
            result.item_number = inv.get("item_number", "")
            if not result.retail_price:
                result.retail_price = inv.get("retail_price", 0.0)

    def _lookup_inventory_price(self, hcpcs: str) -> Optional[Dict[str, Any]]:
        """Look up retail_price from inventory for a given HCPCS code."""
        try:
            conn = get_connection("inventory.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # Exact match first
            cur.execute(
                "SELECT retail_price, cost, item_number FROM inventory WHERE hcpcs_code = ? LIMIT 1",
                (hcpcs,)
            )
            row = cur.fetchone()
            if not row:
                # Try base HCPCS (before dash suffix)
                base = hcpcs.split("-")[0] if "-" in hcpcs else hcpcs
                cur.execute(
                    "SELECT retail_price, cost, item_number FROM inventory WHERE hcpcs_code LIKE ? ORDER BY retail_price DESC LIMIT 1",
                    (base + "%",)
                )
                row = cur.fetchone()
            conn.close()
            if row:
                return {
                    "retail_price": row["retail_price"] or 0.0,
                    "cost": row["cost"] or 0.0,
                    "item_number": row["item_number"] or "",
                }
        except Exception:
            pass
        return None

    def confirm_item_mapping(self, drug_name: str, hcpcs: str, description: str = "") -> None:
        """
        Called when user confirms an item mapping.
        This teaches the DrugMapper for future use.
        """
        self.drug_mapper.learn(drug_name, hcpcs, description)

    # ------------------------------------------------------------------ Scoring

    def _patient_score(self, parsed: ParsedPatient, candidate: Dict) -> float:
        """Score a candidate patient against parsed data. 0.0 to 1.0."""
        score = 0.0
        total = 0.0

        # Last name
        if parsed.last_name and candidate.get("last_name"):
            total += 3.0
            if parsed.last_name.upper() == candidate["last_name"].upper():
                score += 3.0
            elif parsed.last_name.upper() in candidate["last_name"].upper():
                score += 2.0

        # First name
        if parsed.first_name and candidate.get("first_name"):
            total += 2.0
            if parsed.first_name.upper() == candidate["first_name"].upper():
                score += 2.0
            elif parsed.first_name.upper()[:3] == candidate["first_name"].upper()[:3]:
                score += 1.0

        # DOB
        if parsed.dob and candidate.get("dob"):
            total += 3.0
            parsed_dob_clean = re.sub(r"[^0-9]", "", parsed.dob)
            cand_dob_clean = re.sub(r"[^0-9]", "", candidate["dob"])
            if parsed_dob_clean == cand_dob_clean:
                score += 3.0
            elif parsed_dob_clean[:4] == cand_dob_clean[:4]:  # Same year
                score += 1.0

        return score / max(total, 1.0)

    def _prescriber_score(self, parsed: ParsedPrescriber, candidate: Dict) -> float:
        """Score a candidate prescriber against parsed data."""
        score = 0.0
        total = 0.0

        # Last name
        if parsed.last_name and candidate.get("last_name"):
            total += 3.0
            if parsed.last_name.upper() == candidate["last_name"].upper():
                score += 3.0

        # First name
        if parsed.first_name and candidate.get("first_name"):
            total += 2.0
            if parsed.first_name.upper() == candidate["first_name"].upper():
                score += 2.0

        # NPI
        if parsed.npi and candidate.get("npi_number"):
            total += 4.0
            if parsed.npi == candidate["npi_number"]:
                score += 4.0

        return score / max(total, 1.0)

    @staticmethod
    def _dob_variants(dob: str) -> List[str]:
        """
        Generate DOB format variants to match against different DB formats.
        Input: "12/05/2004"
        Output: ["12/05/2004", "2004-12-05", "12-05-2004", "12052004"]
        """
        variants = [dob]
        # Parse MM/DD/YYYY
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", dob)
        if m:
            mm, dd, yyyy = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
            variants.extend([
                f"{yyyy}-{mm}-{dd}",
                f"{mm}-{dd}-{yyyy}",
                f"{mm}/{dd}/{yyyy}",
                f"{dd}/{mm}/{yyyy}",
            ])
        return list(set(variants))
