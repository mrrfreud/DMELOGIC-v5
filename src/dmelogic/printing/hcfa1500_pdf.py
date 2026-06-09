"""
HCFA-1500 (CMS-1500) PDF generator using ReportLab.

This module renders claim data onto letter-size pages optimized for:
1. Preprinted red CMS-1500 forms (default mode)
2. Plain paper with optional background image

Architecture:
- Takes Hcfa1500Claim domain object (no SQL)
- Uses precise coordinate mapping for form fields
- Supports visual tuning via coordinate adjustments

Usage:
    from dmelogic.claims_1500 import hcfa1500_from_order
    from dmelogic.printing.hcfa1500_pdf import render_hcfa1500_pdf
    
    claim = hcfa1500_from_order(order, folder_path=folder_path)
    pdf_path = render_hcfa1500_pdf(claim, "output.pdf")
"""

from dataclasses import dataclass
from typing import Optional, Literal
from pathlib import Path
from decimal import Decimal

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth

from dmelogic.claims_1500 import Hcfa1500Claim


Align = Literal["left", "center", "right"]


@dataclass
class FieldSpec:
    """
    Specification for a single field on the CMS-1500 form.
    
    Coordinates use ReportLab convention:
    - Origin at bottom-left
    - Positive Y goes up
    - Units in points (1 inch = 72 points)
    """
    x: float          # X position in points
    y: float          # Y position in points
    max_width: float  # Maximum width in points
    font: str = "Helvetica"
    font_size: int = 9
    align: Align = "left"


# Page dimensions
PAGE_WIDTH, PAGE_HEIGHT = letter

# Field map for CMS-1500 form
# These coordinates are approximations and should be tuned by printing test pages
# on actual CMS-1500 forms and adjusting as needed
FIELD_MAP = {
    # Box 1a - Insured's ID Number (top right area)
    "insured_id": FieldSpec(
        x=6.3 * inch,
        y=10.0 * inch,
        max_width=2.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 2 - Patient's Name (Last, First, Middle)
    "patient_name": FieldSpec(
        x=0.9 * inch,
        y=9.55 * inch,
        max_width=3.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 3 - Patient's Birth Date
    "patient_dob": FieldSpec(
        x=4.3 * inch,
        y=9.55 * inch,
        max_width=1.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 3 - Patient's Sex
    "patient_sex": FieldSpec(
        x=5.5 * inch,
        y=9.55 * inch,
        max_width=0.3 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 5 - Patient's Address - Street
    "patient_address": FieldSpec(
        x=0.9 * inch,
        y=9.20 * inch,
        max_width=3.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 5 - Patient's Address - City
    "patient_city": FieldSpec(
        x=0.9 * inch,
        y=8.95 * inch,
        max_width=1.8 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 5 - Patient's Address - State
    "patient_state": FieldSpec(
        x=2.8 * inch,
        y=8.95 * inch,
        max_width=0.4 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 5 - Patient's Address - ZIP
    "patient_zip": FieldSpec(
        x=3.3 * inch,
        y=8.95 * inch,
        max_width=1.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 5 - Patient's Phone
    "patient_phone": FieldSpec(
        x=0.9 * inch,
        y=8.70 * inch,
        max_width=1.5 * inch,
        font_size=8,
        align="left",
    ),
    
    # Box 11c - Insurance Plan Name or Program Name
    "insurance_name": FieldSpec(
        x=4.8 * inch,
        y=8.4 * inch,
        max_width=3.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 17 - Name of Referring Provider
    "prescriber_name": FieldSpec(
        x=0.9 * inch,
        y=6.2 * inch,
        max_width=3.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 17b - NPI of Referring Provider
    "prescriber_npi": FieldSpec(
        x=3.2 * inch,
        y=6.0 * inch,
        max_width=1.5 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 21 - Diagnosis or Nature of Illness or Injury (ICD-10)
    # Box 21A
    "diagnosis_a": FieldSpec(
        x=0.9 * inch,
        y=5.6 * inch,
        max_width=1.0 * inch,
        font_size=8,
        align="left",
    ),
    
    # Box 21B
    "diagnosis_b": FieldSpec(
        x=2.1 * inch,
        y=5.6 * inch,
        max_width=1.0 * inch,
        font_size=8,
        align="left",
    ),
    
    # Box 21C
    "diagnosis_c": FieldSpec(
        x=3.3 * inch,
        y=5.6 * inch,
        max_width=1.0 * inch,
        font_size=8,
        align="left",
    ),
    
    # Box 21D
    "diagnosis_d": FieldSpec(
        x=4.5 * inch,
        y=5.6 * inch,
        max_width=1.0 * inch,
        font_size=8,
        align="left",
    ),
    
    # Box 23 - Prior Authorization Number
    "prior_auth": FieldSpec(
        x=5.8 * inch,
        y=5.6 * inch,
        max_width=1.8 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 26 - Patient's Account No.
    "patient_account": FieldSpec(
        x=0.9 * inch,
        y=2.3 * inch,
        max_width=1.5 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 28 - Total Charge
    "total_charge": FieldSpec(
        x=6.8 * inch,
        y=2.3 * inch,
        max_width=1.0 * inch,
        font_size=10,
        align="right",
    ),
    
    # Box 31 - Signature of Physician or Supplier
    "signature": FieldSpec(
        x=0.9 * inch,
        y=1.5 * inch,
        max_width=2.5 * inch,
        font_size=8,
        align="left",
    ),
    
    # Box 31 - Date
    "signature_date": FieldSpec(
        x=3.5 * inch,
        y=1.5 * inch,
        max_width=1.0 * inch,
        font_size=9,
        align="left",
    ),
    
    # Box 33 - Billing Provider Phone
    "billing_phone": FieldSpec(
        x=4.8 * inch,
        y=1.8 * inch,
        max_width=1.5 * inch,
        font_size=8,
        align="left",
    ),
    
    # Box 33a - Billing Provider NPI
    "billing_npi": FieldSpec(
        x=6.5 * inch,
        y=1.3 * inch,
        max_width=1.5 * inch,
        font_size=9,
        align="left",
    ),
}

# Service line configuration (Box 24)
LINE_BASE_Y = 5.0 * inch      # Y position of first service line
LINE_HEIGHT = 0.28 * inch     # Vertical spacing between lines
MAX_LINES = 6                  # Maximum service lines per page

# Column X positions for service line fields
LINE_COLS = {
    "from_date": 0.9 * inch,       # 24A - Date of Service From
    "to_date": 1.6 * inch,         # 24A - Date of Service To
    "place_of_service": 2.3 * inch, # 24B - Place of Service
    "emg": 2.7 * inch,             # 24C - EMG
    "hcpcs": 3.0 * inch,           # 24D - Procedures/Services/Supplies
    "modifier": 3.9 * inch,        # 24D - Modifiers
    "dx_pointer": 4.6 * inch,      # 24E - Diagnosis Pointer
    "charges": 5.3 * inch,         # 24F - Charges
    "units": 6.2 * inch,           # 24G - Days or Units
    "rendering_npi": 6.8 * inch,   # 24J - Rendering Provider ID
}


def _draw_text(c: canvas.Canvas, text: str, spec: FieldSpec):
    """
    Draw text at specified position with alignment and width constraint.
    
    Args:
        c: ReportLab Canvas object
        text: Text to draw
        spec: Field specification with position and formatting
    """
    if not text:
        return
    
    c.setFont(spec.font, spec.font_size)
    
    # Truncate text if too long
    t = str(text)
    while stringWidth(t, spec.font, spec.font_size) > spec.max_width and len(t) > 0:
        t = t[:-1]
    
    # Calculate X position based on alignment
    if spec.align == "left":
        x = spec.x
    elif spec.align == "right":
        text_width = stringWidth(t, spec.font, spec.font_size)
        x = spec.x + spec.max_width - text_width
    else:  # center
        text_width = stringWidth(t, spec.font, spec.font_size)
        x = spec.x + (spec.max_width - text_width) / 2.0
    
    c.drawString(x, spec.y, t)


def _line_y(row_index: int) -> float:
    """
    Calculate Y coordinate for a service line row.
    
    Args:
        row_index: Row index (0-based)
    
    Returns:
        Y coordinate in points
    """
    return LINE_BASE_Y - (row_index * LINE_HEIGHT)


def _format_date_mm_dd_yyyy(date_obj) -> str:
    """Format date as MM/DD/YYYY for CMS-1500 form."""
    if not date_obj:
        return ""
    try:
        return date_obj.strftime("%m/%d/%Y")
    except Exception:
        return str(date_obj)


def render_hcfa1500_pdf(
    claim: Hcfa1500Claim,
    out_path: str | Path,
    *,
    preprinted_form: bool = True,
    background_image: Optional[str | Path] = None,
) -> str:
    """
    Render a CMS-1500 claim form as PDF.
    
    This function creates a production-ready PDF that can be:
    1. Printed on preprinted red CMS-1500 forms (default)
    2. Printed on plain paper with background image
    
    Architecture:
    - Pure transformation: Hcfa1500Claim → PDF
    - No SQL queries, no UI dependencies
    - Coordinate-based positioning for precise alignment
    
    Args:
        claim: HCFA-1500 claim data object
        out_path: Output file path for PDF
        preprinted_form: If True, optimizes for preprinted forms (default)
        background_image: Optional path to form background image (for plain paper)
    
    Returns:
        Path to generated PDF file
    
    Example:
        >>> from dmelogic.claims_1500 import hcfa1500_from_order
        >>> claim = hcfa1500_from_order(order, folder_path=folder_path)
        >>> pdf_path = render_hcfa1500_pdf(claim, "claim_001.pdf")
    """
    out_path = str(out_path)
    c = canvas.Canvas(out_path, pagesize=letter)
    width, height = letter
    
    # Optional: Draw background image for printing on plain paper
    if background_image and not preprinted_form:
        try:
            c.drawImage(
                str(background_image),
                0, 0,
                width=width,
                height=height,
                preserveAspectRatio=True,
                mask='auto'
            )
        except Exception as e:
            print(f"⚠️ Could not draw background image: {e}")
    
    # ==========================================
    # CARRIER SECTION (Top of form)
    # ==========================================
    
    # Box 1a - Insured's ID Number
    _draw_text(c, claim.box_1a_insured_id, FIELD_MAP["insured_id"])
    
    # ==========================================
    # PATIENT INFORMATION (Box 2-8)
    # ==========================================
    
    # Box 2 - Patient's Name
    _draw_text(c, claim.box_2_patient_name, FIELD_MAP["patient_name"])
    
    # Box 3 - Patient's Birth Date and Sex
    dob_str = _format_date_mm_dd_yyyy(claim.box_3_patient_dob)
    _draw_text(c, dob_str, FIELD_MAP["patient_dob"])
    _draw_text(c, claim.box_3_patient_sex, FIELD_MAP["patient_sex"])
    
    # Box 5 - Patient's Address
    _draw_text(c, claim.box_5_patient_address, FIELD_MAP["patient_address"])
    _draw_text(c, claim.box_5_patient_city, FIELD_MAP["patient_city"])
    _draw_text(c, claim.box_5_patient_state, FIELD_MAP["patient_state"])
    _draw_text(c, claim.box_5_patient_zip, FIELD_MAP["patient_zip"])
    _draw_text(c, claim.box_5_patient_phone, FIELD_MAP["patient_phone"])
    
    # ==========================================
    # INSURANCE INFORMATION (Box 9-13)
    # ==========================================
    
    # Box 11c - Insurance Plan Name
    _draw_text(c, claim.box_11c_insurance_plan_name, FIELD_MAP["insurance_name"])
    
    # ==========================================
    # PHYSICIAN/SUPPLIER INFORMATION (Box 17)
    # ==========================================
    
    # Box 17 - Name of Referring Provider
    _draw_text(c, claim.box_17_referring_provider_name, FIELD_MAP["prescriber_name"])
    
    # Box 17b - NPI
    _draw_text(c, claim.box_17b_npi, FIELD_MAP["prescriber_npi"])
    
    # ==========================================
    # DIAGNOSIS CODES (Box 21)
    # ==========================================
    
    _draw_text(c, claim.box_21_diagnosis_a, FIELD_MAP["diagnosis_a"])
    _draw_text(c, claim.box_21_diagnosis_b, FIELD_MAP["diagnosis_b"])
    _draw_text(c, claim.box_21_diagnosis_c, FIELD_MAP["diagnosis_c"])
    _draw_text(c, claim.box_21_diagnosis_d, FIELD_MAP["diagnosis_d"])
    
    # Box 23 - Prior Authorization Number
    _draw_text(c, claim.box_23_prior_auth, FIELD_MAP["prior_auth"])
    
    # ==========================================
    # SERVICE LINES (Box 24)
    # ==========================================
    
    total_charges = Decimal("0.00")
    
    for idx, line in enumerate(claim.service_lines[:MAX_LINES]):
        row_y = _line_y(idx)
        
        c.setFont("Helvetica", 8)
        
        # 24A - Dates of Service
        from_str = _format_date_mm_dd_yyyy(line.date_from)
        to_str = _format_date_mm_dd_yyyy(line.date_to)
        
        c.drawString(LINE_COLS["from_date"], row_y, from_str)
        c.drawString(LINE_COLS["to_date"], row_y, to_str)
        
        # 24B - Place of Service
        c.drawString(LINE_COLS["place_of_service"], row_y, line.place_of_service or "")
        
        # 24C - EMG (Emergency indicator)
        if line.emergency:
            c.drawString(LINE_COLS["emg"], row_y, "Y")
        
        # 24D - Procedures, Services, or Supplies (HCPCS code)
        hcpcs_display = (line.procedure_code or "")[:10]
        c.drawString(LINE_COLS["hcpcs"], row_y, hcpcs_display)
        
        # 24D - Modifiers
        modifiers = []
        if line.modifier_1:
            modifiers.append(line.modifier_1)
        if line.modifier_2:
            modifiers.append(line.modifier_2)
        if line.modifier_3:
            modifiers.append(line.modifier_3)
        if line.modifier_4:
            modifiers.append(line.modifier_4)
        
        if modifiers:
            modifier_str = " ".join(modifiers)
            c.drawString(LINE_COLS["modifier"], row_y, modifier_str[:8])
        
        # 24E - Diagnosis Pointer
        c.drawString(LINE_COLS["dx_pointer"], row_y, line.diagnosis_pointer or "")
        
        # 24F - Charges
        total_charges += line.charges
        charges_str = f"{line.charges:.2f}"
        c.drawRightString(LINE_COLS["charges"] + 0.8 * inch, row_y, charges_str)
        
        # 24G - Days or Units
        units_str = str(line.units)
        c.drawRightString(LINE_COLS["units"] + 0.3 * inch, row_y, units_str)
        
        # 24J - Rendering Provider ID (NPI)
        if line.rendering_provider_id:
            c.drawString(LINE_COLS["rendering_npi"], row_y, line.rendering_provider_id[:15])
    
    # ==========================================
    # TOTALS AND BILLING INFO (Box 25-33)
    # ==========================================
    
    # Box 26 - Patient's Account Number
    _draw_text(c, claim.box_26_patient_account, FIELD_MAP["patient_account"])
    
    # Box 28 - Total Charge
    total_str = f"{claim.box_28_total_charge:.2f}" if claim.box_28_total_charge else f"{total_charges:.2f}"
    _draw_text(c, total_str, FIELD_MAP["total_charge"])
    
    # Box 31 - Signature of Physician or Supplier
    _draw_text(c, claim.box_31_signature, FIELD_MAP["signature"])
    
    # Box 31 - Date
    sig_date_str = _format_date_mm_dd_yyyy(claim.box_31_date)
    _draw_text(c, sig_date_str, FIELD_MAP["signature_date"])
    
    # Box 33 - Billing Provider Info
    _draw_text(c, claim.box_33_billing_phone, FIELD_MAP["billing_phone"])
    _draw_text(c, claim.box_33a_npi, FIELD_MAP["billing_npi"])
    
    # Finalize PDF
    c.showPage()
    c.save()
    
    return out_path
