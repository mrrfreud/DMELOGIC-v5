"""
Agent Order Service — the ONLY entry point for AI agents creating orders.

Any order created through this module is automatically flagged as
``agent_created=True`` and placed in "Pending Approval" status.  Human
users never call these functions; they use the Order Wizard / UI instead.

Typical usage by an agent (or agent-facing integration):

    from dmelogic.services.agent_order_service import create_order_as_agent

    order_id = create_order_as_agent(
        patient_last_name="Smith",
        patient_first_name="John",     # "" if unknown
        items=[
            {"hcpcs": "E0260", "description": "Semi-electric hospital bed"},
            {"description": "Wheelchair — type unclear"},   # placeholder
        ],
        # Optional fields — all can be omitted if the agent can't determine them
        patient_id=42,
        prescriber_id=7,               # DB ID from prescriber lookup
        rx_date="2026-03-20",
        prescriber_name="Dr. Jones",
        prescriber_npi="1234567890",
        primary_insurance="Medicaid",
        icd_codes=["M54.5"],
        rx_origin="Fax",
        source_file_path="C:/scans/rx_smith_2026-03-20.pdf",
        notes="Agent note: Rx was partially illegible.",
    )
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from dmelogic.db.models import OrderInput, OrderItemInput, OrderStatus
from dmelogic.db.orders import create_order
from dmelogic.db.base import get_connection
from dmelogic.config import debug_log
from dmelogic.security.audit import audit_log


# Sentinel values the agent should NEVER pass — used only internally
# by the persistence layer when the DB schema requires NOT NULL.
_PLACEHOLDER_SENTINELS = frozenset({"[TBD]", "[Unknown]", "[NEEDS REVIEW]"})


# ---------------------------------------------------------------------------
# Helper: Ensure prescriber exists in DB
# ---------------------------------------------------------------------------

def _ensure_prescriber_exists(
    prescriber_name: str,
    prescriber_npi: str,
    folder_path: Optional[str] = None,
) -> Optional[int]:
    """
    Look up or create a prescriber in the prescribers database.
    
    If a prescriber with the given NPI exists, returns their ID.
    Otherwise creates a new prescriber record and returns the new ID.
    
    Returns None if creation fails.
    """
    if not prescriber_name or not prescriber_npi:
        return None
    
    try:
        conn = get_connection("prescribers.db", folder_path=folder_path)
        cursor = conn.cursor()
        
        # First, try to find by NPI (most reliable match)
        cursor.execute(
            "SELECT id FROM prescribers WHERE npi_number = ?",
            (prescriber_npi.strip(),)
        )
        row = cursor.fetchone()
        
        if row:
            prescriber_id = row[0] if isinstance(row, tuple) else row["id"]
            conn.close()
            return prescriber_id
        
        # Not found — create a new prescriber record
        # Parse name: try to split "Dr. First Last" or "Last, First" formats
        name = prescriber_name.strip()
        first_name = ""
        last_name = name
        
        # Handle "LAST, FIRST" format
        if "," in name:
            parts = name.split(",", 1)
            last_name = parts[0].strip()
            first_name = parts[1].strip() if len(parts) > 1 else ""
        # Handle "Dr. First Last" or "First Last" format
        elif " " in name:
            parts = name.split()
            # Remove common prefixes
            if parts[0].lower() in ("dr", "dr.", "doctor"):
                parts = parts[1:]
            if len(parts) >= 2:
                first_name = parts[0]
                last_name = " ".join(parts[1:])
            elif len(parts) == 1:
                last_name = parts[0]
        
        debug_log(f"[agent] Creating new prescriber: {last_name}, {first_name} NPI={prescriber_npi}")
        
        cursor.execute(
            """
            INSERT INTO prescribers (last_name, first_name, npi_number, notes)
            VALUES (?, ?, ?, ?)
            """,
            (last_name, first_name, prescriber_npi.strip(), f"Added by agent from order - Full name: {prescriber_name.strip()}")
        )
        
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        debug_log(f"[agent] Created prescriber ID {new_id}")
        return new_id
        
    except Exception as e:
        debug_log(f"[agent] Error ensuring prescriber exists: {e}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_order_as_agent(
    *,
    # ---- Patient (at least one identifier required) ----
    patient_last_name: str = "",
    patient_first_name: str = "",
    patient_id: Optional[int] = None,
    patient_dob: Optional[str] = None,
    patient_phone: Optional[str] = None,
    patient_address: Optional[str] = None,
    # ---- Prescriber (optional — agent may not know) ----
    prescriber_id: Optional[int] = None,
    prescriber_name: Optional[str] = None,
    prescriber_npi: Optional[str] = None,
    # ---- Dates (optional — agent may not be able to read the Rx) ----
    rx_date: Optional[str] = None,
    order_date: Optional[str] = None,
    # ---- Insurance (optional — may not be on file) ----
    primary_insurance: Optional[str] = None,
    primary_insurance_id: Optional[str] = None,
    billing_type: Optional[str] = None,
    # ---- Diagnosis (optional) ----
    icd_codes: Optional[List[str]] = None,
    # ---- Items (at least one required — may be placeholders) ----
    items: Optional[List[Dict[str, Any]]] = None,
    # ---- Origin / Source (optional) ----
    rx_origin: Optional[str] = None,
    source_file_path: Optional[str] = None,
    # ---- Misc ----
    doctor_directions: Optional[str] = None,
    notes: Optional[str] = None,
    # ---- DB ----
    folder_path: Optional[str] = None,
) -> int:
    """
    Create an order on behalf of an AI agent.

    The order is **always** flagged ``agent_created=True`` and enters the
    ``Pending Approval`` status so a human reviewer can approve or reject it.

    **Guardrails that are relaxed for agents:**

    * Patient first/last name can be blank (just provide ``patient_id``).
    * ``rx_date`` can be blank if the agent couldn't read the prescription.
    * Insurance, ICD-10 codes, and prescriber info are all optional.
    * Items may be *placeholders* — provide only ``description`` if the
      HCPCS code is unknown.  ``hcpcs="[TBD]"`` is also accepted and treated
      as a placeholder.

    **Do NOT pass sentinel values** like ``"[Unknown]"`` or ``"[NEEDS REVIEW]"``
    for any field.  Leave them ``None`` / empty and the system will handle
    default fill-ins for NOT-NULL DB columns automatically.

    Args:
        patient_last_name:   Patient surname (best effort).
        patient_first_name:  Patient given name (best effort).
        patient_id:          Patient DB ID if known (helps matching).
        patient_dob:         Date of birth (any legible format).
        patient_phone:       Phone number.
        patient_address:     Address.
        prescriber_id:       Prescriber DB ID if resolved via NPI lookup.
        prescriber_name:     Prescriber full name (None if unknown).
        prescriber_npi:      NPI number (None if unknown).
        rx_date:             Prescription date (None if illegible).
        order_date:          Date the order is being placed.
        primary_insurance:   Insurance name.
        primary_insurance_id: Member / policy ID.
        billing_type:        "Insurance", "Cash", "Rental", "Medicare".
        icd_codes:           List of ICD-10 diagnosis codes.
        rx_origin:           How the Rx arrived — e.g. "Fax", "Phone",
                             "eRx", "Walk-in".
        source_file_path:    Path to the source document (PDF/image) that
                             the agent ingested.  Stored in notes so the
                             human reviewer can locate the original.
        doctor_directions:   Free-text physician directions.
        notes:               Agent notes for the human reviewer.
        items:               List of dicts, each with keys:
                             ``hcpcs`` (str, optional — "[TBD]" treated as
                             placeholder), ``description`` (str, optional),
                             ``quantity`` (int, default 1),
                             ``refills`` (int, default 0),
                             ``days_supply`` (int, default 30),
                             ``directions`` (str, optional),
                             ``is_rental`` (bool, default False).
                             At least ``hcpcs`` *or* ``description``
                             must be present.
        folder_path:         Override for the database folder path.

    Returns:
        The new order ID (int).

    Raises:
        ValueError:  If even the relaxed agent validation fails
                     (e.g. no patient identifier and no items at all).
    """

    # -- Build item DTOs -------------------------------------------------
    item_inputs: List[OrderItemInput] = []
    for raw in (items or []):
        # Accept multiple field name aliases for flexibility
        # "strength" is used by Cloney agent (maps HCPCS code to this field)
        hcpcs = (raw.get("hcpcs") or raw.get("hcpcs_code") or raw.get("strength") or "").strip()
        desc = (raw.get("description") or raw.get("item_description") or "").strip()

        # Treat missing HCPCS *or* the "[TBD]" sentinel as a placeholder
        is_placeholder = (not hcpcs) or (hcpcs.upper() in _PLACEHOLDER_SENTINELS)

        item_inputs.append(
            OrderItemInput(
                hcpcs=hcpcs if not is_placeholder else "",
                description=desc,
                quantity=int(raw.get("quantity") or 1),
                refills=int(raw.get("refills") or 0),
                days_supply=int(raw.get("days_supply") or 30),
                directions=raw.get("directions"),
                is_rental=bool(raw.get("is_rental", False)),
                is_placeholder=is_placeholder,
                modifier1=raw.get("modifier1"),
                modifier2=raw.get("modifier2"),
                modifier3=raw.get("modifier3"),
                modifier4=raw.get("modifier4"),
            )
        )

    # If no items were given at all, insert a single placeholder so the
    # order is still valid and the reviewer can fill it in.
    if not item_inputs:
        item_inputs.append(
            OrderItemInput(
                description="[Agent could not determine items — needs review]",
                is_placeholder=True,
            )
        )

    # -- Build ICD code fields -------------------------------------------
    icd_list = icd_codes or []

    # -- Compose notes (append source_file_path if provided) -------------
    notes_parts: list[str] = []
    if source_file_path:
        notes_parts.append(f"[Source file: {source_file_path}]")
    if notes:
        notes_parts.append(notes)
    combined_notes = "\n".join(notes_parts) if notes_parts else None

    # -- Ensure prescriber exists in prescribers DB ----------------------
    # If we have prescriber name and NPI, ensure they're in the prescribers database
    if prescriber_name and prescriber_npi and not prescriber_id:
        prescriber_id = _ensure_prescriber_exists(
            prescriber_name=prescriber_name,
            prescriber_npi=prescriber_npi,
            folder_path=folder_path,
        )
        if prescriber_id:
            debug_log(f"[agent] Mapped prescriber '{prescriber_name}' NPI={prescriber_npi} to prescriber_id={prescriber_id}")

    # -- Build OrderInput (agent_created=True is the key) ----------------
    order_input = OrderInput(
        patient_last_name=patient_last_name,
        patient_first_name=patient_first_name,
        patient_id=patient_id,
        patient_dob=patient_dob,
        patient_phone=patient_phone,
        patient_address=patient_address,
        prescriber_id=prescriber_id,
        prescriber_name=prescriber_name,
        prescriber_npi=prescriber_npi,
        rx_date=rx_date,
        order_date=order_date,
        rx_origin=rx_origin,
        primary_insurance=primary_insurance,
        primary_insurance_id=primary_insurance_id,
        billing_type=billing_type or "Insurance",
        order_status=OrderStatus.PENDING.value,  # will be overridden to Pending Approval
        icd_code_1=icd_list[0] if len(icd_list) > 0 else None,
        icd_code_2=icd_list[1] if len(icd_list) > 1 else None,
        icd_code_3=icd_list[2] if len(icd_list) > 2 else None,
        icd_code_4=icd_list[3] if len(icd_list) > 3 else None,
        icd_code_5=icd_list[4] if len(icd_list) > 4 else None,
        doctor_directions=doctor_directions,
        notes=combined_notes,
        items=item_inputs,
        agent_created=True,           # <── THE FLAG — always True here
    )

    debug_log(
        f"[agent] Creating order for patient "
        f"{patient_last_name or '[unknown]'}, {patient_first_name or '[unknown]'} "
        f"with {len(item_inputs)} item(s)"
        f"{f', prescriber_id={prescriber_id}' if prescriber_id else ''}"
        f"{f', rx_origin={rx_origin}' if rx_origin else ''}"
        f"{f', source={source_file_path}' if source_file_path else ''}"
    )

    # -- Duplicate detection -----------------------------------------------
    # Guard against the agent submitting the same order twice (same patient
    # + rx_date + same HCPCS codes still in Pending Approval status).
    # Allow multiple orders if HCPCS codes are different (e.g., 2 Rx same day).
    if patient_last_name and rx_date:
        try:
            conn = get_connection("orders.db", folder_path=folder_path)
            # Get new order's HCPCS codes
            new_hcpcs = {item.hcpcs.upper().strip() for item in item_inputs if item.hcpcs}
            
            # Find pending orders for same patient + rx_date
            dups = conn.execute(
                "SELECT id FROM orders "
                "WHERE patient_last_name = ? AND patient_first_name = ? "
                "AND rx_date = ? AND order_status = 'Pending Approval' "
                "AND agent_created = 1",
                (patient_last_name, patient_first_name, rx_date),
            ).fetchall()
            
            for dup in dups:
                existing_id = dup["id"] if isinstance(dup, dict) else dup[0]
                # Get existing order's HCPCS codes
                existing_items = conn.execute(
                    "SELECT hcpcs_code FROM order_items WHERE order_id = ?",
                    (existing_id,)
                ).fetchall()
                existing_hcpcs = {
                    (r["hcpcs_code"] if isinstance(r, dict) else r[0]).upper().strip()
                    for r in existing_items if (r["hcpcs_code"] if isinstance(r, dict) else r[0])
                }
                
                # Block only if HCPCS codes overlap
                overlap = new_hcpcs & existing_hcpcs
                if overlap:
                    debug_log(
                        f"[agent] Duplicate detected — existing order {existing_id} "
                        f"for {patient_last_name}, {patient_first_name} rx_date={rx_date} "
                        f"has overlapping HCPCS: {overlap}"
                    )
                    raise ValueError(
                        f"Duplicate agent order: an order for {patient_last_name}, "
                        f"{patient_first_name} with rx_date={rx_date} already exists "
                        f"(order #{existing_id}, still Pending Approval) with same HCPCS {overlap}. "
                        f"Approve or reject that order before creating a new one."
                    )
            
            if dups and not any(overlap for _ in []):  # If we get here, no overlap found
                debug_log(
                    f"[agent] Same patient/date but different HCPCS — allowing new order "
                    f"(existing orders: {[d[0] for d in dups]}, new HCPCS: {new_hcpcs})"
                )
        except ValueError:
            raise  # re-raise our own ValueError
        except Exception as e:
            debug_log(f"[agent] Duplicate check skipped (non-fatal): {e}")

    order_id = create_order(order_input, folder_path=folder_path)

    # -- Audit trail -------------------------------------------------------
    try:
        audit_log(
            action="agent_create_order",
            resource_type="order",
            resource_id=str(order_id),
            details=(
                f"Agent created order for {patient_last_name or '[unknown]'}, "
                f"{patient_first_name or '[unknown]'} "
                f"({len(item_inputs)} item(s))"
                f"{f' | rx_origin={rx_origin}' if rx_origin else ''}"
                f"{f' | source={source_file_path}' if source_file_path else ''}"
            ),
            folder_path=folder_path,
        )
    except Exception as e:
        debug_log(f"[agent] Audit log failed (non-fatal): {e}")

    debug_log(f"[agent] Order {order_id} created — awaiting approval")
    return order_id
