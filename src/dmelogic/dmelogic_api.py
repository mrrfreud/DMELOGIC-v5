"""
DMELogic REST API  v2.0
=======================
Complete API surface for Nova agent — every queryable domain in DMELogic.

Databases covered:
  patients.db       — patients, insurance
  orders.db         — orders, order_items, order_templates, pending_approvals
  prescribers.db    — prescribers
  inventory.db      — inventory / stock
  billing.db        — claims, fee_schedule, reconciliation
  insurance_names.db— payer list
  notes.db          — sticky notes
  communications.db — patient communications

Run:
    python dmelogic_api.py

Environment variables:
    DMELOGIC_API_KEY   (default: dev-key-change-me)
    DMELOGIC_FOLDER    (default: auto-discovered from settings.json)
    DMELOGIC_API_HOST  (default: 127.0.0.1)
    DMELOGIC_API_PORT  (default: 8400)
"""

from __future__ import annotations

import os, sys, json, sqlite3, logging, uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

# ── FastAPI ────────────────────────────────────────────────────────────────
try:
    from fastapi import FastAPI, HTTPException, Depends, Query, status
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except ImportError:
    sys.exit("Run: pip install fastapi uvicorn")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── DMELogic path ──────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── Config ─────────────────────────────────────────────────────────────────
def _env_or_default(name: str, default: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    placeholders = {
        "your-key",
        "your-key-here",
        "your-strong-key",
        "your-strong-key-here",
        "changeme",
        "change-me",
    }
    if not value or value.lower() in placeholders:
        return default
    return value


API_KEY     = _env_or_default("DMELOGIC_API_KEY", "dev-key-change-me")
API_HOST    = os.getenv("DMELOGIC_API_HOST", "127.0.0.1")
API_PORT    = int(os.getenv("DMELOGIC_API_PORT", "8400"))

# Folder path: env override → settings.json → auto-discover
def _resolve_folder() -> Optional[str]:
    env = os.getenv("DMELOGIC_FOLDER")
    if env:
        return env
    try:
        from dmelogic.paths import db_dir
        return str(db_dir())
    except Exception:
        return None

FOLDER_PATH = _resolve_folder()
log = logging.getLogger("dmelogic_api")

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="DMELogic API",
    description="Complete REST interface to DMELogic — patients, orders, billing, inventory, refills, claims.",
    version="2.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Auth ───────────────────────────────────────────────────────────────────
_bearer = HTTPBearer()
def require_api_key(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if creds.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return creds.credentials

# ── DB helpers ─────────────────────────────────────────────────────────────
def _conn(db_name: str) -> sqlite3.Connection:
    try:
        from dmelogic.db.base import get_connection
        conn = get_connection(db_name, folder_path=FOLDER_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        # Fallback: direct path
        if FOLDER_PATH:
            path = os.path.join(FOLDER_PATH, db_name)
        else:
            path = db_name
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

def _rows(db_name: str, sql: str, params: tuple = ()) -> List[Dict]:
    try:
        conn = _conn(db_name)
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Query error [{db_name}]: {e}")
        return []

def _one(db_name: str, sql: str, params: tuple = ()) -> Optional[Dict]:
    rows = _rows(db_name, sql, params)
    return rows[0] if rows else None

def _exec(db_name: str, sql: str, params: tuple = ()) -> bool:
    try:
        conn = _conn(db_name)
        conn.execute(sql, params)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log.error(f"Exec error [{db_name}]: {e}")
        return False

def _serialize(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return obj

def _audit(action: str, resource_id: Any, details: str = ""):
    try:
        from dmelogic.security.audit import audit_log
        audit_log(action, "api", resource_id, details)
    except Exception:
        pass


def _normalize_patient_field_aliases(payload: Dict[str, Any], cols: set[str]) -> Dict[str, Any]:
    """Map patient field aliases to actual DB column names across legacy/new schemas."""
    normalized = dict(payload or {})

    # Some databases store ZIP as `zip`, others as `zip_code`.
    if "zip_code" in normalized and "zip_code" not in cols and "zip" in cols:
        normalized["zip"] = normalized.pop("zip_code")
    elif "zip" in normalized and "zip" not in cols and "zip_code" in cols:
        normalized["zip_code"] = normalized.pop("zip")

    return normalized

# ── Pydantic models ────────────────────────────────────────────────────────
class OrderStatusUpdate(BaseModel):
    new_status: str
    updated_by: str = "Nova"
    notes: Optional[str] = None
    paid_date: Optional[str] = None


class OrderPrescriberContactUpdate(BaseModel):
    prescriber_phone: Optional[str] = None
    prescriber_fax: Optional[str] = None
    updated_by: str = "Nova"
    notes: Optional[str] = None


class OrderPatientLinkUpdate(BaseModel):
    patient_id: int
    updated_by: str = "Nova"
    notes: Optional[str] = None


class OrderItemRefillsUpdate(BaseModel):
    refills: int = Field(..., ge=0)
    updated_by: str = "Nova"
    notes: Optional[str] = None


class OrderDeleteRequest(BaseModel):
    deleted_by: str = "Nova"
    reason: Optional[str] = None
    preserve_audit_trail: bool = True


class OrderAttachmentItem(BaseModel):
    source_path: str
    original_name: Optional[str] = None
    description: Optional[str] = None


class OrderAttachmentRequest(BaseModel):
    patient_id: Optional[int] = None
    attachments: List[OrderAttachmentItem]
    document_type: Literal["rx", "delivery_ticket"] = "rx"
    updated_by: str = "Nova"
    notes: Optional[str] = None


class PatientCreateRequest(BaseModel):
    first_name: str
    last_name: str
    dob: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    secondary_contact: Optional[str] = None
    primary_insurance: Optional[str] = None
    primary_insurance_id: Optional[str] = None
    policy_number: Optional[str] = None
    group_number: Optional[str] = None
    secondary_insurance: Optional[str] = None
    secondary_insurance_id: Optional[str] = None
    created_by: str = "Nova"
    notes: Optional[str] = None


class PatientUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    dob: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    secondary_contact: Optional[str] = None
    primary_insurance: Optional[str] = None
    primary_insurance_id: Optional[str] = None
    policy_number: Optional[str] = None
    group_number: Optional[str] = None
    secondary_insurance: Optional[str] = None
    secondary_insurance_id: Optional[str] = None
    updated_by: str = "Nova"
    notes: Optional[str] = None

class RefillCheckRequest(BaseModel):
    last_filled: str
    day_supply: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1)
    insurance_type: str = "Commercial"
    max_quantity_per_month: int = 0

class ApprovalAction(BaseModel):
    action: str = Field(..., description="approve or reject")
    by: str = "Nova"
    reason: Optional[str] = None

class NoteCreate(BaseModel):
    title: str = ""
    body: str
    color: str = "#FFF7A8"
    pinned: bool = False


class RemittanceRequest(BaseModel):
    pdf_path: str


class OpenReconciliationUIRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    insurance: Optional[str] = None
    requested_by: str = "Nova"
    notes: Optional[str] = None


class ReconciliationPaidItem(BaseModel):
    order_id: int
    paid: bool
    paid_date: Optional[str] = None


class ReconciliationPaidUpdateRequest(BaseModel):
    updates: List[ReconciliationPaidItem]
    updated_by: str = "Nova"
    notes: Optional[str] = None

# ══════════════════════════════════════════════════════════════════════════
#  HEALTH
# ══════════════════════════════════════════════════════════════════════════
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": "dmelogic_api", "version": "2.0.0",
            "timestamp": datetime.utcnow().isoformat(), "db_folder": FOLDER_PATH}

@app.get("/system/db-status", tags=["System"])
def db_status(_key=Depends(require_api_key)):
    """Check which databases are accessible and their sizes."""
    dbs = ["patients.db","orders.db","prescribers.db","inventory.db",
           "billing.db","insurance_names.db","notes.db","communications.db"]
    result = {}
    for db in dbs:
        try:
            path = os.path.join(FOLDER_PATH or ".", db) if FOLDER_PATH else db
            if os.path.exists(path):
                size = os.path.getsize(path)
                conn = sqlite3.connect(path)
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                conn.close()
                result[db] = {"accessible": True, "size_kb": round(size/1024,1), "tables": tables}
            else:
                result[db] = {"accessible": False, "reason": "File not found"}
        except Exception as e:
            result[db] = {"accessible": False, "reason": str(e)}
    return result

# ══════════════════════════════════════════════════════════════════════════
#  PATIENTS
# ══════════════════════════════════════════════════════════════════════════
@app.get("/patients", tags=["Patients"])
def list_patients(_key=Depends(require_api_key)):
    """All patients ordered by last name."""
    return _rows("patients.db", "SELECT * FROM patients ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE")

@app.get("/patients/search", tags=["Patients"])
def search_patients(
    q: str = Query(..., description="Name, partial name, or 'last first'"),
    _key=Depends(require_api_key)
):
    """Search patients by name (LIKE, handles partial, multi-word)."""
    parts = [p.strip() for p in q.replace(',', ' ').split() if p.strip()]
    if len(parts) >= 2:
        l, f = f"%{parts[0]}%", f"%{parts[1]}%"
        return _rows("patients.db", """
            SELECT * FROM patients
            WHERE (UPPER(last_name) LIKE UPPER(?) AND UPPER(first_name) LIKE UPPER(?))
               OR (UPPER(last_name) LIKE UPPER(?) AND UPPER(first_name) LIKE UPPER(?))
            ORDER BY last_name, first_name LIMIT 50
        """, (l, f, f, l))
    like = f"%{parts[0] if parts else q}%"
    return _rows("patients.db", """
        SELECT * FROM patients
        WHERE UPPER(last_name) LIKE UPPER(?)
           OR UPPER(first_name) LIKE UPPER(?)
           OR UPPER(TRIM(last_name || ' ' || first_name)) LIKE UPPER(?)
        ORDER BY last_name, first_name LIMIT 50
    """, (like, like, like))

@app.get("/patients/search/phone", tags=["Patients"])
def search_by_phone(phone: str = Query(...), _key=Depends(require_api_key)):
    """Find patients by phone number (format-insensitive)."""
    digits = ''.join(c for c in phone if c.isdigit())
    if not digits:
        return []
    def strip_expr(col):
        return f"REPLACE(REPLACE(REPLACE(REPLACE(COALESCE({col},''),'-',''),'(',''),')',''),' ','')"
    return _rows("patients.db", f"""
        SELECT * FROM patients
        WHERE {strip_expr('phone')} = ?
           OR {strip_expr('secondary_contact')} = ?
        ORDER BY last_name, first_name LIMIT 20
    """, (digits, digits))

@app.get("/patients/{patient_id}", tags=["Patients"])
def get_patient(patient_id: int, _key=Depends(require_api_key)):
    p = _one("patients.db", "SELECT * FROM patients WHERE id = ?", (patient_id,))
    if not p:
        raise HTTPException(404, f"Patient {patient_id} not found")
    return p


@app.post("/patients", tags=["Patients"])
def create_patient(body: PatientCreateRequest, _key=Depends(require_api_key)):
    """Create a patient (or return existing match by name + DOB)."""
    first_name = (body.first_name or "").strip()
    last_name = (body.last_name or "").strip()
    dob = (body.dob or "").strip() or None
    if not first_name or not last_name:
        raise HTTPException(422, "first_name and last_name are required")

    existing = None
    if dob:
        existing = _one(
            "patients.db",
            """
            SELECT id FROM patients
            WHERE UPPER(last_name)=UPPER(?) AND UPPER(first_name)=UPPER(?) AND dob=?
            LIMIT 1
            """,
            (last_name, first_name, dob),
        )
    else:
        existing = _one(
            "patients.db",
            """
            SELECT id FROM patients
            WHERE UPPER(last_name)=UPPER(?) AND UPPER(first_name)=UPPER(?)
            LIMIT 1
            """,
            (last_name, first_name),
        )

    try:
        from dmelogic.db.patients import create_or_get_patient
        patient_id = create_or_get_patient(
            last_name=last_name,
            first_name=first_name,
            dob=dob,
            phone=(body.phone.strip() if isinstance(body.phone, str) else body.phone),
            address=(body.address.strip() if isinstance(body.address, str) else body.address),
            primary_insurance=(body.primary_insurance.strip() if isinstance(body.primary_insurance, str) else body.primary_insurance),
            primary_insurance_id=(body.primary_insurance_id.strip() if isinstance(body.primary_insurance_id, str) else body.primary_insurance_id),
            folder_path=FOLDER_PATH,
        )
    except Exception as e:
        log.error(f"create_patient error: {e}")
        raise HTTPException(500, "Failed to create patient")

    if not patient_id:
        raise HTTPException(500, "Failed to create patient")

    extra_fields = {
        "city": body.city,
        "state": body.state,
        "zip_code": body.zip_code,
        "secondary_contact": body.secondary_contact,
        "policy_number": body.policy_number,
        "group_number": body.group_number,
        "secondary_insurance": body.secondary_insurance,
        "secondary_insurance_id": body.secondary_insurance_id,
    }
    extra_fields = {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in extra_fields.items()
        if v is not None
    }

    if extra_fields:
        conn = _conn("patients.db")
        try:
            cur = conn.cursor()
            cols = {str(r[1]) for r in cur.execute("PRAGMA table_info(patients)").fetchall()}
            extra_fields = _normalize_patient_field_aliases(extra_fields, cols)
            valid = {k: v for k, v in extra_fields.items() if k in cols}
            if valid:
                set_parts = [f"{k}=?" for k in valid.keys()]
                values = list(valid.values())
                if "updated_date" in cols:
                    set_parts.append("updated_date=?")
                    values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                values.append(patient_id)
                cur.execute(f"UPDATE patients SET {', '.join(set_parts)} WHERE id = ?", tuple(values))
                conn.commit()
        finally:
            conn.close()

    patient = _one("patients.db", "SELECT * FROM patients WHERE id = ?", (patient_id,))
    _audit(
        "patient.create",
        patient_id,
        f"by {body.created_by}" + (f" | {body.notes}" if body.notes else "")
    )
    return {
        "patient_id": patient_id,
        "created": not bool(existing),
        "patient": patient,
        "created_by": body.created_by,
        "created_at": datetime.utcnow().isoformat(),
    }


@app.patch("/patients/{patient_id}", tags=["Patients"])
def update_patient(patient_id: int, body: PatientUpdateRequest, _key=Depends(require_api_key)):
    """Update patient demographics and insurance fields."""
    current = _one("patients.db", "SELECT * FROM patients WHERE id = ?", (patient_id,))
    if not current:
        raise HTTPException(404, f"Patient {patient_id} not found")

    payload = body.model_dump(exclude_none=True)
    payload.pop("updated_by", None)
    payload.pop("notes", None)
    payload = {
        k: (v.strip() if isinstance(v, str) else v)
        for k, v in payload.items()
    }
    if not payload:
        raise HTTPException(422, "Provide at least one field to update")

    conn = _conn("patients.db")
    try:
        cur = conn.cursor()
        cols = {str(r[1]) for r in cur.execute("PRAGMA table_info(patients)").fetchall()}
        payload = _normalize_patient_field_aliases(payload, cols)
        valid = {k: v for k, v in payload.items() if k in cols}
        if not valid:
            raise HTTPException(422, "No updatable fields were provided")

        set_parts = [f"{k}=?" for k in valid.keys()]
        values = list(valid.values())
        if "updated_date" in cols:
            set_parts.append("updated_date=?")
            values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        values.append(patient_id)
        cur.execute(f"UPDATE patients SET {', '.join(set_parts)} WHERE id = ?", tuple(values))
        conn.commit()
    finally:
        conn.close()

    patient = _one("patients.db", "SELECT * FROM patients WHERE id = ?", (patient_id,))
    _audit(
        "patient.update",
        patient_id,
        f"by {body.updated_by}" + (f" | {body.notes}" if body.notes else ""),
    )
    return {
        "patient_id": patient_id,
        "patient": patient,
        "updated_by": body.updated_by,
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.post("/orders/{order_id}/attachments", tags=["Orders"])
def attach_order_documents(order_id: int, body: OrderAttachmentRequest, _key=Depends(require_api_key)):
    """Attach existing files to an order and link them to patient_documents."""
    order = _one(
        "orders.db",
        "SELECT id, patient_id, attached_rx_files, attached_signed_ticket_files FROM orders WHERE id = ?",
        (order_id,),
    )
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")

    def _split_refs(value: str) -> List[str]:
        refs: List[str] = []
        for part in str(value or "").replace(";", "\n").splitlines():
            ref = part.strip()
            if ref:
                refs.append(ref)
        return refs

    def _pick_existing_path(raw_path: str) -> Optional[str]:
        candidate = Path(str(raw_path or "").strip().strip('"').strip("'"))
        if not str(candidate):
            return None
        if candidate.exists():
            return str(candidate)
        try:
            from dmelogic.paths import resolve_document_path
            resolved = resolve_document_path(str(candidate))
            if resolved.exists():
                return str(resolved)
        except Exception:
            pass
        return str(candidate)

    attachments = body.attachments or []
    if not attachments:
        raise HTTPException(422, "attachments must be a non-empty list")

    document_type = (body.document_type or "rx").strip().lower()
    if document_type not in {"rx", "delivery_ticket"}:
        raise HTTPException(422, "document_type must be 'rx' or 'delivery_ticket'")
    order_attachment_col = "attached_signed_ticket_files" if document_type == "delivery_ticket" else "attached_rx_files"

    patient_id = body.patient_id or order.get("patient_id")
    saved_paths: List[str] = []
    saved_names: List[str] = []

    for idx, item in enumerate(attachments, start=1):
        source_path = _pick_existing_path(item.source_path)
        if not source_path:
            continue
        source = Path(source_path)
        if not source.exists():
            raise HTTPException(404, f"Attachment not found: {item.source_path}")

        saved_paths.append(str(source))
        saved_names.append(item.original_name or source.name or f"attachment_{idx}")

    if not saved_paths:
        raise HTTPException(404, "No valid attachments were found")

    try:
        conn = _conn("orders.db")
        cur = conn.cursor()
        existing_refs = _split_refs(order.get(order_attachment_col) or "")
        merged_refs = list(existing_refs)
        seen = {ref.lower() for ref in merged_refs}
        for path in saved_paths:
            if path.lower() not in seen:
                merged_refs.append(path)
                seen.add(path.lower())

        cur.execute(
            f"UPDATE orders SET {order_attachment_col} = ?, updated_date = CURRENT_TIMESTAMP WHERE id = ?",
            (";".join(merged_refs), order_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"attach_order_documents order update error: {e}")
        raise HTTPException(500, "Failed to update order attachments")

    patient_document_links: List[Dict[str, Any]] = []
    if patient_id:
        try:
            patient_db = _conn("patients.db")
            cur = patient_db.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS patient_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER NOT NULL,
                    description TEXT,
                    original_name TEXT,
                    stored_path TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            order_num = f"ORD-{order_id:03d}"
            for path, original_name, item in zip(saved_paths, saved_names, attachments):
                cur.execute(
                    "SELECT id FROM patient_documents WHERE patient_id = ? AND stored_path = ?",
                    (int(patient_id), path),
                )
                if cur.fetchone():
                    continue
                description = (item.description or f"From {order_num}").strip()
                cur.execute(
                    "INSERT INTO patient_documents (patient_id, description, original_name, stored_path) VALUES (?, ?, ?, ?)",
                    (int(patient_id), description, original_name, path),
                )
                patient_document_links.append({
                    "patient_id": int(patient_id),
                    "description": description,
                    "original_name": original_name,
                    "stored_path": path,
                })
            patient_db.commit()
            patient_db.close()
        except Exception as e:
            log.warning(f"attach_order_documents patient link warning: {e}")

    _audit(
        "order.attachments",
        order_id,
        f"by {body.updated_by}" + (f" | {body.notes}" if body.notes else ""),
    )

    return {
        "success": True,
        "order_id": order_id,
        "document_type": document_type,
        "order_attachment_column": order_attachment_col,
        "attached_count": len(saved_paths),
        "attached_paths": saved_paths,
        "patient_id": int(patient_id) if patient_id else None,
        "patient_documents_linked": patient_document_links,
        "updated_by": body.updated_by,
        "updated_at": datetime.utcnow().isoformat(),
    }

@app.get("/patients/{patient_id}/orders", tags=["Patients"])
def get_patient_orders(patient_id: int, _key=Depends(require_api_key)):
    """Full order history for a patient."""
    try:
        from dmelogic.db.orders import get_orders_for_patient
        patient = _one("patients.db", "SELECT * FROM patients WHERE id=?", (patient_id,))
        if not patient:
            raise HTTPException(404, f"Patient {patient_id} not found")
        return get_orders_for_patient(
            patient_id=patient_id, folder_path=FOLDER_PATH,
            fallback_last_name=patient.get("last_name"),
            fallback_first_name=patient.get("first_name"),
            fallback_dob=patient.get("dob"),
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"get_patient_orders error: {e}")
        # Fallback direct query
        return _rows("orders.db", """
            SELECT id, order_date, order_status, prescriber_name,
                   primary_insurance, tracking_number, refill_number,
                   parent_order_id, billed, paid
            FROM orders WHERE patient_id = ?
            ORDER BY order_date DESC, id DESC LIMIT 100
        """, (patient_id,))

@app.get("/patients/{patient_id}/refills-eligible", tags=["Patients"])
def get_patient_refills_eligible(patient_id: int, _key=Depends(require_api_key)):
    """Orders eligible for refill for this patient."""
    try:
        from dmelogic.db.orders import find_refill_eligible_orders_for_patient
        return find_refill_eligible_orders_for_patient(patient_id=patient_id, folder_path=FOLDER_PATH)
    except Exception as e:
        log.error(f"refills_eligible error: {e}")
        return []

@app.get("/patients/{patient_id}/notes", tags=["Patients"])
def get_patient_notes(patient_id: int, _key=Depends(require_api_key)):
    """Sticky notes linked to this patient."""
    try:
        from dmelogic.db.sticky_notes import list_notes_for_entity
        return list_notes_for_entity("patient", patient_id, folder_path=FOLDER_PATH)
    except Exception as e:
        log.error(f"patient notes error: {e}")
        return []

# ══════════════════════════════════════════════════════════════════════════
#  ORDERS
# ══════════════════════════════════════════════════════════════════════════
@app.get("/orders/pending-approvals", tags=["Orders"])
def get_pending_approvals(_key=Depends(require_api_key)):
    """Agent-created orders awaiting human approval."""
    try:
        from dmelogic.db.pending_approvals import fetch_pending_approval_orders
        rows = fetch_pending_approval_orders(folder_path=FOLDER_PATH)
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"pending_approvals error: {e}")
        return []

@app.get("/orders/pending-approvals/count", tags=["Orders"])
def count_pending_approvals(_key=Depends(require_api_key)):
    try:
        from dmelogic.db.pending_approvals import count_pending_approvals as _count
        return {"count": _count(folder_path=FOLDER_PATH)}
    except Exception:
        row = _one("orders.db", "SELECT COUNT(*) as cnt FROM orders WHERE order_status='Pending Approval' AND agent_created=1")
        return {"count": row.get("cnt", 0) if row else 0}

@app.post("/orders/{order_id}/approval", tags=["Orders"])
def process_approval(order_id: int, body: ApprovalAction, _key=Depends(require_api_key)):
    """Approve or reject an agent-created order."""
    if body.action == "approve":
        from dmelogic.db.pending_approvals import approve_order
        result = approve_order(order_id, approved_by=body.by, folder_path=FOLDER_PATH)
        _audit("order.approve", order_id, f"by {body.by}")
        return {"success": result, "action": "approved", "order_id": order_id}
    elif body.action == "reject":
        from dmelogic.db.pending_approvals import reject_order
        result = reject_order(order_id, rejected_by=body.by, reason=body.reason or "", folder_path=FOLDER_PATH)
        _audit("order.reject", order_id, f"by {body.by}: {body.reason}")
        return {"success": result, "action": "rejected", "order_id": order_id}
    else:
        raise HTTPException(422, "action must be 'approve' or 'reject'")

@app.get("/orders/refills-due", tags=["Orders"])
def get_refills_due(
    days: int = Query(7, description="Days to look ahead"),
    start_date: Optional[str] = Query(None, description="Optional range start: YYYY-MM-DD or MM/DD/YYYY"),
    end_date: Optional[str] = Query(None, description="Optional range end: YYYY-MM-DD or MM/DD/YYYY"),
    _key=Depends(require_api_key)
):
    """Orders with refills due within N days."""
    try:
        from dmelogic.db.refills import fetch_refills_due

        def _parse_refill_date(raw: str) -> date:
            val = str(raw or "").strip()
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                try:
                    return datetime.strptime(val, fmt).date()
                except ValueError:
                    continue
            raise HTTPException(422, "Invalid date. Use YYYY-MM-DD or MM/DD/YYYY")

        today = date.today()
        if start_date or end_date:
            start = _parse_refill_date(start_date or end_date or "")
            end = _parse_refill_date(end_date or start_date or "")
            if end < start:
                raise HTTPException(422, "end_date must be on or after start_date")
        else:
            start = today
            end = today + timedelta(days=days)

        rows = fetch_refills_due(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            today=today.isoformat(),
            folder_path=FOLDER_PATH,
        )
        return rows
    except Exception as e:
        log.error(f"refills_due error: {e}")
        return []

@app.get("/orders/deleted", tags=["Orders"])
def get_deleted_orders(_key=Depends(require_api_key)):
    return _rows("orders.db", """
        SELECT id, original_order_id, rx_date, order_date,
               patient_last_name, patient_first_name, patient_dob,
               prescriber_name, primary_insurance, order_status,
               deleted_date, deleted_by
        FROM deleted_orders ORDER BY deleted_date DESC
    """)

@app.get("/orders/status/{status}", tags=["Orders"])
def get_orders_by_status(
    status: str,
    limit: int = Query(100),
    _key=Depends(require_api_key)
):
    """Get orders filtered by status."""
    return _rows("orders.db", """
        SELECT id, order_date, patient_last_name, patient_first_name,
               patient_dob, prescriber_name, primary_insurance,
               order_status, tracking_number, billed, paid, refill_number
        FROM orders WHERE order_status = ?
        ORDER BY order_date DESC, id DESC LIMIT ?
    """, (status, limit))


@app.get("/orders/filter", tags=["Orders"])
def get_orders_filtered(
    status: Optional[str] = Query(None, description="Order status filter. Use 'All' for no status filter."),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    _key=Depends(require_api_key),
):
    """Get full order rows filtered by status and optional date range."""
    order_date_iso = """(
        CASE
            WHEN COALESCE(order_date, created_date) GLOB '????-??-??*'
                THEN substr(COALESCE(order_date, created_date), 1, 10)
            WHEN COALESCE(order_date, created_date) GLOB '??/??/????*'
                THEN substr(COALESCE(order_date, created_date), 7, 4) || '-' ||
                     substr(COALESCE(order_date, created_date), 1, 2) || '-' ||
                     substr(COALESCE(order_date, created_date), 4, 2)
            ELSE NULL
        END
    )"""

    where_clauses: List[str] = []
    params: List[Any] = []

    status_value = (status or "").strip()
    if status_value and status_value.lower() != "all":
        where_clauses.append("COALESCE(order_status, '') = ?")
        params.append(status_value)

    if start_date:
        where_clauses.append(f"{order_date_iso} >= ?")
        params.append(start_date)

    if end_date:
        where_clauses.append(f"{order_date_iso} <= ?")
        params.append(end_date)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    rows = _rows(
        "orders.db",
        f"""
        SELECT
            id,
            COALESCE(order_date, created_date) AS order_date,
            patient_last_name,
            patient_first_name,
            patient_dob,
            prescriber_name,
            primary_insurance,
            COALESCE(order_status, '') AS order_status,
            tracking_number,
            COALESCE(billed, 0) AS billed,
            COALESCE(paid, 0) AS paid,
            COALESCE(refill_number, 0) AS refill_number,
            COALESCE(delivery_date, '') AS delivery_date
        FROM orders
        {where_sql}
        ORDER BY {order_date_iso} DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [limit, offset]),
    )

    total_row = _one(
        "orders.db",
        f"SELECT COUNT(*) AS total FROM orders {where_sql}",
        tuple(params),
    ) or {"total": 0}

    return {
        "status": status_value or None,
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "total": int(total_row.get("total", 0) or 0),
        "rows": rows,
    }

@app.get("/orders/{order_id}", tags=["Orders"])
def get_order(order_id: int, _key=Depends(require_api_key)):
    """Single order with all line items."""
    try:
        from dmelogic.db.orders import fetch_order_with_items
        order = fetch_order_with_items(order_id, folder_path=FOLDER_PATH)
        if not order:
            raise HTTPException(404, f"Order {order_id} not found")
        if hasattr(order, '__dict__'):
            d = {k: _serialize(v) for k, v in order.__dict__.items() if not k.startswith('_')}
            if hasattr(order, 'items'):
                d['items'] = [
                    {k: _serialize(v) for k, v in item.__dict__.items() if not k.startswith('_')}
                    for item in (order.items or [])
                ]
            return d
        return order
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"get_order error: {e}")
        order = _one("orders.db", "SELECT * FROM orders WHERE id=?", (order_id,))
        if not order:
            raise HTTPException(404, f"Order {order_id} not found")
        items = _rows("orders.db", "SELECT * FROM order_items WHERE order_id=?", (order_id,))
        order["items"] = items
        return order

@app.patch("/orders/{order_id}/status", tags=["Orders"])
def update_order_status(order_id: int, body: OrderStatusUpdate, _key=Depends(require_api_key)):
    """Update order status."""
    VALID = {"Pending","Active","Shipped","Completed","Cancelled","On Hold",
             "Unbilled","Billed","Paid","Docs Needed","Ready","Submitted","Approved","Closed"}
    if body.new_status not in VALID:
        raise HTTPException(422, f"Invalid status. Valid: {', '.join(sorted(VALID))}")
    order = _one("orders.db", "SELECT order_status, paid, paid_date FROM orders WHERE id=?", (order_id,))
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")

    normalized_paid_date: Optional[str] = None
    if body.paid_date:
        raw = str(body.paid_date).strip()
        parsed = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if not parsed:
            raise HTTPException(422, "Invalid paid_date. Use YYYY-MM-DD or MM/DD/YYYY")
        normalized_paid_date = parsed.strftime("%Y-%m-%d")

    old = order.get("order_status")
    today_iso = datetime.now().strftime("%Y-%m-%d")

    if body.new_status == "Paid":
        if not normalized_paid_date:
            normalized_paid_date = order.get("paid_date") or today_iso
        ok = _exec(
            "orders.db",
            "UPDATE orders SET order_status=?, updated_date=?, paid=1, paid_date=? WHERE id=?",
            (body.new_status, today_iso, normalized_paid_date, order_id),
        )
    else:
        ok = _exec(
            "orders.db",
            "UPDATE orders SET order_status=?, updated_date=? WHERE id=?",
            (body.new_status, today_iso, order_id),
        )

    if not ok:
        raise HTTPException(500, "Database update failed")
    _audit("order.status_update", order_id,
           f"{old} → {body.new_status} by {body.updated_by}" + (f" | {body.notes}" if body.notes else ""))
    return {
        "order_id": order_id,
        "old_status": old,
        "new_status": body.new_status,
        "paid": 1 if body.new_status == "Paid" else order.get("paid"),
        "paid_date": normalized_paid_date if body.new_status == "Paid" else order.get("paid_date"),
        "updated_by": body.updated_by,
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.patch("/orders/{order_id}/prescriber-contact", tags=["Orders"])
def update_order_prescriber_contact(order_id: int, body: OrderPrescriberContactUpdate, _key=Depends(require_api_key)):
    """Update prescriber phone/fax fields on an existing order."""
    order = _one(
        "orders.db",
        "SELECT id, prescriber_phone, prescriber_fax FROM orders WHERE id=?",
        (order_id,),
    )
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")

    phone_val = body.prescriber_phone.strip() if isinstance(body.prescriber_phone, str) else body.prescriber_phone
    fax_val = body.prescriber_fax.strip() if isinstance(body.prescriber_fax, str) else body.prescriber_fax

    fields: Dict[str, Any] = {}
    if phone_val is not None:
        fields["prescriber_phone"] = phone_val
    if fax_val is not None:
        fields["prescriber_fax"] = fax_val

    if not fields:
        raise HTTPException(422, "Provide at least one field: prescriber_phone or prescriber_fax")

    try:
        from dmelogic.db.orders import update_order_fields
        update_order_fields(order_id, fields, folder_path=FOLDER_PATH)
    except Exception as e:
        log.error(f"update_order_prescriber_contact error: {e}")
        raise HTTPException(500, "Database update failed")

    _audit(
        "order.prescriber_contact_update",
        order_id,
        f"phone={fields.get('prescriber_phone', '[unchanged]')}; fax={fields.get('prescriber_fax', '[unchanged]')} by {body.updated_by}" + (f" | {body.notes}" if body.notes else ""),
    )

    return {
        "order_id": order_id,
        "prescriber_phone": fields.get("prescriber_phone", order.get("prescriber_phone")),
        "prescriber_fax": fields.get("prescriber_fax", order.get("prescriber_fax")),
        "updated_by": body.updated_by,
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.patch("/orders/{order_id}/patient-link", tags=["Orders"])
def update_order_patient_link(order_id: int, body: OrderPatientLinkUpdate, _key=Depends(require_api_key)):
    """Link an existing order to a specific patient by patient_id."""
    order = _one(
        "orders.db",
        "SELECT id, patient_id FROM orders WHERE id=?",
        (order_id,),
    )
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")

    patient = _one(
        "patients.db",
        "SELECT id, first_name, last_name FROM patients WHERE id=?",
        (int(body.patient_id),),
    )
    if not patient:
        raise HTTPException(404, f"Patient {body.patient_id} not found")

    try:
        from dmelogic.db.orders import update_order_fields
        update_order_fields(order_id, {"patient_id": int(body.patient_id)}, folder_path=FOLDER_PATH)
    except Exception as e:
        log.error(f"update_order_patient_link error: {e}")
        raise HTTPException(500, "Database update failed")

    _audit(
        "order.patient_link_update",
        order_id,
        f"patient_id {order.get('patient_id')} -> {int(body.patient_id)} by {body.updated_by}" + (f" | {body.notes}" if body.notes else ""),
    )

    return {
        "order_id": int(order_id),
        "old_patient_id": order.get("patient_id"),
        "patient_id": int(body.patient_id),
        "patient_name": f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip(),
        "updated_by": body.updated_by,
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.patch("/orders/{order_id}/items/{item_id}/refills", tags=["Orders"])
def update_order_item_refills(order_id: int, item_id: int, body: OrderItemRefillsUpdate, _key=Depends(require_api_key)):
    """Update refill count for a specific order item."""
    item = _one(
        "orders.db",
        "SELECT id, order_id, description, refills FROM order_items WHERE id=?",
        (item_id,),
    )
    if not item:
        raise HTTPException(404, f"Order item {item_id} not found")

    if int(item.get("order_id") or 0) != int(order_id):
        raise HTTPException(422, f"Order item {item_id} does not belong to order {order_id}")

    old_refills_raw = item.get("refills")
    try:
        old_refills = int(str(old_refills_raw).strip()) if old_refills_raw not in (None, "") else 0
    except Exception:
        old_refills = 0

    try:
        from dmelogic.db.orders import update_order_item, recompute_refill_due_date
        update_order_item(item_id, {"refills": str(int(body.refills))}, folder_path=FOLDER_PATH)
        refill_due_date = recompute_refill_due_date(order_id, folder_path=FOLDER_PATH)
    except Exception as e:
        log.error(f"update_order_item_refills error: {e}")
        raise HTTPException(500, "Database update failed")

    _audit(
        "order.item_refills_update",
        item_id,
        f"order_id={order_id}; refills {old_refills} -> {int(body.refills)} by {body.updated_by}" + (f" | {body.notes}" if body.notes else ""),
    )

    updated_item = _one(
        "orders.db",
        "SELECT id, order_id, description, refills FROM order_items WHERE id=?",
        (item_id,),
    )

    return {
        "order_id": int(order_id),
        "item_id": int(item_id),
        "description": updated_item.get("description") if updated_item else item.get("description"),
        "old_refills": old_refills,
        "refills": int(str((updated_item or {}).get("refills", body.refills)).strip() or body.refills),
        "refill_due_date": refill_due_date,
        "updated_by": body.updated_by,
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.delete("/orders/{order_id}", tags=["Orders"])
def delete_order_endpoint(order_id: int, body: Optional[OrderDeleteRequest] = None, _key=Depends(require_api_key)):
    """Delete an order. Optionally preserve an audit trail in deleted_orders/deleted_order_items."""
    payload = body or OrderDeleteRequest()

    order = _one("orders.db", "SELECT id FROM orders WHERE id=?", (order_id,))
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")

    archived = False
    if payload.preserve_audit_trail:
        try:
            conn = _conn("orders.db")
            cur = conn.cursor()

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deleted_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_order_id INTEGER NOT NULL,
                    rx_date TEXT,
                    order_date TEXT,
                    patient_last_name TEXT,
                    patient_first_name TEXT,
                    patient_dob TEXT,
                    patient_address TEXT,
                    patient_phone TEXT,
                    patient_secondary_contact TEXT,
                    icd_code_1 TEXT,
                    icd_code_2 TEXT,
                    icd_code_3 TEXT,
                    icd_code_4 TEXT,
                    icd_code_5 TEXT,
                    prescriber_name TEXT,
                    prescriber_npi TEXT,
                    primary_insurance TEXT,
                    primary_insurance_id TEXT,
                    secondary_insurance TEXT,
                    secondary_insurance_id TEXT,
                    billing_selection TEXT,
                    order_status TEXT,
                    delivery_date TEXT,
                    tracking_number TEXT,
                    parent_order_id INTEGER,
                    refill_number INTEGER,
                    billed INTEGER,
                    paid INTEGER,
                    paid_date TEXT,
                    notes TEXT,
                    is_pickup INTEGER,
                    pickup_date TEXT,
                    doctor_directions TEXT,
                    deleted_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    deleted_by TEXT,
                    deletion_reason TEXT
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deleted_order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deleted_order_id INTEGER NOT NULL,
                    original_item_id INTEGER,
                    rx_no TEXT,
                    hcpcs_code TEXT,
                    description TEXT,
                    item_number TEXT,
                    refills TEXT,
                    day_supply TEXT,
                    qty TEXT,
                    cost_ea TEXT,
                    total TEXT,
                    pa_number TEXT,
                    directions TEXT,
                    FOREIGN KEY (deleted_order_id) REFERENCES deleted_orders (id)
                )
                """
            )

            cur.execute(
                """
                SELECT id, rx_date, order_date, patient_last_name, patient_first_name, patient_dob,
                       patient_address, patient_phone, patient_secondary_contact,
                       icd_code_1, icd_code_2, icd_code_3, icd_code_4, icd_code_5,
                       prescriber_name, prescriber_npi,
                       primary_insurance, primary_insurance_id, secondary_insurance, secondary_insurance_id,
                       billing_selection, order_status, delivery_date, tracking_number,
                       parent_order_id, refill_number, billed, paid, paid_date, notes,
                       is_pickup, pickup_date, doctor_directions
                FROM orders WHERE id = ?
                """,
                (order_id,),
            )
            order_row = cur.fetchone()
            if order_row:
                cur.execute(
                    """
                    INSERT INTO deleted_orders (
                        original_order_id, rx_date, order_date, patient_last_name, patient_first_name,
                        patient_dob, patient_address, patient_phone, patient_secondary_contact,
                        icd_code_1, icd_code_2, icd_code_3, icd_code_4, icd_code_5,
                        prescriber_name, prescriber_npi,
                        primary_insurance, primary_insurance_id, secondary_insurance, secondary_insurance_id,
                        billing_selection, order_status, delivery_date, tracking_number,
                        parent_order_id, refill_number, billed, paid, paid_date, notes,
                        is_pickup, pickup_date, doctor_directions, deleted_by, deletion_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(order_row) + (payload.deleted_by, payload.reason),
                )
                deleted_order_id = cur.lastrowid

                cur.execute(
                    """
                    SELECT id, rx_no, hcpcs_code, description, item_number, refills, day_supply,
                           qty, cost_ea, total, pa_number, directions
                    FROM order_items WHERE order_id = ?
                    """,
                    (order_id,),
                )
                items = cur.fetchall()
                for item in items:
                    cur.execute(
                        """
                        INSERT INTO deleted_order_items (
                            deleted_order_id, original_item_id, rx_no, hcpcs_code, description,
                            item_number, refills, day_supply, qty, cost_ea, total, pa_number, directions
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (deleted_order_id,) + tuple(item),
                    )
                archived = True

            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"delete_order archive failed for {order_id}: {e}")

    try:
        from dmelogic.db.orders import delete_order as _delete_order
        _delete_order(order_id, folder_path=FOLDER_PATH)
    except Exception as e:
        log.error(f"delete_order failed: {e}")
        raise HTTPException(500, "Order delete failed")

    _audit(
        "order.delete",
        order_id,
        f"by {payload.deleted_by}" + (f" | reason={payload.reason}" if payload.reason else "") + (" | archived" if archived else ""),
    )

    return {
        "order_id": order_id,
        "deleted": True,
        "archived": archived,
        "deleted_by": payload.deleted_by,
    }

@app.post("/orders/{order_id}/process-refill", tags=["Orders"])
def process_refill(
    order_id: int,
    force: bool = Query(False, description="Skip eligibility date check"),
    _key=Depends(require_api_key)
):
    """
    Process a refill for an order — creates a new refill order,
    decrements refill count on source, locks the source order.
    Uses the existing refill_service.process_refill business logic.
    """
    try:
        from dmelogic.services.refill_service import process_refill as _process

        try:
            new_order = _process(order_id, folder_path=FOLDER_PATH, force=force)
        except Exception as e:
            msg = str(e).lower()
            if (not force) and ("older than" in msg or "eligib" in msg or "90" in msg or "75%" in msg):
                new_order = _process(order_id, folder_path=FOLDER_PATH, force=True)
            else:
                raise

        if hasattr(new_order, "__dict__"):
            result = {k: _serialize(v) for k, v in new_order.__dict__.items() if not k.startswith("_")}
            if hasattr(new_order, "items"):
                result["items"] = [
                    {k: _serialize(v) for k, v in item.__dict__.items() if not k.startswith("_")}
                    for item in (new_order.items or [])
                ]
            return {"success": True, "new_order_id": new_order.id, "order": result}

        return {"success": True, "new_order_id": getattr(new_order, "id", None)}
    except Exception as e:
        log.error(f"process_refill error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/orders/{order_id}/notes", tags=["Orders"])
def get_order_notes(order_id: int, _key=Depends(require_api_key)):
    try:
        from dmelogic.db.sticky_notes import list_notes_for_entity
        return list_notes_for_entity("order", order_id, folder_path=FOLDER_PATH)
    except Exception:
        return []

# ══════════════════════════════════════════════════════════════════════════
#  REFILLS
# ══════════════════════════════════════════════════════════════════════════
@app.post("/insurance/check-refill", tags=["Refills"])
def check_refill_eligibility(body: RefillCheckRequest, _key=Depends(require_api_key)):
    """Check refill eligibility — 75% rule for Medicare/Medicaid, 80% for commercial."""
    from dmelogic.models.insurance import InsurancePolicy, InsuranceType
    type_map = {t.value: t for t in InsuranceType}
    ins_type = type_map.get(body.insurance_type, InsuranceType.COMMERCIAL)
    policy = InsurancePolicy(name="Check", insurance_type=ins_type,
                             max_quantity_per_month=body.max_quantity_per_month)
    try:
        last_filled = date.fromisoformat(body.last_filled)
    except ValueError:
        raise HTTPException(422, f"Invalid date: {body.last_filled}")
    earliest = policy.get_refill_earliest_date(last_filled, body.day_supply)
    is_allowed, reason = policy.is_refill_allowed(last_filled, body.day_supply, body.quantity)
    return {"is_allowed": is_allowed,
            "reason": reason if not is_allowed else "Refill is allowed",
            "earliest_refill_date": earliest.isoformat()}

# ══════════════════════════════════════════════════════════════════════════
#  PRESCRIBERS
# ══════════════════════════════════════════════════════════════════════════
@app.get("/prescribers", tags=["Prescribers"])
def list_prescribers(_key=Depends(require_api_key)):
    return _rows("prescribers.db", "SELECT * FROM prescribers ORDER BY last_name COLLATE NOCASE")

@app.get("/prescribers/search", tags=["Prescribers"])
def search_prescribers(q: str = Query(...), _key=Depends(require_api_key)):
    try:
        from dmelogic.db.prescribers import search_prescribers as _search
        return _search(q, folder_path=FOLDER_PATH)
    except Exception:
        like = f"%{q}%"
        return _rows("prescribers.db", """
            SELECT * FROM prescribers
            WHERE last_name LIKE ? OR first_name LIKE ? OR npi_number LIKE ?
            ORDER BY last_name LIMIT 50
        """, (like, like, like))

@app.get("/prescribers/npi/{npi}", tags=["Prescribers"])
def get_prescriber_by_npi(npi: str, _key=Depends(require_api_key)):
    p = _one("prescribers.db", "SELECT * FROM prescribers WHERE npi_number=?", (npi,))
    if not p:
        raise HTTPException(404, f"No prescriber with NPI {npi}")
    return p

@app.get("/prescribers/{prescriber_id}", tags=["Prescribers"])
def get_prescriber(prescriber_id: int, _key=Depends(require_api_key)):
    p = _one("prescribers.db", "SELECT * FROM prescribers WHERE id=?", (prescriber_id,))
    if not p:
        raise HTTPException(404, f"Prescriber {prescriber_id} not found")
    return p

@app.get("/npi-registry/{npi}", tags=["Prescribers"])
def lookup_npi_registry(npi: str, _key=Depends(require_api_key)):
    """Look up a prescriber from the CMS NPI Registry by 10-digit NPI number."""
    try:
        from dmelogic.services.npi_service import get_npi_service
        service = get_npi_service()
        data, error = service.lookup_by_npi(npi)
        if error:
            raise HTTPException(404, error)
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"NPI Registry lookup failed: {e}")

@app.get("/npi-registry/search/{query}", tags=["Prescribers"])
def search_npi_registry(query: str, _key=Depends(require_api_key)):
    """Search the CMS NPI Registry by name (last,first or organization)."""
    try:
        from dmelogic.services.npi_service import get_npi_service
        service = get_npi_service()
        results, error = service.lookup_by_name(last_name=query)
        if error:
            raise HTTPException(404, error)
        return results or []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"NPI Registry search failed: {e}")

# ══════════════════════════════════════════════════════════════════════════
#  INVENTORY
# ══════════════════════════════════════════════════════════════════════════
@app.get("/inventory", tags=["Inventory"])
def list_inventory(
    needs_reorder: Optional[bool] = Query(None),
    in_stock_only: Optional[bool] = Query(None),
    out_of_stock: Optional[bool] = Query(None),
    _key=Depends(require_api_key)
):
    rows = _rows("inventory.db", "SELECT * FROM inventory ORDER BY hcpcs_code")
    if needs_reorder is True:
        rows = [r for r in rows if (r.get("stock_quantity",0) or 0) <= (r.get("reorder_level",0) or 0)]
    if in_stock_only is True:
        rows = [r for r in rows if (r.get("stock_quantity",0) or 0) > 0]
    if out_of_stock is True:
        rows = [r for r in rows if (r.get("stock_quantity",0) or 0) == 0]
    return rows

@app.get("/inventory/hcpcs/{hcpcs_code}", tags=["Inventory"])
def get_inventory_by_hcpcs(hcpcs_code: str, _key=Depends(require_api_key)):
    item = _one("inventory.db", "SELECT * FROM inventory WHERE UPPER(hcpcs_code)=UPPER(?)", (hcpcs_code,))
    if not item:
        raise HTTPException(404, f"No inventory item with HCPCS {hcpcs_code}")
    return item

@app.get("/inventory/search", tags=["Inventory"])
def search_inventory(q: str = Query(...), _key=Depends(require_api_key)):
    like = f"%{q}%"
    return _rows("inventory.db", """
        SELECT * FROM inventory
        WHERE hcpcs_code LIKE ?
           OR description LIKE ?
           OR brand LIKE ?
           OR supplier LIKE ?
           OR item_number LIKE ?
           OR category LIKE ?
        ORDER BY hcpcs_code LIMIT 50
    """, (like, like, like, like, like, like))

@app.get("/inventory/{item_id}", tags=["Inventory"])
def get_inventory_item(item_id: int, _key=Depends(require_api_key)):
    item = _one("inventory.db", "SELECT * FROM inventory WHERE item_id=?", (item_id,))
    if not item:
        raise HTTPException(404, f"Inventory item {item_id} not found")
    return item

# ══════════════════════════════════════════════════════════════════════════
#  BILLING & CLAIMS
# ══════════════════════════════════════════════════════════════════════════
@app.get("/billing/claims", tags=["Billing"])
def get_claims(
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    _key=Depends(require_api_key)
):
    """Get billing claims, optionally filtered by status."""
    if status:
        return _rows("billing.db", "SELECT * FROM claims WHERE status=? ORDER BY claim_date DESC LIMIT ?", (status, limit))
    return _rows("billing.db", "SELECT * FROM claims ORDER BY claim_date DESC LIMIT ?", (limit,))

@app.get("/billing/claims/aging", tags=["Billing"])
def get_claims_aging(_key=Depends(require_api_key)):
    """AR aging report — outstanding claims bucketed by age."""
    rows = _rows("billing.db", """
        SELECT claim_id,
               COALESCE(claim_date, created_date) as claim_date,
               COALESCE(insurance_name, 'Unknown') as insurance_name,
               COALESCE(claim_amount, 0) as billed,
               COALESCE(paid_amount, 0) as paid,
               COALESCE(status, 'Pending') as status,
               patient_id
        FROM claims
        WHERE status NOT IN ('Paid', 'Closed', 'Denied')
        ORDER BY claim_date ASC
    """)
    today = date.today()
    buckets = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    result = []
    for r in rows:
        billed = float(r.get("billed") or 0)
        paid = float(r.get("paid") or 0)
        balance = billed - paid
        try:
            cd = date.fromisoformat(str(r.get("claim_date",""))[:10])
            days = (today - cd).days
        except Exception:
            days = 0
        if days <= 30:
            bucket = "0-30 days"
            buckets["0-30"] += balance
        elif days <= 60:
            bucket = "31-60 days"
            buckets["31-60"] += balance
        elif days <= 90:
            bucket = "61-90 days"
            buckets["61-90"] += balance
        else:
            bucket = "90+ days"
            buckets["90+"] += balance
        r["balance"] = balance
        r["days_old"] = days
        r["age_bucket"] = bucket
        result.append(r)
    return {"claims": result, "summary": buckets, "total_ar": sum(buckets.values())}

@app.get("/billing/reconciliation", tags=["Billing"])
def get_reconciliation(
    months: int = Query(12, description="How many months back"),
    _key=Depends(require_api_key)
):
    """Monthly reconciliation: expected vs actual payments."""
    return _rows("billing.db", """
        SELECT strftime('%Y-%m', COALESCE(claim_date, created_date)) as month,
               COUNT(*) as claim_count,
               SUM(COALESCE(claim_amount, 0)) as expected,
               SUM(COALESCE(paid_amount, 0)) as actual,
               SUM(COALESCE(claim_amount,0) - COALESCE(paid_amount,0)) as variance
        FROM claims
        WHERE COALESCE(claim_date, created_date) >= date('now', ? || ' months')
        GROUP BY strftime('%Y-%m', COALESCE(claim_date, created_date))
        ORDER BY month DESC
    """, (f"-{months}",))


def _to_iso_date_sql(expr: str) -> str:
    """Normalize MM/DD/YYYY and ISO datetime strings to YYYY-MM-DD for SQL filtering."""
    return f"""CASE
        WHEN {expr} LIKE '__/__/____%' THEN
            substr(substr({expr},1,10),7,4)||'-'||substr(substr({expr},1,10),1,2)||'-'||substr(substr({expr},1,10),4,2)
        WHEN {expr} LIKE '____-__-__%' THEN
            substr({expr},1,10)
        ELSE NULL
    END"""


@app.get("/billing/reconciliation/orders", tags=["Billing"])
def get_reconciliation_orders(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    insurance: Optional[str] = Query("All", description="Insurance name or 'All'"),
    limit: int = Query(1000, ge=1, le=5000),
    _key=Depends(require_api_key),
):
    """Order-level reconciliation rows for billed/paid workflow."""
    sd = (start_date or "2000-01-01").strip()
    ed = (end_date or "2099-12-31").strip()

    order_date_iso = _to_iso_date_sql("o.order_date")
    params: List[Any] = [sd, ed]
    insurance_where = ""
    if insurance and insurance != "All":
        insurance_where = " AND TRIM(COALESCE(o.primary_insurance, '')) = ?"
        params.append(insurance)
    params.append(int(limit))

    rows = _rows(
        "orders.db",
        f"""
        WITH order_totals AS (
            SELECT oi.order_id, SUM(COALESCE(CAST(oi.total AS REAL), 0)) AS order_total
            FROM order_items oi
            GROUP BY oi.order_id
        )
        SELECT
            o.id AS order_id,
            ('ORD-' || o.id) AS order_number,
            {order_date_iso} AS order_date,
            (TRIM(COALESCE(o.patient_last_name, '')) || ', ' || TRIM(COALESCE(o.patient_first_name, ''))) AS patient,
            COALESCE(ot.order_total, 0) AS expected,
            COALESCE(o.paid, 0) AS paid,
            COALESCE(o.paid_date, '') AS paid_date,
            COALESCE(o.order_status, '') AS status,
            TRIM(COALESCE(o.primary_insurance, '')) AS insurance
        FROM orders o
        LEFT JOIN order_totals ot ON ot.order_id = o.id
        WHERE {order_date_iso} IS NOT NULL
          AND {order_date_iso} >= ?
          AND {order_date_iso} <= ?
          AND COALESCE(o.order_status, '') NOT IN ('Cancelled', 'Deleted')
          AND (
                COALESCE(o.billed, 0) = 1
             OR COALESCE(o.paid, 0) = 1
             OR TRIM(COALESCE(o.billing_confirmation_number, '')) <> ''
             OR UPPER(TRIM(COALESCE(o.order_status, ''))) IN ('BILLED', 'PAID', 'SUBMITTED', 'APPROVED')
          )
          {insurance_where}
        ORDER BY {order_date_iso} DESC, o.id DESC
        LIMIT ?
        """,
        tuple(params),
    )

    out_rows: List[Dict[str, Any]] = []
    paid_count = 0
    unpaid_count = 0
    total_expected = 0.0
    for r in rows:
        paid_bool = int(r.get("paid") or 0)
        if paid_bool:
            paid_count += 1
        else:
            unpaid_count += 1
        total_expected += float(r.get("expected") or 0)
        out_rows.append(
            {
                "order_id": int(r.get("order_id") or 0),
                "order_number": r.get("order_number") or "",
                "order_date": r.get("order_date") or "",
                "patient": r.get("patient") or "",
                "expected": float(r.get("expected") or 0),
                "paid": bool(paid_bool),
                "paid_date": r.get("paid_date") or "",
                "status": r.get("status") or "",
                "insurance": r.get("insurance") or "",
            }
        )

    return {
        "rows": out_rows,
        "summary": {
            "total_rows": len(out_rows),
            "paid_orders": paid_count,
            "unpaid_orders": unpaid_count,
            "total_expected": total_expected,
            "start_date": sd,
            "end_date": ed,
            "insurance": insurance or "All",
        },
    }


@app.post("/billing/reconciliation/orders/paid", tags=["Billing"])
def update_reconciliation_paid(
    body: ReconciliationPaidUpdateRequest,
    _key=Depends(require_api_key),
):
    """Bulk update orders.paid and orders.paid_date for reconciliation workflow."""
    updates = body.updates or []
    if not updates:
        raise HTTPException(422, "updates must be a non-empty list")

    conn = _conn("orders.db")
    try:
        cur = conn.cursor()
        changed = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in updates:
            order_id = int(item.order_id)
            paid_val = 1 if bool(item.paid) else 0
            paid_date = (item.paid_date or "").strip() if item.paid_date is not None else ""
            if paid_val and not paid_date:
                paid_date = datetime.now().strftime("%m/%d/%Y")
            if not paid_val:
                paid_date = None

            cur.execute("SELECT id FROM orders WHERE id = ?", (order_id,))
            if not cur.fetchone():
                continue

            cur.execute(
                "UPDATE orders SET paid = ?, paid_date = ?, updated_date = ? WHERE id = ?",
                (paid_val, paid_date, now, order_id),
            )
            if cur.rowcount:
                changed += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"update_reconciliation_paid error: {e}")
        raise HTTPException(500, "Failed to update reconciliation paid status")
    finally:
        conn.close()

    return {
        "success": True,
        "updated_count": changed,
        "requested_count": len(updates),
        "updated_by": body.updated_by,
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.post("/ui/reports/reconciliation/open", tags=["UI"])
def open_reconciliation_report_ui(
    body: OpenReconciliationUIRequest,
    _key=Depends(require_api_key),
):
    """Queue a desktop UI action to open the Reconciliation Report in running DMELogic."""
    try:
        from dmelogic.paths import db_dir

        commands_path = db_dir() / "agent_ui_commands.jsonl"
        commands_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "command_id": str(uuid.uuid4()),
            "action": "open_reconciliation_report",
            "requested_by": body.requested_by,
            "created_at": datetime.utcnow().isoformat(),
            "parameters": {
                "start_date": body.start_date,
                "end_date": body.end_date,
                "insurance": body.insurance,
                "notes": body.notes,
            },
        }

        with commands_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")

        return {
            "queued": True,
            "action": payload["action"],
            "command_id": payload["command_id"],
            "requested_by": body.requested_by,
            "created_at": payload["created_at"],
            "command_file": str(commands_path),
        }
    except Exception as e:
        log.error(f"open_reconciliation_report_ui queue error: {e}")
        raise HTTPException(500, "Failed to queue UI command for reconciliation report")

@app.get("/billing/fee-schedule/{hcpcs}", tags=["Billing"])
def get_fee_schedule(hcpcs: str, rental: bool = Query(False), _key=Depends(require_api_key)):
    """Look up Medicaid fee schedule for a HCPCS code."""
    try:
        from dmelogic.db.fee_schedule import lookup_fee
        result = lookup_fee(hcpcs, folder_path=FOLDER_PATH, rental=rental)
        if not result:
            raise HTTPException(404, f"No fee schedule entry for {hcpcs}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        row = _one("billing.db", "SELECT * FROM fee_schedule WHERE UPPER(hcpcs_code)=UPPER(?)", (hcpcs,))
        if not row:
            raise HTTPException(404, f"No fee schedule entry for {hcpcs}")
        return row

@app.get("/billing/summary", tags=["Billing"])
def billing_summary(_key=Depends(require_api_key)):
    """High-level billing summary: totals billed, paid, outstanding."""
    return _one("billing.db", """
        SELECT COUNT(*) as total_claims,
               SUM(COALESCE(claim_amount,0)) as total_billed,
               SUM(COALESCE(paid_amount,0)) as total_paid,
               SUM(COALESCE(claim_amount,0) - COALESCE(paid_amount,0)) as total_outstanding,
               SUM(CASE WHEN status='Paid' THEN 1 ELSE 0 END) as paid_count,
               SUM(CASE WHEN status NOT IN ('Paid','Closed','Denied') THEN 1 ELSE 0 END) as open_count
        FROM claims
    """) or {}

# ══════════════════════════════════════════════════════════════════════════
#  INSURANCE PAYERS
# ══════════════════════════════════════════════════════════════════════════
@app.get("/insurance/payers", tags=["Insurance"])
def list_insurance_payers(_key=Depends(require_api_key)):
    return _rows("insurance_names.db", "SELECT * FROM insurance_names ORDER BY usage_count DESC, name ASC")

# ══════════════════════════════════════════════════════════════════════════
#  STICKY NOTES
# ══════════════════════════════════════════════════════════════════════════
@app.get("/notes", tags=["Notes"])
def list_notes(
    search: Optional[str] = Query(None),
    include_archived: bool = Query(False),
    _key=Depends(require_api_key)
):
    try:
        from dmelogic.db.sticky_notes import list_notes as _list
        return _list(include_archived=include_archived, search=search, folder_path=FOLDER_PATH)
    except Exception:
        return []

@app.post("/notes", tags=["Notes"])
def create_note(body: NoteCreate, _key=Depends(require_api_key)):
    try:
        from dmelogic.db.sticky_notes import create_note as _create
        note_id = _create(body.title, body.body, body.color, body.pinned, folder_path=FOLDER_PATH)
        return {"note_id": note_id, "created": True}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/notes/{note_id}", tags=["Notes"])
def get_note(note_id: int, _key=Depends(require_api_key)):
    try:
        from dmelogic.db.sticky_notes import get_note as _get
        note = _get(note_id, folder_path=FOLDER_PATH)
        if not note:
            raise HTTPException(404, f"Note {note_id} not found")
        return note
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ══════════════════════════════════════════════════════════════════════════
#  ORDER TEMPLATES
# ══════════════════════════════════════════════════════════════════════════
@app.get("/order-templates", tags=["Templates"])
def list_order_templates(_key=Depends(require_api_key)):
    try:
        from dmelogic.db.order_templates import get_all_templates
        templates = get_all_templates(folder_path=FOLDER_PATH)
        return [{"id": t.id, "name": t.name, "description": t.description,
                 "billing_type": t.billing_type, "item_count": len(t.items)} for t in templates]
    except Exception:
        return _rows("orders.db", "SELECT * FROM order_templates ORDER BY name")

@app.get("/order-templates/{template_id}", tags=["Templates"])
def get_order_template(template_id: int, _key=Depends(require_api_key)):
    try:
        from dmelogic.db.order_templates import get_template_with_items
        t = get_template_with_items(template_id, folder_path=FOLDER_PATH)
        if not t:
            raise HTTPException(404, f"Template {template_id} not found")
        return {"id": t.id, "name": t.name, "description": t.description,
                "billing_type": t.billing_type,
                "items": [{"hcpcs": i.hcpcs, "description": i.description,
                           "quantity": i.quantity, "refills": i.refills,
                           "days_supply": i.days_supply} for i in t.items]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ══════════════════════════════════════════════════════════════════════════
#  REPORTS  (direct SQL — no Qt dependency)
# ══════════════════════════════════════════════════════════════════════════
@app.get("/reports/profit", tags=["Reports"])
def profit_report(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    _key=Depends(require_api_key)
):
    """Order profit report: revenue vs cost vs margin."""
    inv_rows = _rows("inventory.db", "SELECT hcpcs_code, CAST(COALESCE(cost,0) AS REAL) as cost FROM inventory")
    cost_map = {r["hcpcs_code"]: float(r["cost"]) for r in inv_rows if r.get("hcpcs_code")}
    where = ""
    params: tuple = ()
    if start_date and end_date:
        where = "WHERE COALESCE(o.order_date, o.created_date) BETWEEN ? AND ?"
        params = (start_date, end_date)
    rows = _rows("orders.db", f"""
        SELECT o.id, COALESCE(o.order_date, o.created_date) as order_date,
               COALESCE(o.patient_last_name||', '||o.patient_first_name,'') as patient,
               o.order_status,
               oi.hcpcs_code,
               CAST(COALESCE(oi.qty,1) AS REAL) as qty,
               CAST(COALESCE(oi.total,0) AS REAL) as line_total
        FROM orders o JOIN order_items oi ON oi.order_id=o.id {where}
        ORDER BY o.id DESC
    """, params)
    orders: Dict[int, Dict] = {}
    for r in rows:
        oid = r["id"]
        if oid not in orders:
            orders[oid] = {"order_id": oid, "order_date": r["order_date"],
                           "patient": r["patient"], "status": r["order_status"],
                           "revenue": 0.0, "cost": 0.0}
        orders[oid]["revenue"] += float(r["line_total"])
        orders[oid]["cost"] += cost_map.get(r["hcpcs_code"] or "", 0.0) * float(r["qty"])
    result = []
    for o in orders.values():
        profit = o["revenue"] - o["cost"]
        o["profit"] = profit
        o["margin_pct"] = round((profit / o["revenue"] * 100), 1) if o["revenue"] > 0 else 0
        result.append(o)
    total_rev = sum(o["revenue"] for o in result)
    total_cost = sum(o["cost"] for o in result)
    total_profit = total_rev - total_cost
    return {
        "orders": result[:500],
        "summary": {
            "total_revenue": round(total_rev, 2),
            "total_cost": round(total_cost, 2),
            "total_profit": round(total_profit, 2),
            "avg_margin_pct": round((total_profit / total_rev * 100), 1) if total_rev > 0 else 0,
            "order_count": len(result)
        }
    }

@app.get("/reports/inventory-value", tags=["Reports"])
def inventory_value_report(_key=Depends(require_api_key)):
    """Inventory value by category."""
    return _rows("inventory.db", """
        SELECT COALESCE(category,'Uncategorized') as category,
               COUNT(*) as item_count,
               SUM(COALESCE(stock_quantity,0)) as total_units,
               SUM(COALESCE(stock_quantity,0)*COALESCE(cost,0)) as total_cost_value,
               SUM(COALESCE(stock_quantity,0)*COALESCE(retail_price,0)) as total_retail_value,
               SUM(COALESCE(stock_quantity,0)*COALESCE(retail_price,0)) -
               SUM(COALESCE(stock_quantity,0)*COALESCE(cost,0)) as profit_potential
        FROM inventory GROUP BY category ORDER BY total_retail_value DESC
    """)

@app.get("/reports/gross-margin", tags=["Reports"])
def gross_margin_report(_key=Depends(require_api_key)):
    """Gross margin by HCPCS item."""
    rows = _rows("inventory.db", """
        SELECT hcpcs_code, description,
               COALESCE(cost,0) as cost,
               COALESCE(retail_price,0) as retail_price
        FROM inventory WHERE COALESCE(retail_price,0) > 0
        ORDER BY (COALESCE(retail_price,0)-COALESCE(cost,0)) DESC
    """)
    for r in rows:
        cost = float(r["cost"])
        price = float(r["retail_price"])
        r["margin_dollars"] = round(price - cost, 2)
        r["margin_pct"] = round(((price - cost) / price * 100), 1) if price > 0 else 0
    return rows

@app.get("/reports/low-stock", tags=["Reports"])
def low_stock_report(_key=Depends(require_api_key)):
    """Items at or below reorder point."""
    return _rows("inventory.db", """
        SELECT hcpcs_code, description,
               COALESCE(stock_quantity,0) as current_stock,
               COALESCE(reorder_level,10) as reorder_point,
               COALESCE(supplier,'') as supplier
        FROM inventory
        WHERE COALESCE(stock_quantity,0) <= COALESCE(reorder_level,10)
          AND COALESCE(stock_quantity,0) > 0
        ORDER BY stock_quantity ASC
    """)

@app.get("/reports/out-of-stock", tags=["Reports"])
def out_of_stock_report(_key=Depends(require_api_key)):
    """Items with zero stock."""
    return _rows("inventory.db", """
        SELECT hcpcs_code, description, supplier, last_used_date
        FROM inventory WHERE COALESCE(stock_quantity,0) = 0
        ORDER BY last_used_date DESC
    """)

@app.get("/reports/reorder-by-vendor", tags=["Reports"])
def reorder_by_vendor(_key=Depends(require_api_key)):
    """Items needing reorder, grouped by vendor."""
    rows = _rows("inventory.db", """
        SELECT COALESCE(supplier,'Unknown') as supplier,
               hcpcs_code, description,
               COALESCE(stock_quantity,0) as current_stock,
               COALESCE(reorder_level*2,20) as reorder_qty,
               COALESCE(cost,0) as unit_cost
        FROM inventory
        WHERE COALESCE(stock_quantity,0) <= COALESCE(reorder_level,10)
        ORDER BY supplier, hcpcs_code
    """)
    for r in rows:
        r["total_cost"] = round(float(r["reorder_qty"]) * float(r["unit_cost"]), 2)
    return rows

@app.get("/reports/orders-by-status", tags=["Reports"])
def orders_by_status_report(_key=Depends(require_api_key)):
    """Order counts and totals grouped by status."""
    return _rows("orders.db", """
        SELECT order_status,
               COUNT(*) as order_count,
               SUM(CASE WHEN billed=1 THEN 1 ELSE 0 END) as billed_count,
               SUM(CASE WHEN paid=1 THEN 1 ELSE 0 END) as paid_count
        FROM orders GROUP BY order_status ORDER BY order_count DESC
    """)

@app.get("/reports/orders-by-date", tags=["Reports"])
def orders_by_date(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    status: Optional[str] = Query(None, description="Optional order status filter"),
    _key=Depends(require_api_key)
):
    """Orders created within a date range with count and status breakdown."""
    params = [start_date, end_date]
    status_filter = ""
    if status:
        status_filter = " AND order_status = ?"
        params.append(status)
    rows = _rows("orders.db", """
        SELECT order_status,
               COUNT(*) as count,
               MIN(order_date) as earliest,
               MAX(order_date) as latest
        FROM orders
        WHERE (
            CASE
                WHEN order_date GLOB '????-??-??*'
                    THEN substr(order_date, 1, 10)
                WHEN order_date GLOB '??/??/????*'
                    THEN substr(order_date, 7, 4) || '-' || substr(order_date, 1, 2) || '-' || substr(order_date, 4, 2)
                ELSE NULL
            END
        ) BETWEEN ? AND ?
        """ + status_filter + """
        GROUP BY order_status
        ORDER BY count DESC
    """, tuple(params))
    total = sum(r["count"] for r in rows)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "total_orders": total,
        "by_status": rows
    }

# ══════════════════════════════════════════════════════════════════════════
#  AGENT SUMMARY
# ══════════════════════════════════════════════════════════════════════════
@app.get("/agent/morning-summary", tags=["Agent"])
def morning_summary(_key=Depends(require_api_key)):
    """One-call daily digest for Nova's morning intake."""
    # Patient counts
    pt = _one("patients.db", "SELECT COUNT(*) as total FROM patients") or {}

    # Order status counts
    status_rows = _rows("orders.db", "SELECT order_status, COUNT(*) as cnt FROM orders GROUP BY order_status")
    statuses = {r["order_status"]: r["cnt"] for r in status_rows}

    # Pending approvals
    pending_approvals = _one("orders.db",
        "SELECT COUNT(*) as cnt FROM orders WHERE order_status='Pending Approval' AND agent_created=1") or {}

    # Refills due today and next 7 days
    try:
        from dmelogic.db.refills import fetch_refills_due
        today = date.today()
        due_today = fetch_refills_due(today.isoformat(), today.isoformat(), today.isoformat(), folder_path=FOLDER_PATH)
        due_week = fetch_refills_due(today.isoformat(), (today+timedelta(days=7)).isoformat(), today.isoformat(), folder_path=FOLDER_PATH)
    except Exception:
        due_today, due_week = [], []

    # Low/out of stock
    low_stock = _rows("inventory.db", """
        SELECT hcpcs_code, description, COALESCE(stock_quantity,0) as qty
        FROM inventory WHERE COALESCE(stock_quantity,0) <= COALESCE(reorder_level,10)
        AND COALESCE(stock_quantity,0) >= 0
        ORDER BY stock_quantity ASC LIMIT 10
    """)

    # Billing summary
    billing = _one("billing.db", """
        SELECT SUM(COALESCE(claim_amount,0)) as total_billed,
               SUM(COALESCE(paid_amount,0)) as total_paid,
               SUM(CASE WHEN status NOT IN ('Paid','Closed','Denied') THEN
                   COALESCE(claim_amount,0)-COALESCE(paid_amount,0) ELSE 0 END) as outstanding
        FROM claims
    """) or {}

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "patients": {"total": pt.get("total", 0)},
        "orders": {
            "by_status": statuses,
            "pending_approvals": pending_approvals.get("cnt", 0),
        },
        "refills": {
            "due_today": len(due_today),
            "due_next_7_days": len(due_week),
            "due_today_list": due_today[:5],
        },
        "inventory": {
            "low_or_out_of_stock_count": len(low_stock),
            "items": low_stock,
        },
        "billing": {
            "total_billed": float(billing.get("total_billed") or 0),
            "total_paid": float(billing.get("total_paid") or 0),
            "outstanding_ar": float(billing.get("outstanding") or 0),
        },
    }

@app.get("/agent/capabilities", tags=["Agent"])
def list_capabilities(_key=Depends(require_api_key)):
    """List all available API endpoints for agent discovery."""
    return {
        "endpoints": [
            {"path": r.path, "methods": list(r.methods), "tags": r.tags}
            for r in app.routes if hasattr(r, "methods")
        ]
    }


# ── Remittance ────────────────────────────────────────────────────────
@app.post("/remittance/parse", tags=["Remittance"])
def parse_remittance(req: RemittanceRequest, _key=Depends(require_api_key)):
    """Parse a NY MMIS Title XIX remittance PDF and match to DMELogic orders."""
    try:
        import sys as _sys

        _HERE = os.path.dirname(os.path.abspath(__file__))
        if _HERE not in _sys.path:
            _sys.path.insert(0, _HERE)

        from dmelogic.nova_remittance_parser import process_remittance

        if not os.path.exists(req.pdf_path):
            raise HTTPException(status_code=404, detail=f"PDF not found: {req.pdf_path}")

        result = process_remittance(req.pdf_path, FOLDER_PATH)
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Remittance parse error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
    log.info(f"DMELogic API v2.0 starting on {API_HOST}:{API_PORT}")
    log.info(f"DB folder: {FOLDER_PATH}")
    log.info(f"Docs: http://{API_HOST}:{API_PORT}/docs")
    uvicorn.run("dmelogic_api:app", host=API_HOST, port=API_PORT, reload=False)
