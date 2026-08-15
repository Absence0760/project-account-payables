# Digital signatures on approvals (SOX non-repudiation)

Every invoice approval carries a cryptographic **"timestamp + user hash"** token
— an HMAC-SHA256 over a canonical serialization of the approval facts — stamped
into the immutable `audit_log` row for that approval. An auditor can later
re-derive and bit-compare the digest to prove the approval record wasn't altered
after the fact (who approved, what amount, when). This is **non-repudiation, not
encryption**: the payload fields stay in the clear on the audit row; the
signature only proves they weren't tampered with.

## How it works

`app/services/approval_signature.py` is a pure module (no DB, no network):

| Function | Purpose |
|----------|---------|
| `sign_approval(*, invoice_id, amount, actor_id, decision, timestamp, signing_key) -> str` | HMAC-SHA256 hex digest over the canonical payload. Empty key → `""` (signing skipped). |
| `verify_approval(*, …, signature, signing_key) -> bool` | Constant-time (`hmac.compare_digest`) re-derive + compare. Empty key / missing, non-string, non-ASCII or empty signature / mismatch → `False` (never raises). |
| `build_signature_detail(*, …, signing_key) -> dict \| None` | The `details["signature"]` block written on the audit row. `None` when no key. |
| `check_approval_row(*, details, invoice_id, amount, actor_id, signing_key) -> SignatureCheck` | Verdict for ONE approval audit row: `valid` / `invalid` / `unsigned`. The single definition both verification endpoints call, so they can't drift on what a tampered row looks like. Never raises. |

### Verdicts, and what each one claims

| Row shape | Verdict | Why |
|-----------|---------|-----|
| digest re-derives | `valid` | the approval facts are unchanged |
| digest doesn't re-derive; corrupt / missing / non-string `signed_at`; missing actor; **empty** `signature: {}`; a `value` that isn't an ASCII string | `invalid` | the row claims to be signed and isn't — a finding |
| no `signature` key; `signature: null`; `details` is not a JSON object at all | `unsigned` | nothing to verify (predates signing, or the column was overwritten wholesale) |

`details` is `JSONB` with **no shape constraint at any level**, so a
hand-written value is reachable at every one of them — by exactly the direct-DB
tamper this feature exists to catch. Every level is therefore shape-checked
rather than allowed to raise: a non-object `details`, a non-object `signature`,
a non-string `signed_at`, and a `value` that isn't an ASCII string (both cases
`hmac.compare_digest` raises `TypeError` on) each resolve to a verdict. On the
population sweep below, one corrupt row must surface as its own finding, not
500 the whole period's control test and take the good rows' evidence with it.

### Canonical payload

The signed bytes are deterministic UTF-8 JSON (`sort_keys`, fixed separators)
over exactly these fields, in this order (`SIGNED_FIELDS`):

```
invoice_id   str(UUID)
amount       string-Decimal, quantized to 0.01   ← money is exact, never float
actor_id     str(UUID)
decision     "approved"
timestamp    ISO-8601 (timezone-aware)
```

`Decimal("100.00")` and `Decimal("100.0")` normalise to the same canonical
amount, so a trivially-different-but-equal amount can't shift the digest.

### The stored block

`details["signature"]` on the `invoice.approved` audit row:

```json
{
  "alg": "HMAC-SHA256",
  "value": "<64-char hex digest>",
  "signed_fields": ["invoice_id", "amount", "actor_id", "decision", "timestamp"],
  "signed_at": "2026-06-17T12:00:00+00:00"
}
```

It records the field **names** signed, never their values (the amount value
already lives elsewhere on the row — the block stays PII-free).

## Where it's wired

- **Signing** happens in `services/review.approve_invoice`, at the moment the
  invoice transitions to `approved`. A single `signed_at` timestamp feeds both
  the digest and the stored block. The signature is included in the `details`
  of the same immutable audit row the transition already writes — no second
  write, no new table.
- **Verification** is `GET /api/audit/invoice/{invoice_id}/verify-signatures`
  (`app/api/audit.py`, admin/CFO — the auditor privilege). It loads every
  `invoice.approved` audit row carrying a signature block and re-derives the
  HMAC against the invoice's **current** `amount`, the row's `actor_id`, and the
  signed timestamp. A post-approval tamper of the amount (or a swapped actor /
  altered timestamp) flips `valid` to `false`. The read is itself audited
  (`audit.viewed`, `details.verify_signatures` = count).

  Response shape:

  ```json
  {
    "invoice_id": "…",
    "signing_configured": true,
    "approvals": [
      {"audit_row_id": "…", "signed_at": "…", "actor": "Manager", "signed": true, "valid": true}
    ]
  }
  ```

  An approval row written before signing was enabled reports `signed: false`
  (nothing to verify) rather than `valid: false`.

## Testing the control over a period (the population sweep)

The per-invoice check answers "is THIS approval still intact" — which presumes
you already know which invoice to suspect. That is not how the control is
tested: an auditor tests a **population** (a quarter's approvals), and a
tampered row nobody happens to open is, on that surface alone, undetectable.

`GET /api/audit/verify-signatures?start=&end=[&limit=]` (`app/api/audit.py`,
admin/CFO) is that surface. It sweeps every `invoice.approved` audit row in the
range, joins each to its invoice on the UNIQUE `invoices.correlation_id`, and
runs the same `check_approval_row` primitive against that invoice's **current**
exact `amount`:

```json
{
  "start": "2026-04-01", "end": "2026-06-30",
  "signing_configured": true,
  "invoices_covered": 412, "approvals_checked": 431,
  "valid": 430, "invalid": 1, "unsigned": 0,
  "findings": [
    {"invoice_id": "…", "invoice_number": "INV-2231", "audit_row_id": "…",
     "actor_id": "…", "actor": "A. Manager",
     "signed_at": "2026-05-14T09:12:03+00:00", "verdict": "invalid"}
  ],
  "findings_truncated": false
}
```

- **`invalid` vs `unsigned` are different claims.** `invalid` means the digest
  no longer re-derives — the amount, the actor, or the timestamp changed after
  the fact. `unsigned` means the row carries no signature block at all: an
  approval written before `FEOH_APPROVAL_SIGNING_KEY` was configured has nothing
  to verify and is not evidence of tampering. Both are listed (an unsigned
  approval inside a period where signing IS configured is worth explaining), but
  they are counted separately so a key-rollout backlog can't read as fraud.
- **Counts are never truncated.** `limit` (default 100, max 1000) bounds the
  `findings` array only and sets `findings_truncated`; the population counts
  always cover the whole range. Rows are streamed (`yield_per`), so a large
  period doesn't materialise in memory.
- **A clean run is the evidence.** `invalid == 0 && unsigned == 0` over the
  period is the control test passing; anything else names exactly the rows to
  investigate, which the per-invoice endpoint then drills into.
- `end` is whole-day inclusive (same convention as `/api/audit/export`); at
  least one of `start`/`end` is required, and an inverted range is a generic
  `400`. The sweep is itself audited (`audit.viewed`, `details` = scope +
  counts, PII-free) and reads only the caller's own tenant DB.

## The signing key (`FEOH_APPROVAL_SIGNING_KEY`)

| | |
|--|--|
| Default | empty → signing is skipped (no signature block on the row) |
| Local dev | a **NON-secret** dev value committed in `backend/.env.development` so the feature is exercisable under `pnpm dev` |
| Deployed | the real key via **sops + KMS** |

**No hardcoded production fallback.** An empty key yields an empty signature,
mirroring `webhook_security.verify_hmac_sha256`'s fail-closed posture. Rotating
the key invalidates verification of rows signed under the old key — treat it as
a key-ceremony event (the digest is re-derivable only with the key that signed
it), so rotate deliberately, not casually.

## Tests

`backend/tests/test_approval_signatures.py`:

- Pure: sign/verify round-trip, tamper detection (amount / actor / timestamp),
  wrong-key, money-exactness (`100.00 == 100.0`), no-key fail-closed.
- Pure (`check_approval_row`): the three verdicts, every "signed but corrupt"
  shape (non-dict block, empty block, unparseable / non-string `signed_at`,
  missing actor, no key), and a non-object `details` column — none of which may
  raise.
- Endpoint (real Postgres + ASGI app): valid signature verifies; a
  post-approval amount tamper → `valid: false`; access-audit row written; RBAC
  (clerk 403, no-auth 401); the full `review.approve_invoice` flow signs a row
  the endpoint then confirms valid.
- Population sweep (real Postgres + ASGI app): a clean period reports zero
  findings; a tampered row is found without naming the invoice up front;
  `unsigned` is counted apart from `invalid`; the date range filters (and `end`
  is whole-day inclusive); `limit` truncates `findings` but never the counts;
  the access-audit row carries counts only; missing / inverted range → 400;
  RBAC (clerk 403, no-auth 401); another tenant's approvals are invisible; and a
  corrupt row — non-object `details`, or a malformed `signature.value` — is a
  finding on both surfaces rather than a 500 that loses the rest of the
  period's evidence.
