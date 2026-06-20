"""1099-NEC / 1099-MISC form generation.

Turns the per-vendor aggregation from ``tax_1099.build_1099_report`` into
form-ready payloads and renders them as PDFs (reportlab, same pattern as
``remittance_pdf``).

Scope + simplifications (documented in ``docs/tax-1099.md``):

  - **1099-NEC** is the default for AP contractor spend — the whole
    reportable total lands in NEC box 1 (nonemployee compensation).
  - **1099-MISC** is supported for the rarer AP cases (rent, attorney
    gross proceeds). We don't yet split MISC across its many boxes — the
    reportable total goes to the requested box (default box 3, "other
    income"); per-box allocation is a future enhancement once we track the
    spend category on the payment.
  - We render the *information* on an IRS-styled form, not a pixel-perfect
    red-ink Copy A (which must be filed electronically or on official
    scannable stock anyway). The PDF is the payer's / recipient's working
    copy + the basis for the e-file payload.

Pure functions: builders take loaded rows, the renderer takes a context and
returns PDF bytes. No DB / network here — the route loads the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.branding import (
    PLATFORM_ACCENT_COLOR,
    BrandContext,
    build_logo_flowable,
    get_brand_context,
)
from app.services.tax_1099 import VendorReportRow

FORM_NEC = "1099-NEC"
FORM_MISC = "1099-MISC"
_VALID_FORMS = frozenset({FORM_NEC, FORM_MISC})

# MISC box labels we support. NEC has a single relevant box (1).
_MISC_BOX_LABELS = {
    "1": "Box 1 — Rents",
    "2": "Box 2 — Royalties",
    "3": "Box 3 — Other income",
    "10": "Box 10 — Gross proceeds paid to an attorney",
}


@dataclass(frozen=True)
class Form1099Context:
    """Everything the form PDF renderer needs, pre-loaded by the route.

    All money is Decimal. ``recipient_tin_masked`` is the display string
    (e.g. ``**-***6789``) — the renderer never receives the full TIN, so a
    rendered working copy can be downloaded without exposing the full TIN in
    the PDF text layer beyond the masked form."""

    tax_year: int
    form_type: str
    payer_name: str
    payer_tin_masked: str | None
    payer_address: str | None
    recipient_name: str
    recipient_tin_masked: str | None
    recipient_address: str | None
    box_label: str
    box_amount: Decimal
    generated_at: date
    # Resolved tenant brand for the header. Defaults to the platform brand so a
    # call site that doesn't pass one still renders.
    brand: BrandContext = field(default_factory=lambda: get_brand_context(None))


def mask_tin(tin: str | None) -> str | None:
    """Mask all but the last 4 digits for display: ``12-3456789`` →
    ``**-***6789``. Returns None when there's no TIN."""
    if not tin:
        return None
    digits = [c for c in tin if c.isdigit()]
    if len(digits) < 4:
        return "****"
    last4 = "".join(digits[-4:])
    # Preserve the separator shape of the original, masking every digit
    # except the trailing four.
    out = []
    masked_so_far = 0
    total_to_mask = len(digits) - 4
    for c in tin:
        if c.isdigit():
            if masked_so_far < total_to_mask:
                out.append("*")
                masked_so_far += 1
            else:
                out.append(c)
        else:
            out.append(c)
    masked = "".join(out)
    return masked if masked else f"***{last4}"


def build_form_context(
    *,
    row: VendorReportRow,
    full_tax_id: str | None,
    tax_year: int,
    form_type: str,
    payer_name: str,
    payer_tax_id: str | None,
    payer_address: str | None,
    recipient_address: str | None,
    misc_box: str = "3",
    brand: BrandContext | None = None,
) -> Form1099Context:
    """Build a render context for one vendor's 1099.

    ``row`` is the aggregation row; ``full_tax_id`` is passed only so we can
    mask it here (it is never stored on the context in full)."""
    if form_type not in _VALID_FORMS:
        raise ValueError(f"Unsupported form type: {form_type}")

    if form_type == FORM_NEC:
        box_label = "Box 1 — Nonemployee compensation"
    else:
        box_label = _MISC_BOX_LABELS.get(misc_box, _MISC_BOX_LABELS["3"])

    return Form1099Context(
        tax_year=tax_year,
        form_type=form_type,
        payer_name=payer_name,
        payer_tin_masked=mask_tin(payer_tax_id),
        payer_address=payer_address,
        recipient_name=row.vendor_name,
        recipient_tin_masked=mask_tin(full_tax_id if full_tax_id is not None else row.tax_id),
        recipient_address=recipient_address,
        box_label=box_label,
        box_amount=row.ytd_paid,
        generated_at=date.today(),
        brand=brand if brand is not None else get_brand_context(None),
    )


def _brand_color(hex_value: str):
    """Resolve a validated brand hex into a ReportLab color, falling back to the
    platform accent if the value is somehow unparseable."""
    try:
        return colors.HexColor(hex_value)
    except Exception:  # noqa: BLE001 — never let a color literal break the PDF.
        return colors.HexColor(PLATFORM_ACCENT_COLOR)


def _money(amount: Decimal) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_1099_pdf(ctx: Form1099Context) -> bytes:
    """Render a single 1099-NEC / 1099-MISC working copy to PDF bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        title=f"{ctx.form_type} ({ctx.tax_year}) — {ctx.recipient_name}",
    )
    brand = ctx.brand
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13)
    label = ParagraphStyle(
        "label",
        parent=body,
        textColor=colors.HexColor("#6b7280"),
        fontSize=8,
        leading=10,
        spaceAfter=2,
    )
    h_title = ParagraphStyle(
        "title",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=2,
    )
    h_brand = ParagraphStyle(
        "brand",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=_brand_color(brand.accent_color),
        spaceAfter=8,
    )

    story = []
    # Branded header (logo when embeddable, else product name in accent).
    logo = build_logo_flowable(brand, max_width_pt=2.4 * inch, max_height_pt=0.6 * inch)
    if logo is not None:
        story.append(logo)
        story.append(Spacer(1, 0.08 * inch))
    else:
        story.append(Paragraph(_escape(brand.product_name), h_brand))
    story.append(Paragraph(f"Form {ctx.form_type}", h_title))
    story.append(
        Paragraph(
            f"Tax year <b>{ctx.tax_year}</b> · This is a working copy — file Copy A "
            "electronically or on official scannable stock.",
            ParagraphStyle("sub", parent=body, fontSize=8, textColor=colors.HexColor("#94a3b8")),
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    payer_block = [Paragraph("PAYER", label), Paragraph(_escape(ctx.payer_name), body)]
    if ctx.payer_address:
        payer_block.append(Paragraph(_escape(ctx.payer_address), body))
    if ctx.payer_tin_masked:
        payer_block.append(Paragraph(f"TIN: {_escape(ctx.payer_tin_masked)}", body))

    recipient_block = [
        Paragraph("RECIPIENT", label),
        Paragraph(_escape(ctx.recipient_name), body),
    ]
    if ctx.recipient_address:
        recipient_block.append(Paragraph(_escape(ctx.recipient_address), body))
    recipient_block.append(
        Paragraph(f"TIN: {_escape(ctx.recipient_tin_masked or 'NOT ON FILE')}", body)
    )

    parties = Table([[payer_block, recipient_block]], colWidths=[3.4 * inch, 3.4 * inch])
    parties.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(parties)
    story.append(Spacer(1, 0.3 * inch))

    amount_table = Table(
        [[Paragraph(f"<b>{_escape(ctx.box_label)}</b>", body), _money(ctx.box_amount)]],
        colWidths=[5.0 * inch, 1.8 * inch],
    )
    amount_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(amount_table)

    doc.build(story)
    return buf.getvalue()
