"""
Smart Duplicate Detection
==========================
Detects when a new order would duplicate an existing pending/recent order
for the same patient with overlapping HCPCS codes.

Usage:
    from dmelogic.services.duplicate_detector import DuplicateDetector, DuplicateWarning

    detector = DuplicateDetector(folder_path=...)
    
    warnings = detector.check(
        patient_last_name="BAH",
        patient_first_name="MARIAMA",
        patient_dob="12/05/2004",
        hcpcs_codes=["A4554", "T4533", "A4927"],
        patient_id=42,        # optional, more precise
    )
    
    if warnings:
        # Show warning dialog to user
        for w in warnings:
            print(f"⚠️ Order ORD-{w.order_id:03d} ({w.status}) has {w.overlap_codes}")
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Set

from dmelogic.db.base import get_connection
from dmelogic.config import debug_log


@dataclass
class DuplicateWarning:
    """Represents a potential duplicate order found."""
    order_id: int
    order_date: str
    status: str
    patient_name: str
    prescriber_name: str
    overlap_codes: List[str]          # HCPCS codes that overlap
    all_order_codes: List[str]        # All HCPCS codes in the existing order
    overlap_descriptions: List[str]   # Descriptions for overlapping items
    days_ago: int = 0                 # How many days old the order is
    severity: str = "warning"         # "warning" or "critical"
    message: str = ""

    @property
    def display_order_number(self) -> str:
        return f"ORD-{self.order_id:03d}"


class DuplicateDetector:
    """
    Checks for potential duplicate orders before creating a new one.
    
    Detection rules:
    1. Same patient (by ID, or by name+DOB)
    2. Overlapping HCPCS codes
    3. Existing order is Pending, Open, Active, Shipped, or Unbilled
    4. Existing order is within the lookback window (default 90 days)
    
    Severity:
    - CRITICAL: Same patient + exact same HCPCS set + order in Pending/Open status
    - WARNING: Same patient + some HCPCS overlap + recent order
    """

    # Statuses that indicate an active/in-progress order
    ACTIVE_STATUSES = {"Pending", "Open", "Active", "Shipped", "Unbilled", "On Hold"}
    
    # How far back to look for duplicates
    DEFAULT_LOOKBACK_DAYS = 90

    def __init__(self, folder_path: Optional[str] = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS):
        self.folder_path = folder_path
        self.lookback_days = lookback_days

    def check(
        self,
        patient_last_name: str = "",
        patient_first_name: str = "",
        patient_dob: Optional[str] = None,
        patient_id: Optional[int] = None,
        hcpcs_codes: Optional[List[str]] = None,
    ) -> List[DuplicateWarning]:
        """
        Check if creating an order with these details would be a duplicate.
        
        Returns a list of DuplicateWarning objects (empty = no duplicates).
        """
        if not hcpcs_codes:
            return []
        
        new_codes = set(c.strip().upper() for c in hcpcs_codes if c.strip())
        if not new_codes:
            return []

        warnings: List[DuplicateWarning] = []

        try:
            conn = get_connection("orders.db", folder_path=self.folder_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Build the patient matching clause
            if patient_id:
                # Most precise: match by patient_id
                patient_clause = "o.patient_id = ?"
                patient_params = [patient_id]
            elif patient_last_name:
                # Match by name (case-insensitive)
                if patient_dob:
                    patient_clause = (
                        "UPPER(o.patient_last_name) = UPPER(?) "
                        "AND UPPER(o.patient_first_name) = UPPER(?) "
                        "AND o.patient_dob = ?"
                    )
                    patient_params = [patient_last_name, patient_first_name, patient_dob]
                else:
                    patient_clause = (
                        "UPPER(o.patient_last_name) = UPPER(?) "
                        "AND UPPER(o.patient_first_name) = UPPER(?)"
                    )
                    patient_params = [patient_last_name, patient_first_name]
            else:
                conn.close()
                return []

            # Lookback date
            cutoff = (datetime.now() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

            # Find recent orders for this patient with active statuses
            status_placeholders = ",".join(["?"] * len(self.ACTIVE_STATUSES))
            
            cur.execute(f"""
                SELECT 
                    o.id,
                    o.order_date,
                    o.order_status,
                    o.patient_last_name,
                    o.patient_first_name,
                    o.prescriber_name,
                    o.created_date
                FROM orders o
                WHERE {patient_clause}
                  AND o.order_status IN ({status_placeholders})
                  AND COALESCE(o.order_date, o.created_date) >= ?
                ORDER BY o.order_date DESC, o.id DESC
            """, patient_params + list(self.ACTIVE_STATUSES) + [cutoff])

            candidate_orders = cur.fetchall()

            for order_row in candidate_orders:
                order_id = order_row["id"]

                # Get all HCPCS codes for this order
                cur.execute("""
                    SELECT hcpcs_code, description
                    FROM order_items
                    WHERE order_id = ?
                """, (order_id,))
                
                items = cur.fetchall()
                existing_codes: Set[str] = set()
                code_to_desc = {}
                for item in items:
                    code = (item["hcpcs_code"] or "").strip().upper()
                    if code:
                        existing_codes.add(code)
                        code_to_desc[code] = item["description"] or ""

                # Check overlap
                overlap = new_codes & existing_codes
                if not overlap:
                    continue

                # Calculate days ago
                days_ago = 0
                try:
                    order_date_str = order_row["order_date"] or order_row["created_date"] or ""
                    if order_date_str:
                        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]:
                            try:
                                order_dt = datetime.strptime(str(order_date_str).split(" ")[0], fmt)
                                days_ago = (datetime.now() - order_dt).days
                                break
                            except ValueError:
                                continue
                except Exception:
                    pass

                # Determine severity
                if overlap == new_codes and order_row["order_status"] in ("Pending", "Open", "On Hold"):
                    severity = "critical"
                    message = (
                        f"⛔ EXACT DUPLICATE — Order {order_id} ({order_row['order_status']}) "
                        f"already has ALL the same items ({', '.join(sorted(overlap))}). "
                        f"Created {days_ago} day(s) ago."
                    )
                elif len(overlap) == len(new_codes):
                    severity = "critical"
                    message = (
                        f"⚠️ All items overlap with Order {order_id} ({order_row['order_status']}), "
                        f"{days_ago} day(s) ago. Codes: {', '.join(sorted(overlap))}"
                    )
                else:
                    severity = "warning"
                    message = (
                        f"🔍 Partial overlap: {len(overlap)}/{len(new_codes)} items match "
                        f"Order {order_id} ({order_row['order_status']}), "
                        f"{days_ago} day(s) ago. Codes: {', '.join(sorted(overlap))}"
                    )

                patient_name = f"{order_row['patient_last_name'] or ''}, {order_row['patient_first_name'] or ''}".strip(", ")
                
                warnings.append(DuplicateWarning(
                    order_id=order_id,
                    order_date=order_row["order_date"] or "",
                    status=order_row["order_status"] or "",
                    patient_name=patient_name,
                    prescriber_name=order_row["prescriber_name"] or "",
                    overlap_codes=sorted(overlap),
                    all_order_codes=sorted(existing_codes),
                    overlap_descriptions=[code_to_desc.get(c, "") for c in sorted(overlap)],
                    days_ago=days_ago,
                    severity=severity,
                    message=message,
                ))

            conn.close()

        except Exception as e:
            debug_log(f"DuplicateDetector error: {e}")

        # Sort: critical first, then by recency
        warnings.sort(key=lambda w: (0 if w.severity == "critical" else 1, w.days_ago))
        return warnings
