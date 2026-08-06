# Roadmap — open work

Feature backlog for the AP automation platform, ordered by impact. **This file
carries only the areas that still have unshipped work.** A section lives here
because something in it is genuinely open; the `**Open:**` line under each
status names exactly what, and every one of those items is tracked with its
category, durable fix, and trigger in [followups.md](followups.md).

Fully-shipped areas were moved to [roadmap_shipped.md](roadmap_shipped.md) —
verbatim, nothing summarized away. 40 of the 51 sections live there. Look for
prior art in the archive before assuming a capability doesn't exist.

**Related:** diagnosed-but-unfixed defects in
[known-issues.md](known-issues.md); the reasoning behind non-obvious design
calls in [decisions.md](decisions.md). This file carries **scope and status**
— when an entry needs to explain *why* something was built a given way, put the
paragraph in `decisions.md` and link it.

## Legend

- **Done** — implemented and working
- **Partial** — backend or models exist, needs completion
- **Planned** — not started

**Prune on landing.** When a section's last open item ships, move the whole
section to `roadmap_shipped.md` in the same commit. That is the discipline that
keeps this file honest — a stale "In progress" status survived unnoticed here
for weeks precisely because it was buried in 1131 lines of shipped prose.

---

## Priority 3: Payments

### Vendor Statement Reconciliation
**Status:** Done (CSV + manual intake; PDF-via-extraction deferred) — pure engine in `backend/app/services/vendor_statement_recon.py`, `/api/vendor-statements` router, `/vendor-statements` frontend route, migration 0047. See `backend/docs/vendor-statement-reconciliation.md`.
**Open:** PDF-via-extraction statement intake + raw-file storage. CSV and manual intake ship. *(c) sized, unstarted.* Tracked in [followups.md](followups.md).

Distinct from bank reconciliation (cleared payments ↔ bank lines): this reconciles a **supplier's statement of open items** against our AP ledger to catch missing invoices, double-posted bills, mis-applied credits, and stale balances before month-end close. A core AP-clerk task that's entirely manual today.

- [x] Statement intake — CSV upload (forgiving header sniff, mirrors the bank-rec CSV parser) + manual pasted-lines path, parsed into a normalized list of `{invoice_number, invoice_date, amount, status}` line items, vendor-scoped. *(PDF-via-extraction + raw-file storage deferred — see the doc's Deferred section.)*
- [x] Reconciliation engine (`services/vendor_statement_recon.py`, pure) — matches statement lines to our `Invoice` rows by normalized invoice number → amount+date-window fallback; classifies each as *matched* / *amount mismatch* (within/over a tolerance) / *missing on our side* (supplier billed, we never received) / *missing on their side* (we have an open invoice they omitted)
- [x] Persist a `VendorStatementReconciliation` run + `VendorStatementReconLine` results (migration 0047, tenant-gated + fans out); the actionable rows (missing-on-our-side + amount-mismatch) surface as the per-run review queue feeding invoice intake. *(Design note: they're recon **lines**, not `Exception` rows — a deliberate choice for their per-line resolve/ignore lifecycle and side-by-side diff. Migration 0049 has since made `Exception.invoice_id` nullable for the Positive Pay feature, so the "we have no invoice" constraint no longer applies, but recon lines remain the right model here. See the doc.)*
- [x] Frontend reconciliation view (`/vendor-statements`) — upload / manual create, side-by-side statement-vs-ledger diff, per-line resolve/ignore; every mutation RBAC-gated + audited (`vendor_statement_recon.created` / `.line_resolved` / `.deleted`)
- [x] Period close tie-in — `GET /api/vendor-statements/close-readiness` flags vendors whose most-recent open run carries a material (over `FEOH_STATEMENT_RECON_MATERIALITY_DEFAULT`, `?materiality=` override) unreconciled balance

**Competitors:** Tipalti, Basware, Medius (statement reconciliation in close workflows); most SMB tools lack it — a differentiator down-market

---

## Priority 5: Multi-Currency & Tax

### Multi-Language UI (Internationalization / i18n)
**Status:** In progress — **web runtime + full starter locale set shipped**: `frontend/src/lib/i18n/` (locale negotiation, typed `en` catalogue + the full `de/fr/es/pt-BR/ja` set as lazy chunks, lazy loader registry, reactive `m()`/`setLocale()`/`initLocale()`, ICU plurals, `<html lang/dir>`, locale picker with endonyms, `messages_parity` vitest) with the shell/nav + **dashboard** + **invoices list** + **payments** + **vendors** + **exceptions** + **notifications** + **contracts** + **recurring** + **organization settings** + **cfo/analytics** + **expenses** (incl. the **`ExpenseModal`** + **`PolicyModal`** dialogs) + the **procurement routes** (**requisitions** + **intake** + **catalogs** + **budgets** + **purchase-orders** + **goods-receipts**) + the **procurement create/edit modals** (`RequisitionModal` + `IntakeModal` + `CatalogModal`/`PunchoutModal` + `BudgetModal` + `ContractModal` + `RecurringModal`) + **positive-pay** + the **supplier portal** (every `routes/portal/**` page) + the **authentication & onboarding routes** (`login` + `login/mfa` + `login/{sso,saml}-callback` + `signup` + `change-password` + `verify`, `auth.*` namespace) + the **admin section** (the **Users & Roles** page + its `UsersPanel`/`RolesPanel`, **API Keys**, **Webhooks**, and **Partner / reseller admin**, `admin.*` namespace) extracted; `formatMoney` **and per-row dates** follow the active locale (the shared `utils/time.ts::formatDate` is locale-aware and now drives the dashboard / payments / exceptions **plus the workflows / discounts / credit-memos / vendor-statements** row dates that previously hardcoded `en-US`). The mobile ARB track is complete at the screen level (every `mobile/lib/screens/*` uses `AppLocalizations`). **All web routes + their feature modals are now extracted** — including the workflows, audit, tax, discounts, credit-memos, vendor-statements (incl. the `VendorStatementReconModal` create/diff dialog), assistant, billing, and experiments routes; server-side email localization is **shipped**. Remaining: only the deferred inline `toLocaleDateString` call sites in the procurement / portal / admin lists (a later date-localization slice). See `frontend/CLAUDE.md` → i18n.
**Open:** The date-localization slice — 16 `.svelte` files still call `toLocaleDateString` inline instead of the locale-aware `utils/time.ts::formatDate`. Every string catalogue, route, and modal is extracted. *(c) sized, unstarted; needs a source-scan guard so the class can't reopen.* Tracked in [followups.md](followups.md).

The data layer is already internationalized (multi-currency rollups, locale-aware `Intl` money/date formatting, country tax rules, e-invoicing) — but every label, button, email, and error string is still hardcoded English. Localizing the **presentation** layer is the remaining piece for genuine international reach (EU mandates, LATAM, APAC, MENA). Basware/Medius ship 20+ UI languages; Tipalti and Bill.com localize the supplier-facing surfaces. Starter set: `en, de, fr, es, pt-BR, ja` (the six [`../project-running`](../../project-running) already ships), with the RTL switch-point in place for a later `ar`/`he`.

**Web (SvelteKit, `frontend/`):**
- [x] i18n runtime under `frontend/src/lib/i18n/` — client-side locale detection on first mount (stored choice → `navigator.languages` → English), reactive `m(key, params)` lookup, `<html lang/dir>` applied. **No `Accept-Language` SSR hook** — the frontend is adapter-static (GitHub Pages), so detection must be client-side
- [x] English statically bundled (fallback dict + prerender default); every other locale a dynamic `import()` chunk via a typed loader registry, so a single-locale visitor downloads only their strings — i18n adds ~nothing to the initial payload (`catalogues.ts`: `en` static, `de/fr/es/pt-BR/ja` lazy `import()`)
- [x] Compile-time + runtime parity: `Messages = typeof en` + `satisfies Messages` per locale (missing/extra key = type error); a `messages_parity` vitest validating every locale is loadable, complete, non-empty, and placeholder-faithful (covers all six locales via `SUPPORTED_LOCALES`)
- [x] ICU inline plurals (`{n, plural, one {…} other {…}}`) resolved via `Intl.PluralRules` for the active locale — not `fooOne`/`fooOther` key pairs (keeps web and mobile plural shapes identical) — e.g. the invoices `selected` count + showing-all string
- [x] Locale picker in settings/shell (endonyms — each language in its own script: English / Deutsch / Français / Español / Português (Brasil) / 日本語), choice persisted to `localStorage`
- [x] Active locale drives the existing `Intl.NumberFormat`/`Intl.DateTimeFormat` formatters (`<Money>` / `formatMoney()`) so numbers and currency localize together (date helpers still pending)
- [x] RTL switch-point (`dirForLocale`) wired to `<html dir>`; audit CSS for logical properties so an `ar`/`he` catalogue drops in with no further layout plumbing (switch-point present + unit-tested; no RTL catalogue ships yet)
- [x] Incremental string extraction — shell/nav first, then route-by-route (shell/nav + dashboard + invoices list + payments + vendors + exceptions + notifications + contracts + recurring + organization settings + cfo/analytics + expenses done); an un-extracted literal simply stays English until its turn

**Mobile (Flutter, `mobile/`):**
- [x] Standard Flutter `gen-l10n` + `intl` + `.arb` catalogues (idiomatic path — plural/placeholder/ICU + `AppLocalizations.of(context)`), committed (non-synthetic) output under `mobile/lib/l10n/gen/`, same six locales (en, de, fr, es, pt-BR, ja; a base `pt` fallback accompanies `pt_BR` as gen-l10n requires). Coverage: nav + dashboard + invoices list + settings + notifications + vendors + exceptions + payments history + approvals + capture + advanced-search + invoice detail/edit (+ sub-widgets) + payment queue/runs + login + MFA + admin users + org settings + workflows (the full screen set is now extracted). See `mobile/CLAUDE.md` → i18n
- [x] Per-device locale via `LocaleStore` (`stores/locale_store.dart`, secure-storage-persisted, never account-roamed) → `MaterialApp.locale` (a `ListenableBuilder` in `main.dart` re-localizes live); endonym picker + "System default" in the Settings screen
- [x] ARB key-parity test (`test/l10n/arb_parity_test.dart`) mirroring the web `messages_parity` — key-complete, non-empty, placeholder-faithful (set-based, so a 1-arm `ja` plural matches a 2-arm `en` one); plus a live locale-switch widget test (`test/l10n/locale_switch_test.dart`)

**Server-side (FastAPI, `backend/`):**
- [x] Localized outbound email — DB-synced `locale` preference on `User` (control plane) + `VendorUser` (tenant-scoped), migration `0059` (existence-guarded, runs on both control + every tenant DB; nullable → English fallback). Consumed by a per-locale email catalogue (`app/services/email_adapters/email_catalogue.py`, same six locales as web/mobile) with English fallback per key. Covers the `email_adapters` surfaces: signup/welcome (locale captured at `/signup/start`, stashed in the `EmailVerification.meta`, reused by the welcome email), invoice notifications (employee via `notification_dispatch` + supplier via `vendor_notifications`, rendered per recipient in their locale), supplier-chat portal-link email (catalogue-routed, English default — no per-user locale at that surface). Set via `PATCH /api/auth/me` (employee) + `PATCH /api/portal/auth/me` (vendor), each validating against the supported set (422 on unknown). **Deferred:** the frontend language-picker → backend write (owned by the frontend track) — the backend endpoint + persistence are in place. See `backend/docs/notifications.md` § Localized email
- [x] Email catalogue parity test (`tests/test_email_catalogue.py` — every locale resolves every key, no empty strings, placeholder-faithful vs English); deep links + brand chrome stay locale-independent — only copy changes (placeholders carry the data unchanged across locales)
- [x] DB `locale` pref kept **separate** from the per-device UI locale — it means "what language to email this person in" (account-level), written from the UI and read by the email-render path ONLY; never returned to drive in-app UI (documented in `backend/docs/notifications.md` § Localized email)

**Pointers from `../project-running`** (it shipped exactly this — three translation surfaces kept in lockstep by parity tests, no shared source because TS/Dart/Python can't import one catalogue):
- Web runtime to model on: `apps/web/src/lib/i18n/` — `locale.ts` (pure negotiation: `SUPPORTED_LOCALES`, `negotiateLocale`, `dirForLocale`, `parseAcceptLanguage`), `messages.ts` (`Messages = typeof en`), `catalogues.ts` (typed lazy-loader registry), `store.svelte.ts` (reactive `m()` + `setLocale` + `initLocale`), `interpolate.ts` (ICU plural + `{placeholder}` substitution), `messages_parity.test.ts`, and `locales/*.ts`
- Decision records spelling out the *why* and the traps to avoid: `docs/architecture/decisions.md` §108 (web client-side + lazy catalogue), §113 (mobile gen-l10n/ARB + per-device locale), §120 (server-side email localization from a DB-synced pref — the one place locale leaves the device)
- Reuse the design wholesale; the only AP-specific delta is that **two** identities email-localize (internal `User` and supplier-portal `VendorUser`) and the email catalogue lives in Python (`backend/app/services/email_adapters/`), not Go

**Competitors:** Basware / Medius (20+ UI languages, EU-mandate-driven), Tipalti & Bill.com (localized supplier portals), SAP Ariba / Coupa (full enterprise localization)

---

## Priority 7: Authentication & Enterprise Security
**Competitive gap: SSO is an enterprise deal-blocker**

### SOC 2 Readiness
**Status:** Engineering prereqs complete — **all code controls landed; process work pending founder sign-off**
**Open:** All six process items below — vendor selection, policy library, onboarding/offboarding evidence, IR runbook + on-call, Type I audit, Type II window. **Every engineering prereq is complete.** *(a) blocked on a founder decision + a vendor contract.* Tracked in [followups.md](followups.md).

SOC 2 Type I (design) → Type II (operating over time) is the table-stakes security attestation for selling to finance teams. Full plan in [`docs/soc2-readiness.md`](soc2-readiness.md) — vendor comparison, control mapping, timeline, and what the founder still needs to do as a human.

**Engineering prerequisites:**
- [x] Access reviews — `backend/scripts/access_review.py` exports every user × role × org as CSV (quarterly)
- [x] Backup + DR runbook — `docs/backup-disaster-recovery.md` with RTO/RPO + restore procedures
- [x] Secrets rotation runbook — `docs/secrets-rotation.md` (cadence + procedure for every secret)
- [x] Vulnerability scanning in CI — Dependabot (shipped) + CodeQL SAST (Python + JS) + Trivy on the backend container, weekly + on push (`.github/workflows/security.yml`)
- [x] RBAC enforcement at API layer (separate roadmap item — already done)
- [x] MFA support + org-level enforcement (separate roadmap item — already done)
- [x] Session management — per-user concurrent session cap (Redis sorted set, `FEOH_MAX_CONCURRENT_SESSIONS`), forced logout on role change / deactivation (`services.session_management.revoke_user_sessions`)
- [x] Centralized audit log shipping — background shipper loop + adapters (CloudWatch Logs + S3 Object Lock) at `backend/app/services/audit_log_shipper.py` + `services/audit_shipping/`. See `backend/docs/audit-log-shipping.md`.
- [x] Auth event audit log — login/logout/MFA/SSO events written via `app/services/audit_dispatch.py::dispatch_auth_audit` into the tenant `audit_log` table
- [x] HSTS header + security-header middleware (`backend/app/main.py` `SecurityHeadersMiddleware`, gated on `FEOH_HSTS_ENABLED`); TLS smoke script at `backend/scripts/verify_tls.py`
- [x] KMS key auto-rotation flag in Terraform — `infra/kms.tf` `enable_key_rotation = true`
- [x] S3 versioning + Object Lock in Terraform — `infra/s3.tf` (versioning Enabled; invoice-files GOVERNANCE 365d, audit-logs COMPLIANCE 2555d)

**Process / attestation work** (founder, not engineer):
- [ ] Vendor selection — Vanta, Drata, Secureframe, or Sprinto. See `docs/soc2-readiness.md` § Vendor comparison.
- [ ] Policy library — info security, incident response, change management, access control, vendor mgmt (vendor templates)
- [ ] Employee onboarding / offboarding checklist with evidence collection
- [ ] Incident response runbook + on-call rotation
- [ ] SOC 2 Type I audit (point-in-time) — typical 4–8 weeks after prereqs
- [ ] Begin Type II observation window (6+ months) for annual renewal

**Competitors:** Every serious competitor has SOC 2 Type II. Without it, enterprise deals stall at security review.

---

## Priority 8: Mobile & Notifications
**Competitive gap: most competitors have mobile apps**

### Flutter Mobile App
**Status:** Done — full iOS + Android app; web-parity + admin screens all shipped
**Open:** Push-token registration to the backend and notification deep-linking (`push_service.dart:49` / `:99`). Every roadmap checkbox below ships; FCM/APNs is wired but no-ops until configured. *(a) blocked on a Firebase project + APNs auth key.* Tracked in [followups.md](followups.md).

Flutter app at `mobile/` with login, dashboard, invoice list, approve/reject, payments, settings. Same backend API as web.

**Done:**
- [x] Login with tenant selection
- [x] Dashboard (KPIs, aging buckets, top vendors)
- [x] Invoice list with search + status filter chips
- [x] Invoice detail with approve/reject
- [x] Approvals tab with swipe-to-approve
- [x] Payment history list
- [x] Role-based bottom navigation
- [x] Settings (profile, tenant info, logout)
- [x] JWT in secure storage (iOS Keychain / Android Keystore)
- [x] API contract tests in backend to prevent client breakage

- [x] Camera OCR — snap photo or pick from gallery → upload → trigger AI extraction
- [x] Push notifications — Firebase Cloud Messaging + local notifications (no-op until Firebase configured)
- [x] Offline mode — SQLite cache for dashboard and invoice list, serves cached data on network failure
- [x] Biometric login — Face ID / fingerprint / device PIN toggle in settings, checked on app launch

**Medium priority — parity with web (see `mobile/CLAUDE.md` for full gap list):**
- [x] Invoice upload via file picker (PDF/PNG/JPG/TIFF support) — `CameraCapture.pickDocument` via `file_picker`, Choose-file button on the capture screen → same `/api/invoices/upload` extraction pipeline as the camera path; PDFs preview as a document card, images inline
- [x] Invoice editing (change fields in detail screen) — edit sheet → `PATCH /api/invoices/{id}` (vendor/number/amount/PO/GL/description/due date; money + dates as string-Decimal), gated to admin/ap_manager/cfo on editable statuses
- [x] Activity timeline in invoice detail (audit log) — `GET /api/invoices/{id}/audit-log` rendered as a timeline widget (actor, action, time, per-field before→after), with empty/loading/error states
- [x] PDF/image viewer for uploaded invoice files — `InvoiceFileViewer` on the invoice detail screen: images via `Image.network` (auth headers), PDFs fetched as bytes (`ApiClient.getBytes`) + rendered with `pdfx`; inline image-thumbnail / PDF-card preview opens the full viewer; loading/error/Retry states
- [x] Exception queue (list, detail, resolve, escalate, dismiss, assign, bulk-resolve) — `ExceptionsScreen` + `ExceptionDetailScreen` + `ExceptionStore` over `GET /api/exceptions`, `GET /api/exceptions/{id}`, `POST /api/exceptions/{id}/resolve`, `POST /api/exceptions/{id}/assign`, `POST /api/exceptions/bulk/resolve`, admin/ap_manager. Detail view shows full fields + linked invoice + SLA/due/overdue + assignee with the three actions; tapping a row opens it. Assign uses an org-user picker (admin-gated — it needs the admin-only `/admin/users` list; ap_manager can still act). Multi-select (long-press / checklist app-bar action) + the shared `BulkActionBar` → `bulkResolve` with an updated/skipped snackbar
- [x] Vendor management (list, verify/reject, ERP sync) — `VendorsScreen` + `VendorStore` over `GET /api/vendors`, status filter chips + search, swipe verify/reject + ERP-sync action, RBAC-gated
- [x] Payment queue (select invoices, choose method) — `PaymentQueueScreen` Queue tab over `GET /api/payments/queue`, per-row method dropdown
- [x] Payment runs (create/execute batches) — Runs tab: create draft from selection, execute/cancel with CFO-approval-gate surfacing
- [x] Payment summary cards (total paid, pending, rebates) — KPI summary bar over `GET /api/payments/summary`
- [x] Advanced search modal (vendor, PO, amount range, date range) — `AdvancedSearchSheet` (app-bar `tune` action with an active-filter badge) → `InvoiceStore.setFilters` → `GET /api/invoices` with `vendor` / `po_number` / `amount_min` / `amount_max` / `due_date_from` / `due_date_to`; seeded from the live filters, validated (min ≤ max, plain decimals)
- [x] Invoice warnings/fraud flags display — `InvoiceWarningsPanel` on the detail screen renders `Invoice.warnings` (severity-coloured) + the `po_match` panel (match type, status, variance %, issues), parity with the web modal
- [x] ERP status display on invoice detail — `ErpStatusPanel` derives ERP reference / document id / send error from the loaded audit log (`invoice.erp_*` / `invoice.completed` entries); shown for ERP-bound statuses + ERP-failed invoices

**Low priority — admin features (less needed on mobile):**
- [x] Bulk operations (select multiple, delete, status change) — invoice
  multi-select (long-press or the checklist app-bar action) + bulk delete /
  bulk status-change over `POST /api/invoices/bulk/{delete,status}`, gated to
  admin/ap_manager/cfo; backend skips immutable-status rows; result snackbar
  reports deleted/updated + skipped counts. `BulkActionBar` widget +
  `InvoiceStore` selection state. See `mobile/CLAUDE.md`.
- [x] Admin user management — `AdminUsersScreen` + `AdminUserStore` over
  `/api/admin/*`: list/search users, **create a user** (validated form sheet →
  `POST /api/admin/users`; the server-generated one-time temporary password is
  surfaced for the admin to hand over, then the list refreshes), edit roles
  (system roles only), activate / deactivate, and **delete a user** (armed /
  confirmed destructive action → `DELETE /api/admin/users/{id}`; self-delete
  disabled client-side, the backend's 409 self / still-referenced reason
  surfaces in the failure snackbar). Admin-only, reached from Settings →
  Administration.
- [x] Organization settings — `OrgSettingsScreen` + `OrgSettingsStore` read +
  edit the safe subset the web app exposes (company profile + invoice defaults)
  via `GET/PATCH /api/organization`; ERP/payment/SSO secrets deliberately not
  surfaced; web-set `logo_url` carried through unedited. Admin-only.
- [x] Export (CSV/XML) — an **Export** action on the invoice `BulkActionBar`
  (role-gated as the other bulk ops) POSTs the selected ids to
  `POST /api/invoices/bulk/export` (CSV or XML format sheet) and hands the
  rendered bytes to the platform share sheet (`share_plus` + `ApiClient.postBytes`).
- [x] Workflow management — **read-only** list + step viewer: `WorkflowsScreen` +
  `WorkflowStore` over `GET /api/workflows` (name / active+default badges / step
  count) → `WorkflowDetailScreen` over `GET /api/workflows/{id}` (per-step
  type/name/enabled + PII-free config summary), reached from Settings →
  Administration, admin-gated. Create/edit stays a desktop surface (the no-code
  builder).

**Files:** `mobile/` — see `mobile/CLAUDE.md` for full structure

---

## Priority 9: AI-Powered Automation (strong differentiators)

### Predictive Cash Flow Forecasting
**Status:** Done — GET /api/analytics/cashflow_forecast (+ /cashflow_whatif, /cash_position) over Invoice/PaymentSchedule/Payment; what-if early/on-time/late with discount capture; cash-position with BYO opening balance + threshold alerts; CSV export via the analytics/export registry; CFO web dashboard at /cfo. Bank-balance auto-sync (optional PaymentAdapter.get_balance capability — mock returns a deterministic balance; best-effort, falls back to the manual opening balance) + persisted thresholds (Organization.settings.cashflow, GET/PUT /api/analytics/cash-position-settings, no migration) now shipped. Mobile shipped: CashFlowScreen + CashFlowStore (CFO/admin) over cashflow_forecast + cash_position — KPI summary (opening/projected-end balance, committed/pending outflow), per-period forecast + running cash-position list, low-balance alert, 30/60/90-day horizon chips; reached from a CFO/admin-gated Dashboard app-bar action; money rendered from server display strings (no device float math).
**Open:** A real banking-aggregator (Plaid-style) balance feed. Bring-your-own opening balance and the provider `get_balance` path both ship. *(a) blocked on an aggregator account.* Tracked in [followups.md](followups.md).

Use AP data to forecast cash outflows and optimize payment timing.

- [x] Forecast daily/weekly/monthly cash outflows from pending invoices
- [x] Factor in payment terms, early-pay discounts, and approval pipeline
- [x] "What-if" scenarios — impact of paying early vs. on-time vs. late
- [x] Cash position dashboard with AP commitments overlay
- [x] Alert when projected outflows exceed thresholds
- [x] Integration with bank balance data for complete cash picture — bring-your-own opening balance (query param or `Organization.settings.cashflow.opening_balance`) PLUS optional auto-sync from the org's configured payment/banking provider via the new `PaymentAdapter.get_balance` capability (`services/cashflow.fetch_provider_balance`; best-effort, falls back to BYO on unsupported/failure; mock returns a deterministic balance for local-first dev). A real banking-aggregator feed (Plaid-style) is the only deferred piece.
- [x] Persisted alert thresholds — `Organization.settings.cashflow.min_balance_threshold` via `GET/PUT /api/analytics/cash-position-settings` (no migration), read by `cash_position` when the request passes no override.
- [x] Export forecasts for CFO reporting (CSV via `/api/analytics/export/cashflow_forecast`)

---

### AI Cash-Flow Copilot
**Status:** Phases 1–2 shipped (read-only cash Q&A + `/cash-flow` copilot; proposed payment plans via `propose_payment_plan` + the display-only plan card); Phase 3 (draft-only enactment) planned — see [cash-flow-copilot.md](cash-flow-copilot.md).
**Open:** Phase 3 (draft-only enactment) plus the deferred bucket — saved plans / plan-vs-actual, opening-balance provenance, consolidated cross-entity mode, shortfall-alert sweep. *(c) sized, unstarted — nothing blocks it.* The largest genuinely open feature in this file. Tracked in [followups.md](followups.md).

A **beyond-parity** differentiator, not a competitive gap: a natural-language,
forward-looking copilot that answers "when do I run low on cash?" and "what
should I pay early / on time / defer, and what does it cost me?", and can
*propose* a cash-constrained payment plan. It builds directly on the shipped
primitives above — pairing the conversational assistant (tenant isolation,
audit, budgeting, local-first mock/ollama/claude adapters) with the cash-flow
forecast, the payment-timing what-if, and the ROI-ranked discount optimizer.
No mid-market competitor pairs a conversational interface with a cash-constrained
early-pay optimizer.

**Hard boundary: the copilot never moves money.** The LLM only turns NL into a
typed tool call and narrates the result — every dollar figure comes from an
existing deterministic pure function (`bucket_outflows`, `compute_cash_position`,
`apply_payment_timing_scenario`, `discount_optimizer.optimize`), so answers are
exact and reproducible under the `mock` adapter. Its most privileged write is
staging a **draft** payment run (existing idempotent, CFO-gated path); funding
stays behind the unchanged human review + CFO gate + segregation.

- [x] Phase 1 — read-only cash Q&A: four new entity-scoped, finance-leader-gated (`admin`/`ap_manager`/`cfo`, not `ap_clerk`) planning tools (`get_cashflow_forecast`, `get_cash_position`, `run_payment_whatif`, `optimize_discount_capture`) registered alongside the existing assistant tools; `/api/cash-flow/copilot(+/stream)` façade; `/cash-flow` chat + cash-position chart. Money as exact strings (must NOT inherit the analytics endpoints' `float()` coercion). Gated by `FEOH_CASHFLOW_COPILOT_ENABLED` (default on); `FEOH_CASHFLOW_COPILOT_DEFAULT_HORIZON_DAYS` (default 90).
- [x] Phase 2 — proposed plans: `propose_payment_plan` tool assembles a plan artifact (period-by-period schedule + discounts to capture + resulting cash curve) from the pure functions (`bucket_outflows`/`compute_cash_position`/the discount optimizer's own selection, reused not re-derived — same source of truth as `optimize_discount_capture`); a selected discount re-times onto its `pay_by` period at its discounted outlay when it matches a single commitment row, otherwise it's flagged `unretimed_offer_ids` rather than misrepresenting the curve. Display-only plan-card UI (`PlanCard.svelte`) — no enact affordance yet (Phase 3). See `services/cash_flow_plan.py`.
- [ ] Phase 3 — draft-only enactment: `POST /api/cash-flow/plans/{id}/draft-run` (idempotent draft run, execute stays CFO-gated) + `.../capture-discounts` (status-only accept), human-confirmed + audited.
- [ ] Deferred: saved plans / plan-vs-actual (`CashPlan` model + migration), opening-balance provenance surfacing, consolidated cross-entity mode, proactive shortfall-alert sweep.

**Competitors:** none pair NL + cash-constrained early-pay optimization; Coupa/Basware have cash analytics without a conversational copilot.

---

## Priority 10: Compliance & E-Invoicing

### Sanctions & Vendor Risk Screening
**Status:** Shipped — screening on vendor create/update, periodic re-screen sweep, payment-block gate, adverse-media support, composite risk scoring, Dow Jones/Refinitiv/ComplyAdvantage adapter skeletons, the append-only screening trail, and the dedicated review-queue page (`/vendors/screening`) all landed. Real-provider wiring (live keys) is the remaining deployment work.
**Open:** Live-provider wiring. The ComplyAdvantage / Dow Jones / Refinitiv adapters are fail-closed skeletons; `mock` is the local-first default and the screening path itself ships and is tested. *(a) blocked on provider keys.* Tracked in [followups.md](followups.md).

Tipalti, Coupa, Medius, and Basware all screen vendors against sanctions lists. Required for financial services, government contractors, and regulated industries. See `backend/docs/vendor-risk-screening.md`.

- [x] OFAC/SDN sanctions screening on vendor creation and update — `services/vendor_screening.screen_vendor_record` runs on `POST`/`PATCH /api/vendors` (best-effort, savepoint-isolated so a provider failure never blocks the vendor write) + manual `POST /api/vendors/{id}/screen`. Gated by `FEOH_VENDOR_SCREENING_ENABLED` (default on; mock-safe local-first).
- [x] Ongoing monitoring — re-screen vendors periodically (daily/weekly) — `services/vendor_rescreen.py` background sweep (mirrors `contract_renewal`): re-screens active vendors whose `last_screened_at` is stale per `FEOH_VENDOR_RESCREEN_AFTER_DAYS`. Disabled by default (`FEOH_VENDOR_RESCREEN_ENABLED`).
- [x] Flag and block payments to sanctioned entities — a `match` sets `vendors.payments_blocked`; `check_payment_compliance` refuses a blocked vendor up front (before FX lock / any adapter call). Manual `POST /api/vendors/{id}/block` \| `/unblock`.
- [x] Adverse media screening — `ScreeningResult.categories` (`("adverse_media",)`); mock fixtures + provider adapters surface adverse-media hits via the same path (list NAME `ADVERSE_MEDIA`).
- [x] Vendor risk scoring (sanctions + fraud signals + payment history) — `services/vendor_risk_scoring.py` blends latest sanctions check + open `fraud_flag` exceptions + trailing-12m payment history into a 0–100 composite + bucket (PII-free factors). `GET /api/vendors/{id}/risk`, `POST .../risk/recompute`, `GET /api/vendors/risk/summary`.
- [x] Integration with screening providers (Dow Jones, Refinitiv, ComplyAdvantage) — `sanctions_adapters/`: `mock` (default), `complyadvantage`, `dowjones`, `refinitiv` (skeletons — live key required, fail-closed without one). Selected per-org via `Organization.settings.compliance.sanctions.provider`.
- [x] Screening audit trail — log all checks and results — append-only `sanctions_checks` (every screen: initial/periodic/manual/pre_payment) + PII-free `vendor.screened` audit rows; `GET /api/vendors/{id}/screening-history` + `GET /api/vendors/screening/review-queue`.

**Competitors:** Tipalti (OFAC/SDN built-in), Coupa (community risk), Basware (sanctions + fraud module), Medius (fraud intelligence)

---

### Automated E-Invoicing
**Status:** Inbound + outbound UBL 2.1 shipped (parse + generate, auto-detect, schema validation, country VAT/GST/IVA tax validation); PEPPOL AS4 **outbound send AND inbound receive** shipped (hosted Access Point adapter, mock default, idempotent transmission log; inbound webhook dedupes redeliveries by AS4 MessageId and routes to the einvoice extractor). Country-specific outbound formats — **FatturaPA (IT), CFDI 4.0 (MX), NF-e (BR), DIAN (CO)** — shipped as pure local-first generators + national validation, wired into the export route via `?format=` and registered behind `e_invoice/country_formats/`; **live government clearance** (SdI / SAT-PAC / SEFAZ / DIAN authorization) is the one remaining deferral, tracked per-format below.
**Open:** Live government clearance — SdI (IT), SAT-PAC (MX), SEFAZ (BR), DIAN (CO). Generators and national validation ship as pure local-first code. *(a) blocked on per-country registration.* Tracked in [followups.md](followups.md).

Support structured electronic invoice formats required in the EU, Australia, and other regions. Inbound parsing is pure/local-first (no network, no SaaS key) and on by default; see `backend/docs/e-invoicing.md`.

- [x] Factur-X / ZUGFeRD — hybrid PDF/XML format (EU standard): embedded CII XML extracted from PDF/A-3 and parsed
- [x] UBL (Universal Business Language) 2.1 — **parse + generate** (PEPPOL BIS Billing 3.0 payload). `generate_ubl(doc) -> bytes` is the exact inverse of the parser; round-trip property `parse_ubl(generate_ubl(doc)) == doc` holds on core fields
- [x] Auto-detect format on upload — structured data parsed instead of OCR (`extraction.run_extraction` choke point routes to the `einvoice` adapter at confidence 1.0)
- [x] Validate against schema — malformed e-invoices rejected with clear field-level errors (EN 16931 structural subset)
- [x] UBL 2.1 **generate** (outbound) — reuses `EInvoiceDocument` via `mapper.invoice_to_einvoice_document`; `GET /api/invoices/{id}/einvoice` (role-gated AP export, 422 on tax-invalid) + `GET /portal/invoices/{id}/einvoice` (vendor-scoped supplier download).
- [x] CII (UN/CEFACT Cross-Industry Invoice, D16B) **generate** (outbound) — `e_invoice/generate_cii.py::generate_cii(doc) -> bytes`, the exact inverse of the CII parser (`cii.py`); emits `rsm:CrossIndustryInvoice` with the `ram:`/`udt:` namespaces (ExchangedDocument + SupplyChainTradeTransaction). Round-trip `parse_cii(generate_cii(doc)) == doc` on core fields. Wired into the AP export route as `?format=cii` (built-in path alongside `ubl`, shares the same `assert_valid` 422-on-tax-invalid guard, format-tagged filename). Pure / local-first, no model change. This is the Factur-X / ZUGFeRD payload dialect — embedding the CII into a PDF/A-3 is the one remaining deferral (own slice; trigger: a corridor that mandates the hybrid PDF, not bare CII XML).
- [x] Peppol BIS Billing 3.0 — **receive and send** via Peppol network shipped. **Send:** `POST /api/invoices/{id}/peppol-send` (reuses the UBL generator; `PEPPOL_BIS_BILLING_DOCTYPE`/`PROCESSID` constants). **Receive:** `POST /api/peppol/inbound/{tenant_slug}` (public-by-design, HMAC-gated webhook; dedupes redeliveries by AS4 MessageId via the `uq_peppol_message_id` index; parses the inbound UBL with the existing `e_invoice` parser, creates the Invoice, and hands to `dispatch_extraction` → the `einvoice` adapter). Reuses the `PeppolTransmission.direction`/`message_id` columns, `ParticipantId`, and `webhook_security`
- [x] FatturaPA — Italian e-invoicing format. `e_invoice/country_formats/fatturapa.py` generates the `FatturaElettronica` v1.2 (`FPR12`) document (DatiTrasmissione + Cedente/Cessionario header, DatiGeneraliDocumento/DatiBeniServizi/DatiRiepilogo body) + national validation (seller **and** buyer Partita IVA required). *Deferred: SdI transmission + the `.p7m` (CAdES) digital signature — own slice; trigger: first IT customer going live.*
- [x] CFDI 4.0 — Mexican e-invoicing. `country_formats/cfdi.py` generates `cfdi:Comprobante` v4.0 (Emisor/Receptor RFC, Conceptos, Impuestos) + national validation (emisor **and** receptor RFC required). *Deferred: SAT-PAC stamping → `Sello`/`Certificado`/`tfd:TimbreFiscalDigital` UUID (folio fiscal) — own slice; trigger: first MX customer going live.*
- [x] NF-e / NFS-e — Brazilian electronic invoicing. `country_formats/nfe.py` generates `NFe/infNFe` v4.00 (ide/emit/dest/det·prod/total·ICMSTot) + national validation (emit CNPJ required). *Deferred: SEFAZ authorization → 44-digit chave de acesso + protocolo + digital signature (a deterministic placeholder `Id` is emitted meanwhile); municipal NFS-e schema — own slice; trigger: first BR customer going live.*
- [x] DIAN — Colombian e-invoicing. `country_formats/dian.py` generates DIAN-profiled UBL 2.1 (`CustomizationID=10`, DIAN `ProfileID`, `UBLExtensions` placeholder) + national validation (supplier NIT required). *Deferred: CUFE + XAdES signature + `dian:DianExtensions` injected at clearance — own slice; trigger: first CO customer going live.*
- [x] Access point / PEPPOL AS4 gateway integration — **send and receive** shipped (`services/peppol_adapters/`: mock in-process default + `as4_gateway` real adapter talking to a hosted AP's HTTP API; SMP/SML resolution behind `resolve_participant`; SBDH wrapping in the adapter, not the generator). Inbound delivery: both adapters implement `parse_inbound`; the AP's inbound POST is verified (`FEOH_PEPPOL_INBOUND_SIGNING_SECRET`) and deduped at the receive webhook. See `backend/docs/peppol.md`
- [x] Country-specific tax validation (VAT, GST, IVA) — `e_invoice/tax_rules.py`: per-country tax-ID format (EU/GB VAT, AU ABN, NZ/IN/CA GST, MX/ES/IT IVA), rate plausibility per regime, zero-rate/reverse-charge handling. Pure, PII-free `FieldError`s; wired into inbound `validate_document` + the outbound export guard

---

### Accessibility (WCAG 2.2 AA / EU EAA / ADA)
**Status:** Shipped — WCAG 2.2 AA adopted as the conformance target across web, supplier portal, and Flutter mobile; baseline fixes landed across the shared component library + every route, automated guards (`axe-core` + a navigability/reflow/focus-trap spec on web, `meetsGuideline` widget tests on mobile) lock against regressions, and a conformance statement + VPAT/ACR are published. The structural follow-ups are all closed (shared `focusTrap` action on every dialog, keyboard step-reorder in the workflow builder, `autocomplete` tokens, 320px reflow). The one remaining item is the **manual screen-reader device pass** (VoiceOver / NVDA / TalkBack), now a documented repeatable procedure (`docs/accessibility-screen-reader-checklist.md`) — it needs real AT hardware so it can't run in CI. See `docs/accessibility.md` + `docs/accessibility-vpat.md`.
**Open:** The manual screen-reader device pass (VoiceOver / NVDA / TalkBack). The procedure is documented and repeatable; automated axe-core + `meetsGuideline` guards ship. *(a) blocked on real AT hardware — cannot run in CI.* Tracked in [followups.md](followups.md).

Legally required, not optional: the **EU Accessibility Act** is in force (June 2025), and US ADA Title III + Section 508 apply to enterprise buyers.

- [x] Adopt **WCAG 2.2 AA** as the conformance target across web (SvelteKit), mobile (Flutter), and the supplier portal; publish a VPAT/ACR — `docs/accessibility.md` (conformance statement) + `docs/accessibility-vpat.md` (VPAT 2.5 criterion table)
- [x] Web baseline — skip link + named landmarks, global `:focus-visible` ring, `Modal` focus trap/restore (no keyboard traps), form-label + error association (`aria-describedby`/`aria-invalid`), `aria-live` on async + toast surfaces, `prefers-reduced-motion` blanket, AA contrast on `StatusBadge`/`ScreeningBadge`/charts. Shared `lib/components/` carry the baseline so route pages inherit it
- [x] Audit-and-fix pass route by route (shared `lib/components/` first so fixes propagate); findings driven to closure. The two items first deferred are now **done**: (a) keyboard step-reorder in the workflow-builder canvas — per-node Move ↑/↓ buttons over `onreorder` (WCAG 2.5.7), covered by `workflow-builder.spec.ts`; (b) the four hand-rolled modal shells (`InvoiceModal`, `RunDetailModal`, `BulkRecodeGLModal`, portal `discount-offers`) now get shared focus trap/restore via the reusable `$lib/actions/focusTrap` action. Plus `autocomplete` tokens (1.3.5) and 320px reflow (1.4.10) closed + guarded
- [x] Automated regression guard — `axe-core` assertions wired into the Playwright e2e suite (`tests-e2e/a11y/`, auto-run in the standard glob so a regression fails CI) + Flutter `meetsGuideline` semantics/contrast/tap-target widget tests (`mobile/test/a11y/`)
- [x] `prefers-reduced-motion` respected (global app.css rule + component-scoped guards; mobile uses default Material transitions which honor the platform setting). Manual **screen-reader pass** (VoiceOver / NVDA / TalkBack) on the invoice → approve → pay flow + supplier portal is the tracked outstanding VPAT item (the supporting semantics — labels, roles, live regions — are in place)

**Competitors:** enterprise suites (Coupa, SAP Ariba, Basware) ship VPATs; a clean ACR is increasingly a procurement gate, especially for public-sector + EU buyers

---

## Priority 13: Platform Expansion (adjacent markets)

These features expand beyond core AP automation into broader spend management. Airbase and Coupa win mid-market deals by offering all-in-one spend platforms. Consider these only after core AP gaps are closed.

### Platform Billing & Metering
**Status:** First slice shipped (model + rollup + adapter + entitlements + read endpoint); later slices planned.
**Open:** The live-Stripe plan-change UI (`billing/+page.svelte:302`, deliberately disabled). The backend `POST /api/billing/change-plan` ships — Decimal-exact proration, idempotent, audited. *(a) blocked on a provisioned Stripe account; testable against the `mock` adapter first.* Tracked in [followups.md](followups.md).

The product meters extraction usage (`ExtractionUsage`, `CardRebate`) but had no way to **bill** for the SaaS itself — plans, subscription state, usage rollups, invoices to customers. Needed before commercial launch beyond hand-managed contracts. The first slice productizes the existing meters; live Stripe + dunning + the customer billing UI are next.

- [x] Plan / subscription model (control-plane) — `Plan` (tier, monthly price `Numeric`, per-seat + usage components JSON, feature entitlements JSON, trial_days) + `Subscription` (org FK, plan FK, status `trialing|active|past_due|canceled`, period + trial window, nullable `external_subscription_id`). Migration 0056 (control-plane, idempotent); both in `CONTROL_TABLES`. See `backend/docs/billing.md`
- [x] Usage rollup — `services/billing/usage_rollup.py` aggregates `ExtractionUsage` (+ `CardRebate` total) into Decimal-exact billable meters per org/period (pure read, no mutation). Payment-volume + per-meter overage pricing are later slices
- [x] Billing adapter family (`services/billing_adapters/`) — `mock` default (local-first, deterministic) + `stripe_billing` skeleton (live key via sops, **fail-closed**; `parse_webhook` implemented end-to-end with HMAC verify; the actual Stripe API calls are documented skeletons). Registry decorator + `get_billing_adapter()`; `FEOH_BILLING_PROVIDER` + per-org override. The webhook **route** (dedupe-by-event-id + 204-silent) and live API calls + dunning are later slices
- [x] Entitlement gating — `require_entitlement` (JWT) / `require_api_entitlement` (API key) in `deps.py`, 402 on a plan miss, composes with `require_roles` / `require_api_scope`; wired onto the public `/api/v1` surface (`public_api` feature). Reads `services/billing/entitlements.py`
- [x] Customer-facing read endpoint — `GET /api/billing/subscription` (admin/cfo): current plan + status + usage-to-date
- [x] Live Stripe Billing API calls (create/get subscription, report usage) + the inbound webhook route (HMAC-verified, deduped) + dunning / past-due automation — `stripe_billing` adapter's create/get-subscription + report-usage now hit the Stripe REST API via `httpx` (idempotency-key header on create, exact decimal-string usage values, fail-closed without a key); `POST /api/billing/webhook/{provider}` verifies the HMAC + dedupes by `event_id` + drives the idempotent `Subscription` lifecycle transition (`trialing→active→past_due→canceled`) with an append-only audit row, 204-silent on every rejection; the `billing_dunning` sweep cancels subscriptions overdue past the grace window (never moves money). `FEOH_BILLING_WEBHOOK_ENABLED` / `FEOH_BILLING_DUNNING_ENABLED` kill switches. See `backend/docs/billing.md`
- [x] Per-org Stripe customer/price provisioning + mid-period proration + plan change — `ensure_customer` / `ensure_price` (idempotent Stripe creates; minor-units via exact Decimal) + `services/billing/provisioning.provision_org_billing` resolves-and-persists the per-org `stripe_customer_id` + per-plan `plan_price_ids` on `settings.billing` (no migration), so `create_subscription` succeeds with the resolved ids (still fail-closed without a key). `services/billing/proration.compute_proration` is a pure Decimal-exact mid-period proration (`ROUND_HALF_UP`, 2 dp); `POST /api/billing/change-plan` (admin/cfo) repoints the live subscription, records the proration, and writes an append-only `billing.plan_changed` audit row — idempotent (same-plan retry is a no-op, never double-charges) and never moves money directly. See `backend/docs/billing.md`
- [x] Payment-method endpoint — adapter `create_setup_intent(customer_id)` → `ProviderSetupIntent` (single-use `client_secret`; no charge, no PAN) + `list_payment_methods(customer_id)` → `ProviderPaymentMethod` **PII-safe metadata only** (brand/last4/exp — **never a PAN**) capabilities (base supplies safe `None` / `[]` defaults): `mock` returns a deterministic SetupIntent + a deterministic `visa ****4242`; `stripe_billing` POSTs `/v1/setup_intents` + GETs `/v1/payment_methods?type=card`, fail-closed without a key. `POST /api/billing/payment-method/setup-intent` (returns the `client_secret` to start adding a card) + `GET /api/billing/payment-methods` (list saved cards) — both admin/cfo, read `settings.billing.stripe_customer_id`, and degrade gracefully (no customer / unconfigured → `configured=false` + null secret / empty list, never a 500). `backend/tests/test_billing.py`. The payment-method UI (frontend track) now ships (see the Customer-facing billing surface item below). See `backend/docs/billing.md`
- [x] Billing invoices / receipts list — adapter `list_invoices(customer_id, limit)` capability (base supplies a safe `[]` default) returning the org's past billing invoices/receipts as `ProviderInvoice` DTOs (id, number, period, `amount` exact decimal **string**, currency, status `paid|open|void`, hosted URL, created date): `mock` fabricates a deterministic run of monthly `$49.00` receipts (or `[]` with no customer); `stripe_billing` GETs `/v1/invoices?customer=…` and normalizes (minor-units → exact Decimal string; status map), fail-closed without a key. `GET /api/billing/invoices` (admin/cfo) reads `settings.billing.stripe_customer_id`, returns the list with money as exact strings, and degrades gracefully — no customer / unconfigured / provider error → empty list, never a 500. `backend/tests/test_billing.py`. See `backend/docs/billing.md`
- [x] Customer-facing billing surface (UI) — **read/display slice shipped**: `/billing` route (Subscription sub-tab of the Billing nav group, admin/cfo-gated) shows the current plan + price (`<Money>`), a `SubscriptionBadge` status pill, the period/trial window, granted entitlements, and usage-to-date meters from `GET /api/billing/subscription` (via `$lib/api/billing.ts`); loading / error / empty states handled. The **invoices/receipts list** is shipped — a section on `/billing` (`DataTable`: number, period, `<Money>` amount + currency, paid/open/void status pill, created date, and a new-tab "View" link when the provider supplies a `hosted_url`) over `GET /api/billing/invoices` (via `getBillingInvoices` in `$lib/api/billing.ts`), with its own loading / error / empty ("No invoices yet.") states, loaded independently of the plan/usage block. The **payment-method UI** is now shipped too — a **Payment methods** `DataTable` (PII-safe `Brand ····last4` / `Expires MM/YYYY` / `Default` pill — never a PAN) over `GET /api/billing/payment-methods` (`getBillingPaymentMethods`), plus an **Add / replace card** flow over `POST /api/billing/payment-method/setup-intent` (`startBillingSetupIntent`): `configured=false` → a clear "billing not configured" state, a returned `client_secret` → a "ready" state with a clearly-marked **deployed-only Stripe Elements seam** (no Stripe keys hardcoded, the static frontend never calls a secret-bearing service), re-listing cards after the flow — its own loading / error / empty ("No payment method on file.") states, loaded independently of the plan/usage block. Live-Stripe **plan change** stays a disabled "contact us" affordance (it rides the live-Stripe plan-change path — a later frontend slice). e2e: `tests-e2e/billing/billing.spec.ts`. See `backend/docs/billing.md` § Customer-facing UI

**Competitors:** standard SaaS monetization; the metering primitives (`ExtractionUsage`) already exist — this productizes them

---

### White-Label / Partner Branding
**Status:** In progress (per-tenant brand config + frontend CSS-var theming shipped; branded outbound PDFs + emails shipped; custom-domain tenant resolution shipped — vanity-host → tenant mapping on `settings.brand.custom_domains`, JWT cross-check preserved; custom-domain admin UI + endpoint pair shipped — `GET/PUT /api/organization/branding/custom-domains`, admin-mutate, normalized + cross-org-unique + audited, with the `/organization` Custom Domains panel; **partner/reseller admin shipped** — `Organization.parent_org_id` (migration 0065) + admin-gated `/api/partner` list/read/write child branding, SQL-layer-scoped to the caller's children, + the `/admin/partner` panel; **link provisioning shipped** — two-sided-consent attach (child mints an HMAC-signed single-use link code; partner redeems it) + scoped detach, `FEOH_PARTNER_LINK_SIGNING_KEY` fail-closed; **new-tenant provisioning shipped** — `POST /api/partner/children/provision` wraps `provision_tenant` + stamps `parent_org_id` + audits both trails, with the `/admin/partner` "Create child tenant" modal; only the TLS/DNS provisioning runbook for the new tenant's vanity domain remains deferred)
**Open:** The TLS/DNS provisioning runbook for a partner-provisioned child tenant's vanity domain. Provisioning and the custom-domain resolver both ship. *(b) operator step on merged code; durable fix is a runbook under `docs/founder-runbooks/`.* Tracked in [followups.md](followups.md).

Per-tenant theming so resellers, banks, and ERP partners can offer the platform under their own brand — a common mid-market distribution channel and an enterprise procurement ask.

- [x] Per-tenant brand config — logo, accent/theme tokens, product name, support + legal links on `Organization.settings.brand` (no migration), `GET/PUT /api/organization/branding` (admin mutate, audited, hex/URL-validated). Frontend `brand` rune store applies `--accent`/`--accent-strong` CSS custom properties on mount (org colors override the AA defaults only when set), logo + product name in the sidebar + `<title>`, edited from the Organization → Branding panel. See `docs/white-label.md`
- [x] Custom domain / subdomain support beyond `*.localhost` tenant routing — backend resolution layer shipped: a tenant can register vanity hostnames on `settings.brand.custom_domains` (JSON, no migration); `app/tenant.py::get_tenant_slug` falls back to matching the request `Host` against that list when no `X-Tenant-Slug` header is present (`resolve_tenant_slug_by_custom_domain` + `normalize_custom_domain`, JSONB `@>` lookup). The resolved slug is only a **candidate** — `get_tenant`'s JWT `org`-claim cross-check still gates it, so a forged `Host` can't widen access (invariant #4 preserved); unknown/malformed host falls back to the original 400. **Admin UI + endpoint pair now shipped**: `GET/PUT /api/organization/branding/custom-domains` (read open to any authed user, admin-only mutate; each host normalized through the resolver's own `normalize_custom_domain`, de-duped, **cross-org-unique** — a host claimed by another tenant is 409 — and audited `organization.custom_domains_updated` PII-free count-only; a branding save preserves the list) + the Organization → Custom Domains panel (list / add / armed-remove). TLS + DNS provisioning stays infra (out of scope for app code; runbook deferred). See `docs/white-label.md § Custom domains` + `docs/multi-tenancy.md`
- [x] Branded outbound surfaces (PDFs + emails) — remittance / 1099 / SOX-audit PDFs and outbound transactional emails carry the tenant product name + logo + accent (resolved through one `services/branding.get_brand_context` helper; PDF logo embed is size/time-bounded + fail-soft to product-name text; email From display name + HTML header + support footer applied in the shared email-adapter base). **Analytics report exports now branded too**: `GET /api/analytics/export/{report}` (invoice_register / vendor_spend / payment_register / aging_snapshot / cashflow_forecast) takes `format=csv|pdf` — the PDF (`services/analytics_report_pdf.py`) draws the same branded header (logo / product-name / accent) via the shared `build_logo_flowable` (size/time-bounded + fail-soft to text), and the CSV prepends a `#`-comment brand-provenance block (product name + org + report + generated-at) ahead of the unchanged, still-column-positional data grid (`report_export.brand_provenance_header`). See `docs/white-label.md`. **Supplier-portal theming now ships too**: the supplier portal (`/portal/*`, including the unauthenticated login page) carries the tenant brand (accent + logo + product name + `<title>`) via the public-by-design, PII-free `GET /api/portal/branding` (tenant resolved by the existing `get_tenant` chokepoint; returns only the whitelisted `BrandConfig` fields) + the `portalBrand` store. See `docs/white-label.md` § Supplier-portal theming. (The localized email catalogue remains.)
- [x] Partner/reseller admin — a parent org administers a set of branded **child** tenants. Control-plane self-FK `Organization.parent_org_id` (migration `0065_org_parent`, control-plane-only); "partner" is **derived** (referenced by ≥1 child — can't be self-claimed). `/api/partner` (admin-gated) lists children + reads/writes each child's `settings.brand`, scoped at the SQL layer to `parent_org_id == caller org` (the `get_tenant` org-claim cross-check still gates; a non-child id is an opaque 404 — no enumeration); child-branding writes preserve `custom_domains` + audit into the **child's** trail PII-free. Frontend `/admin/partner` panel. **Link provisioning shipped (round 2):** attaching an *existing* tenant uses **two-sided consent** — the prospective child's own admin mints a short-lived HMAC-signed single-use link code (`POST /api/partner/link-code`), the partner's admin redeems it (`POST /api/partner/children`); a partner can't forge a code or adopt a non-consenting org, no re-parent without detach, single-use via Redis `jti`, `DELETE /api/partner/children/{id}` detaches (scoped to own children), both mutations audited PII-free on both org trails, gated by `FEOH_PARTNER_LINK_SIGNING_KEY` (fail-closed, no fallback; non-secret dev value committed). No migration (stateless token + existing column). **New-tenant provisioning shipped (this slice):** `POST /api/partner/children/provision` (admin-only) is the thin wrapper over `services/tenant_provisioning.provision_tenant` that creates a brand-**new** tenant already parented to the caller — no `parent_org_id` input (always the caller's org, so a partner can only create a child under itself), validates slug (format/reserved/availability) + admin-email shape like signup, reuses `provision_tenant`'s orphan-DB rollback (clean 409 on a slug race, never a half-create), audits `partner.child_provisioned`/`partner.parent_linked` PII-free on both trails, and returns a one-time temp password for the new admin. `/admin/partner` "Create child tenant" modal drives it; no migration. The TLS/DNS automation for the new tenant's vanity domain stays an infra-owned follow-up. See `docs/white-label.md § Partner / reseller admin`

**Competitors:** AvidXchange + several bank-channel AP products ship white-label; a distribution lever more than a feature

---

