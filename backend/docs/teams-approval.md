# Microsoft Teams interactive approval — approve / reject from the Teams card

When an invoice is **assigned for review** and the org's chat provider is Teams,
the approval card posted to the channel can carry **Approve / Reject actions**.
An approver clicks an action and the decision runs through the **exact same**
`services.review` path the in-app buttons use — no app login, no JWT. This is the
Teams counterpart of the already-shipped Slack interactive approval and closes
the "Slack/Teams approval" gap under **Advanced Approval Routing** in
`docs/roadmap_shipped.md`.

It builds directly on the same two pieces as Slack and adds nothing weaker:

- the **outbound Teams adapter**
  (`services/chat_notification_adapters/teams_adapter.py`) — posts the approval
  MessageCard / card to the channel; and
- the **email-approval action token** (`services/email_action_token.py`) — reused
  as the credential, bound to a new `teams` channel so the surfaces' tokens are
  not interchangeable.

## The button credential — the action token, bound to a channel

Each action's payload carries a signed, single-use **action token** — the same
primitive the email-approval link and the Slack buttons use. It binds, under an
HMAC-SHA256 signature the platform alone can produce, the exact facts the action
will run against:

```
tenant_slug + invoice_id + actor_id (the intended approver) + action + channel + expiry + jti
```

The `channel` claim (`email` / `slack` / `teams`) is what makes the surfaces
distinct:

- `build_teams_action_tokens(...)` mints `channel="teams"` tokens for the card;
- the Teams endpoint calls `verify_action_token(..., expected_channel="teams")`;
- the Slack endpoint uses `"slack"`; the email endpoint keeps the default
  `"email"`.

So a Teams token presented to the email-confirm or Slack endpoint — or a Slack /
email token fed into the Teams endpoint — fails verification. A token built with
no channel (the original email callers) defaults to `email`, so the email-approval
flow is unchanged.

The token is minted **per intended approver** (same no-privilege-escalation
property as the per-recipient email link). Tokens are only added when
**`FEOH_EMAIL_ACTION_SIGNING_KEY` is set and the chat provider is `teams`**;
otherwise the card stays a plain (non-interactive) post.

## The inbound webhook

```
POST /api/approvals/teams/interactivity   — PUBLIC, no JWT
```

`app/api/teams_approvals.py`. Public-by-design (Teams POSTs it; the signature +
the token are the gates) and listed in `NO_AUTH_REQUIRED` in `tests/test_rbac.py`,
exactly like the PEPPOL-inbound, email-approval, and Slack routes.

Two gates, layered, both fail closed:

1. **Teams request signature.** A Teams **Outgoing Webhook** signs every POST as
   `Authorization: HMAC <base64(hmac-sha256 over the raw body)>` using a
   base64-encoded shared **security token** (`FEOH_TEAMS_SECURITY_TOKEN`). We
   base64-decode the secret to get the HMAC key, recompute the digest over the
   raw bytes, and compare with a constant-time `hmac.compare_digest`. When Teams
   includes an `X-Teams-Request-Timestamp` header we additionally **reject a
   timestamp more than `FEOH_TEAMS_REQUEST_MAX_AGE_SECONDS` (default 300s) from
   now** so a captured POST can't be replayed; the header is optional (Teams does
   not always send it), and when absent the single-use `jti` + the workflow state
   machine still bound replay. No secret configured → the feature is OFF and every
   request is rejected (no hardcoded fallback).
2. **Action token.** Parse the JSON Activity envelope, pull the token from the
   action payload (`value.token`, or — for configs that echo it — the Activity
   `text`), and `verify_action_token(..., expected_channel="teams")` (HMAC +
   expiry + channel). Then load the named reviewer (active, right org, holds an
   approver role `admin`/`ap_manager`/`cfo`), **claim the token `jti`** in Redis
   (single-use), and call `review.approve_invoice` / `review.reject_invoice` **as
   the reviewer**. Segregation of duties, the approval thresholds, the CFO gate,
   the `invoice.approved`/`invoice.rejected` immutable audit row, and the approval
   digital signature all apply exactly as if they had logged in.

If the action turns out not applicable / not permitted (invoice no longer
`ready_for_review`, segregation block, CFO gate, over the cap), the `jti` claim is
**released** so the reviewer can still act in-app.

### Opaque responses — no enumeration

Every path — success **and** every rejection (bad signature, missing
`Authorization`, stale timestamp, expired / replayed / wrong-channel token,
unknown tenant / invoice, non-approver, feature off) — returns the **same opaque
`200` message Activity** (`{"type": "message", "text": "..."}`). A 4xx, or a
distinct message per failure, would let a probe enumerate tenants, invoices, or
which secret/token shapes are accepted. The handler is fully self-guarded — it
never raises a 500 on the public route — and logs only a PII-free reason code
(never the token, the invoice fields, the webhook URL, or any banking/PII).

### Single-use, layered (replay protection)

- **Teams timestamp window** — when the timestamp header is present, a captured
  POST replayed > 5 min later is rejected before any work.
- **Workflow state machine** — approve/reject move the invoice out of
  `ready_for_review`, so the same decision can't re-fire.
- **Redis `jti` consume** — `SET NX EX` on the token id closes the
  reject→resubmit replay window. The token IS the dedupe — a re-clicked action
  can't double-act. This is the primary guard when Teams omits the timestamp.

## What's PII-free

Nothing sensitive enters the Teams card or the logs. The outbound card carries
only invoice number, vendor name, amount + currency, the status word, and a deep
link (the same fields the outbound chat adapter already allowed) plus the opaque
HMAC token(s). Never bank details, tax IDs, addresses, or payment-method numbers.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FEOH_TEAMS_SECURITY_TOKEN` | (empty) | Teams Outgoing-Webhook **security token** (base64) for the interactivity-POST HMAC. **Empty → feature OFF**: every inbound POST rejected (fail-closed, no hardcoded fallback). NON-secret base64 dev value committed in `.env.development`; real secret via sops. The token's presence IS the on/off switch (mirrors `FEOH_SLACK_SIGNING_SECRET`). |
| `FEOH_TEAMS_REQUEST_MAX_AGE_SECONDS` | `300` | Reject a Teams interactivity POST whose `X-Teams-Request-Timestamp` is more than this far from now (replay-window guard; only enforced when the header is present). |
| `FEOH_EMAIL_ACTION_SIGNING_KEY` | (empty) | Reused to sign the action token (bound to the `teams` channel). Empty → no actions added. |
| `FEOH_EMAIL_ACTION_TTL_HOURS` | `168` | Reused as the action token's validity window. |

The outbound side also needs the org to have its chat provider set to `teams`
with a configured `webhook_url` on `Organization.settings.chat_notifications`
(see `notifications.md` § Chat notifications). The actions appear on the
`invoice_assigned` event.

## Teams app setup (deployed)

1. In the Teams channel, add an **Outgoing Webhook**, point its callback URL at
   `https://<api-host>/api/approvals/teams/interactivity`, and copy the generated
   **security token** into `FEOH_TEAMS_SECURITY_TOKEN` (sops). (Or use an Incoming
   Webhook for the outbound card + an Outgoing Webhook for the action callback.)
2. Set `FEOH_EMAIL_ACTION_SIGNING_KEY` (sops) if not already set for email / Slack
   approval.

No real Teams account is needed for tests — the test suite constructs
correctly-signed interactivity requests in-process.

## Tests

- `tests/test_teams_approvals.py` (realdb) — signed approve/reject happy paths +
  immutable audit / exception rows, token-in-`value` and token-in-`text`,
  single-use replay, bad Teams signature → opaque no-op, missing `Authorization`
  → reject, stale timestamp → reject, expired token → reject, feature-off
  (no secret) → reject, segregation + non-approver gates, the
  `teams`/`slack`/`email` channel-binding (a teams token is rejected under the
  email and slack expectations and vice versa), and the
  `build_teams_action_tokens` primitive.
- Auth-gating is covered by `tests/test_rbac.py` (the route is in
  `NO_AUTH_REQUIRED`).

## Deferred

- **Outbound interactive card rendering** — the Teams adapter currently posts a
  read-only MessageCard. Wiring `build_teams_action_tokens` into an interactive
  Adaptive Card / `Action.Http` payload (the outbound counterpart, mirroring the
  Slack adapter's Block Kit buttons) is a small follow-up on the outbound track;
  the inbound endpoint already accepts the token the moment it is wired.
- **Richer message updates** — on success we return a simple message ack rather
  than rewriting the original card in place. That polish is a follow-up.
