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

When `FEOH_EMAIL_ACTION_SIGNING_KEY` is set, the **`invoice_assigned`** email gains
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

## Chat notifications (Slack / Teams)

In addition to per-recipient email + in-app, an org can fan **approval-lifecycle
events** out to a team chat channel (Slack or Microsoft Teams) via that
provider's **incoming webhook**. This is a single channel post per event (not
per-recipient) and is **entirely best-effort** — a chat-send failure never
breaks the invoice transition.

### Adapter family

`services/chat_notification_adapters/` mirrors `email_adapters/` exactly — a
decorator registry + a per-org-config-aware factory + a local-first `mock`
default:

| Provider | Body shape | Notes |
|---|---|---|
| `mock` | — | **Default.** No network, no credential. Records sends on `mock_adapter.SENT` + logs a PII-free line, so `pnpm dev` exercises the full path with no real Slack/Teams. |
| `slack` | `{"text": ..., "blocks": [section]}` | Posts to a Slack incoming-webhook URL via httpx. |
| `teams` | legacy `MessageCard` (`@type`/`sections.facts`/`potentialAction`) | Posts to a Teams incoming-webhook URL via httpx. |

`get_chat_notification_adapter(org_config)` resolves the provider from
`org_config["provider"]` → `FEOH_CHAT_NOTIFICATION_PROVIDER` (default `mock`); an
unknown key falls back to `mock` and never raises.
`render_chat_message(event_type, ...)` builds the PII-free `ChatMessage`
(returns `None` for non-approval events like `chat_message` /
`contract_renewal_due`, so they're skipped).

### Per-org configuration

`Organization.settings.chat_notifications` (JSONB — no migration, mirroring how
`notifications` / `residency` carry config in settings-JSON):

```json
{
  "enabled": true,
  "provider": "slack",
  "webhook_url": "https://hooks.slack.com/services/...",
  "events": { "invoice_assigned": true, "invoice_approved": true,
              "invoice_rejected": true, "invoice_paid": false }
}
```

- `enabled` is the per-org master gate and defaults **off** — chat is opt-in per
  org (unlike email/in-app, which default on). When off, no adapter is built.
- `events` is opt-out *within* an enabled org: a missing event key defaults on.
- `webhook_url` is the per-org credential, carried in settings (it's a
  channel-scoped URL, not a platform secret). The `slack` / `teams` adapters
  **fail closed** when it's absent: a no-op + a PII-free warning, never an
  exception. There is **no hardcoded fallback** webhook URL.

### Wiring

`notification_dispatch.notify_event` dispatches chat **after** the per-recipient
email/in-app loop, once per event, for the four approval events email already
handles (`invoice_assigned` / `invoice_approved` / `invoice_rejected` /
`invoice_paid`). `_send_chat_best_effort` loads the org's
`settings.chat_notifications`, checks the enable + per-event gate, renders the
message, builds the adapter, and sends — the whole thing wrapped in its own
try/except so any failure (config load, adapter build, transport) is swallowed
and logged PII-free. The deep link is built from `FEOH_TENANT_URL_TEMPLATE` +
invoice id (no secrets).

### PII

The chat message carries **only** invoice number, vendor name, amount + currency
(rendered exactly from the `Decimal`), a human status word, and an optional deep
link — **never** bank details, tax IDs, full addresses, or payment-method
numbers. Failure logs record the event type only, never the webhook URL or the
amount.

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

`services/notification_templates.py::render(event_type, InvoiceContext, *, locale=None)`
returns `(title, body_text, body_html)`. Templates reference **only** invoice
number, vendor name, amount (rendered exactly from the `Decimal`), currency, and
an optional rejection reason — never bank details, tax IDs, full addresses, or
payment-method numbers. Email-send failures log the event type + nothing else
(never the recipient address). The copy strings live in the per-locale **email
catalogue** (`render` pulls them keyed by `notif.<event>.{title,body}` — see §
Localized email); `locale=None` is English, which the **in-app** notification
center always uses (the locale pref drives email only).

## Localized email

Outbound transactional email renders server-side, so the recipient's language is
carried by a DB-synced, account-level `locale` preference — "what language to
email this person in". It is **separate** from the frontend's per-device UI
locale picker (`frontend/src/lib/i18n/`): the DB pref is written from the UI and
read by the **email-render path only**, and is **never** returned to drive in-app
UI.

**Where the pref lives** (nullable; NULL → English fallback):

- `User.locale` — control plane (AP employees). Set via `PATCH /api/auth/me`
  (`{"locale": "<loc>"}` — supported value sets it, `""` clears it → English, an
  unknown value 422s). Surfaced on `GET/PATCH /api/auth/me` (`UserResponse.locale`).
- `VendorUser.locale` — tenant-scoped (supplier-portal users). Set via
  `PATCH /api/portal/auth/me`, same validation. Surfaced on the portal me-route.

Both added by migration **`0059_email_locale_pref`** — a single, existence-guarded
revision that runs on BOTH the control DB (adds `users.locale`) and every tenant
DB (adds `vendor_users.locale`); each `ADD COLUMN IF NOT EXISTS` is gated on its
table existing, so the same revision is safe on both. **Fan-out**: control DB via
`alembic upgrade head`; every existing tenant via
`python scripts/migrate_all_tenants.py` (or `FEOH_MIGRATE_TENANT=feoh_<slug> alembic
upgrade head`); fresh tenants get the column from `create_all` in
`tenant_provisioning` (the model field). Nullable + reversible.

**The catalogue** — `app/services/email_adapters/email_catalogue.py`:

- `SUPPORTED_EMAIL_LOCALES = (en, de, fr, es, pt-BR, ja)` — the same six as
  web/mobile. `DEFAULT_LOCALE = "en"`.
- `normalize_locale(locale)` — coerces any value (case / `_`-vs-`-` / base
  language like `pt` → `pt-BR`, `de-AT` → `de`) to a supported locale; unknown /
  `None` → `"en"`. Never raises. Used at the **render** path.
- `is_supported_locale(locale)` — STRICT exact-match (no coercion). Used at the
  **write** path so a stored preference is always a canonical locale.
- `translate(key, locale, /, **params)` — per-key resolution: requested locale →
  English → raw key (so a missing key is visible, never empty / a crash).
  `{placeholder}` tokens are filled from `params`; a missing param leaves the
  literal token rather than raising. **English is the always-present fallback** —
  a non-English catalogue may translate a subset; any untranslated key resolves
  to the English string.

**Only copy is localized.** Deep links, brand chrome (the adapter's
`apply_brand` header/footer), money amounts, invoice numbers, and vendor names
are interpolated as `{placeholder}` tokens — a translation can reorder them but
can't drop or distort them (the parity test asserts the placeholder set matches
English exactly per key).

**Surfaces covered:**

| Surface | Locale source |
|---|---|
| Invoice notifications → employees (`notification_dispatch.notify_event`) | per-recipient `User.locale`; re-rendered per email recipient (the in-app row stays English) |
| Invoice notifications → suppliers (`vendor_notifications.notify_vendor_of_invoice_event`) | per-recipient `VendorUser.locale`; rendered per portal user |
| Signup verification + welcome emails (`api/signup.py`) | optional `locale` on `POST /api/signup/start`, normalized + stashed in `EmailVerification.meta`, reused by the welcome email at `/complete` |
| Supplier-chat portal-link email (`supplier_chat.notify_supplier_of_ap_message`) | catalogue-routed, English default — the recipient is the `Vendor` contact address, not an identified `VendorUser`, so there's no per-user locale to read |

**Deferred (frontend track):** the language-picker → backend write. The backend
endpoints + persistence are in place; the frontend profile/portal picker calling
`PATCH .../me` with the chosen locale is owned by the frontend i18n track.

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

- `FEOH_NOTIFICATIONS_ENABLED` (default `true`) — master kill switch. When off,
  both hooks skip dispatch entirely.
- No new external dependency: outbound email reuses the existing adapter stack
  (`FEOH_EMAIL_PROVIDER`, `console` default — no network, no secrets). Mailpit
  (`pnpm mail:up`) previews SMTP locally. The in-app center is pure Postgres.
- `FEOH_CHAT_NOTIFICATION_PROVIDER` (default `mock`) — platform-default chat
  adapter. Per-org override + webhook URL + per-event toggles live on
  `Organization.settings.chat_notifications` (see § Chat notifications above).
  The `mock` default needs no Slack/Teams credential, so `pnpm dev` runs
  unchanged.

## Tests

- `tests/test_notification_templates.py` — content + PII safety (pure).
- `tests/test_email_catalogue.py` — email-catalogue parity (every locale resolves
  every key, no empty strings, placeholder-faithful vs English), English fallback
  on a missing key / unknown locale, `normalize_locale` / `is_supported_locale`,
  and `render(locale=…)` producing non-English copy while keeping the
  number/vendor/exact-money identical + PII-free (all pure).
- `tests/test_locale_pref_endpoints.py` — set-locale endpoints (employee
  `PATCH /api/auth/me` + supplier `PATCH /api/portal/auth/me`): persists a valid
  locale, rejects unknown (422), clears on `""`, omitting leaves it untouched,
  auth enforced; per-recipient persistence to the right DB (real DB).
- `tests/test_notification_dispatch.py` — recipient mapping per event,
  preference gating (in-app/email suppression), email-failure isolation (the
  transition + audit row survive), kill switch (real DB).
- `tests/test_notifications.py` — router: per-user scoping, cross-user/tenant
  isolation, mark-read 404, read-all, pagination shape, prefs round-trip.
- `tests/test_chat_notification_adapters.py` — chat fan-out: mock default +
  unknown-key fallback, per-org provider override, Slack vs Teams body shaping
  (httpx mocked, no network), fail-closed when no webhook URL, PII absent from
  the rendered message, and `_send_chat_best_effort` swallowing a send failure /
  honouring the enable + per-event gate (all pure / mocked — no DB).
- `tests/test_vendor_notification_prefs.py` — vendor prefs: pure mapping
  (`prefs_to_response` / `apply_pref_update`), the GET/PATCH portal endpoints
  (vendor-scoped, auth enforced, audited, caller-only), and the dispatch
  substance — paid emails the vendor when on, rejected suppressed when off,
  inactive/other-vendor users skipped, failing adapter never breaks the
  transition (real DB).
- `frontend/tests-e2e/notifications/` — center badge/list, mark read, unread
  filter + empty state, preference persistence.
