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
| `verify_approval(*, …, signature, signing_key) -> bool` | Constant-time (`hmac.compare_digest`) re-derive + compare. Empty key / empty signature / mismatch → `False` (never raises). |
| `build_signature_detail(*, …, signing_key) -> dict \| None` | The `details["signature"]` block written on the audit row. `None` when no key. |

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
- Endpoint (real Postgres + ASGI app): valid signature verifies; a
  post-approval amount tamper → `valid: false`; access-audit row written; RBAC
  (clerk 403, no-auth 401); the full `review.approve_invoice` flow signs a row
  the endpoint then confirms valid.
