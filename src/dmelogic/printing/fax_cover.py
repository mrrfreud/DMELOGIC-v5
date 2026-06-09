"""Generate fax cover page PDFs for outbound faxes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Union

PagePath = Union[str, Path]

# Company details come from the configured company profile (set in onboarding
# or Settings). These generic fallbacks are used only if the profile is empty.
_FALLBACK_TITLE = "Your Company Name"
_FALLBACK_SUBTITLE = "Durable Medical Equipment"

CONFIDENTIALITY_NOTICE = (
    "CONFIDENTIALITY NOTICE: This faxed message, including any attachments, is for the sole use of the "
    "intended recipient(s) and may contain confidential and privileged information, including Protected "
    "Health Information (PHI) protected under the Health Insurance Portability and Accountability Act "
    "(HIPAA). Any unauthorized review, use, disclosure, or distribution is strictly prohibited. If you "
    "are not the intended recipient, please contact the sender by reply email and destroy all copies of "
    "the original message and any attachments immediately. Thank you."
)


def _import_reportlab():
    try:
        from reportlab.lib.pagesizes import letter  # type: ignore
        from reportlab.lib.units import inch  # type: ignore
        from reportlab.lib.utils import simpleSplit  # type: ignore
        from reportlab.pdfgen import canvas  # type: ignore
    except ImportError as exc:  # pragma: no cover - handled via UI flow
        raise RuntimeError("ReportLab is required to build fax cover pages.") from exc

    return letter, inch, simpleSplit, canvas


def _draw_wrapped_text(
    pdf,
    text: str,
    x: float,
    y: float,
    width: float,
    font_name: str,
    font_size: int,
    leading: float,
    splitter,
) -> float:
    if not text:
        return y

    lines = splitter(text, font_name, font_size, width)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= leading
    return y


def generate_fax_cover_page(
    output_path: PagePath,
    *,
    to_number: str,
    attention: Optional[str] = None,
    body: Optional[str] = None,
    patient_name: Optional[str] = None,
    recipient_name: Optional[str] = None,
    include_confidentiality: bool = True
) -> Path:
    letter, inch, simple_split, canvas = _import_reportlab()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdf = canvas.Canvas(str(output_path), pagesize=letter)
    width, height = letter
    left_margin = 0.75 * inch
    right_margin = width - 0.75 * inch
    usable_width = right_margin - left_margin
    current_y = height - 0.75 * inch

    # ── Company header (from the configured company profile) ────────
    from dmelogic.company import load_company_profile
    company = load_company_profile()

    # Optional logo, drawn centered above the name.
    if company.has_logo():
        try:
            from reportlab.lib.utils import ImageReader
            logo = ImageReader(company.logo_path)
            iw, ih = logo.getSize()
            max_h = 0.6 * inch
            disp_h = min(max_h, ih)
            disp_w = iw * (disp_h / ih)
            pdf.drawImage(logo, (width - disp_w) / 2, current_y - disp_h,
                          width=disp_w, height=disp_h, mask="auto",
                          preserveAspectRatio=True)
            current_y -= disp_h + 0.12 * inch
        except Exception:
            pass

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(width / 2, current_y, company.name or _FALLBACK_TITLE)
    current_y -= 0.22 * inch
    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(width / 2, current_y, company.subtitle or _FALLBACK_SUBTITLE)
    if company.full_address():
        current_y -= 0.18 * inch
        pdf.drawCentredString(width / 2, current_y, company.full_address())
    if company.contact_line():
        current_y -= 0.18 * inch
        pdf.drawCentredString(width / 2, current_y, company.contact_line())
    if company.email.strip():
        current_y -= 0.18 * inch
        pdf.drawCentredString(width / 2, current_y, company.email.strip())

    # ── Divider ─────────────────────────────────────────────────────
    current_y -= 0.2 * inch
    pdf.setLineWidth(2)
    pdf.line(left_margin, current_y, right_margin, current_y)
    current_y -= 0.12 * inch
    pdf.setLineWidth(0.5)
    pdf.line(left_margin, current_y, right_margin, current_y)
    current_y -= 0.35 * inch

    # ── FAX COVER SHEET title ───────────────────────────────────────
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(width / 2, current_y, "FAX COVER SHEET")
    current_y -= 0.4 * inch

    # ── Detail rows (label + value) ─────────────────────────────────
    label_x = left_margin
    value_x = left_margin + 1.5 * inch
    row_height = 0.28 * inch

    def _draw_row(label: str, value: str):
        nonlocal current_y
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(label_x, current_y, label)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(value_x, current_y, value or "")
        current_y -= row_height

    _draw_row("DATE:", datetime.now().strftime("%B %d, %Y"))
    _draw_row("TO:", recipient_name or "")
    _draw_row("FAX:", to_number)
    _draw_row("ATTENTION:", attention or "To Whom It May Concern")
    if patient_name:
        _draw_row("REGARDING:", patient_name)

    # ── Thin separator ──────────────────────────────────────────────
    current_y -= 0.08 * inch
    pdf.setLineWidth(0.5)
    pdf.line(left_margin, current_y, right_margin, current_y)
    current_y -= 0.3 * inch

    # ── Message body ────────────────────────────────────────────────
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(left_margin, current_y, "MESSAGE:")
    current_y -= 0.22 * inch

    pdf.setFont("Helvetica", 11)
    body_text = body.strip() if body else "Please review the accompanying documents."
    current_y = _draw_wrapped_text(
        pdf,
        body_text,
        left_margin,
        current_y,
        usable_width,
        "Helvetica",
        11,
        15,
        simple_split,
    )
    current_y -= 0.35 * inch

    # ── Signature (from the company profile) ────────────────────────
    pdf.setFont("Helvetica", 10)
    for line in company.signature_block().splitlines():
        pdf.drawString(left_margin, current_y, line)
        current_y -= 0.18 * inch

    # ── Confidentiality notice ──────────────────────────────────────
    if include_confidentiality:
        current_y -= 0.25 * inch
        pdf.setLineWidth(0.3)
        pdf.line(left_margin, current_y, right_margin, current_y)
        current_y -= 0.18 * inch
        pdf.setFont("Helvetica-Oblique", 8)
        current_y = _draw_wrapped_text(
            pdf,
            CONFIDENTIALITY_NOTICE,
            left_margin,
            current_y,
            usable_width,
            "Helvetica-Oblique",
            8,
            11,
            simple_split,
        )

    pdf.showPage()
    pdf.save()
    return output_path


__all__ = ["generate_fax_cover_page"]
