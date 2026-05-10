"""Payment remittance PDF generation.

A remittance advice is the receipt the AP team emails to the vendor
after a payment goes out — it confirms what was paid, against which
invoices, and on what date. This module produces a single-page PDF
suitable for attaching to an email or downloading from the History tab.

Pure-function: takes the already-loaded model rows + the issuing org
profile, returns the PDF bytes. No DB / network calls. The HTTP route
fetches the rows and wraps them in a `Response` with the right headers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


@dataclass
class RemittanceLine:
    """One invoice covered by the remittance."""

    invoice_number: str
    description: str | None
    amount: Decimal


@dataclass
class RemittanceContext:
    """Everything the PDF renderer needs. Pre-loaded by the route so the
    PDF function stays sync + DB-free."""

    payer_name: str
    payer_address: str | None
    vendor_name: str
    vendor_address: str | None
    payment_date: datetime
    payment_method: str
    payment_reference: str | None
    payment_amount: Decimal
    currency: str
    lines: list[RemittanceLine]
    notes: str | None = None


def render_remittance_pdf(ctx: RemittanceContext) -> bytes:
    """Render the remittance to a single-page PDF and return the bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        title=f"Remittance Advice — {ctx.payment_reference or 'Payment'}",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13)
    label = ParagraphStyle(
        "label",
        parent=body,
        textColor=colors.HexColor("#6b7280"),
        fontSize=8,
        leading=10,
        spaceAfter=2,
        alignment=0,
    )
    h_title = ParagraphStyle(
        "title",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=4,
    )
    h_section = ParagraphStyle(
        "section",
        parent=styles["Heading2"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=6,
        spaceAfter=4,
    )

    story = []

    story.append(Paragraph("Remittance Advice", h_title))
    story.append(
        Paragraph(
            f"Payment date: <b>{ctx.payment_date.strftime('%B %d, %Y')}</b> · "
            f"Method: <b>{_method_label(ctx.payment_method)}</b>"
            + (f" · Ref: <b>{ctx.payment_reference}</b>" if ctx.payment_reference else ""),
            body,
        )
    )
    story.append(Spacer(1, 0.18 * inch))

    # Two-column header: payer left, vendor right.
    payer_block = [
        Paragraph("FROM", label),
        Paragraph(_escape(ctx.payer_name), body),
    ]
    if ctx.payer_address:
        payer_block.append(Paragraph(_escape(ctx.payer_address), body))

    vendor_block = [
        Paragraph("TO", label),
        Paragraph(_escape(ctx.vendor_name), body),
    ]
    if ctx.vendor_address:
        vendor_block.append(Paragraph(_escape(ctx.vendor_address), body))

    parties = Table(
        [[payer_block, vendor_block]],
        colWidths=[3.4 * inch, 3.4 * inch],
    )
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
    story.append(Spacer(1, 0.25 * inch))

    # Invoice line items.
    story.append(Paragraph("Invoices Paid", h_section))
    table_data = [["Invoice #", "Description", "Amount"]]
    for ln in ctx.lines:
        table_data.append(
            [
                ln.invoice_number,
                _escape(ln.description or "—"),
                _format_money(ln.amount, ctx.currency),
            ]
        )
    table_data.append(
        [
            "",
            Paragraph("<b>Total</b>", body),
            Paragraph(
                f"<b>{_format_money(ctx.payment_amount, ctx.currency)}</b>",
                body,
            ),
        ]
    )

    invoice_table = Table(
        table_data,
        colWidths=[1.6 * inch, 4.0 * inch, 1.4 * inch],
    )
    invoice_table.setStyle(
        TableStyle(
            [
                # Header row.
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#475569")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                # Body.
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fafafa")]),
                # Total row.
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(invoice_table)

    if ctx.notes:
        story.append(Spacer(1, 0.25 * inch))
        story.append(Paragraph("Notes", h_section))
        story.append(Paragraph(_escape(ctx.notes), body))

    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "Questions about this remittance? Reply to this email and reference the payment "
            f"number above.",
            ParagraphStyle(
                "footer",
                parent=body,
                fontSize=8,
                textColor=colors.HexColor("#94a3b8"),
            ),
        )
    )

    doc.build(story)
    return buf.getvalue()


def _method_label(method: str) -> str:
    return {
        "ach": "ACH",
        "wire": "Wire transfer",
        "check": "Check",
        "rtp": "RTP",
        "virtual_card": "Virtual card",
    }.get(method, method.title())


def _format_money(amount: Decimal, currency: str) -> str:
    # Currency-aware basic formatter; sufficient for USD / EUR / GBP. The
    # remittance is a courtesy — locale-perfect formatting can wait until
    # we ship localised templates.
    sign = ""
    if amount < 0:
        sign = "-"
        amount = -amount
    return f"{sign}{currency} {amount:,.2f}"


def _escape(s: str) -> str:
    """ReportLab's Paragraph parses the string as inline XML — escape <, >, &
    so a vendor with `<Manufacturing>` in its name doesn't break the doc."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
