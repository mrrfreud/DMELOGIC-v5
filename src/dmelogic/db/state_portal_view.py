"""
State Portal Order View - Presentation layer for state billing portal.

This module provides a view mapping layer that takes neutral Order domain
models and produces fields arranged exactly how the state portal expects them.

The underlying database and business rules stay neutral and can later feed:
- HCFA-1500 claim forms
- UB-04 institutional claims  
- Electronic claims (837P, 837I)
- State-specific portals
- Private insurance portals

This separation allows:
1. Domain model to stay clean and normalized
2. Multiple view mappings for different outputs
3. Easy adaptation when portal formats change
4. Testing views without database dependencies
"""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import date
from decimal import Decimal

from .models import Order, OrderItem, OrderStatus, BillingType


@dataclass
class StatePortalOrderView:
    """
    Order data formatted for state billing portal submission.
    
    This is a VIEW - it doesn't persist to database, it's a transformation
    of Order data into the format required by the state portal.
    
    Different states may require different field arrangements - create
    state-specific subclasses as needed (e.g., CaliforniaPortalView).
    """
    
    # Patient Information (formatted for portal) - Required fields first
    patient_name_formatted: str  # "LAST, FIRST" or state-required format
    patient_first_name: str
    patient_last_name: str
    patient_dob_formatted: str  # MM/DD/YYYY or state format
    patient_address_line1: str
    patient_city: str
    patient_state: str
    patient_zip: str
    
    # Prescriber Information - Required fields
    prescriber_name_formatted: str  # "Dr. Last, First" or state format
    prescriber_npi: str
    
    # Insurance/Billing - Required fields
    primary_insurance_name: str
    primary_insurance_id: str  # Member/Policy ID
    billing_type_code: str  # Code expected by portal (e.g., "INS", "CASH")
    
    # Order Details - Required fields
    rx_date_formatted: str  # Date format required by portal
    order_date_formatted: str
    
    # Clinical Information - Required fields
    primary_diagnosis: str  # ICD-10 code
    
    # Optional fields with defaults
    patient_address_line2: Optional[str] = None
    patient_phone_formatted: str = ""  # (XXX) XXX-XXXX or state format
    prescriber_phone_formatted: str = ""
    prescriber_address: Optional[str] = None
    delivery_date_formatted: Optional[str] = None
    secondary_diagnoses: List[str] = field(default_factory=list)
    doctor_directions: Optional[str] = None
    
    # Line Items (formatted for portal)
    line_items: List["StatePortalLineItem"] = field(default_factory=list)
    
    # Totals (calculated for portal submission)
    total_allowed_amount: Decimal = Decimal("0.00")
    total_billed_amount: Decimal = Decimal("0.00")
    
    # Status and Workflow (for portal tracking)
    portal_status: str = "PENDING"  # Portal-specific status
    claim_number: Optional[str] = None  # Assigned by portal after submission
    submission_date: Optional[str] = None
    
    # Internal tracking (not sent to portal)
    internal_order_id: int = 0
    internal_notes: Optional[str] = None
    
    @classmethod
    def from_order(cls, order: Order, folder_path: Optional[str] = None) -> "StatePortalOrderView":
        """
        Create state portal view from domain Order model.
        
        This is the key transformation method - takes normalized Order
        and produces portal-ready fields.
        
        Args:
            order: Domain Order model
            folder_path: Optional database folder for FK resolution
        
        Returns:
            StatePortalOrderView ready for portal submission
        """
        # Get current patient data via FK if available, with name+DOB fallback
        current_patient = None
        try:
            # Prefer explicit patient_id on the order, if present
            if order.patient_id:
                from .patients import fetch_patient_by_id
                current_patient = fetch_patient_by_id(order.patient_id, folder_path=folder_path)
            
            # Fallback: match by patient name + DOB snapshot on the order
            if current_patient is None:
                from .patients import find_patient_by_name_and_dob
                
                # Extract name from snapshot
                last_name = ""
                first_name = ""
                if order.patient_name_at_order_time and ',' in order.patient_name_at_order_time:
                    parts = order.patient_name_at_order_time.split(',')
                    last_name = parts[0].strip()
                    first_name = parts[1].strip() if len(parts) > 1 else ""
                
                # Get DOB from snapshot
                dob = None
                if order.patient_dob_at_order_time:
                    if isinstance(order.patient_dob_at_order_time, date):
                        dob = order.patient_dob_at_order_time.strftime("%Y-%m-%d")
                    else:
                        dob = str(order.patient_dob_at_order_time)
                
                if last_name and first_name:
                    current_patient = find_patient_by_name_and_dob(
                        last_name,
                        first_name,
                        dob,
                        folder_path=folder_path,
                    )
        except Exception as e:
            print(f"PORTAL: error loading patient for order {order.id}: {e}")
        
        # DEBUG: Log what we got
        print(f"PORTAL current_patient: {current_patient}")
        if current_patient:
            try:
                addr = current_patient.get("address") or current_patient.get("address") if "address" in current_patient.keys() else None
                print(f"PORTAL address: {addr}")
            except Exception as e:
                print(f"PORTAL address access error: {e}")
        
        # Get current prescriber data via FK if available
        current_prescriber = order.get_current_prescriber(folder_path) if order.prescriber_id else None
        
        # Patient name formatting
        if current_patient:
            # Handle both sqlite3.Row and Patient domain model
            if hasattr(current_patient, 'first_name'):
                # Domain model
                patient_first = current_patient.first_name
                patient_last = current_patient.last_name
            else:
                # sqlite3.Row
                patient_first = current_patient['first_name']
                patient_last = current_patient['last_name']
        else:
            # Fall back to snapshot
            patient_first = order.patient_name_at_order_time.split(',')[1].strip() if order.patient_name_at_order_time and ',' in order.patient_name_at_order_time else ""
            patient_last = order.patient_name_at_order_time.split(',')[0].strip() if order.patient_name_at_order_time and ',' in order.patient_name_at_order_time else ""
        
        patient_name_formatted = f"{patient_last}, {patient_first}"
        
        # Patient DOB formatting (MM/DD/YYYY for most portals)
        patient_dob_formatted = ""
        if order.patient_dob_at_order_time:
            if isinstance(order.patient_dob_at_order_time, date):
                patient_dob_formatted = order.patient_dob_at_order_time.strftime("%m/%d/%Y")
            else:
                # Handle string dates
                patient_dob_formatted = str(order.patient_dob_at_order_time)
        
        # Patient address:
        # 1) Prefer the current patient record (live demographics)
        # 2) Fall back to the snapshot string stored on the order
        address_line1 = ""
        patient_city = ""
        patient_state = ""
        patient_zip = ""

        # Prefer current patient demographics if we have them
        if current_patient:
            # fetch_patient_by_id returns sqlite3.Row, not Patient domain model
            try:
                address_line1 = (current_patient.get("address") or "").strip()
                patient_city = (current_patient.get("city") or "").strip()
                patient_state = (current_patient.get("state") or "").strip()
                # Handle both 'zip' and 'zip_code' column names
                patient_zip = (current_patient.get("zip_code") or current_patient.get("zip") or "").strip()
            except (KeyError, AttributeError):
                pass  # Fall through to snapshot fallback

        # If that's still empty (older orders / missing address), use the snapshot
        if not any([address_line1, patient_city, patient_state, patient_zip]):
            address_parts = (order.patient_address_at_order_time or "").split(",")
            address_line1 = address_parts[0].strip() if len(address_parts) > 0 else ""
            patient_city = address_parts[1].strip() if len(address_parts) > 1 else ""
            patient_state = address_parts[2].strip() if len(address_parts) > 2 else ""
            patient_zip = address_parts[3].strip() if len(address_parts) > 3 else ""
        
        # Prescriber formatting
        prescriber_name = order.prescriber_name_at_order_time or ""
        prescriber_npi = order.prescriber_npi_at_order_time or ""
        
        # Insurance
        insurance_name = order.insurance_name_at_order_time or "Self-Pay"
        insurance_id = order.insurance_id_at_order_time or ""
        
        # Billing type code mapping
        billing_code_map = {
            BillingType.INSURANCE: "INS",
            BillingType.MEDICARE: "MED",
            BillingType.MEDICAID: "MCD",
            BillingType.CASH: "CASH",
            BillingType.RENTAL: "RENT",
        }
        billing_code = billing_code_map.get(order.billing_type, "INS")
        
        # Date formatting (MM/DD/YYYY)
        rx_date_fmt = order.rx_date.strftime("%m/%d/%Y") if order.rx_date else ""
        order_date_fmt = order.order_date.strftime("%m/%d/%Y") if order.order_date else ""
        delivery_date_fmt = order.delivery_date.strftime("%m/%d/%Y") if order.delivery_date else None
        
        # ICD codes
        primary_dx = order.icd_codes[0] if order.icd_codes else ""
        secondary_dx = order.icd_codes[1:] if len(order.icd_codes) > 1 else []
        
        # Line items
        line_items = [
            StatePortalLineItem.from_order_item(item, line_num=idx+1)
            for idx, item in enumerate(order.items)
        ]
        
        # Calculate totals
        total_billed = sum(item.line_total for item in line_items)
        
        # Portal status mapping
        portal_status_map = {
            OrderStatus.PENDING: "PENDING",
            OrderStatus.DOCS_NEEDED: "INCOMPLETE",
            OrderStatus.READY: "READY",
            OrderStatus.DELIVERED: "DELIVERED",
            OrderStatus.BILLED: "SUBMITTED",
            OrderStatus.DENIED: "DENIED",
            OrderStatus.PAID: "PAID",
            OrderStatus.CLOSED: "CLOSED",
            OrderStatus.CANCELLED: "CANCELLED",
            OrderStatus.ON_HOLD: "ON_HOLD",
        }
        portal_status = portal_status_map.get(order.order_status, "PENDING")
        
        return cls(
            patient_name_formatted=patient_name_formatted,
            patient_first_name=patient_first,
            patient_last_name=patient_last,
            patient_dob_formatted=patient_dob_formatted,
            patient_address_line1=address_line1,
            patient_city=patient_city,
            patient_state=patient_state,
            patient_zip=patient_zip,
            patient_phone_formatted="",  # TODO: Format phone from order
            prescriber_name_formatted=prescriber_name,
            prescriber_npi=prescriber_npi,
            primary_insurance_name=insurance_name,
            primary_insurance_id=insurance_id,
            billing_type_code=billing_code,
            rx_date_formatted=rx_date_fmt,
            order_date_formatted=order_date_fmt,
            delivery_date_formatted=delivery_date_fmt,
            primary_diagnosis=primary_dx,
            secondary_diagnoses=secondary_dx,
            doctor_directions=order.doctor_directions,
            line_items=line_items,
            total_billed_amount=total_billed,
            portal_status=portal_status,
            internal_order_id=order.id,
            internal_notes=order.notes,
        )
    
    def to_portal_json(self) -> dict:
        """
        Convert to JSON structure expected by state portal API.
        
        Adjust field names and structure based on actual portal API spec.
        """
        return {
            "patient": {
                "name": self.patient_name_formatted,
                "firstName": self.patient_first_name,
                "lastName": self.patient_last_name,
                "dateOfBirth": self.patient_dob_formatted,
                "address": {
                    "line1": self.patient_address_line1,
                    "line2": self.patient_address_line2,
                    "city": self.patient_city,
                    "state": self.patient_state,
                    "zip": self.patient_zip,
                },
                "phone": self.patient_phone_formatted,
            },
            "prescriber": {
                "name": self.prescriber_name_formatted,
                "npi": self.prescriber_npi,
                "phone": self.prescriber_phone_formatted,
            },
            "insurance": {
                "name": self.primary_insurance_name,
                "memberId": self.primary_insurance_id,
            },
            "claim": {
                "billingType": self.billing_type_code,
                "rxDate": self.rx_date_formatted,
                "orderDate": self.order_date_formatted,
                "deliveryDate": self.delivery_date_formatted,
                "primaryDiagnosis": self.primary_diagnosis,
                "secondaryDiagnoses": self.secondary_diagnoses,
                "directions": self.doctor_directions,
                "lineItems": [item.to_portal_json() for item in self.line_items],
                "totalAmount": str(self.total_billed_amount),
            },
            "status": self.portal_status,
            "claimNumber": self.claim_number,
        }
    
    def to_csv_row(self) -> List[str]:
        """
        Convert to CSV row for bulk portal upload.
        
        Many state portals accept CSV uploads. Adjust order/format
        based on portal's CSV template.
        """
        return [
            self.patient_last_name,
            self.patient_first_name,
            self.patient_dob_formatted,
            self.patient_address_line1,
            self.patient_city,
            self.patient_state,
            self.patient_zip,
            self.prescriber_name_formatted,
            self.prescriber_npi,
            self.primary_insurance_name,
            self.primary_insurance_id,
            self.rx_date_formatted,
            self.primary_diagnosis,
            self.doctor_directions or "",
            str(self.total_billed_amount),
        ]


@dataclass
class StatePortalLineItem:
    """Portal-formatted line item."""
    line_number: int
    hcpcs_code: str
    description: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    modifier: Optional[str] = None
    place_of_service: str = "12"  # 12 = Home (common for DME)
    
    @classmethod
    def from_order_item(cls, item: OrderItem, line_num: int) -> "StatePortalLineItem":
        """Convert domain OrderItem to portal line item."""
        unit_price = item.cost_ea or Decimal("0.00")
        line_total = item.total_cost or (unit_price * item.quantity)
        
        return cls(
            line_number=line_num,
            hcpcs_code=item.hcpcs_code,
            description=item.description,
            quantity=item.quantity,
            unit_price=unit_price,
            line_total=line_total,
        )
    
    def to_portal_json(self) -> dict:
        """Convert to portal JSON format."""
        return {
            "lineNumber": self.line_number,
            "hcpcsCode": self.hcpcs_code,
            "description": self.description,
            "quantity": self.quantity,
            "unitPrice": str(self.unit_price),
            "total": str(self.line_total),
            "modifier": self.modifier,
            "placeOfService": self.place_of_service,
        }


# ============================================================================
# State-Specific Views (Examples)
# ============================================================================

class CaliforniaPortalOrderView(StatePortalOrderView):
    """California-specific portal view with state requirements."""
    
    def to_portal_json(self) -> dict:
        """Override for California-specific format."""
        base = super().to_portal_json()
        # Add California-specific fields
        base["californiaFields"] = {
            "countyCode": "",  # Required by CA
            "facilityNPI": "",  # If institutional
        }
        return base


class TexasPortalOrderView(StatePortalOrderView):
    """Texas-specific portal view."""
    
    def to_portal_json(self) -> dict:
        """Override for Texas-specific format."""
        base = super().to_portal_json()
        # Texas requires different field names
        base["texasFields"] = {
            "providerNumber": self.prescriber_npi,
            "recipientId": self.primary_insurance_id,
        }
        return base
