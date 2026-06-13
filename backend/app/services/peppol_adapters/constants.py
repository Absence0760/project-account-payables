"""PEPPOL BIS Billing 3.0 identifiers (UBL Invoice).

These are the exact document-type and process identifiers a PEPPOL Access
Point uses to route a UBL 2.1 Invoice under the EN 16931-compliant BIS
Billing 3.0 specification. The doc-type id encodes the customization id; the
process id names the billing process. Both are fixed constants — never
construct them by string concatenation at a call site.
"""

from __future__ import annotations

PEPPOL_BIS_BILLING_DOCTYPE = (
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice"
    "##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
)
PEPPOL_BIS_BILLING_PROCESSID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
