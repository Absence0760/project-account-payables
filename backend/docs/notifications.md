# Notifications

Email + in-app notifications for invoice lifecycle events. One centralized
hook, preference-gated per recipient, fully best-effort (a notification failure
never breaks a status transition or its audit row).

## Hook point

Every invoice status change funnels through
`services/workflow_engine.py::transition_invoice()`, which writes the audit row
(`dispatch_audit`) and then — keyed off the **resulting status** — calls
`notification_dispatch.notify_event()`. Because all paths that converge on a
status (payment webhook, ERP webhook, and ERP-sync all reach `paid`) go through
this one function, notifications fire once, centrally, instead of at every call
site.

The "assigned" event is the exception: assignment (`review.assign_reviewer`)
sets `assigned_to_id` without always changing status, so it calls
`notify_event(... event_type="invoice_assigned", recipient_user_ids=[reviewer])`
explicitly after its audit write.

Both the in-transition hook and the assign hook are wrapped so any failure is
logged (PII-free) and swallowed — the transition/assignment always completes.

## Event → recipient matrix

| Event type | Trigger (resulting status / action) | Recipients |
|---|---|---|
| `invoice_assigned` | `review.assign_reviewer` | the (possibly delegated) reviewer |
| `invoice_approved` | `transition_invoice → approved` | invoice uploader (`uploaded_by_id`) |
| `invoice_rejected` | `transition_invoice → rejected` | invoice uploader |
| `invoice_paid` | `transition_invoice → paid` (payment webhook / ERP webhook / ERP-sync) | invoice uploader + every `ap_manager` in the org |
| `chat_message` | supplier posts on the portal → every `ap_manager`; AP posts with @mentions → the mentioned AP users (poster excluded) | see below |

`ap_manager` recipients are resolved against the control plane via
`notification_dispatch.resolve_role_user_ids(org_id, "ap_manager")`.

For `invoice_paid` and `invoice_rejected`, the same chokepoint **also** emails
the invoice's **vendor portal users** — see § Vendor recipients below.

### Vendor recipients (`invoice_paid` / `invoice_rejected`)

The supplier portal lets a vendor get **emailed** when one of *their own*
invoices is paid or rejected (supplier-portal Phase 2). This fans out from the
same `transition_invoice` chokepoint, but through a **separate** path —
`services/vendor_notifications.notify_vendor_of_invoice_event` — because the
recipients are `VendorUser`s (tenant DB), not control-plane `User`s, and
`notify_event` only ever reaches control-plane Users:

- It loads the **active** portal users of the invoice's `vendor_id` and emails
  each one whose per-user preference allows it.
- It is **independent** of the control-plane recipient resolution: a
  portal-submitted invoice usually has no `uploaded_by_id` (the actor was a
  VendorUser), so it has zero control-plane recipients yet still must reach the
  supplier. The vendor fan-out runs even when the control-plane recipient list
  is empty.
- It runs under its own best-effort guard (in addition to `notify_event`'s) so a
  failure never breaks the invoice transition or its audit row.
- Preferences are stored on `vendor_users.notification_prefs` (JSONB, migration
  0052), keyed by the **same** `invoice_paid` / `invoice_rejected` event strings
  and read with the **same** `resolve_prefs` helper (opt-out, defaults on).
  Vendors have no in-app center, so only the `email` channel is consulted.
- Emails reuse the shared **PII-free** templates (`notification_templates.render`)
  — invoice number, vendor name, amount, currency, optional rejection reason.
  Failures log the event type only, never the recipient address.

The vendor-friendly API shape (`email_on_payment` / `email_on_rejection`) and
the GET/PATCH portal endpoints are documented in
[supplier-portal.md](supplier-portal.md) § Notification preferences.

### Email approval links (`invoice_assigned` only)

When `AP_EMAIL_ACTION_SIGNING_KEY` is set, the **`invoice_assigned`** email gains
per-recipient **Approve / Reject** links so the reviewer can decide straight from
the email without logging in. `notify_event` resolves the tenant slug once and
calls `email_action_token.build_email_action_links` per recipient (the token
binds to that reviewer + invoice + action). The links land on the public
`GET /api/invoices/email-action/{token}` confirm page → `POST .../confirm`, which
runs the normal `services/review` approve/reject path. Empty key → no links
added. Full design + security properties: [email-approval.md](email-approval.md).

### `chat_message` (supplier chat)

The embedded supplier-chat feature fans out via `services/supplier_chat.py`
(not the `transition_invoice` chokepoint — chat posts don't change invoice
status):

- **Supplier posts (portal)** → `notify_supplier_post` notifies every
  `ap_manager` so the AP team learns of the reply (`actor_id=None`).
- **AP posts with @mentions** → `notify_ap_mentions` notifies the mentioned
  control-plane `users.id`, **excluding the posting AP user**.
- **AP posts (any)** → the *supplier* gets a **direct portal-link email**
  (`notify_supplier_of_ap_message`), not an in-app/`notify_event` notification —
  `notify_event` only ever reaches control-plane Users, never a VendorUser. The
  email carries `{base}/portal/invoices/{id}/chat`, is best-effort, PII-free
  (invoice number + vendor/org name only), and is gated on
  `notifications_enabled` explicitly.

The `chat_message` render branch emits only `"New message on {ref}"` plus an
optional short author label (`InvoiceContext.note` — `"from supplier"` /
`"you were mentioned"`). **Never** the raw message body. See
[supplier-chat.md](supplier-chat.md).

## Two effects per recipient, each preference-gated

For each recipient, `notify_event`:

1. **in-app** — inserts a `Notification` row into the **tenant** DB on the
   caller's session (commits atomically with the status change), *if* the
   recipient's `in_app` preference is on for that event.
2. **email** — builds an `EmailMessage` and hands it to the configured email
   adapter (`get_email_adapter()` — `console` default), *if* the recipient's
   `email` preference is on.

Recipients are de-duplicated, so a user who is both uploader and AP manager
gets one notification.

## Preferences

User-global, stored on the control-plane `users.notification_prefs` JSONB:

```json
{
  "invoice_assigned": { "email": true, "in_app": true },
  "invoice_approved": { "email": true, "in_app": true },
  "invoice_rejected": { "email": true, "in_app": true },
  "invoice_paid":     { "email": true, "in_app": true },
  "chat_message":     { "email": true, "in_app": true }
}
```

Opt-out, not opt-in: a missing event or missing channel key defaults to **on**
(`resolve_prefs`). The `/api/notifications/preferences` endpoints read/patch
this blob; the `/profile` page renders the per-event toggle grid.

## Templates & PII

`services/notification_templates.py::render(event_type, InvoiceContext)` returns
`(title, body_text, body_html)`. Templates reference **only** invoice number,
vendor name, amount (rendered exactly from the `Decimal`), currency, and an
optional rejection reason — never bank details, tax IDs, full addresses, or
payment-method numbers. Email-send failures log the event type + nothing else
(never the recipient address).

## In-app data model

Tenant table `notifications` (`models/notification.py`):

- `id`, `correlation_id` (carries the invoice's, for audit-shipping
  correlation), `organization_id`
- `recipient_user_id` (control-plane `users.id`, no FK — cross-DB), indexed
- `event_type`, `entity_type` (default `invoice`), `entity_id`
- `title`, `body`, `read_at` (NULL = unread)
- indexes: `(recipient_user_id)` and `(recipient_user_id, read_at)` for the
  unread-count query

## API

All endpoints require auth and are tenant-scoped to `recipient_user_id ==
current_user.id` (preferences via the control plane). Mark-read on another
user's row returns the same 404 as a missing row — no enumeration.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/notifications?unread_only=&page=&page_size=` | paginated list; envelope includes `unread` |
| GET | `/api/notifications/unread-count` | `{unread}` for the badge |
| POST | `/api/notifications/{id}/read` | mark one read (naturally idempotent) |
| POST | `/api/notifications/read-all` | `{updated}` |
| GET | `/api/notifications/preferences` | current prefs (defaults if unset) |
| PATCH | `/api/notifications/preferences` | partial update; unspecified events unchanged |

## Configuration & local-first

- `AP_NOTIFICATIONS_ENABLED` (default `true`) — master kill switch. When off,
  both hooks skip dispatch entirely.
- No new external dependency: outbound email reuses the existing adapter stack
  (`AP_EMAIL_PROVIDER`, `console` default — no network, no secrets). Mailpit
  (`pnpm mail:up`) previews SMTP locally. The in-app center is pure Postgres.

## Tests

- `tests/test_notification_templates.py` — content + PII safety (pure).
- `tests/test_notification_dispatch.py` — recipient mapping per event,
  preference gating (in-app/email suppression), email-failure isolation (the
  transition + audit row survive), kill switch (real DB).
- `tests/test_notifications.py` — router: per-user scoping, cross-user/tenant
  isolation, mark-read 404, read-all, pagination shape, prefs round-trip.
- `tests/test_vendor_notification_prefs.py` — vendor prefs: pure mapping
  (`prefs_to_response` / `apply_pref_update`), the GET/PATCH portal endpoints
  (vendor-scoped, auth enforced, audited, caller-only), and the dispatch
  substance — paid emails the vendor when on, rejected suppressed when off,
  inactive/other-vendor users skipped, failing adapter never breaks the
  transition (real DB).
- `frontend/tests-e2e/notifications/` — center badge/list, mark read, unread
  filter + empty state, preference persistence.
