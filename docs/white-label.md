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

`remittance_pdf.py`, `tax_1099_forms.py`, `audit_report_pdf.py`, and
`analytics_report_pdf.py` each take a resolved `BrandContext` on their render
context (defaulting to the platform brand so an old call site still renders) and
draw a branded header — the tenant **logo** when one is configured and
embeddable, otherwise the tenant **product name** in the **accent color**. The
remittance footer also appends the tenant's support URL when set.

### Analytics report exports (CSV + PDF)

The analytics export surface — `GET /api/analytics/export/{report}` for
`invoice_register` / `vendor_spend` / `payment_register` / `aging_snapshot` /
`cashflow_forecast` — is white-label branded through the same
`get_brand_context` helper. The endpoint takes a `format` query param
(`csv` default | `pdf`); RBAC + tenant-scoping are unchanged
(admin/ap_manager/cfo, entity-scoped).

- **PDF** (`?format=pdf`) — `services/analytics_report_pdf.render_analytics_report_pdf`
  renders a branded, landscape, tabular PDF: a header with the tenant logo (when
  configured + embeddable, via the shared size/time-bounded `build_logo_flowable`,
  fail-soft to the product-name text in the accent color), the report title /
  org / period / generated-at, then the data table. It re-parses the CSV the
  exporter already produced into header + rows, so the PDF renders **exactly** the
  same cells the CSV dialect emits — never broader, no PII beyond what the CSV
  already carries.
- **CSV** (default) — CSV has no visual chrome, so branding is a leading
  **provenance comment block**: a handful of `# `-prefixed lines carrying the
  tenant **product name**, the org name, the report name, and the generated-at
  timestamp, prepended ahead of the unchanged data grid
  (`report_export.brand_provenance_header`). A `#`-comment header is the standard
  CSV-export provenance convention — the data grid (column header row + rows) is
  byte-for-byte the same as before, so it still parses column-positionally: a
  consumer that doesn't recognise comments skips the leading `#` lines
  (`csv.reader` yields them as single-cell rows; pandas takes `comment="#"`). The
  org name (the only tenant-supplied field in the block) is CR/LF-stripped so it
  can't inject a fake row. PII-free: product name + org + report + timestamp only.
  The pure per-report exporters in `report_export.py` are unchanged — the brand
  block is composed in the route, so passing `brand=None` keeps the legacy
  byte-for-byte output.

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
  HTML header / support footer. **Analytics exports**:
  `backend/tests/test_report_export.py` (the `brand_provenance_header` block —
  product name + metadata, `None`-brand no-op, the data grid still parsing
  column-positionally below the comment block, org-name newline-injection
  sanitised), `backend/tests/test_analytics_report_pdf.py` (the pure PDF renderer
  — real PDF bytes, configured + default brand, **logo-fetch failure fail-soft**,
  empty rows), and `backend/tests/test_cashflow_forecast_api.py` (the route —
  `format=pdf` content-type + filename, the branded CSV provenance block,
  `format=xlsx` → 422).
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

### Managing the list — admin endpoint + UI

The `custom_domains` list is managed through a dedicated admin endpoint pair
(distinct from `GET/PUT /api/organization/branding`, because `custom_domains`
lives under `settings.brand` but is **not** a `BrandConfig` field):

`GET /api/organization/branding/custom-domains` — returns
`{ "custom_domains": [...] }`. **Read-gated to any authenticated org user**
(same posture as `GET .../branding`) — the resolver reads it too.

`PUT /api/organization/branding/custom-domains` — **admin-only**
(`require_roles(ROLE_ADMIN)`). Full-replace semantics. Each host is normalized
through the **same** `normalize_custom_domain` the resolver uses (strip `:port`,
lowercase, reject empty / IPv6-literal / malformed) — so a stored value can never
diverge from what actually resolves. Malformed entries are rejected (`422`); the
normalized list is de-duplicated.

**Cross-org uniqueness (anti-hijack).** A host already registered to a
*different* org is rejected (`409`). A custom domain is only a *candidate*
tenant selector — the JWT `org`-claim cross-check (below) is what actually gates
access — but letting two orgs claim the same host would make resolution
ambiguous, so it is refused at registration time, queried via the **same** JSONB
containment the resolver uses (so the check and the resolution can't disagree).
Re-saving a host the tenant already owns is **not** a self-conflict.

Every mutation audits `organization.custom_domains_updated` into the tenant
trail, **PII-free**: it records only the host **count** (old → new), never the
hostnames themselves (tenant infra config kept out of the trail). A branding
save (`PUT .../branding`) **preserves** `custom_domains` — it carries the
existing list forward rather than letting `BrandConfig.model_dump()` wipe it.

**Operator responsibility — DNS + TLS.** Registering a host here only tells the
platform which tenant that host maps to. The vanity host's **DNS** (a CNAME to
the platform edge) and **TLS certificate** (CloudFront/ALB + ACM) are infra,
provisioned out of band — see *Custom domains* intro above.

**Admin UI** — the **Custom Domains** section on `/organization`
(`frontend/src/routes/organization/+page.svelte`, below the Branding panel):
lists the current hostnames, adds one (client-side validated to mirror the
backend `normalize_custom_domain`, surfacing a typo inline instead of as a
`422`/`409`), and removes one (armed two-click confirm). Loading / error / empty
states; the operator's DNS+TLS responsibility is called out in the panel hint.

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

`backend/tests/test_custom_domains_admin.py` — the management endpoint: GET
readable by any authed role + 401 without auth; PUT admin-only (403 for a
manager); normalize + de-dupe + persist to `settings.brand.custom_domains`;
PII-free `organization.custom_domains_updated` audit (count only); round-trip
through GET; 422 on a malformed host; clearing the list; **cross-org uniqueness
409** (tenant B claims a host → tenant A is refused, A's list unchanged);
re-registering a host the tenant already owns is fine; and the regression that a
branding save preserves `custom_domains`.

`frontend/tests-e2e/organization/custom-domains.spec.ts` — the panel renders;
adding a host through the UI round-trips through GET; removing one (armed
confirm) drops it; an invalid hostname surfaces an inline error and fires no PUT.

## Deferred to later slices

- The operational runbook for the TLS-cert + DNS provisioning that pairs with a
  registered custom domain (the app-side `custom_domains` admin UI + endpoint
  pair shipped — see *Managing the list* above; the infra automation that issues
  the ACM cert and wires the CNAME is a separate, infra-owned slice).
- Reseller / partner multi-tenant admin (one partner managing many tenants'
  branding).
