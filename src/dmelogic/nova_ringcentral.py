"""
Nova RingCentral Integration
============================
Connects Nova to RingCentral using credentials already stored by DMELogic.

Reads Client ID and Secret from settings.json.
Reads OAuth tokens from Windows Credential Manager or rc_tokens.enc fallback.
Uses ringcentral_service.py patterns already established in DMELogic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
import sqlite3
import tempfile

import requests

log = logging.getLogger("nova_rc")

APPROVED_INCONTINENCE_ICD10_LINES = [
    "Urinary Incontinence (ICD-10)",
    "N39.41 - Urge incontinence",
    "N39.3 - Stress incontinence (male/female)",
    "N39.46 - Mixed incontinence (male/female)",
    "N39.42 - Incontinence without sensory awareness",
    "N39.43 - Post-void dribbling",
    "N39.44 - Nocturnal enuresis",
    "N39.45 - Continuous leakage",
    "N39.490 - Overflow incontinence",
    "R39.81 - Functional urinary incontinence",
    "",
    "Fecal Incontinence (ICD-10)",
    "R15.9 - Fecal incontinence, unspecified",
    "R15.0 - Incomplete defecation",
    "R15.1 - Fecal smearing",
    "R15.2 - Fecal urgency",
]


def _draw_icd10_guidance_page(c, width, height, invalid_code: str = ""):
    """Render a second page listing approved ICD-10 codes for incontinence scripts."""
    from reportlab.lib.units import inch

    c.showPage()
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.5 * inch, height - 0.7 * inch, "1ST AID PHARMACY & SURGICAL SUPPLIES")
    c.setFont("Helvetica", 9)
    c.drawString(0.5 * inch, height - 0.9 * inch, "Fordham Road, Bronx, NY")
    c.drawString(0.5 * inch, height - 1.05 * inch, "Tel: 347-647-2347  |  Fax: 347-947-8102")

    y = height - 1.35 * inch
    c.setLineWidth(1)
    c.line(0.5 * inch, y, width - 0.5 * inch, y)
    y -= 0.25 * inch

    invalid_label = str(invalid_code or "R32").strip().upper()
    c.setFont("Helvetica-Bold", 10)
    c.drawString(
        0.5 * inch,
        y,
        f"IMPORTANT: ICD-10 CODES ARE REQUIRED. {invalid_label} AND OTHER NON ICD-10 CODES ARE NOT ACCEPTED.",
    )
    y -= 0.2 * inch
    c.setFont("Helvetica", 9)
    c.drawString(
        0.5 * inch,
        y,
        "Prescriptions for incontinence supplies must include a valid ICD-10 diagnosis code.",
    )
    y -= 0.15 * inch
    c.drawString(0.5 * inch, y, "Please use the following approved codes when issuing or resubmitting prescriptions:")
    y -= 0.25 * inch

    for line in APPROVED_INCONTINENCE_ICD10_LINES:
        if y < 0.8 * inch:
            c.showPage()
            y = height - 0.75 * inch
            c.setFont("Helvetica", 9)
        if not line:
            y -= 0.1 * inch
            continue
        if line.endswith("(ICD-10)"):
            c.setFont("Helvetica-Bold", 9)
            c.drawString(0.5 * inch, y, line)
        else:
            c.setFont("Helvetica", 9)
            c.drawString(0.6 * inch, y, line)
        y -= 0.15 * inch

    y -= 0.15 * inch
    c.setFont("Helvetica", 9)
    c.drawString(
        0.5 * inch,
        y,
        "Please re-submit prescriptions (fax or E-RX) with an appropriate ICD-10 diagnosis code.",
    )
    y -= 0.15 * inch
    c.drawString(0.5 * inch, y, "For questions, contact 1st Aid Pharmacy at 347-647-2347.")


def _looks_like_new_rx_request_context(
    order_status: str,
    order_notes: str,
    doctor_directions: str,
    items_rows: List[tuple],
) -> bool:
    """Heuristic guard to avoid sending refill-form faxes for new-RX request workflows."""
    text_parts = [str(order_status or ""), str(order_notes or ""), str(doctor_directions or "")]
    for row in items_rows or []:
        # row shape: (hcpcs_code, description, qty, refills, day_supply)
        desc = row[1] if len(row) > 1 else ""
        text_parts.append(str(desc or ""))
    haystack = " ".join(text_parts).lower()

    request_markers = (
        "new prescription request",
        "prescription request",
        "first-time",
        "first time",
        "new patient",
        "placeholder",
        "please specify incontinence supplies",
        "request items",
        "requested items",
    )
    if any(marker in haystack for marker in request_markers):
        return True

    # Additional guard for placeholder On Hold requests.
    if str(order_status or "").strip().lower() == "on hold" and "refill" not in haystack:
        return True

    return False


def _digits_only(number: str) -> str:
    return "".join(ch for ch in str(number or "") if ch.isdigit())


def _phone_variants(number: str) -> List[str]:
    digits = _digits_only(number)
    if not digits:
        return []
    out = {digits}
    if len(digits) == 11 and digits.startswith("1"):
        out.add(digits[1:])
    if len(digits) == 10:
        out.add("1" + digits)
    return sorted(out)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _looks_failed(status: Optional[str]) -> bool:
    value = str(status or "").upper()
    return any(marker in value for marker in ("FAILED", "FAIL", "ERROR", "REJECT", "CANCEL"))


def _lookup_known_party(number: str, folder_path: str = None) -> Dict[str, Any]:
    """Best-effort phone match against patients/prescribers DBs."""
    try:
        from dmelogic.paths import db_dir

        db_folder = Path(folder_path) if folder_path else Path(db_dir())
        patients_db = db_folder / "patients.db"
        prescribers_db = db_folder / "prescribers.db"
        variants = _phone_variants(number)
        if not variants:
            return {"known": False}

        for variant in variants:
            like_expr = f"%{variant}%"
            if patients_db.exists():
                conn = sqlite3.connect(str(patients_db))
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, last_name, first_name, dob, phone
                    FROM patients
                    WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone,''), '-', ''), '(', ''), ')', ''), ' ', ''), '+', '') LIKE ?
                    LIMIT 1
                    """,
                    (like_expr,),
                )
                row = cur.fetchone()
                conn.close()
                if row:
                    pid, ln, fn, dob, phone = row
                    return {
                        "known": True,
                        "kind": "patient",
                        "patient_id": pid,
                        "name": f"{ln or ''}, {fn or ''}".strip(", "),
                        "dob": dob,
                        "phone": phone,
                    }

            if prescribers_db.exists():
                conn = sqlite3.connect(str(prescribers_db))
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(prescribers)")
                cols = {str(r[1]) for r in (cur.fetchall() or []) if len(r) > 1}

                if "full_name" in cols:
                    base_name_expr = "COALESCE(full_name,'')"
                else:
                    base_name_expr = "TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,''))"

                if "practice_name" in cols:
                    name_expr = f"COALESCE(NULLIF({base_name_expr}, ''), COALESCE(practice_name, ''))"
                else:
                    name_expr = base_name_expr

                cur.execute(
                    f"""
                    SELECT id, {name_expr}, COALESCE(fax,''), COALESCE(phone,''), COALESCE(npi_number,'')
                    FROM prescribers
                    WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone,''), '-', ''), '(', ''), ')', ''), ' ', ''), '+', '') LIKE ?
                       OR REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(fax,''), '-', ''), '(', ''), ')', ''), ' ', ''), '+', '') LIKE ?
                    LIMIT 1
                    """,
                    (like_expr, like_expr),
                )
                row = cur.fetchone()
                conn.close()
                if row:
                    prid, full_name, fax, phone, npi = row
                    return {
                        "known": True,
                        "kind": "prescriber",
                        "prescriber_id": prid,
                        "name": full_name,
                        "phone": phone,
                        "fax": fax,
                        "npi": npi,
                    }
    except Exception as e:
        log.warning(f"Known-party phone lookup failed: {e}")

    return {"known": False}


def _get_rc_credentials() -> Dict[str, str]:
    """Load RingCentral credentials from DMELogic settings.json."""
    try:
        from dmelogic.settings import load_settings

        settings = load_settings()
        rc = settings.get("ringcentral", {})
        return {
            "client_id": rc.get("client_id", ""),
            "client_secret": rc.get("client_secret", ""),
            "phone": rc.get("phone_number", ""),
            "server": rc.get("server_url", "https://platform.ringcentral.com"),
        }
    except Exception as e:
        log.error(f"Could not load RC credentials: {e}")
        return {}


def _get_rc_service():
    """Get the existing RingCentral service from DMELogic."""
    try:
        from dmelogic.settings import load_settings
        from dmelogic.services.ringcentral_service import get_ringcentral_service

        settings = load_settings()
        return get_ringcentral_service(settings)
    except Exception as e:
        log.error(f"Could not initialize RC service: {e}")
        return None


def ringcentral_status() -> Dict[str, Any]:
    """Return RingCentral configuration/connection status for Nova."""
    try:
        creds = _get_rc_credentials() or {}
        svc = _get_rc_service()
        configured = bool(creds.get("client_id") and creds.get("client_secret"))
        connected = bool(svc and svc.is_connected)

        result: Dict[str, Any] = {
            "configured": configured,
            "connected": connected,
            "server": creds.get("server"),
            "phone": creds.get("phone"),
        }

        if not svc:
            result["message"] = "RingCentral service not available"
            return result

        conn_test = svc.test_connection()
        if isinstance(conn_test, dict):
            result["connection_test"] = conn_test

        unread = svc.get_unread_count()
        if isinstance(unread, dict):
            result["unread"] = unread

        return result
    except Exception as e:
        return {"error": str(e)}


def ringcentral_connect(timeout: int = 180) -> Dict[str, Any]:
    """Run OAuth authorization flow in-browser and connect RingCentral."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available or not configured"}

        ok = svc.authorize(timeout=max(30, int(timeout)), open_browser=True)
        if not ok:
            return {
                "success": False,
                "connected": False,
                "message": "Authorization failed or timed out",
            }

        test = svc.test_connection()
        return {
            "success": True,
            "connected": True,
            "message": "RingCentral connected",
            "connection_test": test,
        }
    except Exception as e:
        return {"error": str(e)}


def ringcentral_disconnect() -> Dict[str, Any]:
    """Disconnect RingCentral and clear locally stored OAuth tokens."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}
        ok = svc.disconnect()
        return {
            "success": bool(ok),
            "connected": False,
            "message": "RingCentral disconnected" if ok else "RingCentral disconnect failed",
        }
    except Exception as e:
        return {"error": str(e)}


def send_sms(to_number: str, message: str) -> Dict[str, Any]:
    """Send SMS to a patient."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        result = svc.send_sms(to_number=to_number, message=message)
        if not isinstance(result, dict):
            return {"error": "Unexpected response from RingCentral service"}

        if not result.get("success"):
            return {"error": result.get("error", "SMS failed")}

        return {
            "success": True,
            "message_id": result.get("message_id"),
            "to": result.get("to", to_number),
            "status": result.get("status"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_call_log(
    direction: str = "All",
    limit: int = 50,
    date_from: str = None,
) -> Dict[str, Any]:
    """Get recent call log entries."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        if not svc.is_connected:
            return {"error": "RingCentral is not connected"}

        # Default to 7 days back if no date is specified.
        if not date_from:
            date_from = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")

        params: Dict[str, Any] = {
            "perPage": max(1, min(int(limit), 1000)),
            "dateFrom": date_from,
            "type": "Voice",
        }
        if direction and direction.lower() != "all":
            params["direction"] = direction

        url = f"{svc.config.server_url}/restapi/v1.0/account/~/extension/~/call-log"
        response = requests.get(url, headers=svc._get_auth_header(), params=params, timeout=30)
        response.raise_for_status()

        data = response.json()
        records = []
        for r in (data.get("records") or []):
            from_info = r.get("from") or {}
            records.append(
                {
                    "caller": from_info.get("name") or from_info.get("phoneNumber"),
                    "number": from_info.get("phoneNumber"),
                    "time": r.get("startTime"),
                    "duration": r.get("duration"),
                    "result": r.get("result"),
                    "direction": r.get("direction"),
                    "missed": r.get("result") == "Missed",
                }
            )
        return {"records": records, "total": len(records)}
    except Exception as e:
        return {"error": str(e)}


def get_missed_calls(limit: int = 25, date_from: str = None, flag_known: bool = True) -> Dict[str, Any]:
    """Get missed calls and optionally flag known patients/prescribers."""
    data = get_call_log(direction="Inbound", limit=limit, date_from=date_from)
    if data.get("error"):
        return data

    missed = []
    for rec in (data.get("records") or []):
        if not rec.get("missed"):
            continue
        out = dict(rec)
        if flag_known and rec.get("number"):
            party = _lookup_known_party(rec.get("number"))
            out["known_party"] = party
            out["urgent"] = bool(party.get("known"))
        else:
            out["known_party"] = {"known": False}
            out["urgent"] = False
        missed.append(out)
    return {"records": missed, "total": len(missed)}


def get_voicemails(unread_only: bool = True, limit: int = 20) -> Dict[str, Any]:
    """List voicemail messages with summary metadata."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        result = svc.get_messages(
            message_type="VoiceMail",
            direction="Inbound",
            read_status="Unread" if unread_only else None,
            per_page=max(1, min(int(limit), 1000)),
        )
        if not result.get("success"):
            return {"error": result.get("error", "Failed to fetch voicemails")}

        rows = []
        for msg in (result.get("messages") or []):
            rows.append(
                {
                    "id": msg.get("id"),
                    "from": msg.get("from_name") or msg.get("from_number"),
                    "number": msg.get("from_number"),
                    "time": msg.get("created_at"),
                    "read": msg.get("read_status") == "Read",
                    "message_status": msg.get("message_status"),
                    "transcription_preview": msg.get("subject") or "",
                }
            )
        return {"records": rows, "total": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def get_voicemail_transcription(voicemail_id: str) -> Dict[str, Any]:
    """Fetch full voicemail details and best-available transcription text."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        url = f"{svc.config.server_url}/restapi/v1.0/account/~/extension/~/message-store/{voicemail_id}"
        response = requests.get(url, headers=svc._get_auth_header(), timeout=30)
        response.raise_for_status()
        data = response.json()

        transcription = (
            data.get("vmTranscription")
            or data.get("transcription")
            or data.get("subject")
            or ""
        )

        return {
            "id": voicemail_id,
            "from": (data.get("from") or {}).get("name") or (data.get("from") or {}).get("phoneNumber"),
            "number": (data.get("from") or {}).get("phoneNumber"),
            "created_at": data.get("creationTime"),
            "read": data.get("readStatus") == "Read",
            "transcription": transcription,
            "raw_subject": data.get("subject"),
        }
    except Exception as e:
        return {"error": str(e), "id": voicemail_id}


def initiate_call(to_number: str, from_number: str = "", caller_id: str = "", play_prompt: bool = True) -> Dict[str, Any]:
    """Initiate a click-to-call RingOut call."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}
        result = svc.initiate_call(
            to_number=to_number,
            from_number=from_number or None,
            caller_id=caller_id or None,
            play_prompt=bool(play_prompt),
        )
        if not result.get("success"):
            return {"error": result.get("error", "Call initiation failed")}
        return result
    except Exception as e:
        return {"error": str(e)}


def get_active_calls(limit: int = 50) -> Dict[str, Any]:
    """Get active telephony sessions across extensions (if scope permits)."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        url = f"{svc.config.server_url}/restapi/v1.0/account/~/telephony/sessions"
        params = {"perPage": max(1, min(int(limit), 200))}
        response = requests.get(url, headers=svc._get_auth_header(), params=params, timeout=30)
        response.raise_for_status()

        data = response.json()
        sessions = []
        for s in (data.get("records") or []):
            parties = s.get("parties") or []
            sessions.append(
                {
                    "id": s.get("id"),
                    "direction": s.get("direction"),
                    "start_time": s.get("startTime"),
                    "telephony_status": s.get("telephonyStatus"),
                    "parties": [
                        {
                            "id": p.get("id"),
                            "status": p.get("status"),
                            "from": (p.get("from") or {}).get("phoneNumber"),
                            "to": (p.get("to") or {}).get("phoneNumber"),
                            "extension_id": (p.get("extension") or {}).get("id"),
                        }
                        for p in parties
                    ],
                }
            )
        return {"records": sessions, "total": len(sessions)}
    except Exception as e:
        return {"error": str(e)}


def get_sms_inbox(unread_only: bool = False, limit: int = 50) -> Dict[str, Any]:
    """Get inbound SMS messages."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        result = svc.get_messages(
            message_type="SMS",
            direction="Inbound",
            read_status="Unread" if unread_only else None,
            per_page=max(1, min(int(limit), 1000)),
        )
        if not result.get("success"):
            return {"error": result.get("error", "Failed to fetch SMS inbox")}
        return {"records": result.get("messages") or [], "total": len(result.get("messages") or [])}
    except Exception as e:
        return {"error": str(e)}


def get_sms_thread(phone_number: str, limit: int = 100) -> Dict[str, Any]:
    """Get full SMS thread history for one phone number."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        target_variants = set(_phone_variants(phone_number))
        if not target_variants:
            return {"error": "Invalid phone number"}

        result = svc.get_messages(message_type="SMS", per_page=max(1, min(int(limit) * 4, 1000)))
        if not result.get("success"):
            return {"error": result.get("error", "Failed to fetch SMS thread")}

        thread = []
        for msg in (result.get("messages") or []):
            from_variants = set(_phone_variants(msg.get("from_number")))
            to_variants = set(_phone_variants(msg.get("to_number")))
            if target_variants.intersection(from_variants) or target_variants.intersection(to_variants):
                thread.append(msg)

        thread.sort(key=lambda m: _parse_iso(m.get("created_at")) or datetime.min)
        thread = thread[-max(1, min(int(limit), 1000)) :]
        return {"phone_number": phone_number, "records": thread, "total": len(thread)}
    except Exception as e:
        return {"error": str(e)}


def get_unread_sms(limit: int = 100) -> Dict[str, Any]:
    """Get unread inbound SMS messages."""
    return get_sms_inbox(unread_only=True, limit=limit)


def send_bulk_sms(numbers: List[str], message: str) -> Dict[str, Any]:
    """Send the same SMS message to a list of phone numbers."""
    if not isinstance(numbers, list) or not numbers:
        return {"error": "numbers must be a non-empty list"}

    results = []
    for number in numbers:
        result = send_sms(str(number), message)
        results.append({"to": number, **result})

    sent = sum(1 for r in results if r.get("success"))
    failed = len(results) - sent
    return {"success": failed == 0, "sent": sent, "failed": failed, "results": results}


def send_refill_reminder(to_number: str, item: str, due_date: str) -> Dict[str, Any]:
    """Send templated refill reminder SMS."""
    msg = f"Your refill for {item} is due {due_date}. Reply YES to confirm."
    result = send_sms(to_number=to_number, message=msg)
    if result.get("error"):
        return result
    return {**result, "template": "refill_reminder", "message": msg}


def send_delivery_notification(
    to_number: str,
    driver_name: str,
    order_number: str = "",
    eta_window: str = "",
) -> Dict[str, Any]:
    """Send templated delivery notification SMS."""
    order_part = f" for order {order_number}" if order_number else ""
    eta_part = f" ETA: {eta_window}." if eta_window else ""
    msg = f"Your order{order_part} is out for delivery today. Driver: {driver_name}.{eta_part}".strip()
    result = send_sms(to_number=to_number, message=msg)
    if result.get("error"):
        return result
    return {**result, "template": "delivery_notification", "message": msg}


def get_fax_inbox(unread_only: bool = False, limit: int = 50) -> Dict[str, Any]:
    """Get inbound faxes."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        result = svc.get_messages(
            message_type="Fax",
            direction="Inbound",
            read_status="Unread" if unread_only else None,
            per_page=max(1, min(int(limit), 1000)),
        )
        if not result.get("success"):
            return {"error": result.get("error", "Failed to fetch fax inbox")}

        records = []
        for m in (result.get("messages") or []):
            records.append(
                {
                    "id": m.get("id"),
                    "from": m.get("from_name") or m.get("from_number"),
                    "number": m.get("from_number"),
                    "time": m.get("created_at"),
                    "read": m.get("read_status") == "Read",
                    "status": m.get("message_status"),
                    "pages": m.get("fax_page_count"),
                }
            )
        return {"records": records, "total": len(records)}
    except Exception as e:
        return {"error": str(e)}


def get_fax_status(fax_id: str) -> Dict[str, Any]:
    """Alias wrapper for fax status checks."""
    return check_fax_status(fax_id)


def list_sent_faxes(limit: int = 50, date_from: str = None) -> Dict[str, Any]:
    """List outbound fax messages for auditing."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        result = svc.get_messages(
            message_type="Fax",
            direction="Outbound",
            date_from=date_from,
            per_page=max(1, min(int(limit), 1000)),
        )
        if not result.get("success"):
            return {"error": result.get("error", "Failed to fetch sent faxes")}

        records = []
        for m in (result.get("messages") or []):
            status = m.get("message_status")
            records.append(
                {
                    "id": m.get("id"),
                    "to": m.get("to_name") or m.get("to_number"),
                    "number": m.get("to_number"),
                    "time": m.get("created_at"),
                    "status": status,
                    "failed": _looks_failed(status),
                    "pages": m.get("fax_page_count"),
                }
            )
        return {"records": records, "total": len(records)}
    except Exception as e:
        return {"error": str(e)}


def match_caller_to_patient(phone_number: str, folder_path: str = None) -> Dict[str, Any]:
    """Resolve a caller number to a known patient/prescriber record."""
    party = _lookup_known_party(phone_number, folder_path=folder_path)
    return {"phone_number": phone_number, **party}


def get_call_analytics(days: int = 7) -> Dict[str, Any]:
    """Basic call analytics: volume, missed rate, avg duration, by day."""
    date_from = (datetime.utcnow() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%dT00:00:00Z")
    data = get_call_log(direction="All", limit=1000, date_from=date_from)
    if data.get("error"):
        return data

    records = data.get("records") or []
    inbound = [r for r in records if (r.get("direction") or "").lower() == "inbound"]
    missed = [r for r in inbound if r.get("missed")]
    durations = [int(r.get("duration") or 0) for r in records if str(r.get("duration") or "").isdigit()]

    by_day: Dict[str, int] = {}
    for r in records:
        ts = _parse_iso(r.get("time"))
        key = (ts.date().isoformat() if ts else str(r.get("time") or "")[:10])
        if not key:
            continue
        by_day[key] = by_day.get(key, 0) + 1

    missed_rate = (len(missed) / len(inbound) * 100.0) if inbound else 0.0
    avg_duration = (sum(durations) / len(durations)) if durations else 0.0
    return {
        "days": int(days),
        "total_calls": len(records),
        "inbound_calls": len(inbound),
        "missed_calls": len(missed),
        "missed_call_rate_pct": round(missed_rate, 2),
        "avg_handle_time_sec": round(avg_duration, 1),
        "volume_by_day": by_day,
    }


def get_extension_status(limit: int = 50) -> Dict[str, Any]:
    """Get extension availability status and DND, when API scope allows."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        ext_url = f"{svc.config.server_url}/restapi/v1.0/account/~/extension"
        ext_resp = requests.get(
            ext_url,
            headers=svc._get_auth_header(),
            params={"perPage": max(1, min(int(limit), 200))},
            timeout=30,
        )
        ext_resp.raise_for_status()
        ext_data = ext_resp.json()

        statuses = []
        for ext in (ext_data.get("records") or []):
            ext_id = ext.get("id")
            presence = {}
            if ext_id:
                try:
                    p_url = f"{svc.config.server_url}/restapi/v1.0/account/~/extension/{ext_id}/presence"
                    p_resp = requests.get(p_url, headers=svc._get_auth_header(), timeout=15)
                    if p_resp.ok:
                        presence = p_resp.json()
                except Exception:
                    pass
            statuses.append(
                {
                    "extension_id": ext_id,
                    "extension_number": ext.get("extensionNumber"),
                    "name": ext.get("name") or (ext.get("contact") or {}).get("firstName"),
                    "status": presence.get("presenceStatus") or "Unknown",
                    "dnd_status": presence.get("dndStatus") or "Unknown",
                    "telephony_status": presence.get("telephonyStatus") or "Unknown",
                }
            )
        return {"records": statuses, "total": len(statuses)}
    except Exception as e:
        return {"error": str(e)}


def get_call_queue_stats() -> Dict[str, Any]:
    """Get call queue depth and wait-time stats for account queues."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        url = f"{svc.config.server_url}/restapi/v1.0/account/~/call-queues"
        response = requests.get(url, headers=svc._get_auth_header(), timeout=30)
        response.raise_for_status()
        data = response.json()

        queues = []
        for q in (data.get("records") or []):
            q_id = q.get("id")
            metrics = {}
            if q_id:
                try:
                    m_url = f"{svc.config.server_url}/restapi/v1.0/account/~/call-queues/{q_id}/presence"
                    m_resp = requests.get(m_url, headers=svc._get_auth_header(), timeout=15)
                    if m_resp.ok:
                        metrics = m_resp.json()
                except Exception:
                    pass
            queues.append(
                {
                    "queue_id": q_id,
                    "name": q.get("name"),
                    "extension_number": q.get("extensionNumber"),
                    "member_count": len(q.get("members") or []),
                    "queue_depth": metrics.get("queueStatus", {}).get("queueLength"),
                    "wait_time_seconds": metrics.get("queueStatus", {}).get("waitTime"),
                }
            )
        return {"records": queues, "total": len(queues)}
    except Exception as e:
        return {"error": str(e)}


def get_sms_response_rate(days: int = 7) -> Dict[str, Any]:
    """Compute SMS response rate and median first-response minutes."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        date_from = (datetime.utcnow() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%dT00:00:00Z")
        result = svc.get_messages(message_type="SMS", date_from=date_from, per_page=1000)
        if not result.get("success"):
            return {"error": result.get("error", "Failed to fetch SMS data")}

        outbound_by_number: Dict[str, List[datetime]] = {}
        inbound_by_number: Dict[str, List[datetime]] = {}

        for msg in (result.get("messages") or []):
            ts = _parse_iso(msg.get("created_at"))
            if not ts:
                continue
            direction = (msg.get("direction") or "").lower()
            from_digits = _digits_only(msg.get("from_number"))
            to_digits = _digits_only(msg.get("to_number"))
            if direction == "outbound" and to_digits:
                outbound_by_number.setdefault(to_digits, []).append(ts)
            elif direction == "inbound" and from_digits:
                inbound_by_number.setdefault(from_digits, []).append(ts)

        for values in outbound_by_number.values():
            values.sort()
        for values in inbound_by_number.values():
            values.sort()

        outbound_total = sum(len(v) for v in outbound_by_number.values())
        responded_numbers = 0
        first_response_minutes: List[float] = []

        for number, outbound_times in outbound_by_number.items():
            inbound_times = inbound_by_number.get(number, [])
            if not inbound_times:
                continue
            matched = False
            for out_ts in outbound_times:
                reply_ts = next((in_ts for in_ts in inbound_times if in_ts > out_ts), None)
                if reply_ts is not None:
                    delta = (reply_ts - out_ts).total_seconds() / 60.0
                    first_response_minutes.append(delta)
                    matched = True
                    break
            if matched:
                responded_numbers += 1

        response_rate = (responded_numbers / len(outbound_by_number) * 100.0) if outbound_by_number else 0.0
        median_minutes = 0.0
        if first_response_minutes:
            first_response_minutes.sort()
            mid = len(first_response_minutes) // 2
            if len(first_response_minutes) % 2:
                median_minutes = first_response_minutes[mid]
            else:
                median_minutes = (first_response_minutes[mid - 1] + first_response_minutes[mid]) / 2.0

        return {
            "days": int(days),
            "outbound_sms_total": outbound_total,
            "distinct_conversations_outbound": len(outbound_by_number),
            "conversations_with_reply": responded_numbers,
            "response_rate_pct": round(response_rate, 2),
            "median_first_response_minutes": round(median_minutes, 1),
        }
    except Exception as e:
        return {"error": str(e)}


def get_communications_monitor(days: int = 30) -> Dict[str, Any]:
    """
    Unified communications monitoring for calls, SMS, and fax (inbound + outbound).
    Default window is 30 days for month-overview monitoring.
    """
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}
        if not svc.is_connected:
            return {"error": "RingCentral is not connected"}

        days = max(1, int(days))
        date_from = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

        # Calls
        calls = get_call_log(direction="All", limit=1000, date_from=date_from)
        if calls.get("error"):
            return calls
        call_rows = calls.get("records") or []
        inbound_calls = sum(1 for r in call_rows if (r.get("direction") or "").lower() == "inbound")
        outbound_calls = sum(1 for r in call_rows if (r.get("direction") or "").lower() == "outbound")
        missed_calls = sum(1 for r in call_rows if r.get("missed"))

        # SMS
        sms_result = svc.get_messages(message_type="SMS", date_from=date_from, per_page=1000)
        if not sms_result.get("success"):
            return {"error": sms_result.get("error", "Failed to fetch SMS data")}
        sms_rows = sms_result.get("messages") or []
        inbound_sms = sum(1 for m in sms_rows if (m.get("direction") or "").lower() == "inbound")
        outbound_sms = sum(1 for m in sms_rows if (m.get("direction") or "").lower() == "outbound")
        unread_sms = sum(
            1
            for m in sms_rows
            if (m.get("direction") or "").lower() == "inbound" and (m.get("read_status") or "") == "Unread"
        )

        # Fax
        fax_result = svc.get_messages(message_type="Fax", date_from=date_from, per_page=1000)
        if not fax_result.get("success"):
            return {"error": fax_result.get("error", "Failed to fetch fax data")}
        fax_rows = fax_result.get("messages") or []
        inbound_fax = sum(1 for m in fax_rows if (m.get("direction") or "").lower() == "inbound")
        outbound_fax = sum(1 for m in fax_rows if (m.get("direction") or "").lower() == "outbound")
        failed_fax = sum(1 for m in fax_rows if _looks_failed(m.get("message_status")))

        # Existing analytics helpers
        call_analytics = get_call_analytics(days=days)
        sms_analytics = get_sms_response_rate(days=days)

        return {
            "days": days,
            "date_from": date_from,
            "calls": {
                "total": len(call_rows),
                "inbound": inbound_calls,
                "outbound": outbound_calls,
                "missed": missed_calls,
            },
            "sms": {
                "total": len(sms_rows),
                "inbound": inbound_sms,
                "outbound": outbound_sms,
                "unread_inbound": unread_sms,
            },
            "fax": {
                "total": len(fax_rows),
                "inbound": inbound_fax,
                "outbound": outbound_fax,
                "failed_outbound": failed_fax,
            },
            "analytics": {
                "calls": call_analytics,
                "sms": sms_analytics,
            },
        }
    except Exception as e:
        return {"error": str(e)}


def get_messages(unread_only: bool = True, limit: int = 20) -> Dict[str, Any]:
    """Get voicemails and SMS messages."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        read_status = "Unread" if unread_only else None
        msgs = svc.get_messages(read_status=read_status, per_page=max(1, min(int(limit), 1000)))
        if not isinstance(msgs, dict):
            return {"error": "Unexpected response from RingCentral service"}

        if not msgs.get("success"):
            return {"error": msgs.get("error", "Failed to fetch messages")}

        records = []
        for m in (msgs.get("messages") or []):
            records.append(
                {
                    "from": m.get("from_name") or m.get("from_number"),
                    "number": m.get("from_number"),
                    "type": m.get("type"),
                    "subject": m.get("subject"),
                    "time": m.get("created_at"),
                    "read": m.get("read_status") == "Read",
                    "direction": m.get("direction"),
                }
            )
        return {"records": records, "total": len(records)}
    except Exception as e:
        return {"error": str(e)}


def send_fax(to_number: str, file_path: str, cover_note: str = "") -> Dict[str, Any]:
    """Send a fax."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        result = svc.send_fax(to_number=to_number, file_path=file_path, cover_text=cover_note)
        if not isinstance(result, dict):
            return {"error": "Unexpected response from RingCentral service"}

        if not result.get("success"):
            return {"error": result.get("error", "Fax failed")}

        return {"success": True, "fax_id": result.get("message_id")}
    except Exception as e:
        return {"error": str(e)}


def check_fax_status(fax_id: str) -> Dict[str, Any]:
    """Check delivery status for a sent fax."""
    try:
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available"}

        status_result = svc.get_fax_status(fax_id)
        if not isinstance(status_result, dict):
            return {"error": "Unexpected response from RingCentral service"}
        if not status_result.get("success"):
            return {"error": status_result.get("error", "Fax status lookup failed"), "fax_id": fax_id}

        status = str(status_result.get("status") or "Unknown")
        status_upper = status.upper()
        failed_markers = ("FAILED", "FAIL", "ERROR", "CANCEL", "REJECT")
        delivered_markers = ("SENT", "DELIVERED", "RECEIVED", "SUCCESS")

        failed = any(marker in status_upper for marker in failed_markers)
        delivered = (not failed) and any(marker in status_upper for marker in delivered_markers)

        return {
            "fax_id": fax_id,
            "status": status,
            "delivered": delivered,
            "failed": failed,
            "pages": status_result.get("fax_pages"),
            "error_code": status_result.get("error_code"),
        }
    except Exception as e:
        return {"error": str(e), "fax_id": fax_id}


def send_refill_fax(
    patient_id: int,
    order_id: int,
    prescriber_fax: str = None,
    folder_path: str = None,
    cover_note: str = "",
    send_now: bool = True,
    request_type: str = "refill",
    include_approved_icd10_list: bool = False,
    invalid_diagnosis_code: str = "",
    allow_refill_in_new_context: bool = False,
) -> Dict[str, Any]:
    """
    Generate the proper DMELogic refill request PDF and send via RingCentral fax.
    Uses the same _generate_refill_request_packet logic from app_legacy.py.
    """
    try:
        import os
        from datetime import datetime as dt
        from dmelogic.settings import load_settings
        from dmelogic.paths import db_dir

        settings = load_settings()
        _ = settings  # keep aligned with app settings load behavior
        db_folder = folder_path or str(db_dir())
        orders_db = os.path.join(db_folder, "orders.db")
        patients_db = os.path.join(db_folder, "patients.db")
        prescribers_db = os.path.join(db_folder, "prescribers.db")

        # Fetch order
        conn = sqlite3.connect(orders_db)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT patient_last_name, patient_first_name, patient_dob,
                   patient_address, patient_phone,
                   prescriber_name, prescriber_npi,
                   icd_code_1, icd_code_2, icd_code_3, icd_code_4, icd_code_5,
                   prescriber_fax,
                   order_status,
                   notes,
                   doctor_directions
            FROM orders WHERE id = ?
            """,
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            conn.close()
            return {"error": f"Order {order_id} not found"}

        (
            last_name,
            first_name,
            patient_dob,
            patient_address,
            patient_phone,
            prescriber_name,
            prescriber_npi,
            icd1,
            icd2,
            icd3,
            icd4,
            icd5,
            order_fax,
            order_status,
            order_notes,
            doctor_directions,
        ) = order

        cur.execute(
            """
            SELECT hcpcs_code, description, qty, refills, day_supply
            FROM order_items WHERE order_id = ?
            """,
            (order_id,),
        )
        items_rows = cur.fetchall()
        conn.close()

        # Fetch patient city/state/zip
        patient_city = patient_state = patient_zip = ""
        try:
            p_conn = sqlite3.connect(patients_db)
            p_cur = p_conn.cursor()
            p_cur.execute(
                """
                SELECT address, city, state, zip FROM patients
                WHERE UPPER(last_name)=UPPER(?) AND UPPER(first_name)=UPPER(?) AND dob=?
                """,
                (last_name, first_name, patient_dob),
            )
            p_row = p_cur.fetchone()
            p_conn.close()
            if p_row:
                if not patient_address:
                    patient_address = p_row[0] or ""
                patient_city = p_row[1] or ""
                patient_state = p_row[2] or ""
                patient_zip = p_row[3] or ""
        except Exception:
            pass

        # Fetch prescriber fax fallback
        prescriber_title = ""
        if not prescriber_fax:
            prescriber_fax = order_fax or ""
        try:
            pr_conn = sqlite3.connect(prescribers_db)
            pr_cur = pr_conn.cursor()
            pr_cur.execute("SELECT title, fax FROM prescribers WHERE npi_number=?", (prescriber_npi,))
            pr_row = pr_cur.fetchone()
            pr_conn.close()
            if pr_row:
                prescriber_title = pr_row[0] or ""
                if not prescriber_fax and pr_row[1]:
                    prescriber_fax = pr_row[1]
        except Exception:
            pass

        if send_now and not prescriber_fax:
            return {"error": "No fax number found for prescriber"}

        # Build items list
        request_items = []
        for hcpcs, desc, qty, refills, day_supply in items_rows:
            try:
                refills_remaining = int(refills) if refills else 0
            except (ValueError, TypeError):
                refills_remaining = 0
            request_items.append((desc, qty, hcpcs, refills_remaining))

        dx_codes = [dx for dx in [icd1, icd2, icd3, icd4, icd5] if dx and str(dx).strip()]

        # Generate PDF using ReportLab (same as app_legacy.py)
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.units import inch

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(downloads, exist_ok=True)
        request_mode = str(request_type or "refill").strip().lower()
        is_new_request = request_mode in {"new", "new_rx", "new-rx", "new_prescription", "new_prescription_request"}

        if (not is_new_request) and (not allow_refill_in_new_context):
            if _looks_like_new_rx_request_context(order_status, order_notes, doctor_directions, items_rows):
                return {
                    "error": (
                        "Order appears to be a NEW prescription request context. "
                        "Use send_new_rx_request_fax/get_new_rx_request_form_path instead of send_refill_fax."
                    ),
                    "order_id": order_id,
                    "patient_id": patient_id,
                }

        file_prefix = "NewPrescriptionRequest" if is_new_request else "RefillRequest"
        pdf_path = os.path.join(downloads, f"{file_prefix}_{(last_name or 'Patient').upper()}_{timestamp}.pdf")

        c = rl_canvas.Canvas(pdf_path, pagesize=letter)
        width, height = letter

        # Logo
        logo_path = r"C:\FAX_MANAGER_PRO\assets\logo.jpg"
        y = height - 0.5 * inch
        try:
            if os.path.exists(logo_path):
                logo_height = 0.75 * inch
                c.drawImage(
                    logo_path,
                    0.5 * inch,
                    y - logo_height,
                    width=1.8 * inch,
                    height=logo_height,
                    preserveAspectRatio=True,
                    mask="auto",
                )
        except Exception:
            pass

        # Header
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(width - 0.5 * inch, y - 0.1 * inch, "1st Aid Pharmacy & Surgical Supplies")
        c.setFont("Helvetica", 9)
        c.drawRightString(width - 0.5 * inch, y - 0.25 * inch, "23 W. Fordham Road, Bronx, NY 10468")
        c.drawRightString(width - 0.5 * inch, y - 0.38 * inch, "Pharmacy Tel: 718-450-3555")
        c.drawRightString(width - 0.5 * inch, y - 0.51 * inch, "DME Tel: 347-647-2347  |  DME Fax: 347-947-8102")

        # Divider: keep this below the logo so it never crosses the image.
        line_y = y - 0.95 * inch
        c.setLineWidth(1)
        c.line(0.5 * inch, line_y, width - 0.5 * inch, line_y)

        # To/From block
        text_y = line_y - 0.25 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(0.5 * inch, text_y, "TO:")
        c.setFont("Helvetica", 10)
        prescriber_display = f"{prescriber_title} {prescriber_name}".strip() if prescriber_title else (prescriber_name or "")
        c.drawString(1.0 * inch, text_y, prescriber_display)

        text_y -= 0.2 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(0.5 * inch, text_y, "FROM:")
        c.setFont("Helvetica", 10)
        c.drawString(1.0 * inch, text_y, "Melvin Ramirez")
        text_y -= 0.15 * inch
        c.drawString(1.0 * inch, text_y, "DME Operations Manager")

        # Subject
        text_y -= 0.35 * inch
        c.line(0.5 * inch, text_y + 0.15 * inch, width - 0.5 * inch, text_y + 0.15 * inch)
        text_y -= 0.1 * inch
        c.setFont("Helvetica-Bold", 14)
        if is_new_request:
            c.drawString(0.5 * inch, text_y, f"Subject: New Prescription Request for: {(first_name or '').upper()} {(last_name or '').upper()}")
        else:
            c.drawString(0.5 * inch, text_y, f"Subject: REFILL REQUEST for: {(last_name or '').upper()}, {(first_name or '').upper()}")
        text_y -= 0.1 * inch
        c.line(0.5 * inch, text_y, width - 0.5 * inch, text_y)

        # Patient info
        text_y -= 0.25 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(0.5 * inch, text_y, f"Patient: {(last_name or '').upper()}, {(first_name or '').upper()}")
        text_y -= 0.18 * inch
        c.setFont("Helvetica", 10)
        dob_fmt = patient_dob or "N/A"
        try:
            dob_fmt = dt.strptime(str(patient_dob), "%Y-%m-%d").strftime("%m/%d/%Y")
        except Exception:
            pass
        c.drawString(0.5 * inch, text_y, f"DOB:      {dob_fmt}")
        text_y -= 0.18 * inch
        addr = patient_address or "N/A"
        if patient_city:
            addr += f", {patient_city}, {patient_state} {patient_zip}".strip()
        c.drawString(0.5 * inch, text_y, f"Address: {addr}")
        text_y -= 0.18 * inch
        c.drawString(0.5 * inch, text_y, f"Phone:    {patient_phone or 'N/A'}")

        # Body text
        text_y -= 0.35 * inch
        c.line(0.5 * inch, text_y + 0.1 * inch, width - 0.5 * inch, text_y + 0.1 * inch)
        text_y -= 0.15 * inch
        c.setFont("Helvetica", 10)
        if is_new_request:
            body = (
                "The patient (or the patient's parent/guardian) has requested that we obtain prescriptions for the items\n"
                "listed below and has provided your contact information so that we may reach out on their behalf."
            )
        else:
            body = (
                "The above patient has been receiving durable medical equipment (DME) supplies from our pharmacy.\n"
                "We are reaching out because they have exhausted their refills and require new prescriptions to continue\n"
                "receiving these necessary medical supplies."
            )
        for line in body.split("\n"):
            c.drawString(0.5 * inch, text_y, line)
            text_y -= 0.18 * inch

        text_y -= 0.1 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(0.5 * inch, text_y, "If you have any questions, please contact us at 347-647-2347.")
        text_y -= 0.18 * inch
        c.setFont("Helvetica", 10)
        c.drawString(0.5 * inch, text_y, "If you need to reach the patient directly, their contact information is listed above.")
        text_y -= 0.25 * inch
        if is_new_request:
            c.drawString(0.5 * inch, text_y, "Below is a list of the DME items requested along with estimated monthly quantities.")
        else:
            c.drawString(0.5 * inch, text_y, "Below is a list of the DME items that require new prescriptions along with the current quantities being supplied.")
        text_y -= 0.18 * inch
        c.setFont("Helvetica-Bold", 10)
        if is_new_request:
            c.drawString(0.5 * inch, text_y, "Please issue new prescriptions or completed form at your earliest convenience.")
        else:
            c.drawString(0.5 * inch, text_y, "Please issue new prescriptions at your earliest convenience.")

        # MD approval instructions (refill form only)
        if not is_new_request:
            text_y -= 0.24 * inch
            instruction_title_y = text_y
            instruction_line_height = 0.22 * inch
            instruction_text_x = 0.6 * inch
            instruction_title_gap = 0.24 * inch
            instruction_box_top_padding = 0.16 * inch
            instruction_box_bottom_padding = 0.14 * inch
            instruction_lines = [
                ("Helvetica-Bold", 9, "Please send new prescriptions via EMR or fill, sign and fax form back to approve refills."),
                ("Helvetica", 9, "1. Check the box next to each item you are approving for refill."),
                ("Helvetica", 9, "2. Write in how many refills you are ordering next to each item."),
                ("Helvetica", 9, "3. Sign and date below, then fax this completed form back to us at 347-947-8102."),
                ("Helvetica", 9, "   This signed fax will serve as the new prescription."),
            ]
            instruction_box_top = instruction_title_y + instruction_box_top_padding
            instruction_box_bottom = (
                instruction_title_y
                - instruction_title_gap
                - (len(instruction_lines) * instruction_line_height)
                - instruction_box_bottom_padding
            )
            c.setLineWidth(0.5)
            c.rect(0.5 * inch, instruction_box_bottom, width - 1.0 * inch, instruction_box_top - instruction_box_bottom)

            c.setFont("Helvetica-Bold", 10)
            c.drawString(instruction_text_x, text_y, "INSTRUCTIONS FOR PRESCRIBER:")
            text_y -= instruction_title_gap
            for font_name, font_size, ln in instruction_lines:
                c.setFont(font_name, font_size)
                c.drawString(instruction_text_x, text_y, ln)
                text_y -= instruction_line_height

        # Items table
        if not is_new_request:
            # Keep a consistent visual gap below the instruction box.
            text_y = instruction_box_bottom - 0.22 * inch
        else:
            text_y -= 0.2 * inch
        c.setFont("Helvetica-Bold", 10)
        if is_new_request:
            c.drawString(0.5 * inch, text_y, "REQUESTED ITEMS (Monthly Usage):")
        else:
            c.drawString(0.5 * inch, text_y, "ITEMS REQUIRING NEW PRESCRIPTIONS:")
        text_y -= 0.05 * inch
        c.line(0.5 * inch, text_y, width - 0.5 * inch, text_y)
        text_y -= 0.2 * inch
        refill_row_height = 0.26 * inch

        for item_num, (desc, qty, hcpcs, refills_rem) in enumerate(request_items, start=1):
            c.setFont("Helvetica", 10)
            if is_new_request:
                c.drawString(0.6 * inch, text_y, f"{item_num}.  {desc}")
                c.drawRightString(width - 0.5 * inch, text_y, f"Qty/Month: {qty}")
                text_y -= 0.18 * inch
            else:
                # Checkbox placeholder (square), item number, description
                c.rect(0.5 * inch, text_y - 0.01 * inch, 0.13 * inch, 0.13 * inch)
                c.drawString(0.7 * inch, text_y, f"{item_num}.  {desc}")
                c.drawRightString(width - 0.5 * inch, text_y,
                                  f"Qty: {qty}  |  Refills Left: {refills_rem}  |  Refills to order: __________")
                text_y -= refill_row_height

        # "Approve ALL" shortcut row (refill form only)
        if not is_new_request and len(request_items) > 1:
            text_y -= 0.05 * inch
            c.setLineWidth(0.25)
            c.setDash(3, 3)
            c.line(0.5 * inch, text_y + 0.14 * inch, width - 0.5 * inch, text_y + 0.14 * inch)
            c.setDash()
            c.setLineWidth(0.5)
            c.rect(0.5 * inch, text_y - 0.01 * inch, 0.13 * inch, 0.13 * inch)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(0.7 * inch, text_y, "Approve ALL items listed above")
            c.drawRightString(width - 0.5 * inch, text_y, "Refills (all items): __________")
            text_y -= refill_row_height

        # Diagnosis codes (only on refill form)
        if (not is_new_request) and dx_codes:
            text_y -= 0.15 * inch
            c.setFont("Helvetica-Bold", 10)
            c.drawString(0.5 * inch, text_y, "Current Diagnosis Codes on File:")
            text_y -= 0.18 * inch
            c.setFont("Helvetica", 10)
            c.drawString(0.6 * inch, text_y, "  ".join(dx_codes))
            text_y -= 0.25 * inch

        # MD approval signature block (refill form only)
        if not is_new_request:
            text_y -= 0.15 * inch
            c.line(0.5 * inch, text_y, width - 0.5 * inch, text_y)
            text_y -= 0.18 * inch
            c.setFont("Helvetica-Bold", 10)
            c.drawString(0.5 * inch, text_y, "PRESCRIBER APPROVAL — please sign below and fax back to 347-947-8102")
            text_y -= 0.35 * inch
            # Signature line (left 3/4)
            sig_end = 0.5 * inch + 4.5 * inch
            c.line(0.5 * inch, text_y, sig_end, text_y)
            # Date line (right 1/4)
            c.line(sig_end + 0.2 * inch, text_y, width - 0.5 * inch, text_y)
            text_y -= 0.14 * inch
            c.setFont("Helvetica", 8)
            c.drawString(0.5 * inch, text_y, "Prescriber Signature")
            c.drawString(sig_end + 0.2 * inch, text_y, "Date (MM/DD/YYYY)")
            text_y -= 0.28 * inch
            # Printed name / NPI line (full width)
            c.line(0.5 * inch, text_y, width - 0.5 * inch, text_y)
            text_y -= 0.14 * inch
            c.drawString(0.5 * inch, text_y, "Printed Name & NPI")
            text_y -= 0.3 * inch

        # Sign off
        text_y -= 0.05 * inch
        c.line(0.5 * inch, text_y, width - 0.5 * inch, text_y)
        text_y -= 0.2 * inch
        c.setFont("Helvetica", 10)
        c.drawString(0.5 * inch, text_y, "Thank you,")
        text_y -= 0.18 * inch
        c.setFont("Helvetica-Bold", 10)
        c.drawString(0.5 * inch, text_y, "DME Team")

        # Disclaimer
        text_y -= 0.35 * inch
        c.setFont("Helvetica-Bold", 9)
        c.drawString(0.5 * inch, text_y, "**** We are 1st Aid Pharmacy & Surgical Supplies located on Fordham Road.")
        text_y -= 0.15 * inch
        c.setFont("Helvetica", 9)
        c.drawString(0.5 * inch, text_y, "Please note: there is a different pharmacy named First Aid Pharmacy on Tremont Avenue.")
        text_y -= 0.15 * inch
        c.drawString(0.5 * inch, text_y, "To avoid delays, please ensure all prescriptions are sent to 1st Aid Pharmacy on Fordham Road. Thank you")

        guidance_requested = bool(include_approved_icd10_list)
        invalid_code = str(invalid_diagnosis_code or "").strip().upper()
        if invalid_code in {"R32"}:
            guidance_requested = True

        if guidance_requested:
            _draw_icd10_guidance_page(c, width, height, invalid_code=invalid_code or "R32")

        c.save()

        # If caller only needs the generated DMELogic form path, stop here.
        if not send_now:
            return {
                "success": True,
                "generated_only": True,
                "request_type": "new" if is_new_request else "refill",
                "pdf_path": pdf_path,
                "fax_to": prescriber_fax,
                "prescriber": prescriber_display,
            }

        # Send via RingCentral
        svc = _get_rc_service()
        if not svc:
            return {"error": "RingCentral service not available", "pdf_path": pdf_path}

        result = svc.send_fax(to_number=prescriber_fax, file_path=pdf_path)
        if not isinstance(result, dict):
            return {"error": "Unexpected response from RingCentral service", "pdf_path": pdf_path}
        if not result.get("success"):
            return {"error": result.get("error", "Fax failed"), "pdf_path": pdf_path}

        return {
            "success": True,
            "request_type": "new" if is_new_request else "refill",
            "pdf_path": pdf_path,
            "fax_to": prescriber_fax,
            "prescriber": prescriber_display,
            "fax_id": result.get("message_id"),
        }

    except Exception as e:
        log.error(f"send_refill_fax error: {e}")
        return {"error": str(e)}


def send_new_rx_request_fax(
    patient_id: int,
    order_id: int,
    prescriber_fax: str = None,
    folder_path: str = None,
    cover_note: str = "",
    send_now: bool = True,
    include_approved_icd10_list: bool = True,
    invalid_diagnosis_code: str = "R32",
) -> Dict[str, Any]:
    """Generate/send the New Prescription Request fax format (not refill format)."""
    return send_refill_fax(
        patient_id=patient_id,
        order_id=order_id,
        prescriber_fax=prescriber_fax,
        folder_path=folder_path,
        cover_note=cover_note,
        send_now=send_now,
        request_type="new",
        include_approved_icd10_list=bool(include_approved_icd10_list),
        invalid_diagnosis_code=invalid_diagnosis_code,
    )
