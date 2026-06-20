# White-Label / Partner Branding

Per-tenant white-labeling so an organization can present the app under its own
product name, logo, and accent colors. Shipped so far:

1. **Brand config + frontend theming** (first slice) — `settings.brand`,
   `GET/PUT /api/organization/branding`, CSS custom-property theming.
2. **Branded outbound surfaces** — the generated PDFs (remittance,
   1099 working copy, SOX audit report) and outbound transactional emails carry
   the tenant's product name + logo + accent, not the platform's.
3. **Custom domains** (this slice) — a tenant can be served on its own vanity
   hostname (`ap.acmecorp.com`) and have the backend resolve it to the right
   tenant, in addition to the `*.localhost` subdomain / `X-Tenant-Slug`
   mechanism. See [Custom domains](#custom-domains-vanity-hostnames) below.

Reseller / partner multi-tenant admin is a later slice (see `docs/roadmap.md`).

## What it covers

| Field | Stored as | Effect |
|-------|-----------|--------|
| `product_name` | `settings.brand.product_name` | Sidebar product name + document `<title>`. Fallback: **"Accounts Payable"**. |
| `logo_url` | `settings.brand.logo_url` | Sidebar logo `<img>`. Fallback: the bundled "AP" mark. |
| `accent_color` | `settings.brand.accent_color` | Overrides the `--accent` CSS token (borders, focus rings, accent text). |
| `accent_strong_color` | `settings.brand.accent_strong_color` | Overrides the `--accent-strong` token (text-bearing accent backgrounds — buttons, active chips). |
| `support_url` | `settings.brand.support_url` | Reserved for support links (exposed via the brand store). |
| `legal_url` | `settings.brand.legal_url` | Reserved for legal/terms links (exposed via the brand store). |

No migration — the brand block lives in `Organization.settings` JSONB, exactly
like `residency` and `chat_notifications`.

## Backend

`GET /api/organization/branding` — returns the tenant's `BrandConfig`. **Read
gated to any authenticated org user** (the whole app themes itself, not just
admins), mirroring `GET /api/organization` / data-residency. An empty field
means "use the platform default".

`PUT /api/organization/branding` — **admin-only** (`require_roles(ROLE_ADMIN)`).
Validated by the Pydantic `BrandConfig` schema (`app/schemas/organization.py`):

- `accent_color` / `accent_strong_color` must be a 3- or 6-digit **hex literal**
  (`#638cff` / `#abc`) — the value is injected verbatim into a CSS custom
  property, so nothing else (no `url(...)`, `expression()`, etc.) is permitted.
- `logo_url` / `support_url` / `legal_url` must be **http(s) URLs** — the logo
  renders as `<img src>` and the links as `<a href>`, so `javascript:`,
  `data:`, and other schemes are rejected.

The mutate path writes `org.settings["brand"]` via `flag_modified` (nested JSONB
in-place mutation isn't auto-marked dirty) and audits
`organization.branding_updated` into the tenant trail. The audit detail is
**PII-free and value-free** — it records only *which* fields are now set
(booleans), never the configured values.

A persisted-but-now-invalid brand block never breaks the read: `_resolve_brand`
falls back to all-empty (= platform defaults).

## Frontend

**Brand store** — `frontend/src/lib/stores/brand.svelte.ts` (a Svelte 5 rune
store). Lazy-loads the branding once per session (`ensureLoaded()`), exactly
like `orgCurrency`: cache + single in-flight request + resilient fallback to
defaults on any failure. Exposes `productName`, `logoUrl`, `supportUrl`,
`legalUrl`, plus `applyTheme()` / `ensureLoadedAndApply()` / `reset()`.

**Pure theming helpers** — `frontend/src/lib/stores/brandTheme.ts` (runtime-free,
unit-tested under vitest): `isValidHexColor()` and `brandThemeVars(brand)`. The
latter returns only the CSS custom-property overrides for colors the org
configured **with a valid hex** — an unset or malformed color is omitted so the
AA-passing `src/app.css` default token stands. The theme is **never duplicated**;
only `--accent` / `--accent-strong` are overridden.

**Application** — `frontend/src/routes/+layout.svelte` runs an `$effect` (gated
on the user being fully signed into a tenant, since the read needs auth) that
calls `brand.ensureLoadedAndApply()`. `applyTheme()` writes the configured
accent tokens onto `document.documentElement.style` (or removes them when
unset). The document `<title>` and the sidebar logo + product name read the
store reactively. `reset()` clears both the cache and the inline `<html>`
overrides (logout / tenant switch).

**Admin UI** — the **Branding** section on `/organization`
(`frontend/src/routes/organization/+page.svelte`): product name, logo URL,
two accent color pickers (native swatch + hex text input), support + legal URLs,
saving via `PUT /api/organization/branding`. Client-side validation mirrors the
backend guards (inline toast on a bad hex / non-http(s) URL). On save it
re-loads the brand store so the sidebar + theme update without a reload.

## Accessibility note

When an org sets a custom accent, that's their choice. The defaults
(`#638cff` / `#3f5fd6`) are the AA-passing tokens documented in `src/app.css`;
the **strong** accent exists specifically as a darker companion so white text on
an accent background clears WCAG 1.4.3 contrast. Orgs are encouraged (UI hint) to
set a darker strong accent for the same reason, but the app does not enforce a
contrast ratio on custom colors in this slice.

## Branded outbound surfaces (PDFs + emails)

All outbound surfaces resolve brand through **one** helper —
`backend/app/services/branding.py::get_brand_context(org_settings)` — which
returns a frozen `BrandContext` (product name, logo URL, accent color, support /
legal URLs) with platform defaults baked in. It is **pure + total**: tolerates a
`None` settings dict, a missing / non-dict `brand` block, and individually
malformed fields (each falls back to its platform default for text/accent, or to
empty for URLs), and never touches the network. Platform defaults: product name
**"Accounts Payable"**, accent **`#638cff`** (kept in sync with the frontend
`app.css` token).

### PDFs

`remittance_pdf.py`, `tax_1099_forms.py`, and `audit_report_pdf.py` each take a
resolved `BrandContext` on their render context (defaulting to the platform brand
so an old call site still renders) and draw a branded header — the tenant **logo**
when one is configured and embeddable, otherwise the tenant **product name** in
the **accent color**. The remittance footer also appends the tenant's support
URL when set.

**Logo embed is best-effort and bounded** (`fetch_logo_bytes` /
`build_logo_flowable`): the fetch is time-bounded (`LOGO_FETCH_TIMEOUT_SECONDS`,
3s) and size-bounded (`LOGO_MAX_BYTES`, 1 MiB, enforced both via the
`Content-Length` header and a hard cap on the streamed bytes), only `http(s)`
URLs are fetched, and **any** failure (no URL, bad scheme, timeout, oversized,
non-2xx, undecodable image) returns `None` so the renderer falls back to the
product-name text. Logo embedding can never break PDF generation, and a dev box
with no network renders fine. Money stays exact and no PII enters the header /
footer (brand chrome only).

### Emails

The `EmailMessage` dataclass carries an optional `brand: BrandContext`. The
email adapters (`console` / `smtp` / `ses`) apply it uniformly via shared
`EmailAdapter` helpers (`_branded_from` / `_branded_html` / `_branded_text`):

- the **From** display name becomes the tenant product name
  (`Acme Pay <no-reply@platform.com>` — the deliverable address is unchanged;
  an address that already has a display name, or is empty, is left alone; the
  name is sanitized of quotes / CR / LF so it can't break the header);
- the **HTML** body is wrapped with a small brand header line (product name in
  the accent color) and a support-link footer (only when a support URL is set);
- the **plaintext** body gets the same support-link footer.

A message with no `brand` set uses the **platform-default** brand, so every
email still presents consistently. The tenant-aware senders that resolve and
pass the brand: `notification_dispatch.notify_event` (invoice-lifecycle emails),
`vendor_notifications.notify_vendor_of_invoice_event` (supplier paid / rejected),
and `supplier_chat.notify_supplier_of_ap_message` (portal chat link). The
control-plane signup / MFA emails fire before a tenant brand exists, so they use
the platform default. Brand resolution for emails is best-effort — a load
failure degrades to the platform brand, never breaking the send.

## Tests

- Backend: `backend/tests/test_branding.py` — schema validation (hex/URL
  guards), admin-only mutate, persistence to `settings.brand`, PII-free audit
  row, 422 on bad hex/URL, 401 without auth, GET readable by any role; **plus**
  `get_brand_context` resolution + platform-default + malformed-field fallback,
  the remittance / 1099 / audit PDFs rendering the product name (+ logo-fetch
  failure falling back to text), and the email adapters branding the From /
  HTML header / support footer.
- Frontend: `frontend/src/lib/stores/brandTheme.test.ts` — the pure
  color-application / fallback logic (`isValidHexColor`, `brandThemeVars`).

## Custom domains (vanity hostnames)

A tenant can be reached on its own hostname — `ap.acmecorp.com` — instead of (or
in addition to) the `*.localhost` / `<slug>.app.com` subdomain. This is the
white-label "served under the partner's own domain" ask. The TLS certificate and
DNS for the vanity host are **infra** concerns (CloudFront/ALB + ACM + a CNAME to
the platform) and out of scope for the app code; the app's job is purely to map
an inbound request on that host back to the right tenant.

### Storage — no migration

The vanity hostnames live on `Organization.settings.brand.custom_domains`, a JSON
array of bare, lowercase hostnames, e.g.:

```json
{ "brand": { "custom_domains": ["ap.acmecorp.com", "pay.acmecorp.com"] } }
```

Like every other white-label field, this is settings JSONB — **no migration**. A
malformed / non-array `custom_domains` value never breaks resolution (see below).

### Resolution — `app/tenant.py`

`get_tenant_slug` resolves the tenant selector for every request, in order:

1. **Primary** — the `X-Tenant-Slug` header (set by the SPA from the subdomain).
   If present, it is used verbatim and the custom-domain lookup is skipped (no DB
   query).
2. **Fallback** — when the header is absent, the request `Host` header is
   normalized (`normalize_custom_domain`: strip `:port`, lowercase, reject empty /
   IPv6-literal / malformed values) and matched against the per-org
   `settings.brand.custom_domains` array via a JSONB `@>` containment query
   (`resolve_tenant_slug_by_custom_domain`). A match yields the owning org's slug.
3. An unknown/unmatched host (or a malformed settings blob — the lookup catches
   and swallows the error) **falls back to the original 400** "Missing
   X-Tenant-Slug header". It never resolves a wrong tenant and never 500s.

The resolved slug then flows through the **unchanged** `get_tenant` →
`get_tenant_db` chain — the tenant engine is still built only through the existing
`get_tenant_engine(tenant.db_name)` chokepoint, never by a hardcoded DB name.

### Trust model — the JWT cross-check still gates everything

This is the load-bearing point. A custom domain resolves only a **candidate**
tenant slug; it does **not** grant access. `get_tenant` still performs the
cross-tenant guard: for an employee JWT (`typ != "vendor"`), the token's `org`
claim **must equal** the resolved Organization's id, or the request is rejected
with `403`. So:

- A forged / leaked `Host: ap.acmecorp.com` header, on its own, can no more widen
  access than a forged `X-Tenant-Slug: acme` header can — both only pick a
  candidate; the JWT org claim is the actual authority. An attacker holding a
  techflow JWT who points a request at acme's vanity host still gets a 403.
- Vendor-portal tokens (`typ="vendor"`) and unauthenticated requests are exempt
  from the cross-check exactly as before (VendorUser rows are tenant-local;
  unauthenticated requests are rejected by the downstream auth dependency). The
  custom-domain fallback changes *how the slug is derived*, not the guard that
  follows it.

In short: custom-domain resolution is a convenience for picking the tenant when
the SPA can't supply the header; it is **not** a new trust boundary, and the
existing tenant-isolation invariant (project invariant #4) is fully preserved.

### Tests

`backend/tests/test_tenant_custom_domain.py` — `normalize_custom_domain`
edge cases (port strip, case-fold, IPv6/malformed reject); the header taking
priority and skipping the DB lookup; a custom domain resolving to the right slug;
unknown domain / no host falling back to 400; a malformed settings blob not
500-ing; and — the security headline — that the JWT `org`-claim cross-check still
rejects a mismatched token on the custom-domain path (and allows a matching one).

## Deferred to later slices

- Admin UI to manage a tenant's `custom_domains` list (currently set via the
  settings JSON / API directly) and the operational runbook for the
  TLS-cert + DNS provisioning that pairs with it.
- Reseller / partner multi-tenant admin (one partner managing many tenants'
  branding).
