---
description: Targeted audit of every webhook handler against invariant #9 (HMAC verification + event dedup + silent rejection). Use after adding a new webhook endpoint or after changing the shared `services/webhook_security.py` helpers.
---

Sweep every inbound webhook handler and report whether each one verifies the provider's HMAC, dedupes by event id, and rejects bad input silently. Read-only.

## Why this command exists

Webhook endpoints are public-by-design — they have no JWT, no tenant header, no rate-limited authentication path. The only thing standing between a public URL and a state mutation is the HMAC. Three failure modes recur:

1. **No signature check.** The handler reads the body and acts on it. Caught in this codebase before (card webhook auto-creating rebates; ERP webhook flipping invoices to `paid`).
2. **Loud rejection on bad input.** Distinct 4xx for "bad signature" vs "unknown tenant" vs "no such payment" enumerates the system. Webhooks must 204 silently regardless of which path failed.
3. **No dedup.** Providers retry on any non-2xx. Without an event-id-based dedup, a single settled-event re-delivery doubles the rebate.

This command pins all three across every webhook handler in one pass.

## Procedure

### 1. Enumerate the webhook surface

Run `grep -rEn '@router\.(post|put|patch).*webhook' backend/app/api/` and list every match. Today the set is:

- `POST /api/payments/webhook/{tenant_slug}/{provider}` (`app/api/payments.py:payment_webhook`)
- `POST /api/cards/webhook/{provider}` (`app/api/cards.py:card_webhook`)
- `POST /api/erp/webhook/{erp_type}` (`app/api/erp_webhook.py:erp_webhook`)
- `POST /api/email-intake/inbound` (`app/api/email_intake.py` — see signature verification helper there)

If the grep finds anything NOT in this list, that's automatically a finding — every webhook must be enumerated here.

### 2. For each handler, verify the four contract points

For each endpoint, read the source (`Read` the file) and check:

1. **HMAC verification present.** Does the handler reach `verify_hmac_sha256` from `app.services.webhook_security`, or `parse_webhook` on an adapter, before mutating state? If not → **Critical**.
2. **Dedup present.** Does the handler call `is_event_already_processed(provider, event_id)` (or rely on `provider_payment_id` uniqueness)? If neither → **Critical**.
3. **Silent rejection.** Every failure path must return 204 / None. A handler that `raise HTTPException(status_code=400, ...)` on missing-tenant / bad-sig / unknown-event is an enumeration vector → **Improvement** (sometimes Critical if it leaks tenant existence).
4. **Side effects gated on the verification result.** Specifically: no DB write, no audit dispatch, no email sent, no ERP push UNTIL the signature has verified AND the event isn't a replay.

### 3. Cross-check the shared helper

Open `backend/app/services/webhook_security.py` and confirm:

- `verify_hmac_sha256` uses `hmac.compare_digest` (constant-time).
- `is_event_already_processed` uses `set ... nx=True, ex=...` semantics (atomic SET-IF-NOT-EXISTS with TTL).
- Empty / missing event id falls through as "not a replay" with an INFO log (so a provider that stops including ids doesn't blackhole every event).

Any regression here is **Critical** because every webhook handler depends on it.

### 4. Spawn the auditor for nuance

If steps 1–3 look clean by inspection, call the security auditor for the parts that grep can't see:

```
Agent({
  subagent_type: "repo-security-auditor",
  prompt: "Audit webhook security across the four endpoints listed in
  `.claude/commands/audit-webhooks.md`. Focus on (a) any state
  mutation that runs before the HMAC verify, (b) any code path
  that distinguishes 'bad signature' from 'unknown tenant' in the
  response, (c) any side effect that fires twice on a replayed
  event. Output critical / improvement / nit, file:line."
})
```

### 5. Render the report

Group findings by handler. For each handler, list the four contract points and PASS/FAIL each one. End with a summary table.

```
## /audit-webhooks report

### /api/payments/webhook/{tenant_slug}/{provider}
  - HMAC verification:   PASS — adapter.parse_webhook handles it
  - Event dedup:         PASS — provider_payment_id + terminal-status guard
  - Silent rejection:    PASS — every failure path is `return`
  - Side-effect gating:  PASS — DB session opens only after parse_webhook returns

### /api/cards/webhook/{provider}
  ...

### /api/erp/webhook/{erp_type}
  ...

Summary: <N>/<total> handlers fully compliant. Critical: <N>. Improvement: <N>.
```

## How "future bugs" get caught

The structure above is invariant-driven, not pattern-driven. When a fifth webhook handler ships next quarter, the agent automatically asks the same four questions. A new handler that introduces a fifth failure mode (say, a TOCTOU between signature verify and DB write) shows up under "side-effect gating" because that section's framing is "no mutation until verify completes" — not "grep for verify_hmac_sha256."
