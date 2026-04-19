# PO Matching

## Overview

PO matching compares invoices against purchase orders (and optionally goods receipts) to catch discrepancies before payment. This prevents overpayment, duplicate payment, and fraud.

## Match Types

| Type | What's compared | When to use |
|---|---|---|
| **2-way** | Invoice vs. PO (amount, vendor) | Standard — most invoices |
| **3-way** | Invoice vs. PO vs. Goods Receipt (amount + quantities) | Manufacturing, physical goods |

## How It Works

```
Invoice has po_number?
    |
    ├── No → status: "no_po" (skip matching)
    |
    └── Yes → Find PO by number
              |
              ├── PO not found → status: "no_po", issue flagged
              |
              └── PO found → 2-way match
                    |
                    ├── Amount within tolerance → status: "matched"
                    |
                    └── Amount outside tolerance → status: "mismatch", issue flagged
                          |
                          GR exists for PO?
                          |
                          ├── No → stay as 2-way match
                          |
                          └── Yes → 3-way match
                                ├── Quantities match → status: "matched"
                                └── Partial receipt → status: "partial"
```

## Tolerance

The system allows a configurable variance percentage (default: 5%) between invoice amount and PO total.

| Variance | Result |
|---|---|
| Invoice = $1,500, PO = $1,500 | Matched (0% variance) |
| Invoice = $1,525, PO = $1,500 | Matched (1.7% variance, within 5%) |
| Invoice = $1,600, PO = $1,500 | **Mismatch** (6.7% variance, exceeds 5%) |

## MatchResult

```python
MatchResult:
    match_type: "none" | "2-way" | "3-way"
    status: "no_po" | "matched" | "mismatch" | "partial"
    po_id, po_number, po_total
    gr_id  # if 3-way
    amount_variance: float      # invoice - PO in dollars
    amount_variance_pct: float  # as percentage
    within_tolerance: bool
    issues: list[str]           # human-readable issues
    details: dict               # full match data for audit
```

## Integration Points

### After Extraction
PO matching can run after extraction when the invoice has a `po_number`. The match result can be:
- Stored on the invoice (as a warning or in `state_data`)
- Shown in the invoice modal
- Routed to the exception queue if mismatched

### In the Review Step
Reviewers see the match status:
- **Matched** (green) — PO found, amounts within tolerance
- **Mismatch** (red) — PO found but amounts differ
- **Partial** (yellow) — 3-way match, not all goods received
- **No PO** (gray) — no PO number on invoice, or PO not found

### Before Payment
Mismatched invoices can be blocked from the payment queue until the mismatch is resolved (exception cleared).

## Data Models

The procurement models already exist:

| Table | Purpose |
|---|---|
| `purchase_orders` | PO header (po_number, vendor_id, total, status) |
| `po_line_items` | PO lines (description, quantity, unit_price, total) |
| `goods_receipts` | GR header (gr_number, po_id, received_date, status) |
| `gr_line_items` | GR lines (description, quantity_received) |

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/invoices/{id}/match` | Run PO matching for an invoice (planned) |
| `GET` | `/api/invoices/{id}/match-result` | Get the match result (planned) |

## Implementation Status

| Feature | Status |
|---|---|
| PO matching service (2-way and 3-way) | Done |
| Tolerance configuration | Done (5% default) |
| Vendor-aware matching (match PO by vendor_id) | Done |
| Goods receipt quantity comparison | Done |
| Procurement models (PO, GR) | Done (existed) |
| Wired into extraction + invoice-mutation pipeline (`services.invoice_warnings.refresh_warnings`) | Done |
| Persisted on `invoice.po_match` (JSONB, alembic 0006) | Done |
| Match result display in invoice modal (PO Match panel with status badge, variance, issues) | Done |
| Exception routing for mismatches (`po_mismatch` exceptions auto-created by severity: error / warning / info) | Done |
| PO management UI (list + detail page) | Planned |
| PO sync from ERP (real adapter `list_pos()` — currently mock data) | Planned |
| Configurable tolerance per org (`Organization.settings.po_matching.tolerance_pct`) | Planned |
