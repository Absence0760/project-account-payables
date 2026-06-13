# PEPPOL AS4 — outbound e-invoice transmission

Transmits an approved invoice onto the PEPPOL network as a UBL 2.1 / BIS
Billing 3.0 document. This is the **send** half of automated e-invoicing; the
inbound (receive) half is the next slice and reuses everything here.

## Four-corner model

PEPPOL is a four-corner network:

```
 C1 (us, sender)  →  C2 (our Access Point)  →  C3 (receiver AP)  →  C4 (receiver)
```

We integrate with a **hosted Access Point** (C2) over its HTTP API; the gateway
does the raw AS4/ebMS3 SOAP handshake to C3. We do **not** implement AS4 SOAP
ourselves in this slice. The `mock` adapter simulates the Access Point entirely
in-process — no DNS, no SMP/SML lookup, no network — so `pnpm dev` transmits
end-to-end with no PEPPOL credential.

## Adapter family — `app/services/peppol_adapters/`

Same registry shape as `payment_adapters` / `card_adapters`:

```python
@register_peppol_adapter("my_ap")
class MyAdapter(PeppolAdapter):
    async def resolve_participant(self, pid: ParticipantId) -> ParticipantCapability: ...
    async def send(self, request: TransmissionRequest) -> TransmissionResult: ...
    async def test_connection(self) -> bool: ...
    def parse_inbound(self, headers, body): ...   # inbound-ready stub
```

Registered:

| Provider | What it is |
|----------|-----------|
| `mock` | **Local-first default** — in-process, no network. Canned SMP capability for any known scheme; `receiver_not_registered` for a value containing `UNREGISTERED`. Deterministic `MessageId`. |
| `as4_gateway` | Real adapter — `httpx` to a hosted Access Point's REST API. Base URL + key from config; **no hardcoded key fallback** (returns `peppol_not_configured` when unkeyed). |

Selection: `Organization.settings.peppol.provider` → falls back to
`AP_PEPPOL_PROVIDER` (default `mock`). Unknown provider → `mock` fallback.

## ParticipantId value object

A frozen `(scheme, value)` dataclass. Wire form
`iso6523-actorid-upis::<scheme>:<value>`, e.g.
`iso6523-actorid-upis::9930:DE123456789`. `scheme` is the EAS code (0088=GLN,
9930=DE org, 0192=NO org). `ParticipantId.parse()` accepts the full wire form
**or** the bare `9930:DE123456789`; on a malformed id it raises a **PII-free**
`ValueError` that names the field but never echoes the value.

## SMP/SML resolution

`adapter.resolve_participant(receiver_id)` answers "is this receiver registered
on PEPPOL, and on which Access Point?" → `ParticipantCapability(registered,
access_point_url, supported_doc_types, unregistered_reason)`. The mock returns a
canned capability (no DNS). The send service refuses an unregistered receiver
(`PeppolSendError("receiver_not_registered")`) before persisting any row.

## BIS Billing 3.0 identifiers (`constants.py`)

```python
PEPPOL_BIS_BILLING_DOCTYPE = (
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice"
    "##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
)
PEPPOL_BIS_BILLING_PROCESSID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
```

## Payload — reuse the `e_invoice` package

The transmission carries the **same** UBL the export route emits. The send
service reuses the shipped package wholesale (no UBL duplication):

```
invoice_to_einvoice_document(invoice, line_items, BuyerIdentity)
  → assert_valid(doc)          # check_tax=True → hard reject (422)
  → generate_ubl(doc) -> bytes
```

The buyer identity is built by the existing
`invoices._buyer_identity_from_org` helper. **SBDH** (Standard Business
Document Header) wrapping happens in the **adapter** layer (the gateway builds
it from the participant/doc-type/process metadata + the UBL), never in the
`e_invoice` generator — which only emits clean UBL.

## Transmission model + DB idempotency guard

`PeppolTransmission` (`peppol_transmissions`, tenant-scoped — `EntityMixin` +
`TimestampMixin` + explicit `organization_id`). Money (`amount`) is
`Numeric(15,2)` — never float. PII (`participant_value` = supplier tax/org id)
lives on the row legitimately but never in a log or error body.

Idempotency is enforced at the **data layer**, not by application code:

```sql
CREATE UNIQUE INDEX uq_peppol_one_live_per_invoice_direction
  ON peppol_transmissions (invoice_id, direction) WHERE status <> 'failed';
```

At most one **non-failed** (`sending`/`sent`/`delivered`) transmission per
`(invoice_id, direction)`. The send service inserts the row in `sending` state
and **flushes before the networked transmit**, so two concurrent POSTs cannot
both reach the gateway — the loser hits `IntegrityError`, rolls back, and gets
the committed live row returned (`already_sent=True`). A prior **failed** send
is excluded from the index, so a legitimate retry is allowed and creates a
fresh row.

A second partial unique index dedupes the AP-assigned `message_id`
(`WHERE message_id IS NOT NULL`) so the future inbound slice can dedupe
redeliveries the same way payment webhooks dedupe by `event_id`.

The model's `__table_args__` partial-index predicates match the
`0034_peppol_transmissions` migration verbatim, so a fresh tenant built by
`tenant_provisioning` (`create_all`) is schema-identical to a migrated one.

## Send route

```
POST /api/invoices/{invoice_id}/peppol-send
  body: { receiver_scheme, receiver_value, sender_scheme?, sender_value? }
  → 200 PeppolSendResponse { transmission_id, status, message_id, direction, already_sent }
```

Role-gated `require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)` (same gate as
the e-invoice export). `sender_*` falls back to
`Organization.settings.peppol.{sender_scheme,sender_value}`.

| Outcome | HTTP |
|---------|------|
| transmitted (or idempotent re-send) | 200 |
| invoice not found | 404 |
| malformed participant id / no sender configured | 400 |
| tax-invalid document / receiver not registered | 422 (PII-free detail) |
| role not permitted | 403 |

A send writes a `invoice.peppol_sent` (or `invoice.peppol_send_failed`) audit
row through `dispatch_audit` (`details` records provider, `receiver_scheme`,
`message_id`, `doc_type` — **never** the receiver value / tax id). The
idempotent short-circuit deliberately writes **no** duplicate audit row.

## Inbound-ready design

The `direction` column (default `outbound`), the partial-unique `message_id`
column, the `ParticipantId` value object, and the `parse_inbound` adapter stub
all exist so the next (inbound) slice can verify the gateway HMAC via
`services/webhook_security.py` and dedupe redeliveries by AS4 `MessageId` —
no schema churn.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_PEPPOL_PROVIDER` | `mock` | `mock` (in-process default) \| `as4_gateway` |
| `AP_PEPPOL_GATEWAY_URL` | (empty) | Hosted Access Point base URL (deployed only) |
| `AP_PEPPOL_GATEWAY_API_KEY` | (empty) | Gateway API key — **no hardcoded fallback**; sops in deployed |

Per-org overrides live on `Organization.settings.peppol` and win over the
process-level defaults.

## Local-first

The mock adapter is in-process, so there is **no** new long-running service and
**no** new `pnpm` script — `pnpm dev` transmits e-invoices without any PEPPOL
credential. Set `AP_PEPPOL_PROVIDER=as4_gateway` with a real gateway URL + key
(sops) to transmit for real.

## Tests

- `tests/test_peppol_adapters.py` — ParticipantId parse/format + PII-free
  errors, mock resolve/send, registry fallback, gateway HTTP (stubbed,
  no-network) incl. the no-key-fallback path, constants.
- `tests/test_peppol_send.py` — happy path (row + one audit), idempotent
  re-send (one adapter call), tax-invalid (no row), unknown receiver, failed-
  then-retry, PII-not-in-logs.
- `tests/test_peppol_route.py` — 200 / already_sent / 404 / 400 / 422 / 403.
- `tests/test_tenant_provisioning.py` — `peppol_transmissions` fans out + the
  two partial unique indexes are built by `create_all`.
