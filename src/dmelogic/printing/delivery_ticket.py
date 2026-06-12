"""
Shared delivery-ticket PDF generator.

A single source of truth so the Order Editor, the ePACES helper, and the Orders
tab all produce the *identical* delivery ticket (patient + prescriber blocks,
items, directions, signature lines, and the optional delivery note). Previously
each screen had its own copy and they drifted — the ePACES version was missing
the prescriber block.

Usage:
    from dmelogic.printing.delivery_ticket import (
        build_delivery_ticket_pdf, build_delivery_tickets_combined)
    path = build_delivery_ticket_pdf(order_id, folder_path=folder_path)
    path = build_delivery_tickets_combined([id1, id2, ...], folder_path=folder_path)
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


def _format_order_number(order) -> str:
    """Continuous display number, annotated with refill context when present."""
    try:
        display = f"ORD-{int(order.id):03d}"
        if getattr(order, "parent_order_id", None) and (getattr(order, "refill_number", 0) or 0) > 0:
            display += f" (Refill of ORD-{int(order.parent_order_id):03d} R{int(order.refill_number)})"
        return display
    except Exception:
        return str(getattr(order, "id", "") or "Order")


def _output_dir() -> str:
    try:
        downloads = str(Path.home() / "Downloads")
        return downloads if os.path.exists(downloads) else str(Path.home())
    except Exception:
        return os.getcwd()


def _build_order_story(order_id: int, folder_path: Optional[str], styles) -> list:
    """Build the list of reportlab flowables for one order's delivery ticket."""
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle

    from dmelogic.db.orders import fetch_order_with_items
    from dmelogic.db.inventory import fetch_all_inventory
    from dmelogic.db.base import resolve_db_path
    from dmelogic.services.patient_address import get_patient_full_address

    order = fetch_order_with_items(order_id, folder_path=folder_path)
    if not order:
        raise ValueError(f"Order {order_id} not found")

    # Load latest inventory descriptions so tickets reflect corrected names.
    inv_desc_by_code = {}
    for r in fetch_all_inventory(folder_path=folder_path):
        try:
            rd = dict(r)
        except Exception:
            rd = {}
        code = str(rd.get("hcpcs_code", "") or rd.get("HCPCS", "")).upper()
        desc_val = rd.get("description") or rd.get("DESCRIPTION") or ""
        if code:
            inv_desc_by_code[code] = str(desc_val).strip()

    patient_name = order.patient_full_name or "N/A"

    def format_date(val):
        if not val or val in ('01/01/2000', '1/1/2000'):
            return ''
        try:
            if hasattr(val, 'strftime'):
                return val.strftime('%m/%d/%Y')
            return datetime.strptime(str(val), '%Y-%m-%d').strftime('%m/%d/%Y')
        except Exception:
            return str(val)

    rx_date = format_date(order.rx_date)
    order_date = format_date(order.order_date)

    patient_dob = order.patient_dob or 'N/A'
    patient_phone = order.patient_phone or 'N/A'

    patient_db_path = resolve_db_path("patients.db", folder_path=folder_path)
    patient_address = get_patient_full_address(
        patient_db_path,
        getattr(order, "patient_id", None),
        order.patient_last_name or "",
        order.patient_first_name or "",
    )
    if not patient_address:
        addr_snapshot = (
            getattr(order, "patient_address_at_order_time", None)
            or getattr(order, "patient_address", None)
            or ""
        )
        patient_address = addr_snapshot.strip()
    patient_address = patient_address or 'N/A'

    prescriber_name = (
        order.prescriber_name_at_order_time
        or getattr(order, "prescriber_name", None)
        or 'N/A'
    )
    prescriber_npi = (
        order.prescriber_npi_at_order_time
        or getattr(order, "prescriber_npi", None)
        or 'N/A'
    )

    doctor_directions = (order.doctor_directions or '').strip()
    special_instructions = (order.special_instructions or '').strip()

    item_rows = [["HCPCS", "Description", "Qty", "Refills", "Days"]]
    if order.items:
        for item in order.items:
            full_hcpcs = (item.hcpcs_code or '').strip()
            desc = inv_desc_by_code.get(full_hcpcs.upper(), '').strip() or (item.description or '').strip()
            display_hcpcs = full_hcpcs.split('-')[0].strip() if '-' in full_hcpcs else full_hcpcs
            qty = str(item.quantity or 1)
            refills = str(item.refills or 0)
            days = str(item.days_supply or 0)
            item_rows.append([display_hcpcs, desc, qty, refills, days])
    if len(item_rows) == 1:
        item_rows.append(["-", "No items", "-", "-", "-"])

    heading_style = ParagraphStyle(
        'Heading', parent=styles['Heading2'], spaceAfter=6,
        textColor=colors.HexColor('#2c3e50'))
    order_num = _format_order_number(order)

    story = []
    story.append(Paragraph("DELIVERY TICKET", styles['Title']))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(f"<b>Order #:</b> {order_num}", styles['Heading3']))
    story.append(Spacer(1, 0.15 * inch))

    pt_tbl_data = [
        [Paragraph("<b>Patient</b>", heading_style), '', Paragraph("<b>Prescriber</b>", heading_style)],
        [
            Paragraph(f"<b>Name:</b> {patient_name}", styles['Normal']),
            Paragraph(
                f"<b>DOB:</b> {patient_dob}<br/>"
                f"<b>Phone:</b> {patient_phone}<br/>"
                f"<b>Address:</b> {patient_address}",
                styles['Normal']),
            Paragraph(f"<b>Name:</b> {prescriber_name}", styles['Normal']),
        ],
        ['', '', Paragraph(f"<b>NPI:</b> {prescriber_npi}", styles['Normal'])],
    ]
    pt_tbl = Table(pt_tbl_data, colWidths=[1.5*inch, 3.5*inch, 3.0*inch])
    pt_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(pt_tbl)
    story.append(Spacer(1, 0.15 * inch))

    md_table = Table([
        [Paragraph('<b>RX Date</b>', styles['Normal']), rx_date or 'N/A',
         Paragraph('<b>Order Date</b>', styles['Normal']), order_date or 'N/A'],
    ], colWidths=[1.2*inch, 2.5*inch, 1.5*inch, 2.8*inch])
    md_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(md_table)
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("ITEMS", heading_style))
    t = Table(item_rows, colWidths=[1.2*inch, 4.0*inch, 0.7*inch, 0.8*inch, 0.7*inch])
    t.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (2, 0), (4, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.2 * inch))

    if doctor_directions:
        story.append(Paragraph("DOCTOR'S DIRECTIONS", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph(
            doctor_directions.replace('\n', '<br/>'),
            ParagraphStyle('Directions', parent=styles['Normal'], fontSize=10, leading=14,
                           leftIndent=10, rightIndent=10, spaceAfter=10,
                           textColor=colors.HexColor('#2c3e50'),
                           backColor=colors.HexColor('#fffef0'),
                           borderPadding=8, borderWidth=2,
                           borderColor=colors.HexColor('#f0ad4e'))))
        story.append(Spacer(1, 0.2 * inch))

    story.append(Spacer(1, 0.4 * inch))
    sig_style = ParagraphStyle('Signature', parent=styles['Normal'], fontSize=10, leading=14)

    name_table = Table(
        [[Paragraph("Print Name:", sig_style), '', Paragraph("Relationship:", sig_style), '']],
        colWidths=[1.2*inch, 3.0*inch, 1.3*inch, 2.2*inch], rowHeights=[0.4*inch])
    name_table.setStyle(TableStyle([
        ('LINEBELOW', (1, 0), (1, 0), 1, colors.black),
        ('LINEBELOW', (3, 0), (3, 0), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
    ]))
    story.append(name_table)

    story.append(Spacer(1, 0.15 * inch))
    sig_table = Table(
        [[Paragraph("Signature:", sig_style), '', Paragraph("Date:", sig_style), '']],
        colWidths=[1.2*inch, 4.6*inch, 0.8*inch, 1.2*inch], rowHeights=[0.4*inch])
    sig_table.setStyle(TableStyle([
        ('LINEBELOW', (1, 0), (1, 0), 1, colors.black),
        ('LINEBELOW', (3, 0), (3, 0), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
    ]))
    story.append(sig_table)

    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "I acknowledge receipt of the items listed above in good condition.",
        ParagraphStyle('Acknowledgment', parent=styles['Normal'], fontSize=9,
                       textColor=colors.grey, alignment=1)))

    if special_instructions:
        story.append(Spacer(1, 0.3 * inch))
        story.append(Paragraph("📋 NOTE FOR DELIVERY", heading_style))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph(
            special_instructions.replace('\n', '<br/>'),
            ParagraphStyle('DeliveryNote', parent=styles['Normal'], fontSize=11, leading=15,
                           leftIndent=10, rightIndent=10, spaceAfter=10,
                           textColor=colors.HexColor('#2c3e50'),
                           backColor=colors.HexColor('#e8f4fd'),
                           borderPadding=10, borderWidth=2,
                           borderColor=colors.HexColor('#5bc0de'))))

    return story


def build_delivery_ticket_pdf(order_id: int, folder_path: Optional[str] = None) -> str:
    """Build the delivery-ticket PDF for ``order_id`` and return its file path."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    story = _build_order_story(order_id, folder_path, styles)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_path = os.path.join(_output_dir(), f"DeliveryTicket_ORD-{int(order_id):03d}_{ts}.pdf")
    doc = SimpleDocTemplate(
        file_path, pagesize=letter,
        leftMargin=0.5*inch, rightMargin=0.5*inch,
        topMargin=0.5*inch, bottomMargin=0.5*inch)
    doc.build(story)
    return file_path


def build_delivery_tickets_combined(order_ids: Sequence[int],
                                    folder_path: Optional[str] = None) -> str:
    """Build ONE PDF containing a delivery ticket for each order (one per page).

    Returns the combined file path. Orders that fail to load are skipped; if none
    succeed a ValueError is raised.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    full_story = []
    ok = 0
    for oid in order_ids:
        try:
            story = _build_order_story(int(oid), folder_path, styles)
        except Exception:
            continue
        if full_story:
            full_story.append(PageBreak())
        full_story.extend(story)
        ok += 1

    if ok == 0:
        raise ValueError("No delivery tickets could be generated for the selected orders.")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    file_path = os.path.join(_output_dir(), f"DeliveryTickets_Batch_{ok}orders_{ts}.pdf")
    doc = SimpleDocTemplate(
        file_path, pagesize=letter,
        leftMargin=0.5*inch, rightMargin=0.5*inch,
        topMargin=0.5*inch, bottomMargin=0.5*inch)
    doc.build(full_story)
    return file_path
