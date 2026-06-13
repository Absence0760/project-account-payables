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
- `frontend/tests-e2e/notifications/` — center badge/list, mark read, unread
  filter + empty state, preference persistence.
