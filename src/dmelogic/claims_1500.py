"""
HCFA-1500 (CMS-1500) claim form data structure and mapping.

This module provides a clean mapping layer:
Order domain model → HCFA-1500 claim data

NO SQL queries - all data comes from the Order domain object.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from dmelogic.db.models import Order, OrderItem, BillingType


@dataclass
class Hcfa1500ServiceLine:
    """Single service line on HCFA-1500 form (Box 24)."""
    
    # Box 24A - Date(s) of Service
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    
    # Box 24B - Place of Service
    place_of_service: str = "12"  # Home (default for DME)
    
    # Box 24C - EMG (Emergency)
    emergency: bool = False
    
    # Box 24D - Procedures, Services, or Supplies
    procedure_code: str = ""  # HCPCS code
    modifier_1: str = ""
    modifier_2: str = ""
    modifier_3: str = ""
    modifier_4: str = ""
    
    # Box 24E - Diagnosis Pointer
    diagnosis_pointer: str = ""  # e.g., "A" or "A,B"
    
    # Box 24F - Charges
    charges: Decimal = Decimal("0.00")
    
    # Box 24G - Days or Units
    units: int = 1
    
    # Box 24H - EPSDT Family Plan
    epsdt: str = ""
    
    # Box 24I - ID Qualifier (for rendering provider)
    id_qualifier: str = ""
    
    # Box 24J - Rendering Provider ID
    rendering_provider_id: str = ""
    
    # Additional metadata (not on form but useful)
    description: str = ""
    item_number: str = ""


@dataclass
class Hcfa1500Claim:
    """
    Complete HCFA-1500 (CMS-1500) claim form data structure.
    
    Maps to all boxes on the standard CMS-1500 form.
    Field names follow "box_N" convention for clarity.
    """
    
    # CARRIER SECTION (Top)
    carrier_name: str = ""
    carrier_address_line1: str = ""
    carrier_address_line2: str = ""
    carrier_city_state_zip: str = ""
    
    # Box 1 - Type of Insurance
    box_1_insurance_type: str = "MEDICARE"  # MEDICARE, MEDICAID, TRICARE, etc.
    box_1_medicare: bool = False
    box_1_medicaid: bool = False
    box_1_tricare: bool = False
    box_1_champva: bool = False
    box_1_group_health: bool = False
    box_1_feca: bool = False
    box_1_other: bool = False
    
    # Box 1a - Insured's ID Number
    box_1a_insured_id: str = ""
    
    # Box 2 - Patient's Name (Last, First, Middle)
    box_2_patient_name: str = ""
    
    # Box 3 - Patient's Birth Date and Sex
    box_3_patient_dob: Optional[date] = None
    box_3_patient_sex: str = ""  # M or F
    
    # Box 4 - Insured's Name
    box_4_insured_name: str = ""
    
    # Box 5 - Patient's Address
    box_5_patient_address: str = ""
    box_5_patient_city: str = ""
    box_5_patient_state: str = ""
    box_5_patient_zip: str = ""
    box_5_patient_phone: str = ""
    
    # Box 6 - Patient Relationship to Insured
    box_6_self: bool = True
    box_6_spouse: bool = False
    box_6_child: bool = False
    box_6_other: bool = False
    
    # Box 7 - Insured's Address
    box_7_insured_address: str = ""
    box_7_insured_city: str = ""
    box_7_insured_state: str = ""
    box_7_insured_zip: str = ""
    box_7_insured_phone: str = ""
    
    # Box 8 - Reserved for NUCC Use
    
    # Box 9 - Other Insured's Name
    box_9_other_insured_name: str = ""
    box_9a_other_insured_policy: str = ""
    box_9b_other_insured_dob: Optional[date] = None
    box_9b_other_insured_sex: str = ""
    box_9c_other_insured_employer: str = ""
    box_9d_other_insurance_name: str = ""
    
    # Box 10 - Is Patient's Condition Related To
    box_10a_employment: bool = False
    box_10b_auto_accident: bool = False
    box_10b_state: str = ""
    box_10c_other_accident: bool = False
    box_10d_claim_codes: str = ""  # Condition codes
    
    # Box 11 - Insured's Policy Group or FECA Number
    box_11_insured_policy_group: str = ""
    box_11a_insured_dob: Optional[date] = None
    box_11a_insured_sex: str = ""
    box_11b_other_claim_id: str = ""
    box_11c_insurance_plan_name: str = ""
    box_11d_is_other_insurance: bool = False
    
    # Box 12 - Patient's or Authorized Person's Signature
    box_12_patient_signature: str = "Signature on File"
    box_12_date: Optional[date] = None
    
    # Box 13 - Insured's or Authorized Person's Signature
    box_13_insured_signature: str = "Signature on File"
    
    # Box 14 - Date of Current Illness, Injury, or Pregnancy
    box_14_date_illness: Optional[date] = None
    box_14_qualifier: str = ""  # 431=Onset, 484=Last Menstrual Period
    
    # Box 15 - Other Date
    box_15_other_date: Optional[date] = None
    box_15_qualifier: str = ""
    
    # Box 16 - Dates Unable to Work
    box_16_from_date: Optional[date] = None
    box_16_to_date: Optional[date] = None
    
    # Box 17 - Name of Referring Provider
    box_17_referring_provider_name: str = ""
    box_17a_qualifier: str = ""  # 0B=State License, 1G=Provider UPIN
    box_17a_id: str = ""
    box_17b_npi: str = ""
    
    # Box 18 - Hospitalization Dates
    box_18_from_date: Optional[date] = None
    box_18_to_date: Optional[date] = None
    
    # Box 19 - Additional Claim Information
    box_19_additional_info: str = ""
    
    # Box 20 - Outside Lab
    box_20_outside_lab: bool = False
    box_20_charges: Decimal = Decimal("0.00")
    
    # Box 21 - Diagnosis or Nature of Illness (ICD-10)
    box_21_diagnosis_a: str = ""
    box_21_diagnosis_b: str = ""
    box_21_diagnosis_c: str = ""
    box_21_diagnosis_d: str = ""
    box_21_diagnosis_e: str = ""
    box_21_diagnosis_f: str = ""
    box_21_diagnosis_g: str = ""
    box_21_diagnosis_h: str = ""
    box_21_diagnosis_i: str = ""
    box_21_diagnosis_j: str = ""
    box_21_diagnosis_k: str = ""
    box_21_diagnosis_l: str = ""
    
    # Box 22 - Resubmission Code
    box_22_resubmission_code: str = ""
    box_22_original_ref: str = ""
    
    # Box 23 - Prior Authorization Number
    box_23_prior_auth: str = ""
    
    # Box 24 - Service Lines (up to 6 lines)
    service_lines: list[Hcfa1500ServiceLine] = field(default_factory=list)
    
    # Box 25 - Federal Tax ID Number
    box_25_tax_id: str = ""
    box_25_ssn: bool = False
    box_25_ein: bool = True
    
    # Box 26 - Patient's Account Number
    box_26_patient_account: str = ""
    
    # Box 27 - Accept Assignment
    box_27_accept_assignment: bool = True
    
    # Box 28 - Total Charge
    box_28_total_charge: Decimal = Decimal("0.00")
    
    # Box 29 - Amount Paid
    box_29_amount_paid: Decimal = Decimal("0.00")
    
    # Box 30 - Reserved for NUCC Use (formerly Balance Due)
    box_30_balance_due: Decimal = Decimal("0.00")
    
    # Box 31 - Signature of Physician or Supplier
    box_31_signature: str = "Signature on File"
    box_31_date: Optional[date] = None
    
    # Box 32 - Service Facility Location
    box_32_facility_name: str = ""
    box_32_facility_address: str = ""
    box_32_facility_city_state_zip: str = ""
    box_32_facility_npi: str = ""
    box_32a_npi: str = ""
    box_32b_other_id: str = ""
    
    # Box 33 - Billing Provider Info & Phone
    box_33_billing_name: str = ""
    box_33_billing_address: str = ""
    box_33_billing_city_state_zip: str = ""
    box_33_billing_phone: str = ""
    box_33a_npi: str = ""
    box_33b_other_id: str = ""
    
    def to_dict(self) -> dict:
        """Convert claim to dictionary for JSON output or processing."""
        result = {}
        
        # Carrier
        result["carrier_name"] = self.carrier_name
        result["carrier_address_line1"] = self.carrier_address_line1
        result["carrier_address_line2"] = self.carrier_address_line2
        result["carrier_city_state_zip"] = self.carrier_city_state_zip
        
        # Box 1
        result["box_1_insurance_type"] = self.box_1_insurance_type
        result["box_1_medicare"] = self.box_1_medicare
        result["box_1_medicaid"] = self.box_1_medicaid
        result["box_1_tricare"] = self.box_1_tricare
        result["box_1_champva"] = self.box_1_champva
        result["box_1_group_health"] = self.box_1_group_health
        result["box_1_feca"] = self.box_1_feca
        result["box_1_other"] = self.box_1_other
        
        # Box 1a-33
        for attr in dir(self):
            if attr.startswith("box_") and not attr.startswith("box_1_"):
                value = getattr(self, attr)
                if isinstance(value, (date, Decimal)):
                    result[attr] = str(value)
                elif not callable(value):
                    result[attr] = value
        
        # Service lines
        result["service_lines"] = [
            {
                "date_from": str(line.date_from) if line.date_from else "",
                "date_to": str(line.date_to) if line.date_to else "",
                "place_of_service": line.place_of_service,
                "emergency": line.emergency,
                "procedure_code": line.procedure_code,
                "modifier_1": line.modifier_1,
                "modifier_2": line.modifier_2,
                "modifier_3": line.modifier_3,
                "modifier_4": line.modifier_4,
                "diagnosis_pointer": line.diagnosis_pointer,
                "charges": str(line.charges),
                "units": line.units,
                "epsdt": line.epsdt,
                "id_qualifier": line.id_qualifier,
                "rendering_provider_id": line.rendering_provider_id,
                "description": line.description,
                "item_number": line.item_number,
            }
            for line in self.service_lines
        ]
        
        return result


def hcfa1500_from_order(order: Order, folder_path: Optional[str] = None) -> Hcfa1500Claim:
    """
    Map an Order domain object to HCFA-1500 claim data.
    
    This is a pure transformation - NO SQL queries.
    All data comes from the Order object or lookups via FK references.
    
    Args:
        order: Rich Order domain object with snapshot fields
        folder_path: Path to data folder for FK lookups
    
    Returns:
        Hcfa1500Claim with all boxes populated
    """
    claim = Hcfa1500Claim()
    
    # --- Box 2: Patient Name (from snapshot) ---
    claim.box_2_patient_name = order.patient_name_at_order_time or ""
    
    # --- Box 3: Patient DOB and Sex (from snapshot) ---
    claim.box_3_patient_dob = order.patient_dob_at_order_time
    # Note: patient_sex not in snapshot - would need to fetch from patient FK if needed
    claim.box_3_patient_sex = ""  # TODO: Add to Order snapshot or fetch via FK
    
    # --- Box 5: Patient Address (from snapshot) ---
    # patient_address_at_order_time can be string or dict depending on schema
    addr_snapshot = order.patient_address_at_order_time
    if addr_snapshot:
        if isinstance(addr_snapshot, dict):
            claim.box_5_patient_address = addr_snapshot.get("street", "")
            claim.box_5_patient_city = addr_snapshot.get("city", "")
            claim.box_5_patient_state = addr_snapshot.get("state", "")
            claim.box_5_patient_zip = addr_snapshot.get("zip", "")
        elif isinstance(addr_snapshot, str):
            # If stored as single string, put it in address field
            claim.box_5_patient_address = addr_snapshot
    # Note: patient_phone not in snapshot - would need to fetch from patient FK if needed
    claim.box_5_patient_phone = ""  # TODO: Add to Order snapshot or fetch via FK
    
    # --- Box 1a & 4: Insured's ID and Name (from snapshot) ---
    claim.box_1a_insured_id = order.insurance_id_at_order_time or ""
    claim.box_4_insured_name = order.patient_name_at_order_time or ""  # Usually same as patient
    
    # --- Box 11c: Insurance Plan Name (from snapshot) ---
    claim.box_11c_insurance_plan_name = order.insurance_name_at_order_time or ""
    
    # --- Box 1: Insurance Type (from billing_type enum) ---
    if order.billing_type == BillingType.MEDICARE:
        claim.box_1_insurance_type = "MEDICARE"
        claim.box_1_medicare = True
    elif order.billing_type == BillingType.MEDICAID:
        claim.box_1_insurance_type = "MEDICAID"
        claim.box_1_medicaid = True
    elif order.billing_type == BillingType.INSURANCE:
        claim.box_1_insurance_type = "GROUP HEALTH PLAN"
        claim.box_1_group_health = True
    elif order.billing_type == BillingType.CASH:
        claim.box_1_insurance_type = "SELF PAY"
        claim.box_1_other = True
    else:
        claim.box_1_insurance_type = "OTHER"
        claim.box_1_other = True
    
    # --- Box 6: Patient Relationship to Insured (default: Self) ---
    claim.box_6_self = True
    
    # --- Box 17: Referring/Prescribing Provider (from snapshot) ---
    claim.box_17_referring_provider_name = order.prescriber_name_at_order_time or ""
    claim.box_17b_npi = order.prescriber_npi_at_order_time or ""
    
    # --- Box 21: Diagnosis Codes (from icd_codes list) ---
    icd_codes = order.icd_codes or []
    diagnosis_fields = [
        "box_21_diagnosis_a", "box_21_diagnosis_b", "box_21_diagnosis_c",
        "box_21_diagnosis_d", "box_21_diagnosis_e", "box_21_diagnosis_f",
        "box_21_diagnosis_g", "box_21_diagnosis_h", "box_21_diagnosis_i",
        "box_21_diagnosis_j", "box_21_diagnosis_k", "box_21_diagnosis_l"
    ]
    for idx, icd_code in enumerate(icd_codes[:12]):  # Max 12 diagnosis codes
        setattr(claim, diagnosis_fields[idx], icd_code)
    
    # --- Box 24: Service Lines (from order.items) ---
    total_charges = Decimal("0.00")
    
    for idx, item in enumerate(order.items[:6]):  # Max 6 service lines per claim
        line = Hcfa1500ServiceLine()
        
        # Dates
        line.date_from = order.order_date
        line.date_to = order.order_date
        
        # Place of Service: 12 = Home (standard for DME)
        line.place_of_service = "12"
        
        # Procedure code (HCPCS)
        line.procedure_code = item.hcpcs_code or ""
        line.description = item.description or ""
        line.item_number = item.item_number or ""
        
        # Diagnosis pointer: map to first available diagnosis
        if icd_codes:
            # Point to diagnosis A (first one)
            line.diagnosis_pointer = "A"
        
        # Charges and Units
        line.charges = item.total_cost or Decimal("0.00")
        line.units = item.quantity or 1
        
        # Rendering provider (prescriber NPI)
        line.id_qualifier = "1C"  # NPI
        line.rendering_provider_id = order.prescriber_npi_at_order_time or ""
        
        claim.service_lines.append(line)
        total_charges += line.charges
    
    # --- Box 26: Patient Account Number ---
    claim.box_26_patient_account = f"ORD-{order.id:03d}"
    
    # --- Box 28: Total Charge ---
    claim.box_28_total_charge = total_charges
    
    # --- Box 27: Accept Assignment ---
    claim.box_27_accept_assignment = True
    
    # --- Box 31: Signature Date ---
    claim.box_31_date = order.order_date
    
    # --- Box 12: Patient Signature Date ---
    claim.box_12_date = order.order_date
    
    # --- Box 33: Billing Provider (load from settings/config if available) ---
    # This would come from your practice settings
    # For now, leave blank - will be filled by PDF generator from config
    
    return claim
