# White-Label / Partner Branding

Per-tenant white-labeling so an organization can present the app under its own
product name, logo, and accent colors. This is the **first slice**: brand config
+ frontend theming via CSS custom properties. Custom domains, branded
PDFs/emails, and reseller multi-tenant admin are later slices (see
`docs/roadmap.md`).

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

## Tests

- Backend: `backend/tests/test_branding.py` — schema validation (hex/URL
  guards), admin-only mutate, persistence to `settings.brand`, PII-free audit
  row, 422 on bad hex/URL, 401 without auth, GET readable by any role.
- Frontend: `frontend/src/lib/stores/brandTheme.test.ts` — the pure
  color-application / fallback logic (`isValidHexColor`, `brandThemeVars`).

## Deferred to later slices

- Custom domains (vanity hostnames per tenant).
- Branded PDFs (remittance, audit report) and outbound emails.
- Reseller / partner multi-tenant admin (one partner managing many tenants'
  branding).
