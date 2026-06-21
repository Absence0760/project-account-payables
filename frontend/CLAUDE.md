# Frontend — CLAUDE.md

Frontend-specific guidance. See root `CLAUDE.md` for project-wide context.

## Stack

- **SvelteKit 2** with **Svelte 5** (runes syntax), adapter-static
- **TypeScript** 5.8, **pnpm**
- **Icons**: unplugin-icons with `@iconify-json/material-symbols`
- **Markdown**: mdsvex
- **Styling**: normalize.css + custom CSS in `src/app.css`
- **Sanitization**: isomorphic-dompurify

## Commands (from `frontend/`)

```bash
pnpm dev              # dev server on :7777
pnpm build            # production build (adapter-static)
pnpm preview          # preview build on :8888
pnpm check            # typecheck
pnpm test:unit        # vitest unit tests (i18n parity + pure helpers)
```

## Routes → API mappings

| Route | File | API calls |
|-------|------|-----------|
| `/` (tenant) | `routes/+page.svelte` | `GET /api/dashboard` |
| `/` (no-tenant) | `lib/components/marketing/Landing.svelte` (inline in `+layout.svelte`) | Marketing landing page with features, pricing, signup CTA |
| `/signup` | `routes/signup/+page.svelte` | `GET /api/public-config`, `GET /api/signup/slug-check`, `POST /api/signup/start` |
| `/verify` | `routes/verify/+page.svelte` | `POST /api/signup/complete` |
| `/login` | `routes/login/+page.svelte` | `POST /api/auth/login`, `GET /api/auth/sso/config` (renders SSO button when enabled) |
| `/login/mfa` | `routes/login/mfa/+page.svelte` | `POST /api/auth/mfa/challenge/email`, `POST /api/auth/mfa/verify` (totp/email), `POST /api/auth/mfa/passkey/authenticate[/verify]` (passkey factor, via `auth.completePasskey` + `$lib/webauthn.ts`) — second-factor step after password; offers passkey / totp / email per the challenge's `methods` |
| `/login/sso-callback` | `routes/login/sso-callback/+page.svelte` | `POST /api/auth/sso/callback` — exchanges OIDC code+state for our JWT after IdP redirect |
| `/profile` | `routes/profile/+page.svelte` | `POST /api/auth/mfa/enroll`, `POST /api/auth/mfa/enroll/verify`, `POST /api/auth/mfa/disable` — manage two-factor (TOTP); `GET /api/auth/mfa/passkey`, `POST /api/auth/mfa/passkey/register[/verify]`, `DELETE /api/auth/mfa/passkey/{id}` — manage passkeys (via `auth.{listPasskeys,registerPasskey,deletePasskey}` + `$lib/webauthn.ts`) |
| `/change-password` | `routes/change-password/+page.svelte` | `POST /api/auth/change-password` |
| `/invoices` | `routes/invoices/+page.svelte` | `GET /api/invoices` (returns `priors_summary`), `GET /api/invoices/counts` (status-chip tallies), `GET /api/invoices/{id}` (`?id=` deep-link opens the detail modal), `POST /api/invoices/upload` (supports multi-file; frontend batches 5 at a time via `Promise.allSettled`), `PATCH /api/invoices/{id}`, `GET /api/invoices/{id}/priors`, `GET /api/invoices/{id}/summary` (audit-log summary; `POST .../summary/regenerate` for admins/managers), supplier chat — `GET/POST /api/invoices/{id}/chat`, `POST .../chat/attachments`, `POST .../chat/{resolve,reopen}`, `GET /api/invoices/chat/templates`, `GET /api/invoices/{id}/chat/file/{key}` (via `$lib/api/supplierChat.ts`, surfaced in `InvoiceModal`), bulk ops |
| `/vendors` | `routes/vendors/+page.svelte` | `GET /api/vendors`; sanctions screening + risk (via `$lib/api/vendors.ts`) — `POST /api/vendors/{id}/screen`, `GET /api/vendors/{id}/screening-history`, `POST /api/vendors/{id}/{block,unblock}`, `GET /api/vendors/{id}/risk` + `POST .../risk/recompute`, `GET /api/vendors/risk/summary`, `GET /api/vendors/screening/review-queue`. Row screening/risk pill (`ui/ScreeningBadge.svelte`); clickable rows open `modals/VendorModal.svelte` (Screening & Risk panel + history timeline; re-screen / recompute / block-unblock gated to admin + ap_manager via `auth.isManager`). **Vendor consolidation** — a `vendor.manage`-gated (`auth.can`) **Merge duplicates** header action opens `modals/VendorConsolidationModal.svelte` over `$lib/api/vendors.ts` (`getVendorConsolidationSuggestions` / `mergeVendorConsolidation` → `GET /api/enrichment/vendors/consolidation-suggestions`, `POST /api/enrichment/vendors/consolidation/merge`): per-cluster canonical-vs-duplicate diff, two-step-confirm merge (soft-retire-irreversible), surfaces the backend's 4xx refusals, refreshes the list |
| `/payments` | `routes/payments/+page.svelte` | `GET /api/payments/{queue,summary,runs/}`, `GET /api/payments`, `POST /api/payments/runs` (creates draft), `GET /api/payments/runs/{id}` + `POST .../execute` (via `RunDetailModal`) |
| `/discounts` | `routes/discounts/+page.svelte` | dynamic discounting (via `$lib/api/discounts.ts`) — `GET /api/discounts/dashboard`, `GET /api/discounts/offers`, `POST /api/discounts/offers/{id}/{accept,decline}`, `GET /api/discounts/invoices/{id}/roi`, `POST /api/discounts/optimize`. KPI row, status `FilterChips`, offers `DataTable` (tiers via `ui/DiscountTierBar.svelte`, accept-tier `Modal`), early-payment optimizer panel. admin/ap_manager/cfo |
| `/recurring` | `routes/recurring/+page.svelte` | Recurring / subscription invoice templates (via `$lib/api/recurring.ts`) — `GET /api/recurring` (list; `status`/`vendor_id`/`search`/`page` params), `POST /api/recurring`, `GET/PATCH/DELETE /api/recurring/{id}`, `POST /api/recurring/{id}/{pause,resume,end,generate-now}`, `GET /api/recurring/{id}/upcoming-schedule?count=`, `GET /api/recurring/{id}/history`. Under the **Billing** nav group. KPI row, status `FilterChips` (active / paused / ended), template `DataTable` with clickable rows, create/edit `Modal`; the detail modal shows the upcoming-schedule preview + generated-invoice history. Read all four roles; mutate gated to admin/ap_manager (`auth.isManager`) |
| `/billing` | `routes/billing/+page.svelte` | Platform billing & metering (read/display) — `GET /api/billing/subscription` + `GET /api/billing/invoices` (via `$lib/api/billing.ts` → `getBillingSubscription` / `getBillingInvoices`, types in `$lib/types/billing.ts`). The AP platform's OWN customer subscription (control-plane), distinct from the customer AP money path. Surfaced as the **Subscription** sub-tab of the **Billing** nav group, admin/cfo-gated (clerk/manager redirected to `/`). Shows the current plan + price (`<Money>`), a `SubscriptionBadge` status pill (trialing/active/past_due/canceled), the period/trial window, granted entitlements, and usage-to-date `KpiCard`s; plus an **Invoices & receipts** section (`DataTable`: number, period, `<Money>` amount + row currency, paid/open/void status pill, created date, and a new-tab "View" link when the provider supplies a `hosted_url`) — loaded independently so a slow/failed invoices fetch doesn't block the plan surface, with its own loading / error / empty ("No invoices yet.") states. Also a **Payment methods** section (`DataTable` of saved cards — PII-safe `Brand ····last4` / `Expires MM/YYYY` / `Default` pill, **never a PAN** — over `GET /api/billing/payment-methods` via `getBillingPaymentMethods`) + an **Add / replace card** flow over `POST /api/billing/payment-method/setup-intent` (`startBillingSetupIntent`): `configured=false` → a "billing not configured" state, a returned `client_secret` → a "ready" state with a clearly-marked **deployed-only Stripe Elements seam** (no Stripe keys in the static frontend; never calls a secret-bearing service directly), re-listing cards after; its own loading / error / empty ("No payment method on file.") states. Live-Stripe **plan-change** stays a disabled "contact us" affordance (later frontend slice). See `backend/docs/billing.md` § Customer-facing UI |
| `/vendor-statements` | `routes/vendor-statements/+page.svelte` | Vendor statement reconciliation (via `$lib/api/vendorStatementRecon.ts`) — `GET /api/vendor-statements` (list; `vendor_id`/`status`/`page` params), `POST /api/vendor-statements` (manual lines) + `POST /api/vendor-statements/upload` (CSV), `GET /api/vendor-statements/{id}` (detail+lines), `POST /api/vendor-statements/{id}/lines/{lineId}/resolve`, `DELETE /api/vendor-statements/{id}`, `GET /api/vendor-statements/close-readiness`. Under the **Billing** nav group. KPI row, status `FilterChips` (all / open / resolved), runs `DataTable` with clickable rows; the detail modal (`modals/VendorStatementReconModal.svelte`) shows the side-by-side statement-vs-ledger diff with per-line resolve/ignore. Create modal supports manual statement lines or CSV upload. Read all four roles; mutate gated to admin/ap_manager (`auth.isManager`) |
| `/exceptions` | `routes/exceptions/+page.svelte` | `GET /api/exceptions`, `PATCH /api/exceptions/{id}` |
| `/workflows` | `routes/workflows/+page.svelte` | `GET /api/workflows`, `POST /api/workflows`; no-code builder management — `GET /api/workflows/templates`, `POST /api/workflows/from-template`, `GET/POST /api/workflows/{id}/versions`, `POST /api/workflows/{id}/restore/{versionId}`, `GET /api/workflows/{id}/versions/diff`, `POST /api/workflows/{id}/simulate`, `GET /api/workflows/{id}/export`, `POST /api/workflows/import` (via the `workflow-mgmt` modals) |
| `/workflows/[id]` | `routes/workflows/[id]/+page.svelte` | `GET/PATCH /api/workflows/{id}`, `GET /api/organization` — drag-and-drop builder canvas (`workflow-builder` components) |
| `/experiments` | `routes/experiments/+page.svelte` | A/B testing of workflow rules (via `$lib/api/experiments.ts`, types in `$lib/types/experiments.ts`) — `GET /api/experiments` (list), `POST /api/experiments`, `POST /api/experiments/{id}/{start,stop,conclude}`, `DELETE /api/experiments/{id}`, `GET /api/experiments/{id}/results`; loads workflow definitions via `GET /api/workflows`. Under the **Settings** nav group. Status `FilterChips` (all / draft / running / concluded), experiments `DataTable` with clickable rows opening a **results readout** `Modal` (winner / not-enough-data banner + per-variant metric table, primary-metric row highlighted), a create `Modal` (pick a definition — seeds both configs from its live steps — split %, primary metric, min sample, two JSON config editors), per-row start/stop/conclude/delete gated by status. Read managers/CFO; mutate gated to admin (`auth.isAdmin`; the backend 403s the rest). |
| `/audit` | `routes/audit/+page.svelte` | `GET /api/audit/export` (JSON + CSV) — SOX auditor console, admin/CFO only (content-gated on `auth.isCfo`; backend 403s otherwise). Date-range or by-invoice query + CSV download. |
| `/organization` | `routes/organization/+page.svelte` | `GET/PATCH /api/organization`, `GET/PUT /api/organization/branding`, `GET/PUT /api/organization/branding/custom-domains` (Custom Domains panel — list / add / armed-remove vanity hostnames) |
| `/admin` | `routes/admin/+page.svelte` | **Users & Roles** page (`?tab=users` default \| `?tab=roles`). Users tab → `GET/POST/PATCH/DELETE /api/admin/users`; Roles tab → `GET/POST/PATCH/DELETE /api/admin/roles`. Bodies live in `$lib/components/admin/{UsersPanel,RolesPanel}.svelte`; the page derives the active panel from `?tab=` (`$page.url`) and owns the per-tab PageHeader action (calls the active panel's exported `openCreate()`). **Users + Roles are peer tabs in the sidebar's Settings section bar** (`layout/SectionTabs.svelte`), not a tab row inside the page — clicking them navigates to `/admin?tab=…`. `/admin/roles` redirects to `/admin?tab=roles` (back-compat). |
| `/admin/api-keys` | `routes/admin/api-keys/+page.svelte` | Developer-API key management (admin only — redirects non-admins, the backend 403s them) via `$lib/api/apiKeys.ts` (types in `$lib/types/apiKeys.ts`). `GET /api/api-keys` (list — prefix + scopes + created/last-used + Active/Revoked status), `POST /api/api-keys` (Create-key modal → a **one-time** copy-able plaintext reveal: "shown only once", never re-fetchable, dropped from memory on close), `DELETE /api/api-keys/{id}` (armed two-click Revoke, idempotent), `GET /api/api-keys/{id}/usage?window_days=` (per-key usage view modal — all-time + trailing-window totals + per-day breakdown, opened by clicking the key-name `RowLink`). Surfaced under the **Settings** nav group. Loading / empty / error states; never echoes the plaintext after the reveal closes. See `backend/docs/public-api.md` § API keys |
| `/admin/partner` | `routes/admin/partner/+page.svelte` | **Partner / reseller multi-tenant admin** (admin only — redirects non-admins, the backend 403s them) via `$lib/api/partner.ts` (types in `$lib/types/partner.ts`). `GET /api/partner` (the partner's child tenants; a standalone org shows the "not a partner" empty state), `GET /api/partner/children/{id}/branding` + `PUT .../branding` (view/push a child's white-label brand — product name, logo, two accent colors, support/legal URLs — in a `Modal` with the same hex/URL validation as the org Branding panel; clickable `DataTable` rows). Surfaced under the **Settings** nav group. Loading / empty / error states. See `docs/white-label.md` § Partner / reseller admin |
| `/cfo` | `routes/cfo/+page.svelte` | `GET /api/analytics/{cashflow_forecast,cashflow_whatif,cash_position}`, `GET /api/analytics/export/cashflow_forecast` (admin + cfo) — predictive cash-flow dashboard |
| `/tax` | `routes/tax/+page.svelte` | `GET /api/tax/1099-report?year=` (via `lib/api/tax.ts`) — 1099 vendor reporting dashboard. KPI summary, year selector, per-vendor 1099-eligible / W-9-on-file / TIN-verified chips, >$600 threshold flags, vendor search + filter chips (all / reportable / missing W-9 / over threshold). admin/ap_manager/cfo. The report outer-joins vendors→payments, so the row set is the tenant's vendor list and the year only re-aggregates each vendor's YTD; the table is empty only when there are no vendors. |
| `/notifications` | `routes/notifications/+page.svelte` | `GET /api/notifications` (list, `?unread_only=`), `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all` — clickable rows open the related invoice (`/invoices?id=`). Reached via the sidebar-header **bell** (`layout/NotificationBell.svelte`, recent-popover + "View all"), not a nav row. |
| `/assistant` | `routes/assistant/+page.svelte` | Conversational AP Assistant. Streams a turn via `streamAssistantChat()` over `POST /api/assistant/chat/stream` (SSE) and falls back to `POST /api/assistant/chat` when the stream endpoint is unavailable / fails before any content. `GET /api/assistant/usage` (usage meter, refreshed after each turn), `GET /api/assistant/conversations` (recent-chats rail), `GET /api/assistant/conversations/{id}` (open a thread). Empty state offers the three built-in example prompts; tool results render as charts/tables. Open to all four employee roles. |
| `/profile` (notifications prefs) | `routes/profile/+page.svelte` | `GET/PATCH /api/notifications/preferences` — per-event in-app/email toggles |

Root layout (`+layout.svelte`) routing logic:
- No tenant subdomain → Landing component (public) or `<slot />` for `/signup` / `/verify`
- Tenant present, not logged in → redirect to `/login` (or `/login/mfa` if a challenge is pending in `sessionStorage`)
- Tenant present, logged in, `must_change_password=true` → redirect to `/change-password`
- Tenant present, logged in, flag clear → app shell with sidebar

MFA flow:
- `/login` calls `auth.login()`. If it returns `{kind:'mfa', challenge}`, the page stashes the challenge in `sessionStorage` and navigates to `/login/mfa`.
- `/login/mfa` reads the challenge, lets the user pick TOTP or email, calls `auth.completeMfa(...)` or `auth.requestEmailMfa(...)`. On success, removes the challenge and navigates home — or to `/profile` if `must_enroll=true`.
- `/profile` renders enrollment (QR + verify) and disable forms backed by `/api/auth/mfa/{enroll,enroll/verify,disable}`.

## Key modules

### API client — `src/lib/api.ts`

All data fetching goes through this module. Never call `fetch()` directly for API requests.

- Auto-adds `Authorization: Bearer <token>` from localStorage
- Auto-adds `X-Tenant-Slug` header from subdomain
- 401 responses clear token and redirect to `/login`
- Methods: `api.get<T>()`, `api.post<T>()`, `api.patch<T>()`, `api.put<T>()`, `api.delete()`, `api.upload<T>()`
- Token helpers: `setToken()`, `clearToken()`, `hasToken()`
- **Streaming** — `streamAssistantChat(body, { onTool, onDelta, onDone, onError }, signal?)` streams the AP assistant turn from `POST /api/assistant/chat/stream` (`text/event-stream`). It uses `fetch` + `response.body.getReader()` (NOT `EventSource`, which can't set the Authorization / tenant / entity headers) and a small SSE parser (split on `\n\n`, read `event:`/`data:` lines), reusing the shared `authHeaders()` helper so the header logic can't drift from `request()`. SSE frames: `tool` (one per tool invocation; `result` carries the structured output for charts), `delta` (incremental answer text), `done` (authoritative payload), `error` (mid-stream failure). A pre-stream HTTP 429 throws `AssistantBudgetError` (carries `used`/`budget`/`period`); any other non-OK / network failure throws a plain `Error` — the `/assistant` page catches both and falls back to the non-streaming `POST /api/assistant/chat`.
- Typed feature helpers wrap `api` per domain — e.g. `src/lib/api/audit.ts` (`getInvoiceAuditLog`, `getAuditExport`, `downloadAuditExportCsv`) over the SOX audit endpoints, with `AuditEntry` / `AuditFieldChange` types in `src/lib/types/audit.ts`. The invoice-modal Activity timeline renders `details.changes` (per-field before/after) from these. `src/lib/api/tax.ts` (`get1099Report`) wraps the 1099 endpoint, with `Report1099` / `Vendor1099Row` types in `src/lib/types/tax.ts`.

### Money formatting — `src/lib/utils/money.ts` + `ui/Money.svelte`

**Never hand-roll `Intl.NumberFormat` for currency.** `formatMoney(amount, opts?, placeholder?)` is the single locale-aware formatter (currency-code driven via `Intl.NumberFormat`, browser locale), and `<Money amount currency? whole? accounting? mono? />` is the component over it. Each amount renders with its **own** ISO 4217 code — never a hardcoded `$`.

- Per-row amounts pass their own `currency` (invoices, credit memos, POs, cards, payments).
- Tenant-wide roll-ups (dashboard KPIs, payment-summary totals, aging, CFO forecast) have no per-row currency, so they use the **org default** from the `orgCurrency` store (`src/lib/stores/orgSettings.svelte.ts`). It lazy-loads `Organization.settings.invoice_defaults.currency` from `GET /api/organization` once per session and falls back to USD for non-admin roles (403) or any error. Call `orgCurrency.ensureLoaded()` from the page's init `$effect` and read `orgCurrency.currency`.
- `formatMoney` accepts `number | string-Decimal | null` and returns the placeholder (`—` by default) for null/empty/non-finite; a bad currency code falls back to USD rather than throwing.
- **i18n-aware locale** — when a caller passes no explicit `locale`, `formatMoney` defaults to the active in-app locale (the i18n picker), read from `$lib/i18n/formatLocale.ts::getActiveFormatLocale()`. Until a locale is actively selected the holder is `undefined` (browser locale), so nothing changed pre-i18n. Selecting German in the picker makes `$1,234.50` render as `1.234,50 $`. **`utils/time.ts`'s `formatDate` / `formatPeriod` date helpers read the same holder**, so dates switch locale alongside money (German renders `Jun 20, 2026` as `20. Juni 2026`). Both parse bare `YYYY-MM-DD` / `YYYY-MM` keys at *local* midnight (not UTC) so a negative-offset timezone can't roll the displayed day back. `formatDate` takes an optional `Intl.DateTimeFormatOptions` third arg so a caller can vary the parts (no-year for a due-date cell, date+time for a queue row — it auto-switches to `toLocaleString` when `hour`/`minute`/`second` is asked for) while still localizing off the active locale; omit it for the standard short date. The per-row date cells on the **dashboard** (due dates), **payments** (created/executed/expiry), and the **exceptions** queue have been migrated onto this shared helper — they previously hardcoded `en-US` so the picker didn't move them. Remaining inline `toLocaleDateString` call sites elsewhere (procurement / portal / admin lists) are not yet migrated (a later slice).

### Internationalization (i18n) — `src/lib/i18n/`

Client-side multi-language UI runtime. The frontend is adapter-static (GitHub
Pages, no SSR), so locale is negotiated **client-side** — there is no
`Accept-Language` SSR hook. English is statically bundled (the fallback dict +
prerender default); every other locale is a lazy `import()` chunk, so a
single-locale visitor downloads only their strings. **Shipped: the full
`en, de, fr, es, pt-BR, ja` starter set** (`en` static, the other five lazy
`import()` chunks), structured so a further locale (e.g. an RTL `ar`/`he`)
drops in by extending the four `locale.ts` tables + a catalogue + a loader.

**Public API (`store.svelte.ts`):**
- `m(key, params?)` — reactive message lookup. `key` is a typed `MessageKey`
  (a key of the English dict). Reading it in a template / `$derived`
  re-renders when the locale changes. Falls back English-string → raw-key, so
  an untranslated key degrades gracefully. `params` fills `{placeholder}`
  tokens and drives ICU inline plurals (`{n, plural, one {…} other {…}}`,
  resolved via `Intl.PluralRules` for the active locale).
- `setLocale(locale)` — switch locale: lazy-loads the chunk (English is
  synchronous), persists to `localStorage` (key `ap_locale`, device-scoped),
  sets `<html lang/dir>`, and updates the active `Intl` format locale. Keeps
  the current dict on a failed chunk fetch (never blanks the UI).
- `initLocale()` — detect + apply on first client mount (stored choice →
  `navigator.languages` → English). Called once from `routes/+layout.svelte`.
- `currentLocale()` — the active `Locale` (reactive).

**Files:**
- `locale.ts` — **pure** negotiation: `SUPPORTED_LOCALES`, `negotiateLocale(stored, navigatorLanguages)`, `dirForLocale` (RTL switch-point present for a future `ar`/`he`), `LOCALE_LABELS` (endonyms), `isSupportedLocale`, `parseAcceptLanguage`.
- `interpolate.ts` — **pure** `{placeholder}` substitution + ICU inline plurals.
- `messages.ts` — `type Messages = typeof en` + `MessageKey`.
- `catalogues.ts` — typed lazy-loader registry (`Record<Locale, () => Promise<Messages>>`): `en` static, others dynamic `import()`.
- `locales/en.ts` (source of truth) + `locales/{de,fr,es,pt-BR,ja}.ts` (each `satisfies Messages` — missing/extra key = compile error). Japanese has no grammatical plural, so its ICU plural blocks carry only an `other` arm (`Intl.PluralRules('ja')` never selects `one`).
- `formatLocale.ts` — tiny framework-free holder the `Intl` formatters read (so `money.ts` needn't import the Svelte runtime).
- `store.svelte.ts` — the rune runtime (the API above). **Runes only.**

**Adding a locale:** add it to `SUPPORTED_LOCALES` (+ `EXACT`/`BASE_TO_LOCALE`/`LOCALE_LABELS`) in `locale.ts`, add a `locales/<loc>.ts` that copies `en`'s keys and translates the values with `satisfies Messages`, and add its `CATALOGUE_LOADERS` entry. The compiler + the parity test then enforce completeness automatically.

**Extracting a string:** add a flat, namespaced key (`nav.invoices`, `common.save`) to `locales/en.ts`, translate it in every other locale, and replace the hardcoded literal with `m('…')`. **Extracted so far:** the shell/nav (`$lib/nav.ts` carries a `labelKey` per entry; `Sidebar.svelte`, `SectionTabs.svelte`, the `+layout` skip-link, the profile locale picker), the **dashboard** (`routes/+page.svelte` — KPI labels, chart headings, aging buckets, empty states), the **invoices list** (`routes/invoices/+page.svelte` — title, upload/recode actions, search, bulk-bar, table headers, row actions, the `{n, plural, …}` selected-count + showing-all + load-more strings), the **payments** page (`routes/payments/+page.svelte` — summary cards, the queue/history/cards/runs tab bar, queue pay-bar + review panel + savings banner, all four table headers, row actions, the void + card-details modals, and the pluralized create-draft-run / showing-all strings; per-row payment-status / method badge labels stay data-driven English), the **vendors** list (`routes/vendors/+page.svelte` — title, sync action, search, status filter chips, table headers, verify/reject/bank row actions, bank-details modal; the vendor status/source value maps stay English), the **exceptions** queue (`routes/exceptions/+page.svelte` — title, queue/AI-agents tabs, status filter chips, "All types", table headers, resolve/invoice row actions, bulk-resolve bar, the single + bulk resolve modals with their pluralized titles; the data-driven type-label / severity / status cell badges stay English), the **notifications** page (`routes/notifications/+page.svelte` — title, mark-all-read action + its pluralized toast, all/unread filter chips, table headers, the open-row aria label, the three empty states + load-error, the pluralized load-more / showing-all strings; the `EVENT_LABELS` event-type cell stays data-driven English), the **contracts** list (`routes/contracts/+page.svelte` — title, new-contract action, search, table headers, the open-row aria label, over-spend-limit tooltip, not-found toast, empty state, the pluralized load-more / showing-all strings; the `STATUS_LABELS` / `CONTRACT_TYPE_LABELS` value maps stay English), and the **recurring** templates page (`routes/recurring/+page.svelte` — title, new-template action, search, KPI labels, table headers, day-of-period, the row open/generate-now/pause/resume/end aria labels + button labels + confirm, every lifecycle toast (generate/pause/resume/end + load-failed), the `relativeRun` "today / in N days / N days ago" via ICU plurals, the pluralized load-more / showing-all strings; the `STATUS_LABELS` / `CADENCE_LABELS` value maps stay English), and the **organization settings** page (`routes/organization/+page.svelte` — every section heading + card hint (Company Profile, Invoice Defaults, Branding, Custom Domains, AI Extraction, ERP Integration, Payments, Virtual Cards, Security, Fraud Detection, Data Sync, Plan), all field labels + placeholders + aria-labels, every save/test button (incl. the `Saving…`/`Testing…`/`Test Connection` shared `org.common.*` keys), all `toast` messages (load/save/test failures, section-saved via the `{section}` placeholder, branding hex/URL validation, every custom-domain toast), the fraud-rule hints with their `${min}`/`${amount}`/`{days}` interpolations, the plan badge labels via `planLabel()`, and the `org.plan.created` date string. Data values stay English by the established convention: currency codes, `Net 30`-style payment-terms, ERP/card provider product names (`Microsoft Dynamics 365`, `Lithic`, `Nium`, `Modern Treasury`), and Ollama model ids. Two whole-sentence hints that originally embedded inline `<code>`/`<strong>`/`<em>` styling (the payments-processor and CFO-gate blurbs, the custom-domains example/slug) were flattened to plain i18n strings; the Merge.dev-dashboard link is preserved by splitting its hint into `org.erp.mergeHintPre`/`mergeDashboard`/`mergeHintPost`), and the **Cash Flow / CFO dashboard** (`routes/cfo/+page.svelte` — title + Export-CSV action, the granularity (`day`/`week`/`month` via `granLabel()`) + horizon (`{days}d`) segmented controls, opening-balance / min-balance-alert labels + placeholders + aria-labels, all four KPI labels, the forecast-chart heading (`{granularity}` interpolated), the per-bar `aria-label` + committed/pending bar `title`s, the chart legend, the what-if scenario titles + `net outflow` + `+{amount} discount captured` + `~{days} days to pay`, the cash-position card heading + enter-opening hint + the pluralized `{n}`-period below-minimum-balance breach banner + all four table headers + both empty states + the load/export error fallbacks; `formatPeriod` period labels are now locale-driven via the shared `utils/time.ts` helper, and the embedded **`ByEntityBreakdown`** component is extracted too — `byEntity.*` keys cover the heading, all six column headers, the `default` tag, the Consolidated total row, the loading state, and the load-error fallback; per-entity money stays the currency-driven `<Money>`), and the **Expense Management** page (`routes/expenses/+page.svelte` — title + all five action buttons + the five-tab bar (Expenses / Reports / Policies / Pre-approvals / Cards), every tab's KPI labels, search box, `$derived` chip/COLUMN arrays (the `'All'` chip reuses `common.all`; status-value maps `EXPENSE_STATUS_LABELS` / `EXPENSE_REPORT_STATUS_LABELS` / `EXPENSE_PREAPPROVAL_STATUS_LABELS` / `RECONCILIATION_STATUS_LABELS` stay English by convention), every DataTable header + empty/loading state, all row aria-labels + Delete/Confirm/Detach/Match/Unmatch/Ignore/Create-expense/Approve/Reject row actions, the report-detail submit/approve/reject controls + inline violation panel + reject row + attach row, the bulk-GL bar, all three modals' `ariaLabel`/`title`/fields/buttons (New Report, New Pre-approval, Match-to-expense — no e2e spec keys these), and **every** toast — including the composed pluralized `{n}` import/sync toasts (`Imported # transaction(s) (# duplicate(s) skipped)`, `Synced # virtual-card transaction(s) (# already imported)`) and the GL-coded / showing-all plurals; per-row `formatDate` is now locale-driven via the shared `utils/time.ts` helper), plus the **expense feature dialogs** — **`ExpenseModal`** (`expenseModal.*`: new/edit/view title + aria, every field label + the category placeholder + GL `Select…`, the receipt section (title / view / pending / empty / uploading / replace / attach), close/save/create buttons, and every toast — receipt-uploaded/upload-failed, created/saved, create/save-failed, receipt-load-failed; the `EXPENSE_STATUS_LABELS` / payment-method value maps stay English) and **`PolicyModal`** (`policyModal.*`: new/edit title + aria, all eight field labels + the category placeholder, close/save/create buttons, and the created/saved/save-failed toasts), and the **procurement + positive-pay routes** — the **requisitions** (`requisitions.*`), **intake** (`intake.*`), **catalogs** (`catalogs.*`, incl. the guided-buying panel), **budgets** (`budgets.*`), **purchase-orders** (`purchaseOrders.*`, incl. the detail modal + linked-invoices table), **goods-receipts** (`goodsReceipts.*`, incl. the detail modal), and **positive-pay** (`positivePay.*`) list pages: titles, header actions (incl. ERP-sync), search boxes, KPI labels, status `FilterChips`, every DataTable header + empty/loading state, the row open/view/delete aria-labels + Delete/Confirm + lifecycle row actions (submit/approve/reject/convert/cancel), every toast (incl. the `{poNumber}`/`{number}`/`{name}`/`{label}` interpolations and the pluralized `loadMore`/`showingAll` strings), and the PO/GR detail-modal field labels. The procurement **create/edit modals** (`RequisitionModal` etc.) stay English for a later slice; data-driven status/type value maps stay English by the established convention, the **supplier portal** (every `routes/portal/**` page, `portal.*` keys — the shell/nav + skip-link + log-out + no-tenant notice (`+layout.svelte`), login + the email/TOTP MFA step (`login/`), change-password, the invoices list + submit, the payments list + remittance download, the purchase-orders list + PO-flip, the company page (contact / bank-detail-change request / tax-ID-change request / W-9·W-8 tax-form upload + download / TOTP enroll-disable), the early-payment discount-offers list + accept dialog, the notification-preferences toggles, and the single-use virtual-card reveal (`cards/[token]`); placeholders like `{product}`/`{po}`/`{number}`/`{last4}`/`{date}`/`{amount}`/`{days}`/`{percent}`/`{changeType}` are preserved verbatim in every locale). The rest of the app stays English until later extraction slices — an un-extracted literal simply stays English, the designed incremental path. Reading `m()` inside a `$derived` (e.g. the dashboard's `agingBuckets`) keeps the labels reactive to a locale switch.

**Locale picker:** `routes/profile/+page.svelte` — a `<select>` of endonyms (`LOCALE_LABELS`) bound to `currentLocale()`, persisting via `setLocale`.

**Tests (vitest):** `pnpm test:unit` (or `pnpm exec vitest run`). `messages_parity.test.ts` iterates `SUPPORTED_LOCALES` through the loader registry and asserts every locale is loadable, key-complete vs `en`, non-empty, and placeholder-faithful. `interpolate.test.ts` + `locale.test.ts` cover the pure helpers. Config: `vitest.config.ts` (node env, separate from `vite.config.ts` — the tested modules are pure, no `$app/*` / Svelte compiler). Vitest is the unit-test framework for the frontend (don't add another).

### Tenant — `src/lib/tenant.ts`

`getTenantSlug()` extracts subdomain: `acme.localhost:7777` → `"acme"`, plain `localhost` → `null`.

### Stores (`src/lib/stores/`) — Svelte 5 rune stores

| Store | File | State | Key methods |
|-------|------|-------|-------------|
| `auth` | `auth.svelte.ts` | `user` (incl. `mfa_enabled`, `mfa_required_by_org`), `loggedIn`, role checks (`isAdmin`, `isManager`, `isCfo`, `isClerkOnly`) | `login()` (returns `{kind:'ok'} \| {kind:'mfa', challenge}` — MFA branch routes to `/login/mfa`), `completeMfa(token, code, method)`, `requestEmailMfa(token)`, `logout()`, `fetchUser()`, `hasRole()`, `hasAnyRole()` |
| `invoiceStore` | `invoices.svelte.ts` | `all`, `loading`, `total`, `statusCounts` | `fetch(params)`, `fetchCounts()`, `update(id, changes)` |
| `paymentStore` | `payments.svelte.ts` | `all`, `loading`, `total`, `hasMore` | `fetch(params)`, `loadMore()` (history-tab Load-More; remembers filter params) |
| `workflowStore` | `workflows.svelte.ts` | `all`, `loading`, `total`, `hasMore`, `activeSteps` | `fetch()`, `loadMore()`, `fetchActiveSteps()`, `getById()`, `create()`, `update()` |
| `adminStore` | `admin.svelte.ts` | `users`, `roles`, `loading` | `fetchUsers()`, `fetchRoles()`, `createUser()`, `updateUser()`, `deleteUser()` |
| `sidebar` | `sidebar.svelte.ts` | `collapsed` | `toggle()` |
| `orgCurrency` | `orgSettings.svelte.ts` | `currency` | `ensureLoaded()`, `reset()` — tenant default display currency for aggregate (non-per-row) money; lazy-loads from `/api/organization`, USD fallback |
| `notificationStore` | `notifications.svelte.ts` | `items`, `unread`, `total`, `loading`, `hasMore`, `prefs` | `fetchList({unreadOnly})`, `loadMore()`, `fetchUnreadCount()`, `markRead(id)`, `markAllRead()`, `fetchPrefs()`, `updatePrefs()`, `startPolling()`/`stopPolling()` (60s unread-count poll for the sidebar-header bell badge; started from `+layout` when signed in) |

### Components (`src/lib/components/`)

Grouped into subfolders by role. Import with the full path, e.g.
`import Modal from '$lib/components/ui/Modal.svelte'`. No barrel/index file.

**`ui/` — reusable primitives** (use these; don't hand-roll the markup):
- `PageHeader.svelte` — `.workspace` + `.toolbar` shell. `<PageHeader title="X">` with an optional `{#snippet actions()}` (right-aligned toolbar buttons); page body is `children`. Renders the `<h1>` title.
- `DataTable.svelte` — `.grid-container > table`. Pass `columns={[{label,class?}]}` (or a `{#snippet header()}<tr>…</tr>{/snippet}` for select-all/sortable headers) + a `{#snippet body()}` that renders the `<tr>`/`<td>` rows. `isEmpty` + `empty` render the centred empty row (`colspan` auto from columns). Opt-in `fixed` (table-layout:fixed) and `stickyHeader` props.
- `FilterChips.svelte` — `nav.filters` of `.filter-chip`. `<FilterChips chips={[{key,label,count?,alert?}]} bind:active={var} />`. Single-select; for multi-select status filters keep an inline chip nav (it still uses the global `.filter-chip` CSS).
- `Modal.svelte` — `.backdrop` + `div.modal[role="dialog"]`. `<Modal open ariaLabel="EXACT" title? width="sm|md|lg" onclose>`; keep the page's own `<form>` + `.modal-footer` inside `children` (preserves submit). Custom heading → `{#snippet header()}`. Handles backdrop-click + Esc.
- `KpiCard.svelte` — `.kpi` card. `<KpiCard value label highlight={'green'|'red'|null} />`; wrap a row in `<div class="kpi-row">`.
- `SearchBox`, `StatusBadge`, `RowAction`, `BulkBar`, `BulkDeleteButton`, `Toast` — see the pattern sections below.
- `Tabs.svelte` — underline tab bar for **in-page** panel switching. `<Tabs tabs={[{key,label,count?}]} bind:active ariaLabel? onchange? />`. Owns the `.tab-row` / `.tab` markup + `role="tablist"`/`role="tab"` a11y. The per-route tab copies in `/expenses`, `/payments`, `/audit` predate it and can migrate onto it opportunistically. (Distinct from `layout/SectionTabs.svelte`, which renders the sidebar group's *cross-route* sub-tabs as anchors — that's not this component.)
- `ScreeningBadge.svelte` — sanctions-screening + vendor-risk pill. `<ScreeningBadge screening={v.screening_status} risk={v.risk_level} blocked={v.payments_blocked} />`. Tone map: clear=green, review/medium=amber, match/high/critical/blocked=red, unscreened/low=grey. Shared by the vendor list cell + `VendorModal`.
- `SubscriptionBadge.svelte` — platform-billing subscription-status pill. `<SubscriptionBadge status={sub.status} />` for the four states (`trialing`/`active`/`past_due`/`canceled`); WCAG-1.4.3-calibrated tones matching `StatusBadge`. Used by `/billing`.
- `Money.svelte` — locale-aware currency display. `<Money amount={row.amount} currency={row.currency} />`. Opt-in `whole` (no decimals), `accounting` (parenthesised negatives), `mono` (tabular-nums). Over `utils/money.ts::formatMoney`; see *Money formatting* above. Use this (or `formatMoney` in script) for every currency value — don't write `Intl.NumberFormat` inline.

The visual styling for all of the above lives **globally in `src/app.css`** (class-scoped: `.workspace`, `.grid-container td`, `.filter-chip`, `.modal`, `.kpi`, …) so route pages carry no duplicated `<style>`. Feature components below keep their own scoped CSS (Svelte's `.svelte-<hash>` outranks the bare-class globals).

**`modals/` — feature dialogs:**
- `InvoiceModal.svelte` — invoice detail/edit modal
- `AdvancedSearchModal.svelte` — invoice search filters
- `BulkRecodeGLModal.svelte` — admin bulk GL re-code preview/apply
- `ApprovalMatrixEditor.svelte` — approval-chain matrix builder
- `RunDetailModal.svelte` — payment run detail; status, total, payments table; Execute button when run is `draft`
- `VendorModal.svelte` — vendor detail modal; the "Screening & Risk" panel (status, last-screened, payment-block + reason, risk level/score), re-screen / recompute-risk / block-unblock actions (gated to admin + ap_manager), and the screening-history timeline. Over `$lib/api/vendors.ts`

**`chat/` — supplier collaboration:**
- `SupplierChatThread.svelte` — surface-agnostic per-invoice chat thread shared
  by the AP modal (`surface="ap"`) and the supplier portal (`surface="vendor"`).
  Never imports `api`/`portalApi`; the caller injects `onsend`/`onresolve`/
  `onreopen`/`ondownload`. Renders message bubbles (own-role right-aligned),
  plain-text body (never `{@html}`), attachment chips, relative time, and on the
  AP side @mention autocomplete + a template picker + resolve/reopen. AP calls
  go through `$lib/api/supplierChat.ts` (over `api`); portal calls through
  `$lib/portalChat.ts` (over `portalApi`). Types in `$lib/types/supplierChat.ts`
  (full `Chat*` for AP, masked `PortalChat*` for the portal — no internal id).

**`assistant/` — Conversational AP Assistant (`/assistant`):**
- `ChatMessage.svelte` — one chat bubble (own-role right-aligned). Renders the
  message's tool results (via `ToolResultView`) before the prose, the prose as
  plain text (never `{@html}`), an inline error, or a typing indicator while a
  streamed reply is still arriving.
- `ToolResultView.svelte` — dispatches a `ToolInvocation` to the right view per
  tool name: `get_vendor_spend` / `get_payment_forecast` → `SpendBarChart`;
  `list_invoices` / `list_pending_approvals` → a compact table (reuses
  `StatusBadge` + `Money`); `find_invoices_by_text` → snippet cards. Any
  unrecognised tool falls back to a formatted-JSON view. Each card carries
  `data-tool="<name>"` (e2e selector).
- `SpendBarChart.svelte` — horizontal CSS bar chart (mirrors the CFO dashboard's
  `.cf-bar*` recipe; no charting dependency). Takes `bars=[{label, value,
  amountLabel, sub?}]`; the parent formats money via `formatMoney` and passes
  the numeric `value` only to drive bar width.
- `ExamplePrompts.svelte` — empty-state with the three built-in roadmap prompts;
  `onpick(prompt)` fills + sends.
- `UsageMeter.svelte` — AI token usage bar over `GET /api/assistant/usage`
  (`data-testid="usage-meter"`). Budget `0` = unlimited (running total, no bar);
  amber ≥80%, red at/over budget.

**`workflow-builder/`** — drag-and-drop no-code builder canvas for the
`/workflows/[id]` editor (step palette, canvas nodes, SVG connectors;
native HTML5 drag-and-drop, no svelte-flow).

**`workflow-mgmt/`** — no-code builder management dialogs mounted on the
`/workflows` list page: `TemplateLibraryModal` (start from a template),
`VersionHistoryModal` (diff + restore versions), `SimulationModal` (dry-run a
sample invoice through the pipeline), and `ImportExportControls` (import a
pasted/uploaded definition; the `exportWorkflowToFile` module helper downloads
a definition as JSON). All wrap the shared `ui/Modal.svelte` and call the
`workflowStore` builder methods.

**`marketing/`** — `Landing.svelte` + `Pricing.svelte` (public no-tenant route).
**`layout/`** — the shared left side panel:
- `Sidebar.svelte` — collapsed/expanded nav + profile popover. The nav is
  driven by **`$lib/nav.ts`** (the single source of truth, also read by
  `SectionTabs`): high-traffic destinations are top-level `link`s; the rest are
  folded into `group`s (Procurement / Billing / Insights / Settings) that show
  ONE sidebar row and open a sub-tabbed page. A group's row links to the first
  child the current role can see; a group hides when the role can see none.
  Add/move a route by editing `$lib/nav.ts` (with its `roles` gate) — don't
  hand-roll nav rows in the component.
- `SectionTabs.svelte` — the per-page section sub-tab bar, rendered once in
  `routes/+layout.svelte` above the page slot. For a grouped route it renders
  the group's RBAC-visible children as tabs (suppressed when ≤1 is visible);
  top-level routes get no bar.
- `NotificationBell.svelte` — bell + unread badge in the sidebar header with a
  recent-notifications popover (replaced the old Notifications nav row; the full
  `/notifications` page is the "View all" target). Closes on Esc / backdrop.
- `EntitySwitcher.svelte` — multi-entity (subsidiary) selector; hidden for
  single-entity tenants.

### Types (`src/lib/types/`)

- `invoice.ts` — `Invoice`, `InvoiceStatus` (12 statuses), `VALID_TRANSITIONS`, `AdvancedSearchFilters`
- `payment.ts` — `Payment`, `PaymentRun`, `PaymentStatus`, `PaymentMethod` (ach, wire, check, virtual_card)
- `workflow.ts` — `WorkflowDefinition`, `WorkflowStep`, step configs (extraction, approval, erp_export)
- `admin.ts` — `AdminUser`, `Role` (admin, ap_manager, ap_clerk, cfo)
- `tax.ts` — `Report1099`, `Vendor1099Row` (1099 reporting dashboard)
- `supplierChat.ts` — `ChatThread`, `ChatMessage`, `ChatAttachment`, `ChatTemplate` (AP, full) + masked `PortalChatThread` / `PortalChatMessage` (portal — no `author_user_id`, no mentions)
- `assistant.ts` — `ToolInvocation`, `ChatResponse`, `ConversationSummary` / `ConversationDetail` / `ConversationListResponse`, `UsageResponse`, the five structured tool-result shapes (`VendorSpendResult`, `ForecastResult`, `InvoiceListResult`, `PendingApprovalsResult`, `TextSearchResult`), the UI-side `UiMessage`, and `EXAMPLE_PROMPTS` (the three built-in empty-state prompts). Money fields are string-Decimal — pass to `formatMoney`, never `parseFloat` for display.
- `vendor.ts` — `Vendor` (incl. `screening_status` / `last_screened_at` / `payments_blocked(+_reason)` / `risk_score` / `risk_level`), `SanctionsCheck`, `ScreeningReviewItem`, `VendorRisk`, `RiskSummaryBucket`, the `ScreeningStatus` / `RiskLevel` unions + label maps

## Multi-tenant routing

- `src/lib/tenant.ts` extracts subdomain → `acme.localhost` becomes `"acme"`
- `src/lib/api.ts` sends `X-Tenant-Slug` header on every request
- `+layout.svelte` shows "no tenant" page if accessed without a subdomain

Access via: http://acme.localhost:7777 or http://techflow.localhost:7777

## Design system & UI patterns

Reuse these patterns instead of inventing new ones. Reach for the
existing component first; only deviate with a written justification.

### Page layout

Wrap every authenticated route in **`<PageHeader title="…">`**
(`$lib/components/ui/PageHeader.svelte`) — it renders the `.workspace`
shell, the `.toolbar` header with the `<h1>` title, and an optional
`{#snippet actions()}` for right-aligned primary actions (e.g.
`+ Invite User`, `+ Upload Invoices`). The page body goes in `children`.
Don't hand-roll `<div class="workspace"><header class="toolbar">` any
more. The shell still produces this layout:

```css
.workspace {
    max-width: 1800px;
    margin: 0 auto;
    padding: 24px 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-height: 100vh;
}
```

This is what produces the consistent left/right gap between sidebar
and content across pages — do not change `max-width` or `padding`
per-route. A new route must use these exact values. The 1800px cap
is wide enough for grid pages on 1920–2560px monitors without leaving
half the viewport empty; on a 13″ laptop the natural body width
constrains it before the cap kicks in.

### Data tables (`DataTable`)

Use **`<DataTable>`** (`$lib/components/ui/DataTable.svelte`) for every
grid page instead of hand-rolling `<div class="grid-container"><table>`:

```svelte
<DataTable columns={COLUMNS} isEmpty={items.length === 0} empty="No items.">
    {#snippet body()}
        {#each items as item (item.id)}
            <tr class:row-selected={selected.has(item.id)}>
                <td>…</td>
                <td class="actions"><RowAction …>Edit</RowAction></td>
            </tr>
        {/each}
    {/snippet}
</DataTable>
```

- `columns = [{label?, class?}]` builds the `<thead>`. For a select-all
  checkbox or sortable headers, pass `{#snippet header()}<tr>…</tr>{/snippet}`
  + `colspan={N}` instead of `columns`.
- The `body` snippet renders the rows; the page keeps full control of
  `<tr>`/`<td>` markup + classes (so bespoke cell styling stays page-scoped).
- `isEmpty` + `empty` render the centred `td.empty` row.
- Opt-in `fixed` (`table-layout: fixed`, pair with `<th>` widths) and
  `stickyHeader`. These two MUST be props (they target DataTable-owned
  `<table>`/`<thead>`, which a page-scoped selector can't reach).

### Search (`SearchBox`)

Pill-shaped search input with a magnifier-glass SVG. Single component:

```svelte
<script lang="ts">
    import SearchBox from '$lib/components/ui/SearchBox.svelte';
    let search = $state('');
</script>

<SearchBox
    bind:value={search}
    placeholder="Search invoices..."
    ariaLabel="Search invoices"
/>
```

- Debounce search before fetching (250–300ms is the convention; see
  `routes/admin/+page.svelte` and `routes/invoices/+page.svelte`).
- Server-side filter via `?search=` param. Backend uses ILIKE on the
  most natural fields for that entity (e.g. name + email, or
  invoice_number + vendor_name).
- Clearing the input must re-fire the request without `?search=`,
  not just visually clear.
- Do NOT re-implement the search-box markup inline. If you find
  yourself writing `<svg ...><circle .../><path .../></svg>` next to
  an `<input>`, you are diverging from the pattern.

### Bulk selection (`BulkBar` + `BulkDeleteButton`)

Floating, fixed-position bar at the bottom of the viewport that
appears when one or more rows are selected:

```svelte
<script lang="ts">
    import BulkBar from '$lib/components/ui/BulkBar.svelte';
    import BulkDeleteButton from '$lib/components/ui/BulkDeleteButton.svelte';

    let selected = $state<Set<string>>(new Set());
</script>

<BulkBar count={selected.size} onclear={() => (selected = new Set())}>
    {#snippet actions()}
        <BulkDeleteButton
            onconfirm={handleBulkDelete}
            disabled={busy}
            label={`Delete ${selected.size}`}
        />
        <!-- additional .bulk-action-btn buttons go here -->
    {/snippet}
</BulkBar>
```

**Required behaviours:**
- Selection lives in a `Set<string>` keyed by row id.
- Header checkbox toggles select-all over the *selectable* subset
  (e.g. excluding the current user, the default workflow, or
  immutable-status invoices). Items that can't be selected render
  their `<td class="checkbox-col">` empty rather than disabled.
- Delete is always armed-confirm (one click arms; outside-click or
  second click un-arms or commits). `BulkDeleteButton` does this.
- Bulk endpoints return a partial-success shape — `{deleted: [],
  failed: [{id, reason, ...}]}` — and the page surfaces the per-row
  reason in a toast. See `bulk_delete_users` in `backend/app/api/admin.py`
  for the canonical contract.

**The one exception:** `/payments` queue uses a non-floating
`<div class="pay-bar">` because it's a payment-run *builder*
(selection drives the next step's UI, not row actions). Don't copy
this pattern elsewhere.

### Pagination + Load more

Default page size is **20** across all list endpoints. Backend
returns `{items, total, page, page_size}`; the front-end renders the
items, then a centred Load More button below the table:

```svelte
{#if store.hasMore}
    <div class="load-more-row">
        <button class="btn-load-more" onclick={loadMore} disabled={store.loading}>
            {store.loading ? 'Loading…' : `Load more (${store.items.length} of ${store.total})`}
        </button>
    </div>
{:else if store.total > 0}
    <div class="load-more-row">
        <span class="load-more-end">Showing all {store.total} <thing>s</span>
    </div>
{/if}
```

- Append, don't replace. `loadMore` issues `page=N+1` and concatenates
  the new items.
- "Showing all N" is the empty-string-of-pagination state — confirms
  for the user that they've reached the end.
- Stores expose `total`, `page`, `hasMore`, and any mutating actions
  (create / delete / bulk-delete) keep `total` in sync without a
  refetch.

### Status filter chips

Use **`<FilterChips>`** (`$lib/components/ui/FilterChips.svelte`) for the
pill-shaped status filter above the table:

```svelte
<FilterChips
    chips={[
        { key: 'all', label: 'All', count: total },
        ...STATUSES.map((s) => ({ key: s, label: STATUS_LABELS[s], count: statusCount(s) }))
    ]}
    bind:active={statusFilter}
/>
```

- `chips = [{key, label, count?, alert?}]`. Omit `count` for label-only
  chips; `alert: true` renders the red attention badge (`.count.alert`).
- The "All" chip comes first; active chip uses `var(--accent)` + white.
- **Single-select only.** For a multi-select status filter (e.g. `/invoices`,
  whose filter is an array) keep an inline `<nav class="filters">` chip
  nav — it still uses the global `.filter-chip` / `.count` CSS, so the
  visible text/counts (and the `/^All\s+\d+/` e2e selectors) stay identical.
- **Quick subset, not every status.** When the lifecycle has many statuses
  (`/invoices` has 12), the inline row shows only a small, high-traffic
  *quick subset* — the stages people triage daily (`new`, `ready_for_review`,
  `approved`, `failed`), gated by the active workflow. The **full** set lives
  in the Advanced Search modal. The two share **one** selection array: opening
  the modal seeds its Status section from the live chip selection, and Apply
  writes it back — so they never fight over `params.status` (do *not*
  reintroduce a second status param in `buildParams`). Any status selected only
  in the modal is appended to the rendered chips so an active filter is never
  invisible (`chipStatuses` = quick subset ∪ active). See
  `routes/invoices/+page.svelte` and `tests-e2e/invoices/advanced-status.spec.ts`.

### Modals

Use **`<Modal>`** (`$lib/components/ui/Modal.svelte`) — backdrop +
centred dialog with backdrop-click + Esc to close:

```svelte
<Modal open={showCreate} ariaLabel="<Action>" title="<Heading>" width="sm" onclose={() => (showCreate = false)}>
    <form onsubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
        <!-- labelled fields -->
        <div class="modal-footer">
            <button type="button" class="btn-cancel" onclick={() => (showCreate = false)}>Cancel</button>
            <button type="submit" class="btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
    </form>
</Modal>
```

- `ariaLabel` becomes the dialog's `aria-label` — **e2e specs select
  modals by this exact string**; never change a label on an existing modal.
- Keep the page's own `<form>` **including the `.modal-footer`** inside
  the children so submit still works (Modal does not own the footer).
- `width="sm|md|lg"` = 440/480/820px. Custom heading markup →
  `{#snippet header()}…{/snippet}` instead of `title`. If the body
  dereferences a nullable var, gate `open={x !== null}` and wrap the
  children in `{#if x}…{/if}`.
- Cancel sits left of the primary action. Required-field markers use
  `<em class="required">*</em>`.
- **Never hand-roll a modal shell.** Every dialog — including the feature
  dialogs in `$lib/components/modals/` — wraps its body in `<Modal>`. Do
  not write your own `.backdrop` / `div.modal[role="dialog"]`, your own
  Esc / backdrop-click handlers, or a `<svelte:window onkeydown>` to close
  — `Modal` already owns all of that, and a private copy drifts (the
  `AdvancedSearchModal` overflow + scrollbar bug came from a hand-rolled
  shell that never picked up the global `.modal` CSS). "Bespoke internals"
  means the dialog's *body* (custom field grids, pickers, footers) is
  feature-specific — the shell, backdrop, and close behaviour are not.
  If you find yourself writing `position: fixed; inset: 0` or
  `role="dialog"` in a component, stop and use `<Modal>`.

### Per-row actions

Use the shared `<RowAction>` component (`$lib/components/ui/RowAction.svelte`)
for every per-row button across every grid page. Variants:
- `default` — neutral border, accent on hover (Edit, Apply, link buttons)
- `success` — green border + text (Verify)
- `danger` — neutral by default, red on hover; pass `armed` for the
  filled-red two-click confirm (Delete, Reject, Void)

Renders as `<a>` when given `href`, otherwise `<button>`. Never copy
the `padding: 4px 12px; ...` recipe inline — use the component.

The actions cell is **always the last column** (right side of the row),
preceded by a header `<th class="actions-col"></th>`. The `<td>` uses
`class="actions"` with the standard left-aligned flex layout:

```css
.actions {
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
}
```

Buttons inside align left within the cell — do not use
`justify-content: flex-end`.

For the destructive armed-confirm pattern, outside-click un-arms by
adding a `<svelte:window onclick>` that clears `confirmDeleteId` when
the click target is not within `.row-action`. See
`routes/admin/+page.svelte` for the canonical implementation.

### Clickable rows (`RowLink` + `isRowOpenClick`)

A list row that has a detail/edit destination opens it **by clicking the
row**, not via a separate "Edit"/"View" button. Two layers make this
accessible:

1. **Primary cell** (the id / name / number — `invoice_number`,
   `po_number`, workflow name, user name) wraps its content in
   **`<RowLink>`** (`$lib/components/ui/RowLink.svelte`). RowLink renders
   a real `<button>` (pass `onclick` — opens a modal) or `<a>` (pass
   `href` — navigates), styled to look like plain cell text but
   focusable, keyboard-operable, and announced by screen readers. This
   is the canonical, a11y-correct affordance — a table `<tr>` must keep
   its implicit `row` role (overriding it to `button` breaks column-header
   semantics), so the focusable control lives in the cell, not on the row.
   **Always pass a row-specific `ariaLabel`** (e.g. `Edit invoice INV-42`)
   — e2e specs select on it.

2. **Whole row** carries `class="clickable"` + an `onclick` that opens
   the same destination, gated by **`isRowOpenClick(e)`**
   (`$lib/utils/rowNav.ts`). The guard bails when the click lands on a
   button, link, input, or the `.checkbox-col` / `.actions` cells — so the
   bulk-select checkbox and the kept **Delete** (and other per-row action)
   buttons still work. This is the Gmail/Linear "click anywhere except the
   controls" pattern.

```svelte
<tr class="clickable" class:row-selected={selected.has(row.id)}
    onclick={(e) => { if (isRowOpenClick(e)) editing = row; }}>
    <td class="checkbox-col"><input type="checkbox" … /></td>
    <td class="mono">
        <RowLink onclick={() => (editing = row)} ariaLabel={`Edit ${row.number}`}>
            {row.number}
        </RowLink>
    </td>
    …
    <td class="actions">
        <!-- no Edit/View button — the row opens the editor. Keep Delete: -->
        <RowAction variant="danger" armed={…} onclick={…}>Delete</RowAction>
    </td>
</tr>
```

- **Don't** add a separate Edit/View `RowAction` when the row is
  clickable — the row IS the affordance. Keep destructive / state-changing
  actions (Delete, Void, Verify, Activate…) as `RowAction`s in the
  `.actions` cell.
- **Don't** put `role="button"` / `tabindex` / `onkeydown` on the `<tr>`
  — that's the wrong fix (kills table semantics, conflicts with nested
  controls). The in-cell `RowLink` is the keyboard/AT path; the row
  `onclick` is a pointer-only enhancement.
- Lists with **no per-row detail view** (vendors, exceptions, credit
  memos, payments history/cards) keep their existing conditional
  `RowAction` buttons — there's no single "open" destination to wire.

### Class-name conventions

The class names below are the shared contract (e2e specs select on
them). Their CSS lives globally in `src/app.css`; the markup comes from
the `ui/` primitive in the Source column.

| Pattern | Class | Source |
|---|---|---|
| Page wrapper + header | `.workspace` / `.toolbar` / `<h1>` | `ui/PageHeader.svelte` |
| Data table | `.grid-container` + `table`/`th`/`td`/`.empty` | `ui/DataTable.svelte` |
| Search input | `.search-box` | `ui/SearchBox.svelte` |
| Bulk bar | `.bulk-bar` | `ui/BulkBar.svelte` |
| Bulk delete | `.bulk-delete-btn` (+ `.armed`) | `ui/BulkDeleteButton.svelte` |
| Bulk action | `.bulk-action-btn` | per-route, but always inside a BulkBar |
| Per-row action | `<RowAction>` (variant + armed) | `ui/RowAction.svelte` |
| Clickable-row open control | `<RowLink>` + `.clickable` row + `isRowOpenClick` | `ui/RowLink.svelte` / `utils/rowNav.ts` |
| Filter pill | `.filter-chip` (+ `.active`, `.count`) | `ui/FilterChips.svelte` |
| Tab bar | `.tab-row` / `.tab` (+ `.active`) | `ui/Tabs.svelte` |
| Load more | `.btn-load-more` / `.load-more-row` / `.load-more-end` | per-route, copy /admin |
| Modal dialog | `.modal[role="dialog"]` + `.backdrop` | `ui/Modal.svelte` |
| KPI card | `.kpi` / `.kpi-value` / `.kpi-label` | `ui/KpiCard.svelte` |
| Status badge | `<StatusBadge>` | `ui/StatusBadge.svelte` |
| Money / currency | `<Money>` / `formatMoney` | `ui/Money.svelte` / `utils/money.ts` |
| Checkbox / radio / file | `input[type='checkbox'\|'radio'\|'file']` (global base) | `src/app.css` |

**Native form controls** are dark-themed globally in `src/app.css` so a
bare `<input>` fits the theme without per-route CSS:
- **Checkbox / radio** — `appearance: none` + a drawn mark (white check
  / inset accent dot) so *both* states are themed — empty = subtle
  outline on `--bg`, on = `--accent` fill, plus
  `:indeterminate` (checkbox) / `:focus-visible` / `:disabled`.
- **File input** — `::file-selector-button` restyled to match the
  secondary (`.btn-cancel`) button.
- **Select** — `appearance: none` + a drawn chevron, and the single
  source of truth for the whole select look (border / radius / surface /
  padding / type). Per-component scoped rules override only what differs
  per context (padding, `border-radius`, `font-size`, `width`,
  `--surface` background) and **must not** re-declare the shared
  border/colour/font or use a `background:` shorthand — the shorthand
  resets the chevron's `background-image`. Scoped rules keep ~30px of
  right padding so text clears the chevron. (Because no rule competes on
  `background-image`, the chevron needs **no `!important`** — earlier it
  did, before the scoped rules were collapsed onto this recipe.)
- **Range** — `accent-color: var(--accent)` (the modern cross-browser
  approach; no pseudo-element rebuild).
- `:root { color-scheme: dark }` puts the remaining native controls
  (date pickers, scrollbars) in dark mode; scrollbars are further
  thinned + tinted, the date-picker indicator gets a hover, and
  `::selection` is accent-tinted.

Don't re-add per-route `accent-color` rules on checkboxes/radios — they
only tint the *checked* state and are redundant no-ops under the global
`appearance: none`.

(All Source paths are under `$lib/components/`.) If a shared style is
missing, add it to `src/app.css` (class-scoped) — not a per-route
`<style>`. If you need a brand-new pattern, add a component under
`$lib/components/ui/` and document it here. **Do not** invent a new
class name for an existing pattern, and **do not** re-introduce a
per-route copy of the table/modal/chip/shell CSS.

### Accessibility patterns (WCAG 2.2 AA)

The shared web foundation carries the baseline a11y so route pages
inherit it for free. Reuse these; don't re-solve them per page.

- **Skip link** (`.skip-link`, app.css; WCAG 2.4.1) — the first
  focusable element in both the app shell (`routes/+layout.svelte`)
  and the supplier portal (`routes/portal/+layout.svelte`). Off-screen
  until focused, then a high-contrast pill. Targets `#main-content` —
  the `<main>` element, which carries `id="main-content" tabindex="-1"`
  so the jump lands focus there. A new top-level shell must keep this
  pairing.
- **Landmarks** (WCAG 1.3.1) — the sidebar nav is `<nav aria-label="Primary">`,
  the section sub-tabs `<nav aria-label="<group> sections">`, the portal
  nav `<nav aria-label="Supplier portal">`. Name every nav landmark so
  multiple navs are distinguishable. Page `<h1>` lives in `PageHeader`.
- **Global focus ring** (app.css; WCAG 2.4.7) — a `:focus-visible`
  accent outline covers `a / button / [role=button|tab|option] /
  [tabindex] / input / select / textarea / summary`. Never set
  `outline: none` without replacing it with a visible ring (checkbox /
  radio do this — accent box-shadow). This is the floor; component-local
  `:focus-visible` (RowLink) layers on top.
- **Reduced motion** (app.css end; WCAG 2.3.3) — a global
  `@media (prefers-reduced-motion: reduce)` block near-zeroes all
  animation/transition durations. Don't gate functionality on a
  transition finishing.
- **Modal / focus trap** (`ui/Modal.svelte` + `$lib/actions/focusTrap.ts`;
  WCAG 2.1.2 / 2.4.3) — `use:focusTrap={{ onEscape }}` on a dialog box
  (with `tabindex="-1"`) moves focus in on open, traps Tab / Shift+Tab
  with wrap-around, closes on Esc, and restores focus to the trigger on
  close. `ui/Modal` uses it, and so do the four pre-existing hand-rolled
  feature shells (`InvoiceModal`, `RunDetailModal`, `BulkRecodeGLModal`,
  portal discount-accept) so every dialog gets identical focus management.
  Prefer `ui/Modal` for new dialogs; if you must hand-roll a shell, add
  `use:focusTrap` rather than re-implementing it.
- **Reorder controls** (WCAG 2.5.7 Dragging Movements) — any drag-to-reorder
  needs a single-pointer + keyboard alternative. The workflow-builder
  `StepNode` pairs its drag handle with per-node Move ↑ / Move ↓ buttons
  (`onmoveup`/`onmovedown` over the canvas `onreorder`). Mirror this for any
  new drag interaction; don't ship drag as the only path.
- **Toast** (`ui/Toast.svelte`; WCAG 4.1.3) — `role="region"` +
  two persistent live containers (`aria-live="assertive"` for errors,
  `"polite"` for the rest). Each toast has a real `<button>` dismiss
  (`aria-label="Dismiss notification"`); auto-dismiss timer kept.
- **Tabs** (`ui/Tabs.svelte`; WAI-ARIA tabs) — roving `tabindex`
  (active=0, others=-1) + Arrow/Home/End key navigation, plus the
  existing `role=tablist/tab` + `aria-selected` + `aria-controls`. The
  caller still gives the panel `role="tabpanel"` + the matching ids.
- **Filter chips** (`ui/FilterChips.svelte`) — `<button aria-pressed>`
  reflects the active chip.
- **DataTable** (`ui/DataTable.svelte`) — auto-rendered `<th>` get
  `scope="col"`. A page that passes its own `{#snippet header()}` owns
  adding `scope` to its `<th>`s.
- **Icon-only controls** — every icon-only `<button>` needs an
  `aria-label` (NotificationBell reflects the unread count; the sidebar
  collapse toggle + profile button carry `aria-label` + `aria-expanded`).

## Conventions

- **Svelte 5 runes** — `$state`, `$derived`, `$effect`, `$props`. No legacy options API.
- **TypeScript** — `lang="ts"` on all `<script>` blocks.
- **API access** — always through `src/lib/api.ts`, never raw `fetch()`.
- **BASE_PATH** — set to `/<repo-name>` during CI builds for GitHub Pages asset paths.
- **No SSR** — static adapter only. Dynamic data comes from the backend API.

## Web vs Mobile feature parity

The mobile app (`mobile/`) covers core approval workflows. These web features are **not yet on mobile**:

- Invoice editing, file upload (PDF), PDF viewer, audit timeline
- Advanced search, bulk operations, export
- Vendors, exceptions, workflows, organization settings, admin
- Payment queue and payment runs

Mobile has features **not on web**: camera OCR, push notifications, offline mode, biometric login, swipe-to-approve.

See `mobile/CLAUDE.md` for the full mobile feature list and `docs/roadmap.md` Priority 8 for the parity roadmap.

## Deployment

- **GitHub Pages**: publishing a GitHub release triggers `.github/workflows/deploy.yml`, whose `frontend` job builds and publishes to Pages. The workflow no-ops on push to `main` by design — the release tag is the gate so the deployed artifact matches a named version.
- `build/.nojekyll` created at build time to bypass Jekyll processing
