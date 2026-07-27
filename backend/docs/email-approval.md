# Email approval — approve / reject from the notification email

An AP reviewer who is assigned an invoice for review can **approve or reject it
straight from a link in the assignment email — without logging in**. The link
carries a signed, expiring, single-action token that *is* the credential; there
is no JWT and no session. Clicking lands on a confirmation page, and the decision
runs through the **exact same** `services.review` path the in-app buttons use, so
every control (segregation of duties, approval thresholds, the CFO gate, the
immutable audit row, the approval digital signature) still applies.

This closes the "Email approval" item under **Advanced Approval Routing** in
`docs/roadmap.md`.

## Why a token, not a login

The reviewer never authenticates. Instead, the email link embeds a token that
binds — under an HMAC-SHA256 signature the platform alone can produce — the exact
facts the action will run against:

```
tenant_slug + invoice_id + actor_id (the reviewer) + action + expiry + jti
```

Because the signing key is held only by the platform (sops + KMS in deployed
envs), the token cannot be forged, and flipping the action, the invoice, or the
actor invalidates the signature. This is the same fail-closed HMAC posture as
[approval signatures](approval-signatures.md) and the webhook handlers.

The token lives in `app/services/email_action_token.py` (pure: no DB, no
network, no settings import — key + TTL are passed in). Format:

```
<base64url(payload-json)>.<hex hmac-sha256 over the base64 string>
```

`verify_action_token` returns the decoded facts or `None` (never raises) on an
empty key, bad signature, unknown action, malformed payload, or expiry.

## The two-step click (prefetch safety)

The action is split across **GET → confirm page** and **POST → perform**:

| Method | Route | Effect |
|---|---|---|
| `GET`  | `/api/invoices/email-action/{token}` | Renders a confirmation page. **Never mutates.** |
| `POST` | `/api/invoices/email-action/{token}/confirm` | Performs the approve/reject. |

The split is deliberate: email clients, corporate link-scanners, and
preview-fetchers routinely issue a **bare GET** on every link in a message. If
the GET performed the action, those scanners would silently auto-approve
invoices. So the GET only *renders* — the state change happens only on the POST
the reviewer submits from the confirmation page (reject offers an optional reason
field).

Both routes are **public-by-design** (the token is the credential) and live in
`NO_AUTH_REQUIRED` in `tests/test_rbac.py`. They are served as small,
self-contained HTML pages by the backend (no SPA dependency) so the flow works
even though the frontend is a static site.

## What the POST actually does

`app/api/email_actions.py` → `email_action_perform`:

1. **Verify** the token (signature + expiry). Invalid → friendly 400 page.
2. **Resolve** the tenant org from the token's slug (control plane).
3. **Load the reviewer** named in the token, scoped to that org, with roles —
   must be active, in the right org, and hold an approver role
   (`admin` / `ap_manager` / `cfo`, matching `require_roles(...)` on the in-app
   approve/reject endpoints — the email door is never weaker than the app door).
4. **Claim the token `jti`** in Redis (`SET NX EX`) — single-use. A replay shows
   "already used".
5. **Open a short-lived tenant session**, row-lock the invoice, and — only if it
   is still `ready_for_review` — call `review.approve_invoice` /
   `review.reject_invoice` **as the reviewer**. This is the same code the
   authenticated endpoints call, so segregation, thresholds, the CFO gate, the
   `invoice.approved` / `invoice.rejected` immutable audit row, and the approval
   signature all happen exactly as normal.
6. **Render** a success / info page.

If the action turns out not to be applicable or not permitted (invoice no longer
awaiting review, segregation block, CFO gate, over the max-amount cap), the jti
claim is **released** so the reviewer can still act in-app, and the page explains
why (e.g. "this invoice requires CFO approval — please sign in").

### Single-use, layered

- **Workflow state machine** — approve/reject move the invoice out of
  `ready_for_review`, so the same decision can't re-fire (the hard guard).
- **Redis `jti` consume** — also closes the reject→resubmit replay window (a
  stale token reused after the invoice cycles back to `ready_for_review`).

## The email link

`services/notification_dispatch.notify_event` injects the Approve/Reject links
into the **`invoice_assigned`** email only, and only when a signing key is
configured. The links are **per-recipient** — the token binds to that specific
reviewer — built by `email_action_token.build_email_action_links` against
`settings.api_public_url`. When no key is set, no links are added (the rest of
the email is unchanged). Preview the rendered email locally with Mailpit
(`pnpm mail:up`, `FEOH_EMAIL_PROVIDER=smtp`).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FEOH_EMAIL_ACTION_SIGNING_KEY` | (empty) | HMAC-SHA256 key for the link token. **Empty → feature OFF**: no links added, every token rejected (fail-closed, no hardcoded fallback). NON-secret dev value committed in `.env.development`; real key via sops in deployed envs. |
| `FEOH_EMAIL_ACTION_TTL_HOURS` | `168` | Link validity window (7 days). Past this, the reviewer signs in instead. |

Single knob: the key's presence *is* the on/off switch (mirrors
`FEOH_APPROVAL_SIGNING_KEY`). No separate enabled flag and no boot guard — an
unset key simply disables the feature everywhere.

## Security properties

- **Unforgeable / tamper-evident** — HMAC over the canonical payload; any edit to
  action/invoice/actor/expiry breaks the signature.
- **Expiring** — default 7 days, then re-auth in the app.
- **Single-use** — state machine + Redis `jti`.
- **No privilege escalation** — runs as the named reviewer with *their* roles;
  segregation + CFO gate + thresholds all enforced.
- **Prefetch-safe** — GET never mutates; POST does.
- **No enumeration / PII** — invalid token, unknown tenant, and missing invoice
  all render the same generic "invalid or expired" page; pages show only the
  invoice number / vendor / amount the reviewer already received in the email —
  never bank details, tax IDs, or addresses. The confirmation pages carry
  `noindex`.

## Tests

- `tests/test_email_action_token.py` — pure token: round-trip, wrong key,
  tampered payload/signature, action-flip, expiry, empty-key fail-closed, link
  builder.
- `tests/test_email_actions.py` (realdb) — GET renders + doesn't mutate, POST
  approve + single-use replay, POST reject + exception row, invalid/tampered/
  no-key tokens, segregation block, non-approver role, wrong-status no-op, and
  the assigned-invoice email link injection.
- Auth-gating is covered by `tests/test_rbac.py` (both routes are in
  `NO_AUTH_REQUIRED`).
