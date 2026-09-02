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
pnpm test:unit        # vitest unit tests (i18n parity, pure helpers, the
                      # stylesheet colour-token/contrast guard)
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
| `/profile` | `routes/profile/+page.svelte` | `POST /api/auth/mfa/enroll`, `POST /api/auth/mfa/enroll/verify`, `POST /api/auth/mfa/disable` — manage two-factor (TOTP); `GET /api/auth/mfa/passkey`, `POST /api/auth/mfa/passkey/register[/verify]`, `DELETE /api/auth/mfa/passkey/{id}` — manage passkeys (via `auth.{listPasskeys,registerPasskey,deletePasskey}` + `$lib/webauthn.ts`); `POST /api/auth/mfa/step-up/passkey` — **passkey step-up** (via `auth.passkeyStepUp(operation)`, which reuses the same `performAuthentication` browser ceremony as passkey login). Every factor change needs a step-up once a factor is live; the page sends the typed password when there is one and otherwise runs the passkey ceremony — the only route open to an SSO-only account, which has no password to type. **Signed-in devices** card — `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{jti}`, `POST /api/auth/sessions/revoke-others` (via `auth.{listSessions,revokeSession,revokeOtherSessions}`): the caller's live sessions with device label / IP / sign-in method, the current one marked **This device** and deliberately not sign-out-able from the row, per-session sign-out + "Sign out everywhere else", both armed two-click. No step-up — these only ever remove access. See `docs/authentication.md` § Self-service session visibility + revocation |
| `/change-password` | `routes/change-password/+page.svelte` | `POST /api/auth/change-password` |
| `/invoices` | `routes/invoices/+page.svelte` | `GET /api/invoices` (returns `priors_summary`), `GET /api/invoices/counts` (status-chip tallies), `GET /api/invoices/{id}` (`?id=` deep-link opens the detail modal), `POST /api/invoices/upload` (supports multi-file; frontend batches 5 at a time via `Promise.allSettled`), `PATCH /api/invoices/{id}`, `GET /api/invoices/{id}/priors`, `GET /api/invoices/{id}/summary` (audit-log summary; `POST .../summary/regenerate` for admins/managers), supplier chat — `GET/POST /api/invoices/{id}/chat`, `POST .../chat/attachments`, `POST .../chat/{resolve,reopen}`, `GET /api/invoices/chat/templates`, `GET /api/invoices/{id}/chat/file/{key}` (via `$lib/api/supplierChat.ts`, surfaced in `InvoiceModal`), bulk ops |
| `/vendors` | `routes/vendors/+page.svelte` | `GET /api/vendors`; sanctions screening + risk (via `$lib/api/vendors.ts`) — `POST /api/vendors/{id}/screen`, `GET /api/vendors/{id}/screening-history`, `POST /api/vendors/{id}/{block,unblock}`, `GET /api/vendors/{id}/risk` + `POST .../risk/recompute`, `GET /api/vendors/risk/summary`, `GET /api/vendors/screening/review-queue`. Row screening/risk pill (`ui/ScreeningBadge.svelte`); clickable rows open `modals/VendorModal.svelte` (Screening & Risk panel + history timeline; re-screen / recompute / block-unblock gated to admin + ap_manager via `auth.isManager`). **Vendor consolidation** — a `vendor.manage`-gated (`auth.can`) **Merge duplicates** header action opens `modals/VendorConsolidationModal.svelte` over `$lib/api/vendors.ts` (`getVendorConsolidationSuggestions` / `mergeVendorConsolidation` → `GET /api/enrichment/vendors/consolidation-suggestions`, `POST /api/enrichment/vendors/consolidation/merge`): per-cluster canonical-vs-duplicate diff, two-step-confirm merge (soft-retire-irreversible), surfaces the backend's 4xx refusals, refreshes the list. **Bank details are dual-control, and the page says where the change went** — the Bank row action's dialog `POST`s `/api/vendors/{id}/bank-change`, which STAGES a `VendorChangeRequest` rather than applying it, so the dialog carries a dual-control hint + a link and the toast names the queue (`vendors.bank.toast.submitted`). A **Bank change approvals** header action (`auth.isManager`, matching the queue's own role gate) links to `/vendors/change-requests`; without it the staged change had no reachable reviewer and vendor banking could not be updated through the app at all |
| `/vendors/screening` | `routes/vendors/screening/+page.svelte` | Sanctions-screening **review queue** (via `$lib/api/vendors.ts`) — `GET /api/vendors/screening/review-queue` (KPI row + flagged-vendor `DataTable` with the `ui/ScreeningBadge.svelte` pill, matched-list, risk score, last-screened). Clickable rows open a detail `Modal` over `GET /api/vendors/{id}/screening-history` (history timeline) with **block / unblock** + **re-screen** actions. Block/unblock is gated on the granular permission `vendor.block` via `auth.can(PERM_VENDOR_BLOCK)` (hidden for a non-holder — the backend enforces regardless); re-screen on `auth.isManager`. Sidebar link **Screening** (admin/ap_manager/cfo). Separate sub-route from the `/vendors` list page |
| `/vendors/change-requests` | `routes/vendors/change-requests/+page.svelte` | **Vendor bank / tax change-approval queue** — the UI half of the dual-control (BEC / bank-redirect) gate, and the only way vendor banking can be changed through the app at all: `/vendors` STAGES a `VendorChangeRequest` and a SECOND user applies it here. `GET /api/vendors/change-requests` (paginated, `?status=` — `pending` default, `approved`/`rejected`/`all`; the list payload MASKS the proposed value to a last-4, rendered via `maskedProposalSummary` in `$lib/types/vendor.ts`), `GET /api/vendors/{vendor_id}/change-requests` (the detail modal's REVEALED full value — the callback control an approver verifies before signing off; `revealedProposalFields` flattens it and falls back to raw JSON rather than risk dropping a field), `POST /api/vendors/change-requests/{id}/approve` \| `/reject` (both take an optional `{review_note}`). **Two different gates, reflected honestly**: the list is role-gated admin \| ap_manager (the backend `require_roles`; a CFO 403s, so the nav entry excludes cfo and the page redirects a non-manager), while **approve** is gated on the granular `vendor.bank_change.approve` via `auth.can(PERM_VENDOR_BANK_CHANGE_APPROVE)` — an ap_manager whose org split that duty away sees the queue with a disabled Approve and a standing explanation, never a button that can only fail. Reject stays role-gated (refusing a change moves no money). **Segregation of duties** is knowable client-side (`requested_by_user_id` vs `auth.user.id`), so the proposer's row says "You requested this" and disables Approve — and the 403 is *still* mapped to that specific message, because the UI is not the gate; 409 (already resolved) re-reads the queue. Approve/Reject are armed two-click `RowAction`s with the documented outside-click un-arm. Status `FilterChips` + Load-More footer + `createRequestSequencer` (a decision `supersedeInFlight()`s, else an in-flight list response puts the approved row back to `pending` on screen). Reached from the sidebar (**Bank Changes**), from the `/vendors` header action, and from the bank-details dialog's dual-control hint. See `backend/docs/vendor-risk-screening.md` |
| `/payments` | `routes/payments/+page.svelte` | `GET /api/payments/{queue,summary,runs/}`, `GET /api/payments`, `POST /api/payments/runs` (creates draft), `GET /api/payments/runs/{id}` + `POST .../execute` (via `RunDetailModal`). **Compliance hold** — a payment the sanctions/KYC gate parked at `pending_compliance` is a first-class status here (`$lib/types/payment.ts`: label + History filter chip + amber `.badge.pending_compliance`; an unlisted status would render a BLANK badge, which is how a held payment used to be invisible — drift-guarded by `src/lib/types/paymentStatus.test.ts`). Its two exits are wired as History row actions, each gated on the granular permission the backend requires: **Release** → `POST /api/payments/{id}/compliance/release` (`auth.can(PERM_PAYMENT_EXECUTE)` — re-runs the SAME compliance-then-adapter path, so the toast reports the *returned* status, "still on hold" when the check hasn't cleared; never a bypass) and **Dismiss** → `POST .../compliance/dismiss` (`auth.can(PERM_PAYMENT_VOID)`, required `{reason}`). Both go through one confirm-then-act `Modal` (`ariaLabel="Resolve compliance hold"`), mirroring the void dialog. `canVoid()` deliberately excludes `pending_compliance` — nothing reached the processor, so there is no rail to reverse, and only release/dismiss close the `payment_compliance_hold` exception. See `backend/docs/payments.md` § Financial-integrity exception gate. **Virtual-card reveal** — `GET /api/cards/{id}/details` returns `CardDetailsResponse` = `{card_number, exp_month, exp_year, cvv}` (`backend/app/schemas/virtual_card.py`), and FastAPI strips anything the response_model doesn't declare. The AP client must read THOSE names and build the card-face expiry as `MM/YYYY` from `exp_month`/`exp_year`; it must NOT reuse the supplier-portal reveal's shape (`{pan, expires_at, last_four}`), which arrives `undefined` and rendered a blank card number and no expiry. Don't widen the backend schema to suit the client — `backend/app/api/portal.py` records that reading `details.pan` was a prior break. Covered by `tests-e2e/payments/card-details-reveal.spec.ts` (the modal's three values carry `data-testid="card-details-{number,cvv,expires}"`) |
| `/discounts` | `routes/discounts/+page.svelte` | dynamic discounting (via `$lib/api/discounts.ts`) — `GET /api/discounts/dashboard`, `GET /api/discounts/offers`, `POST /api/discounts/offers/{id}/{accept,decline}`, `GET /api/discounts/invoices/{id}/roi`, `POST /api/discounts/optimize`. KPI row, status `FilterChips`, offers `DataTable` (tiers via `ui/DiscountTierBar.svelte`, accept-tier `Modal`), early-payment optimizer panel. admin/ap_manager/cfo |
| `/recurring` | `routes/recurring/+page.svelte` | Recurring / subscription invoice templates (via `$lib/api/recurring.ts`) — `GET /api/recurring` (list; `status`/`vendor_id`/`search`/`page` params), `POST /api/recurring`, `GET/PATCH/DELETE /api/recurring/{id}`, `POST /api/recurring/{id}/{pause,resume,end,generate-now}`, `GET /api/recurring/{id}/upcoming-schedule?count=`, `GET /api/recurring/{id}/history`. Under the **Billing** nav group. KPI row, status `FilterChips` (active / paused / ended), template `DataTable` with clickable rows, create/edit `Modal`; the detail modal shows the upcoming-schedule preview + generated-invoice history. A template the background sweep could not generate carries an amber **Not generating** badge beside its status, titled with the consecutive-miss count, the last skipped period and why (`last_skip` on the response; reason codes mapped by `skipReasonKey` in `$lib/types/recurring.ts`, drift-guarded by `recurring.test.ts` against the backend's own code list) — without it, months of missed subscription invoices read exactly like "nothing due yet". See `backend/docs/recurring-invoices.md` § A skipped period is never silent. Read all four roles; mutate gated to admin/ap_manager (`auth.isManager`) |
| `/billing` | `routes/billing/+page.svelte` | Platform billing & metering (read/display + plan-change) — `GET /api/billing/subscription` + `GET /api/billing/invoices` (via `$lib/api/billing.ts` → `getBillingSubscription` / `getBillingInvoices`, types in `$lib/types/billing.ts`). The AP platform's OWN customer subscription (control-plane), distinct from the customer AP money path. Surfaced as the **Subscription** sub-tab of the **Billing** nav group, admin/cfo-gated (clerk/manager redirected to `/`). Shows the current plan + price (`<Money>`), a `SubscriptionBadge` status pill (trialing/active/past_due/canceled), the period/trial window, granted entitlements, and usage-to-date `KpiCard`s; plus an **Invoices & receipts** section (`DataTable`: number, period, `<Money>` amount + row currency, paid/open/void status pill, created date, and a new-tab "View" link when the provider supplies a `hosted_url`) — loaded independently so a slow/failed invoices fetch doesn't block the plan surface, with its own loading / error / empty ("No invoices yet.") states. Also a **Payment methods** section (`DataTable` of saved cards — PII-safe `Brand ····last4` / `Expires MM/YYYY` / `Default` pill, **never a PAN** — over `GET /api/billing/payment-methods` via `getBillingPaymentMethods`) + an **Add / replace card** flow over `POST /api/billing/payment-method/setup-intent` (`startBillingSetupIntent`): `configured=false` → a "billing not configured" state, a returned `client_secret` → a "ready" state with a clearly-marked **deployed-only Stripe Elements seam** (no Stripe keys in the static frontend; never calls a secret-bearing service directly), re-listing cards after; its own loading / error / empty ("No payment method on file.") states. **Live plan-change** now ships too: "Change plan" opens a `Modal` plan picker over `GET /api/billing/plans` (`getBillingPlans`, cheapest first, current plan marked + non-selectable) → an "applies immediately, prorates the current period" notice (the backend has no preview-only mode) → `POST /api/billing/change-plan` (`changeBillingPlan`) on confirm → a result view rendering the REAL returned proration via `<Money accounting>` (or a clean "nothing changed" message when `changed: false`); closing it re-fetches the subscription. A "contact us" link stays alongside for anything outside the self-serve catalog. See `backend/docs/billing.md` § Customer-facing UI |
| `/vendor-statements` | `routes/vendor-statements/+page.svelte` | Vendor statement reconciliation (via `$lib/api/vendorStatementRecon.ts`) — `GET /api/vendor-statements` (list; `vendor_id`/`status`/`page` params), `POST /api/vendor-statements` (manual lines) + `POST /api/vendor-statements/upload` (**CSV or PDF**), `GET /api/vendor-statements/{id}` (detail+lines), `GET /api/vendor-statements/{id}/file` (the archived supplier document, via `downloadSourceStatement` → `api.downloadBlob`), `POST /api/vendor-statements/{id}/lines/{lineId}/resolve`, `DELETE /api/vendor-statements/{id}`, `GET /api/vendor-statements/close-readiness`. Under the **Billing** nav group. KPI row, status `FilterChips` (all / open / resolved), runs `DataTable` with clickable rows; the detail modal (`modals/VendorStatementReconModal.svelte`) shows the side-by-side statement-vs-ledger diff with per-line resolve/ignore. **Intake is an explicit radio choice** in the create modal — type the lines, or upload a CSV/PDF file — because both used to be on screen at once with "a file wins" as the tiebreak (typed lines vanished silently), and `notes` is only sent on the manual path. **A refused statement explains itself on the form**: the backend fails closed with a specific PII-free 422 (a scan with no text layer, a CSV with no usable header), and that message is the actionable half of the refusal, so it renders in a persistent `role="alert"` region (`[data-testid="statement-intake-error"]`) rather than a toast that fades; oversized files are caught client-side against the same 25 MB cap `storage.MAX_FILE_SIZE` enforces. The detail view carries the run's **provenance** — a source pill (typed / CSV / machine-read PDF) and, for a PDF run, the adapter + confidence + open items read off the document plus what the reader's skip-rather-than-guess rule means for the diff below it — and a **Download the source statement** control when `has_source_file`. Confidence goes through the pure `formatExtractionConfidence` (`$lib/types/vendorStatementRecon.ts`), which clamps + guards non-finite provider input; unit-tested in `vendorStatementRecon.test.ts`. Read all four roles; mutate gated to admin/ap_manager (`auth.isManager`). See `backend/docs/vendor-statement-reconciliation.md` § The UI |
| `/exceptions` | `routes/exceptions/+page.svelte` | `GET /api/exceptions`, `PATCH /api/exceptions/{id}`. The queue table carries **three distinct states**, not one: `loading` (initial + filter-change fetch), `errored`, and genuinely-empty — `empty={loading ? common.loading : errored ? exceptions.empty.errored : …}`, the same shape `/notifications` uses. It matters more here than anywhere else: "No open exceptions. Everything looks good!" is a substantive claim about open duplicates, fraud flags, payment-compliance holds and line-total mismatches, and it used to be rendered while the fetch was still in flight AND permanently after a failed one (there was only a `loadingMore` flag, for the Load-More button). Never reintroduce an empty message that outranks "we could not look". `DataTable`'s empty cell carries `data-testid="table-empty"` so the three states are assertable — `tests-e2e/exceptions/load-states.spec.ts` |
| `/workflows` | `routes/workflows/+page.svelte` | `GET /api/workflows`, `POST /api/workflows`; no-code builder management — `GET /api/workflows/templates`, `POST /api/workflows/from-template`, `GET/POST /api/workflows/{id}/versions`, `POST /api/workflows/{id}/restore/{versionId}`, `GET /api/workflows/{id}/versions/diff`, `POST /api/workflows/{id}/simulate`, `GET /api/workflows/{id}/export`, `POST /api/workflows/import` (via the `workflow-mgmt` modals) |
| `/workflows/[id]` | `routes/workflows/[id]/+page.svelte` | `GET/PATCH /api/workflows/{id}`, `GET /api/organization` — drag-and-drop builder canvas (`workflow-builder` components) |
| `/experiments` | `routes/experiments/+page.svelte` | A/B testing of workflow rules (via `$lib/api/experiments.ts`, types in `$lib/types/experiments.ts`) — `GET /api/experiments` (list), `POST /api/experiments`, `POST /api/experiments/{id}/{start,stop,conclude}`, `DELETE /api/experiments/{id}`, `GET /api/experiments/{id}/results`; loads workflow definitions via `GET /api/workflows`. Under the **Settings** nav group. Status `FilterChips` (all / draft / running / concluded), experiments `DataTable` with clickable rows opening a **results readout** `Modal` (winner / not-enough-data banner + per-variant metric table, primary-metric row highlighted), a create `Modal` (pick a definition — seeds both configs from its live steps — split %, primary metric, min sample, two JSON config editors), per-row start/stop/conclude/delete gated by status. Read managers/CFO; mutate gated to admin (`auth.isAdmin`; the backend 403s the rest). |
| `/audit` | `routes/audit/+page.svelte` | `GET /api/audit/export` (JSON + CSV) — SOX auditor console, admin/CFO only (content-gated on `auth.isCfo`; backend 403s otherwise). Date-range or by-invoice query + CSV download. |
| `/organization` | `routes/organization/+page.svelte` | `GET/PATCH /api/organization`, `GET/PUT /api/organization/branding`, `GET/PUT /api/organization/branding/custom-domains` (Custom Domains panel — list / add / armed-remove vanity hostnames), `GET/PUT /api/organization/data-residency` (**Data Residency** panel — GDPR/CCPA region pin + the backend's advisory `alignment` verdict rendered as a tinted box: green `aligned` / amber `misaligned` / muted `unknown`, with the standing "advisory only, nothing is blocked" line. Save enables only when the selection differs from what is persisted, and a refused save snaps the control back so a pin that was never made can't linger on screen. See `docs/data-residency.md` § The UI), `GET/PUT /api/organization/chat-notifications` + `PUT/DELETE .../webhook` (**Chat Notifications** panel, via `$lib/api/chatNotifications.ts`, types in `$lib/types/chatNotifications.ts` — enable/provider/per-event toggles driven entirely by the server's `supported_providers` / `supported_events` so the picker can't offer an adapter that doesn't exist, plus set / replace / armed-remove of the incoming-webhook URL. **The URL is write-only**: it is the credential for both real providers, no endpoint returns it, so the panel holds only the draft being typed (dropped the moment it's stored) and the server-reported `webhook_configured` + bare `webhook_host` — do not add a state field mirroring the persisted value. "Enabled for a real provider with no webhook stored" renders as a persistent amber advisory, because the adapter fails closed and silently posts nothing. See `backend/docs/notifications.md` § Rotating the webhook URL) |
| `/admin` | `routes/admin/+page.svelte` | **Users & Roles** page (`?tab=users` default \| `?tab=roles`). Users tab → `GET/POST/PATCH/DELETE /api/admin/users`; Roles tab → `GET/POST/PATCH/DELETE /api/admin/roles`. Bodies live in `$lib/components/admin/{UsersPanel,RolesPanel}.svelte`; the page derives the active panel from `?tab=` (`$page.url`) and owns the per-tab PageHeader action (calls the active panel's exported `openCreate()`). **Users + Roles are peer tabs in the sidebar's Settings section bar** (`layout/SectionTabs.svelte`), not a tab row inside the page — clicking them navigates to `/admin?tab=…`. `/admin/roles` redirects to `/admin?tab=roles` (back-compat). |
| `/admin/api-keys` | `routes/admin/api-keys/+page.svelte` | Developer-API key management (admin only — redirects non-admins, the backend 403s them) via `$lib/api/apiKeys.ts` (types in `$lib/types/apiKeys.ts`). `GET /api/api-keys` (list — prefix + scopes + created/last-used + Active/Revoked status), `POST /api/api-keys` (Create-key modal → a **one-time** copy-able plaintext reveal: "shown only once", never re-fetchable, dropped from memory on close), `DELETE /api/api-keys/{id}` (armed two-click Revoke, idempotent), `GET /api/api-keys/{id}/usage?window_days=` (per-key usage view modal — all-time + trailing-window totals + per-day breakdown, opened by clicking the key-name `RowLink`). Surfaced under the **Settings** nav group. Loading / empty / error states; never echoes the plaintext after the reveal closes. See `backend/docs/public-api.md` § API keys |
| `/admin/webhooks` | `routes/admin/webhooks/+page.svelte` | Outbound-webhook management (admin only — redirects non-admins, the backend 403s them) via `$lib/api/webhooks.ts` (types in `$lib/types/webhooks.ts`). `GET/POST /api/webhooks` (list + create; create's response is one of only two places the signing secret is returned — surfaced through `ui/SecretReveal.svelte`, then dropped), `PATCH /api/webhooks/{id}` (clickable row → edit modal), **`POST /api/webhooks/{id}/rotate-secret`** (Rotate-secret row action → a confirm dialog carrying the overlap picker → the replacement secret in the same one-time `SecretReveal`), `DELETE /api/webhooks/{id}` (armed two-click), `GET /api/webhooks/deliveries` (URL-backed `?status=` `FilterChips`) + `POST /api/webhooks/deliveries/{id}/redeliver`. **Rotation is the remedy for a leaked secret**: it keeps the subscription id and its whole delivery history, which Delete + re-create CASCADEs away — so the destructive option must never be the easier one during an incident. The overlap picker offers the backend's five accepted windows (`$lib/utils/webhookRotation.ts` — bounds mirrored from `services/webhooks/rotation.py`, since the backend 422s an out-of-range value rather than clamping), leading with `0` = "Compromised — cut over now", which reveals a red warning before it can be committed. Re-rotating while a window is still open reveals a second warning: the backend keeps only **one** previous-secret slot, so rotating again evicts the secret that window was protecting whatever window is picked. The dialog also refuses to close (Cancel / Esc / backdrop) while the POST is in flight — closing wouldn't cancel it, and the reveal is the only place the replacement is ever shown. While a window is open the row shows a `Previous secret until …` pill; it is **session-scoped** (`GET /api/webhooks` doesn't return `previous_secret_expires_at`, so a reload loses it — durable fix tracked in `docs/followups.md`) and self-expires via a 30s clock tick that also prunes closed windows, so the timer stops once nothing is in flight. Surfaced under the **Settings** nav group. See `backend/docs/public-api.md` § Outbound webhooks + § Rotating a signing secret |
| `/admin/partner` | `routes/admin/partner/+page.svelte` | **Partner / reseller multi-tenant admin** (admin only — redirects non-admins, the backend 403s them) via `$lib/api/partner.ts` (types in `$lib/types/partner.ts`). `GET /api/partner` (the partner's child tenants; a standalone org shows the "not a partner" empty state), `GET /api/partner/children/{id}/branding` + `PUT .../branding` (view/push a child's white-label brand — product name, logo, two accent colors, support/legal URLs — in a `Modal` with the same hex/URL validation as the org Branding panel; clickable `DataTable` rows). Surfaced under the **Settings** nav group. Loading / empty / error states. See `docs/white-label.md` § Partner / reseller admin |
| `/cfo` | `routes/cfo/+page.svelte` | `GET /api/analytics/{cashflow_forecast,cashflow_whatif,cash_position}`, `GET /api/analytics/export/cashflow_forecast` (admin + cfo) — predictive cash-flow dashboard. Also embeds **`CfoMetrics.svelte`** (`GET /api/analytics/cfo?period_days=`) — DPO + trend, cash conversion cycle, AP balance, accruals, supplier concentration (flagged-vendor banner), fraud-rate trend, rebate yield, and unrealized FX; a self-fetching component mirroring `ByEntityBreakdown`, below the forecast panels. See `backend/docs/analytics.md` § CFO metrics. And **`ScheduledReportsPanel.svelte`** — the admin surface for the scheduled-report runner (`GET/POST /api/analytics/scheduled-reports`, `GET/PATCH/DELETE .../{id}`, via `$lib/api/scheduledReports.ts`, types in `$lib/types/scheduledReport.ts`, pure display helpers + their vitest guard in `components/analytics/scheduledReportDisplay.ts`). Hosted here because the API prefix and the RBAC are this route's own: read admin + cfo, **mutate admin-only** (`auth.isAdmin` gates create/edit/enable/pause/delete; a CFO reads). Rendered OUTSIDE the forecast `{#if}` so a failed cash-flow load can't hide the only way to re-enable a broken schedule. Three contracts the UI encodes: (a) **the report-type and cadence selects are driven off the list response** (`report_types` / `cadences` — the runner's own registries), never a hardcoded copy, and an unlabelled key still renders via `humaniseKey` so a new backend type is selectable for free; (b) `enabled:false` + `last_run_status:'failure'` + a `[retry 5]` prefix on `last_run_error` is the **auto-disabled** state — a schedule that silently stopped emailing — rendered `danger` + a `[data-health]` hook + its own explanation so it can't be read as the flat `neutral` "Paused" chip, and recovered by ONE `PATCH {enabled:true}` (which also clears the status/error); (c) recipients are de-duped **server-side**, so every save re-renders from the response (and toasts the count dropped) rather than the typed list. `partial` shows `last_run_error` verbatim — counts + an exception class, never an address; validation failures surface `formatApiDetail`'s `field: msg`, which reads `loc`+`msg` only and never echoes `input` (a recipients `input` is the address list). **No entity selector**: `X-Entity-ID` is not honoured by these routes — a schedule is whole-tenant. A 404 on the LIST call means the routes aren't mounted in this deployment and degrades to one quiet line (no error state, no toast). Delete uses the armed two-click `RowAction` with outside-click un-arm. E2E: `tests-e2e/cfo/scheduled-reports.spec.ts` |
| `/tax` | `routes/tax/+page.svelte` | `GET /api/tax/1099-report?year=` (via `lib/api/tax.ts`) — 1099 vendor reporting dashboard. KPI summary, year selector, per-vendor 1099-eligible / W-9-on-file / TIN-verified chips, >$600 threshold flags, vendor search + filter chips (all / reportable / missing W-9 / over threshold / card excluded). admin/ap_manager/cfo. The report outer-joins vendors→payments, so the row set is the tenant's vendor list and the year only re-aggregates each vendor's YTD; the table is empty only when there are no vendors. **Card-rail spend is excluded from the reportable figures** (the processor files it on a 1099-K) and surfaced for reconciliation without competing with them: a muted `Card excluded (1099-K)` column (`card_paid` + a `card_payment_count` sub-line, `—` when zero), a `KpiCard sub` line under Total reportable carrying `total_card_excluded`, a Card-excluded filter chip, and a standing footnote — see `backend/docs/tax-1099.md` § Card payments are excluded. |
| `/reports` | `routes/reports/+page.svelte` | Custom (ad-hoc) **Report Builder** (via `$lib/api/reports.ts`, types in `$lib/types/reports.ts`) — `GET /api/reports/catalog` (drives the WHOLE builder — data sources + dimensions / measures / filters; never hardcoded), `GET /api/reports` (saved defs), `POST /api/reports` (save), `GET/PATCH/DELETE /api/reports/{id}`, `POST /api/reports/run` (ad-hoc spec → `ReportResult`), `POST /api/reports/{id}/run` (saved), `GET /api/reports/{id}/export?format=csv\|pdf` (branded blob download). Under the **Insights** nav group. Builder = source picker → `reports/{DimensionEditor,MeasureEditor,FilterEditor}.svelte` (filter value inputs adapt to field type + op + enum values) → **Run** → `reports/ResultTable.svelte` (money columns via `<Money>`, exact-string aware, paginated). `reports/SaveReportModal.svelte` (name + description) + a saved-reports `DataTable` with load / run / CSV / PDF / delete. URL-backed deep-link `?id=<uuid>` loads a saved def (mirrors the invoices `?id=` pattern). Read all four roles; save/patch/delete gated to admin/ap_manager/cfo (`auth.hasAnyRole`; the backend 403s the rest). Money is exact-decimal string throughout. **Two contracts that are easy to get wrong.** (a) A loaded saved report runs its PERSISTED spec server-side ONLY while untouched — `dirtySinceLoad` flips the Run to the ad-hoc endpoint the moment the builder is edited. The baseline snapshot is taken IMPERATIVELY in `applyDefinition()` (and cleared in `selectSource()`); it must never move into an `$effect`, because an effect that calls `specForCompare()` reads the state it snapshots, re-fires on every edit, and pins the flag to `false` forever — which silently ran the stale saved spec while showing the edited one. (b) `runSavedReport` puts `page` / `page_size` in the QUERY STRING: `POST /api/reports/{id}/run` declares both as `Query(...)` (there is no body spec to carry them), so a body payload is dropped and every run returns page 1. The ad-hoc `POST /api/reports/run` takes them in the body — hence the asymmetry. `tests-e2e/reports/saved-report-run.spec.ts` guards both. |
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
- **Error messages** — every non-OK response is thrown as an `ApiError` whose message comes from `formatApiDetail(body.detail, fallback)` (`src/lib/utils/apiError.ts`, re-exported from `api.ts`). FastAPI's `detail` is **not always a string**: a Pydantic 422 carries a LIST of `{loc, msg, type}`, which the old `body.detail || fallback` stringified as literally `"[object Object]"` in the toast. The helper renders a string as-is, a validation list as `field: msg; field: msg` (dropping the `body`/`query` `loc` prefix), a lone `{msg}`/`{message}`, and falls back otherwise. It is pure (no `$env`, no `fetch`) so it lives in `utils/` and is unit-tested (`apiError.test.ts`). It deliberately FLATTENS to one string — a caller that needs the structure reads the raw body itself, which is why `api/expenses.ts::submitReport` still hand-rolls its own `fetch` to keep the policy-violation list intact.
- **Streaming** — `streamAssistantChat(body, { onTool, onDelta, onDone, onError }, signal?)` streams the AP assistant turn from `POST /api/assistant/chat/stream` (`text/event-stream`). It uses `fetch` + `response.body.getReader()` (NOT `EventSource`, which can't set the Authorization / tenant / entity headers) and a small SSE parser (split on `\n\n`, read `event:`/`data:` lines), reusing the shared `authHeaders()` helper so the header logic can't drift from `request()`. SSE frames: `tool` (one per tool invocation; `result` carries the structured output for charts), `delta` (incremental answer text), `done` (authoritative payload), `error` (mid-stream failure). A pre-stream HTTP 429 throws `AssistantBudgetError` (carries `used`/`budget`/`period`); any other non-OK / network failure throws a plain `Error` — the `/assistant` page catches both and falls back to the non-streaming `POST /api/assistant/chat`.
- Typed feature helpers wrap `api` per domain — e.g. `src/lib/api/audit.ts` (`getInvoiceAuditLog`, `getAuditExport`, `downloadAuditExportCsv`) over the SOX audit endpoints, with `AuditEntry` / `AuditFieldChange` types in `src/lib/types/audit.ts`. The invoice-modal Activity timeline renders `details.changes` (per-field before/after) from these. `src/lib/api/tax.ts` (`get1099Report`) wraps the 1099 endpoint, with `Report1099` / `Vendor1099Row` types in `src/lib/types/tax.ts`.

### Money formatting — `src/lib/utils/money.ts` + `ui/Money.svelte`

**Never hand-roll `Intl.NumberFormat` for currency.** `formatMoney(amount, opts?, placeholder?)` is the single locale-aware formatter (currency-code driven via `Intl.NumberFormat`, browser locale), and `<Money amount currency? whole? accounting? mono? />` is the component over it. Each amount renders with its **own** ISO 4217 code — never a hardcoded `$`.

- Per-row amounts pass their own `currency` (invoices, credit memos, POs, cards, payments).
- Tenant-wide roll-ups (dashboard KPIs, payment-summary totals, aging, CFO forecast) have no per-row currency, so they use the **org default** from the `orgCurrency` store (`src/lib/stores/orgSettings.svelte.ts`). It lazy-loads `GET /api/organization` once per session and resolves the code through the pure `utils/reportingCurrency.ts::resolveReportingCurrency`, which mirrors the backend's own `currency_conversion.resolve_reporting_currency` order — `settings.reporting_currency` → `settings.payments.home_currency` → `settings.invoice_defaults.currency` → USD. **That order is the contract, not a preference**: it is the function deciding what currency the API's cross-currency rollups are actually denominated in, so reading only `invoice_defaults.currency` (as this store did) mislabelled every aggregate figure for any org whose reporting currency differed — a GBP total rendered with a `$`. That route is open to every authenticated role, but its `settings` payload is **projected by role** (`backend/app/services/org_settings_view.py`): a non-admin gets an allow-list that keeps `company` / `invoice_defaults` / `reporting_currency` / `payments.home_currency` / `brand` / `erp.integration_method` and drops the tenant's third-party credentials, which every role could read before — `payments` is admitted for `home_currency` ONLY, never the processor credentials beside it. If a page needs another settings field client-side, it has to be added to that allow-list on purpose — don't assume the raw blob. Call `orgCurrency.ensureLoaded()` from the page's init `$effect` and read `orgCurrency.currency`.
- `formatMoney` accepts `number | string-Decimal | null` and returns the placeholder (`—` by default) for null/empty/non-finite; a bad currency code falls back to USD rather than throwing.
- **Type an API money field `MoneyString` (`= string`), never `number`.** `MoneyString` / `MoneyAmount` (`MoneyString | number | null | undefined`) are exported from `utils/money.ts`; the backend serialises money as an exact decimal string precisely so no figure round-trips through a binary float. A `number`-typed money field silently invites `.toFixed()`, `a - b` and `Math.max()` on currency — which is how `/api/analytics/cfo`'s whole response shape ended up violating the Decimal invariant.
- `isPositiveAmount(amount)` / `isNegativeAmount(amount)` are the **predicate** companions — "is this string-Decimal worth rendering at all?" / "should this figure be tinted as a loss?" (zero / null / unparseable → `false` from both). Use them to gate an optional money element (the `/tax` card-excluded column + KPI sub-line) or a loss tint (the CFO unrealized-FX column). They are deliberately NOT money arithmetic: never add, subtract or compare two amounts client-side — render both figures and let the backend own any delta.
- `parseMoneyForLayout(amount)` is the **one sanctioned** money-string → `number` conversion, and it is named for its purpose so the name refuses the wrong use at the call site: chart bar widths, the `Math.max()` that sets a chart's scale, a sort key. The result is a *geometry* input, never a figure — never render it, never fold two of them into a total, never branch a business decision on it. Absent / unparseable → `0`, so a chart can't render a `NaN%` width. All three helpers are covered by `src/lib/utils/money.test.ts`.
- **i18n-aware locale** — when a caller passes no explicit `locale`, `formatMoney` defaults to the active in-app locale (the i18n picker), read from `$lib/i18n/formatLocale.ts::getActiveFormatLocale()`. Until a locale is actively selected the holder is `undefined` (browser locale), so nothing changed pre-i18n. Selecting German in the picker makes `$1,234.50` render as `1.234,50 $`. **`utils/time.ts`'s `formatDate` / `formatPeriod` date helpers read the same holder**, so dates switch locale alongside money (German renders `Jun 20, 2026` as `20. Juni 2026`). Both parse bare `YYYY-MM-DD` / `YYYY-MM` keys at *local* midnight (not UTC) so a negative-offset timezone can't roll the displayed day back. `formatDate` takes an optional `Intl.DateTimeFormatOptions` third arg so a caller can vary the parts (no-year for a due-date cell, date+time for a queue row — it auto-switches to `toLocaleString` when `hour`/`minute`/`second` is asked for) while still localizing off the active locale; omit it for the standard short date. The per-row date cells on the **dashboard** (due dates), **payments** (created/executed/expiry), the **exceptions** queue, and the **workflows** / **discounts** / **credit-memos** / **vendor-statements** lists (plus the `VendorStatementReconModal` dialog) have been migrated onto this shared helper — they previously hardcoded `en-US` so the picker didn't move them. The **admin** lists (`admin/webhooks`, `admin/api-keys`, and the `UsersPanel`, which each hardcoded `en-US`) and the **supplier-portal** lists (`portal/invoices`, `portal/payments`, `portal/purchase-orders`, `portal/discount-offers`, which each hand-rolled a per-page `fmtDate` on a locale-less `toLocaleDateString()`) are now migrated too — note the portal cells shifted from the browser's numeric short date (`6/20/2026`) to the shared helper's month-name short date (`Jun 20, 2026`, locale-driven), and keep the portal's `m('portal.common.dash')` placeholder by passing it as `formatDate`'s 2nd arg. Remaining inline `toLocaleDateString` call sites elsewhere (procurement lists; the `tax` + `audit` consoles, which already follow the *browser* locale but not yet the picker) are not yet migrated (a later slice).

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
  synchronous), persists to `localStorage` (key `feoh_locale`, device-scoped),
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

**Extracting a string:** add a flat, namespaced key (`nav.invoices`, `common.save`) to `locales/en.ts`, translate it in every other locale, and replace the hardcoded literal with `m('…')`. **Extracted so far:** the shell/nav (`$lib/nav.ts` carries a `labelKey` per entry; `Sidebar.svelte`, `SectionTabs.svelte`, the `+layout` skip-link, the profile locale picker), the **dashboard** (`routes/+page.svelte` — KPI labels, chart headings, aging buckets, empty states), the **invoices list** (`routes/invoices/+page.svelte` — title, upload/recode actions, search, bulk-bar, table headers, row actions, the `{n, plural, …}` selected-count + showing-all + load-more strings), the **payments** page (`routes/payments/+page.svelte` — summary cards, the queue/history/cards/runs tab bar, queue pay-bar + review panel + savings banner, all four table headers, row actions, the void + card-details + compliance-hold (`payments.compliance.*`, release/dismiss) modals, and the pluralized create-draft-run / showing-all strings; per-row payment-status / method badge labels stay data-driven English), the **vendors** list (`routes/vendors/+page.svelte` — title, sync action, search, status filter chips, table headers, verify/reject/bank row actions, bank-details modal; the vendor status/source value maps stay English), the **exceptions** queue (`routes/exceptions/+page.svelte` — title, queue/AI-agents tabs, status filter chips, "All types", table headers, resolve/invoice row actions, bulk-resolve bar, the single + bulk resolve modals with their pluralized titles; the data-driven type-label / severity / status cell badges stay English), the **notifications** page (`routes/notifications/+page.svelte` — title, mark-all-read action + its pluralized toast, all/unread filter chips, table headers, the open-row aria label, the three empty states + load-error, the pluralized load-more / showing-all strings; the `EVENT_LABELS` event-type cell stays data-driven English), the **contracts** list (`routes/contracts/+page.svelte` — title, new-contract action, search, table headers, the open-row aria label, over-spend-limit tooltip, not-found toast, empty state, the pluralized load-more / showing-all strings; the `STATUS_LABELS` / `CONTRACT_TYPE_LABELS` value maps stay English), and the **recurring** templates page (`routes/recurring/+page.svelte` — title, new-template action, search, KPI labels, table headers, day-of-period, the row open/generate-now/pause/resume/end aria labels + button labels + confirm, every lifecycle toast (generate/pause/resume/end + load-failed), the `relativeRun` "today / in N days / N days ago" via ICU plurals, the pluralized load-more / showing-all strings; the `STATUS_LABELS` / `CADENCE_LABELS` value maps stay English), and the **organization settings** page (`routes/organization/+page.svelte` — every section heading + card hint (Company Profile, Invoice Defaults, Branding, Custom Domains, AI Extraction, ERP Integration, Payments, Virtual Cards, Security, Fraud Detection, Data Sync, Plan), all field labels + placeholders + aria-labels, every save/test button (incl. the `Saving…`/`Testing…`/`Test Connection` shared `org.common.*` keys), all `toast` messages (load/save/test failures, section-saved via the `{section}` placeholder, branding hex/URL validation, every custom-domain toast), the fraud-rule hints with their `${min}`/`${amount}`/`{days}` interpolations, the plan badge labels via `planLabel()`, and the `org.plan.created` date string. Data values stay English by the established convention: currency codes, `Net 30`-style payment-terms, ERP/card provider product names (`Microsoft Dynamics 365`, `Lithic`, `Nium`, `Modern Treasury`), and Ollama model ids. Two whole-sentence hints that originally embedded inline `<code>`/`<strong>`/`<em>` styling (the payments-processor and CFO-gate blurbs, the custom-domains example/slug) were flattened to plain i18n strings; the Merge.dev-dashboard link is preserved by splitting its hint into `org.erp.mergeHintPre`/`mergeDashboard`/`mergeHintPost`), and the **Cash Flow / CFO dashboard** (`routes/cfo/+page.svelte` — title + Export-CSV action, the granularity (`day`/`week`/`month` via `granLabel()`) + horizon (`{days}d`) segmented controls, opening-balance / min-balance-alert labels + placeholders + aria-labels, all four KPI labels, the forecast-chart heading (`{granularity}` interpolated), the per-bar `aria-label` + committed/pending bar `title`s, the chart legend, the what-if scenario titles + `net outflow` + `+{amount} discount captured` + `~{days} days to pay`, the cash-position card heading + enter-opening hint + the pluralized `{n}`-period below-minimum-balance breach banner + all four table headers + both empty states + the load/export error fallbacks; `formatPeriod` period labels are now locale-driven via the shared `utils/time.ts` helper, and the embedded **`ByEntityBreakdown`** component is extracted too — `byEntity.*` keys cover the heading, all six column headers, the `default` tag, the Consolidated total row, the loading state, and the load-error fallback; per-entity money stays the currency-driven `<Money>`), and the **Expense Management** page (`routes/expenses/+page.svelte` — title + all five action buttons + the five-tab bar (Expenses / Reports / Policies / Pre-approvals / Cards), every tab's KPI labels, search box, `$derived` chip/COLUMN arrays (the `'All'` chip reuses `common.all`; status-value maps `EXPENSE_STATUS_LABELS` / `EXPENSE_REPORT_STATUS_LABELS` / `EXPENSE_PREAPPROVAL_STATUS_LABELS` / `RECONCILIATION_STATUS_LABELS` stay English by convention), every DataTable header + empty/loading state, all row aria-labels + Delete/Confirm/Detach/Match/Unmatch/Ignore/Create-expense/Approve/Reject row actions, the report-detail submit/approve/reject controls + inline violation panel + reject row + attach row, the bulk-GL bar, all three modals' `ariaLabel`/`title`/fields/buttons (New Report, New Pre-approval, Match-to-expense — no e2e spec keys these), and **every** toast — including the composed pluralized `{n}` import/sync toasts (`Imported # transaction(s) (# duplicate(s) skipped)`, `Synced # virtual-card transaction(s) (# already imported)`) and the GL-coded / showing-all plurals; per-row `formatDate` is now locale-driven via the shared `utils/time.ts` helper), plus the **expense feature dialogs** — **`ExpenseModal`** (`expenseModal.*`: new/edit/view title + aria, every field label + the category placeholder + GL `Select…`, the receipt section (title / view / pending / empty / uploading / replace / attach), close/save/create buttons, and every toast — receipt-uploaded/upload-failed, created/saved, create/save-failed, receipt-load-failed; the `EXPENSE_STATUS_LABELS` / payment-method value maps stay English) and **`PolicyModal`** (`policyModal.*`: new/edit title + aria, all eight field labels + the category placeholder, close/save/create buttons, and the created/saved/save-failed toasts), and the **procurement + positive-pay routes** — the **requisitions** (`requisitions.*`), **intake** (`intake.*`), **catalogs** (`catalogs.*`, incl. the guided-buying panel), **budgets** (`budgets.*`), **purchase-orders** (`purchaseOrders.*`, incl. the detail modal + linked-invoices table), **goods-receipts** (`goodsReceipts.*`, incl. the detail modal), and **positive-pay** (`positivePay.*`) list pages: titles, header actions (incl. ERP-sync), search boxes, KPI labels, status `FilterChips`, every DataTable header + empty/loading state, the row open/view/delete aria-labels + Delete/Confirm + lifecycle row actions (submit/approve/reject/convert/cancel), every toast (incl. the `{poNumber}`/`{number}`/`{name}`/`{label}` interpolations and the pluralized `loadMore`/`showingAll` strings), and the PO/GR detail-modal field labels. the procurement **create/edit modals** — **`RequisitionModal`** (`requisitions.modal.*`), **`IntakeModal`** (`intake.modal.*`), **`CatalogModal`** (`catalogs.modal.*`) + **`PunchoutModal`** (`catalogs.punchout.*`), **`BudgetModal`** (`budgets.modal.*`), **`ContractModal`** (`contracts.modal.*`), and **`RecurringModal`** (`recurring.modal.*`): new/edit/view titles + aria, every field label + placeholder, line-item table headers + per-line `{n}`-indexed aria-labels + add/remove, the spend/preview/upcoming/history panels, document upload (view/replace/upload), the renew + create-PO sub-forms, the lifecycle action buttons (activate/terminate/cancel/renew/create-PO/pause/resume/end/generate-now) with their `{name}` aria-labels, close/save/create buttons, and every toast (incl. the `{number}` PO/requisition-created interpolation); data-driven status/type value maps (`*_STATUS_LABELS` / `*_TYPE_LABELS` / `CADENCE_LABELS` / `BUDGET_DIMENSION_LABELS`) plus per-row vendor/GL option text stay English by the established convention, the **supplier portal** (every `routes/portal/**` page, `portal.*` keys — the shell/nav + skip-link + log-out + no-tenant notice (`+layout.svelte`), login + the email/TOTP MFA step (`login/`), change-password, the invoices list + submit, the payments list + remittance download, the purchase-orders list + PO-flip, the company page (contact / bank-detail-change request / tax-ID-change request / W-9·W-8 tax-form upload + download / TOTP enroll-disable), the early-payment discount-offers list + accept dialog, the notification-preferences toggles, and the single-use virtual-card reveal (`cards/[token]`); placeholders like `{product}`/`{po}`/`{number}`/`{last4}`/`{date}`/`{amount}`/`{days}`/`{percent}`/`{changeType}` are preserved verbatim in every locale). , and the **authentication & onboarding routes** (`auth.*` keys — the pre-auth, high-visibility surface): **login** (`routes/login/+page.svelte` — heading/subtitle, email/password labels, sign-in button + signing-in state, the SSO-only notice, the `or` divider, and the `Sign in with {provider}` SSO/SAML buttons + the login-failed fallback), the **MFA step** (`routes/login/mfa/+page.svelte` — heading + the per-method passkey/totp/email subtitle, the MFA-enrollment notice, the verify-with-passkey / email-me-a-code / verification-code / verify buttons + their busy states, the method-switch buttons, and the passkey/email-send/verify error fallbacks), the **SSO + SAML callback** pages (`routes/login/{sso,saml}-callback/+page.svelte` — shared `auth.callback.*` keys: the `<title>`, signing-in status, the `{error}`-interpolated IdP-error, the missing-code/-state messages, the failed heading + the back-to-sign-in link), **signup** (`routes/signup/+page.svelte` — `<title>`, the check-your-email success block + try-again link, the create-workspace heading/subtitle, every field label (company / workspace-URL / your-name / email), the slug availability states (`checking`/`available`/`unavailable`/`hint`), the submit button + busy state, the captcha-required + signup-failed fallbacks, and the footer split into `footerPre`/`footerPost` around the `<code>{tenantExampleHost}</code>`; the backend's `res.reason` slug rejection stays its server English), **change-password** (`routes/change-password/+page.svelte` — `<title>`, the heading + the forced/voluntary subtitle, the three field labels, the four `strength.*` complexity-hint labels (fed into the `strengthHints` `$derived`), the change/saving + sign-out buttons, and the mismatch/too-weak/failed error fallbacks), and **verify** (`routes/verify/+page.svelte` — `<title>`, the pending/success/error state headings + copy, the success block split into `successSubPre`/`successSubPost` around the `<strong>{admin_email}</strong>`, the three numbered steps, the `Continue to {slug} →` link, and the no-token/verification-failed fallbacks). `initLocale()` already runs in the root `+layout.svelte` `$effect` for every route (the signup/verify no-tenant pages render through the layout slot), so `m()` resolves on these pre-auth pages with no new init plumbing. The `FeohLedger` brand string stays verbatim inside its keys (including the `— FeohLedger` page-title suffix) by the data-value convention. , and the **admin section** (`admin.*` keys) — the **Users & Roles** page (`routes/admin/+page.svelte` host title + the per-tab Invite-User / Create-Role PageHeader actions) and its two panels (`components/admin/UsersPanel.svelte` — search box, bulk-delete bar + the pluralized `{n}` bulk-delete toasts (deleted/none/partial) + the per-row reference-reason fragments (`fail.openInvoices`/`pendingApprovals`/`activeWorkflows` plurals + `fail.referenced`/`self`/`notFound`/`blocked`), DataTable headers + select-all/select aria + the You/No-roles/Active/Inactive cells, deactivate/activate/delete/confirm row actions, the pluralized load-more / showing-all strings, every create/update/toggle/delete toast, and all three modals — Invite/Created-credentials/Edit — fields + buttons + hints; the `ROLE_LABELS` value map stays data-driven English; `components/admin/RolesPanel.svelte` — the `$derived` system/custom column arrays, both section headings + hints (the custom-roles hint split `hintPre`/`hintPermissions`/`hintPost` to preserve the inline `<strong>`), empty states, the No-permissions cell, edit/delete/confirm row actions, every toast (`{name}` interpolated), and the create/edit modals' fields + permission fieldset + buttons; the permission-catalog labels come from the API and stay English), the **API Keys** page (`routes/admin/api-keys/+page.svelte` — title + create action, the page hint split around the `<code>X-API-Key</code>` literal, the `$derived` COLUMN array, loading/error/empty states + retry, the view-usage aria + Active/Revoked status pills + revoke/confirm row action, every toast, the create modal (hint split around `<strong>read</strong>`), the one-time key-reveal modal (warning split `warningStrong`/`warning`, copy/copied, name/prefix meta), and the per-key usage modal (stat labels with `{days}`, recent-activity table + no-requests state, close)), the **Webhooks** page (`routes/admin/webhooks/+page.svelte` — title + create action, the page hint split around `<code>X-Webhook-Signature</code>`, the `$derived` subscription + delivery COLUMN arrays, the delivery status FilterChips (all/pending/delivered/failed/dead via a typed `deliveryStatusLabel`), Active/Inactive sub-status pills + delete/confirm + redeliver/redelivering row actions, loading/error/empty states for both sections, every toast, the create modal (hint split around `<strong>only once</strong>`), the secret-reveal modal, the edit modal incl. the Active toggle, and the **secret-rotation** surface (`admin.webhooks.rotate.*` / `rotated.*` — the Rotate-secret row action + its `{name}` aria, the confirm dialog's `{name}`-interpolated hint, the overlap-window legend/hint and its five radio labels, the hard-cutover + `{time}`-interpolated re-rotation warnings, the rotate/rotating buttons, the rotated reveal's warning + `{time}`-interpolated overlap note + cutover note, the `Previous secret until {time}` in-flight pill + its title, and the rotate-failed toast); the `WEBHOOK_EVENT_TYPES` event-type checkboxes + the delivery `event_type`/`status` cell values stay data-driven English), and the **Partner / reseller admin** page (`routes/admin/partner/+page.svelte` — title + create-child / attach-child actions, the page hint, the Join-a-partner link-code panel (generate/copy/`{minutes}`-expiry), loading/error/not-partner-empty states + retry, the `$derived` COLUMN array + child-tenant empty, the edit-branding aria + detach/confirm-detach row action (`{name}` aria), every toast (brand hex/URL validation reusing `label.*` keys + `{label}` interpolation, mint/copy/attach/provision/detach with `{name}`), the child-branding modal (all field labels + placeholders + buttons), the attach modal (hint + link-code field), and the provision modal — the form fields + the one-time temp-credentials result (hint split around the inline `<strong>{name}</strong> ({slug})` + `<strong>only once</strong>`, copy-password); the child `plan`/`product_name` cell values stay data-driven English), and the **workflow / assistant / billing / modal tail** that completed the web extraction: the **no-code workflow builder** (`routes/workflows/[id]/+page.svelte` + the `workflow-builder/` components — `workflows.builder.*`: toolbar, step-config labels/hints incl. the round-robin ICU plural, canvas nodes + branch tags, condition/parallel/custom-step editors, palette) and the **workflows list + mgmt dialogs** (`routes/workflows/+page.svelte` + `workflow-mgmt/` — `workflows.list.*` / `workflows.mgmt.*`: template library, version history/diff, simulation, import/export), the **assistant** (`routes/assistant/+page.svelte` + `assistant/` components — `assistant.*`: composer/send, recent-chats rail, tool-result titles + mini-tables, usage meter, every runtime/budget notice), the **audit** (`audit.*`) and **tax** (`tax.*`) consoles, the **billing** (`billing.*`), **discounts** (`discounts.*`), **experiments** (`experiments.*`), **credit-memos** (`creditMemos.*`), and **vendor-statements** (`vendorStatements.*`) routes, and the remaining **feature modals** — `InvoiceModal` (`invoices.modal.*`: field/action label maps typed `Record<string, MessageKey>` so dynamic `m(key)` stays type-checked), `VendorModal` (`vendors.modal.*`), `RunDetailModal` (`paymentRuns.runDetail.*`), `AdvancedSearchModal` (`advancedSearch.*`), `ApprovalMatrixEditor` (`approvalMatrix.*`), and `VendorStatementReconModal` (`vendorStatements.modal.*`: create form + intake-lines editor, the summary/diff detail view, resolve/ignore actions, all toasts + aria labels); data-driven value maps (`STEP_TYPE_LABELS`, `STATUS_LABELS`, `RISK_LEVEL_LABELS`, `PAYMENT_METHOD_LABELS`, etc.) and a few e2e-selector aria-labels stay English by the established convention. **All web routes + feature modals are now i18n-extracted** (mobile screens were already done). The rest of the app stays English until later extraction slices — an un-extracted literal simply stays English, the designed incremental path. Reading `m()` inside a `$derived` (e.g. the dashboard's `agingBuckets`) keeps the labels reactive to a locale switch.

**Locale picker:** `routes/profile/+page.svelte` — a `<select>` of endonyms (`LOCALE_LABELS`) bound to `currentLocale()`, persisting via `setLocale`.

**Tests (vitest):** `pnpm test:unit` (or `pnpm exec vitest run`). `messages_parity.test.ts` iterates `SUPPORTED_LOCALES` through the loader registry and asserts every locale is loadable, key-complete vs `en`, non-empty, and placeholder-faithful. `interpolate.test.ts` + `locale.test.ts` cover the pure helpers. Config: `vitest.config.ts` (node env, separate from `vite.config.ts` — the tested modules are pure, no `$app/*` / Svelte compiler). Vitest is the unit-test framework for the frontend (don't add another).

### Tenant — `src/lib/tenant.ts`

`getTenantSlug()` extracts subdomain: `acme.localhost:7777` → `"acme"`, plain `localhost` → `null`.

### Stores (`src/lib/stores/`) — Svelte 5 rune stores

| Store | File | State | Key methods |
|-------|------|-------|-------------|
| `auth` | `auth.svelte.ts` | `user` (incl. `mfa_enabled`, `mfa_required_by_org`), `loggedIn`, role checks (`isAdmin`, `isManager`, `isCfo`, `isClerkOnly`) | `login()` (returns `{kind:'ok'} \| {kind:'mfa', challenge}` — MFA branch routes to `/login/mfa`), `completeMfa(token, code, method)`, `requestEmailMfa(token)`, `completePasskey(token)`, `listPasskeys()`, `passkeyStepUp(operation)` (mint + sign a factor-change step-up assertion), `registerPasskey(name, stepUp)`, `deletePasskey(id, stepUp)`, `listSessions()` / `revokeSession(id)` / `revokeOtherSessions()` (the caller's own live sessions — see the `/profile` row), `logout()`, `fetchUser()`, `hasRole()`, `hasAnyRole()` |
| `invoiceStore` | `invoices.svelte.ts` | `all`, `loading`, `errored`, `total`, `statusCounts` | `fetch(params)`, `fetchCounts()`, `update(id, changes)` |
| `paymentStore` | `payments.svelte.ts` | `all`, `loading`, `errored`, `total`, `hasMore` | `fetch(params)`, `loadMore()` (history-tab Load-More; remembers filter params) |
| `workflowStore` | `workflows.svelte.ts` | `all`, `loading`, `total`, `hasMore`, `activeSteps` | `fetch()`, `loadMore()`, `fetchActiveSteps()`, `getById()`, `create()`, `update()` |
| `adminStore` | `admin.svelte.ts` | `users`, `roles`, `loading` | `fetchUsers()`, `fetchRoles()`, `createUser()`, `updateUser()`, `deleteUser()` |
| `sidebar` | `sidebar.svelte.ts` | `collapsed` | `toggle()` |
| `orgCurrency` | `orgSettings.svelte.ts` | `currency` | `ensureLoaded()`, `reset()` — tenant REPORTING currency for aggregate (non-per-row) money; lazy-loads from `/api/organization` and resolves via `utils/reportingCurrency.ts` in the backend's order, USD fallback |
| `notificationStore` | `notifications.svelte.ts` | `items`, `unread`, **`inboxTotal`** (the WHOLE inbox, filter-independent — the All chip), **`filteredTotal`** (the count of what the current filter matched — `hasMore`, Load-more, "Showing all N"), `loading`, `hasMore`, `prefs`. The two were one field, so an `unread_only=true` response overwrote the inbox count and both chips showed the same number | `fetchList({unreadOnly})`, `loadMore()`, `fetchUnreadCount()`, `markRead(id)`, `markAllRead()`, `fetchPrefs()`, `updatePrefs()`, `startPolling()`/`stopPolling()` (60s unread-count poll for the sidebar-header bell badge; started from `+layout` when signed in) |

### Components (`src/lib/components/`)

Grouped into subfolders by role. Import with the full path, e.g.
`import Modal from '$lib/components/ui/Modal.svelte'`. No barrel/index file.

**`ui/` — reusable primitives** (use these; don't hand-roll the markup):
- `PageHeader.svelte` — `.workspace` + `.toolbar` shell. `<PageHeader title="X">` with an optional `{#snippet actions()}` (right-aligned toolbar buttons); page body is `children`. Renders the `<h1>` title.
- `DataTable.svelte` — `.grid-container > table`. Pass `columns={[{label,class?}]}` (or a `{#snippet header()}<tr>…</tr>{/snippet}` for select-all/sortable headers) + a `{#snippet body()}` that renders the `<tr>`/`<td>` rows. `isEmpty` + `empty` render the centred empty row (`colspan` auto from columns). Opt-in `fixed` (table-layout:fixed) and `stickyHeader` props.
- `FilterChips.svelte` — `nav.filters` of `.filter-chip`. `<FilterChips chips={[{key,label,count?,alert?}]} bind:active={var} />`. Single-select; for multi-select status filters keep an inline chip nav (it still uses the global `.filter-chip` CSS).
- `Modal.svelte` — `.backdrop` + `div.modal[role="dialog"]`. `<Modal open ariaLabel="EXACT" title? width="sm|md|lg" onclose>`; keep the page's own `<form>` + `.modal-footer` inside `children` (preserves submit). Custom heading → `{#snippet header()}`. Handles backdrop-click + Esc, and locks background page scroll while open (restores on close) so a wheel event over the backdrop can't bleed through to the list behind it.
- `KpiCard.svelte` — `.kpi` card. `<KpiCard value label highlight={'green'|'red'|null} sub? />`; wrap a row in `<div class="kpi-row">`. `sub` is an optional muted `.kpi-sub` line under the label for a **qualifier on the headline figure** — money the value deliberately excludes, a caveat, a denominator — so the primary value keeps visual priority instead of competing with a second KPI card. Pass `null` (or omit) when there is nothing to qualify; don't use it for a second metric. First use: `/tax`'s Total-reportable card carrying the card-excluded (1099-K) amount.
- `Badge.svelte` — **the** tinted-badge primitive, and the single owner of the `background: var(--<tone>-tint); color: var(--<tone>-on-tint)` recipe. `<Badge tone="accent|success|warning|danger|muted|neutral|erp" variant? title?>{label}</Badge>`. A caller names a *tone*, so it can't spell one wrong, and a tone that is later recalibrated moves in one place. `variant` is passed through as an extra class for **selector hooks only** (`.badge.approved`, `.badge.violation` — the e2e suite reads them); never give a variant a colour rule in the calling component, pick the tone instead. `neutral` is a flat `--bg` chip for the absence of a signal (cancelled / n-a), deliberately not a tint; `erp` is the one measured literal (purple shares no semantic with the five tones, so it stays here rather than becoming a palette token with one caller). **Sizing is fixed on purpose** — call sites varied padding by a pixel or two with no intent behind it. A pill that genuinely needs different metrics is a different component, not a prop: `ScreeningBadge` is the worked example (its own smaller sentence-case metrics, but the palette tokens for colour). Where a status is badged in more than one place, put a `STATUS_TONES: Record<Status, BadgeTone>` map beside the existing `STATUS_LABELS` in the shared types module so the list page and its modal can't disagree — several did.
- `SearchBox`, `StatusBadge`, `RowAction`, `BulkBar`, `BulkDeleteButton`, `Toast` — see the pattern sections below.
- `Tabs.svelte` — underline tab bar for **in-page** panel switching. `<Tabs tabs={[{key,label,count?}]} bind:active ariaLabel? onchange? />`. Owns the `.tab-row` / `.tab` markup + `role="tablist"`/`role="tab"` a11y. The per-route tab copies in `/expenses`, `/payments`, `/audit` predate it and can migrate onto it opportunistically. (Distinct from `layout/SectionTabs.svelte`, which renders the sidebar group's *cross-route* sub-tabs as anchors — that's not this component.)
- `ScreeningBadge.svelte` — sanctions-screening + vendor-risk pill. `<ScreeningBadge screening={v.screening_status} risk={v.risk_level} blocked={v.payments_blocked} adverseMedia={v.adverse_media} />`. Tone map: clear=green, review/medium=amber, match/high/critical/blocked=red, unscreened/low=grey. `adverseMedia` adds an amber **Negative news** pill for an adverse-media (negative-news) screening hit — it reads *alongside* the verdict, not instead of it, because "review the relationship" is a different instruction than a watchlist match. Reuses the existing calibrated tone classes; don't hand-roll a new pill colour. Shared by the vendor list cell, `/vendors/screening`, and `VendorModal`.
- `SubscriptionBadge.svelte` — platform-billing subscription-status pill. `<SubscriptionBadge status={sub.status} />` for the four states (`trialing`/`active`/`past_due`/`canceled`); WCAG-1.4.3-calibrated tones matching `StatusBadge`. Used by `/billing`.
- `SecretReveal.svelte` — **the** one-time credential reveal dialog. `<SecretReveal open ariaLabel heading warningStrong warning secret testId copyLabel copiedLabel copiedToast copyFailedToast doneLabel meta? onclose />` (+ an optional `{#snippet note()}` under the meta rows). Wraps `Modal`; renders the plaintext in a `user-select:all` `<code>` carrying `testId`, a clipboard Copy button with a "Copied" acknowledgement, and the shown-once warning banner. **The secret is a prop, never state** — the component neither stores, caches nor logs it, and the caller drops its own copy in `onclose`, so the value leaves the DOM with the dialog. Every string is passed in already-localized (the component is i18n-agnostic; each caller keeps its own key namespace). Used by the API-key mint (`/admin/api-keys`) and both webhook secret reveals — create and rotate (`/admin/webhooks`). Use this for any new "shown once, never retrievable" value; don't hand-roll a third copy.
- `FieldWarning.svelte` — inline advisory attached to a form field: "this is
  legal, and here is what it will cost you". `<FieldWarning show message />`
  (the message arrives already-localized; the component is i18n-agnostic).
  `role="status"` / polite, because it updates as the user types — an
  assertive region would interrupt on every keystroke. Distinct from a toast
  (transient, on submit) and from the `role="alert"` refusal panels (a request
  the server rejected). First use: the brand strong-accent contrast advisory on
  `/organization` + `/admin/partner`.
- `Money.svelte` — locale-aware currency display. `<Money amount={row.amount} currency={row.currency} />`. Opt-in `whole` (no decimals), `accounting` (parenthesised negatives), `mono` (tabular-nums). Over `utils/money.ts::formatMoney`; see *Money formatting* above. Use this (or `formatMoney` in script) for every currency value — don't write `Intl.NumberFormat` inline.
- `EmptyState.svelte` — the first-run / zero-data affordance: an optional emoji
  `icon`, a `heading`, a `description`, and an optional primary action rendered
  as a `<button>` (`onaction`) or `<a>` (`actionHref`). i18n-agnostic (strings
  passed in already-localized, like `FieldWarning`). **Render it ONLY for the
  genuinely-empty-and-unfiltered case** — `loading`, `errored`, and "a filter
  matched nothing" keep their own copy (§ Data tables, "empty must distinguish
  loading / errored / genuinely-empty"). Adopted on the dashboard (zero
  invoices → link to `/invoices`), `/invoices` (zero rows, no filter → the
  upload action, role-gated), and `/portal/invoices` (vendor submitted nothing
  → the submit action). The page keeps its `DataTable` for every other state.

The visual styling for all of the above lives **globally in `src/app.css`** (class-scoped: `.workspace`, `.grid-container td`, `.filter-chip`, `.modal`, `.kpi`, …) so route pages carry no duplicated `<style>`. Feature components below keep their own scoped CSS (Svelte's `.svelte-<hash>` outranks the bare-class globals).

**`modals/` — feature dialogs:**
- `InvoiceModal.svelte` — invoice detail/edit modal. **Line-total reconciliation:** `saveLineItems` reads `PUT /api/invoices/{id}/line-items`'s `{saved, line_items_total, header_amount, reconciles_with_header}` and, on a divergence, renders a persistent `role="alert"` panel (`[data-testid="line-total-mismatch"]`) naming both figures via `<Money>` plus the money consequence ("cannot enter a payment run" — `line_total_mismatch` is payment-blocking). Response-driven **by necessity**: the `invoice` prop is a snapshot the store's refetch doesn't refresh, so the warning the save just raised isn't on it. Never computes a delta client-side (that would be float money math). See `backend/docs/line-total-reconciliation.md` § What the editor sees. **Approver picker:** `GET /api/invoices/assignable-reviewers` is its ONLY source — the admin-only `GET /api/admin/users` fallback is gone. That endpoint gates on exactly what `POST /invoices/{id}/assign` gates on, so a CFO gets a 403 from it too (deliberately — a CFO cannot assign either), which makes the submit-UNASSIGNED path load-bearing for a whole role rather than a failure cushion: `approverRequired` is false whenever the list is empty, and the note explains that the invoice goes to the queue unassigned. Don't "fix" the CFO 403 by widening the endpoint. `tests-e2e/invoices/approver-picker.spec.ts` asserts the admin directory is never called, from either role.
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

**`portal/` — supplier-portal-only components:**
- `PortalListFilters.svelte` — the filter bar for the portal invoice + payment
  lists: a debounced number `<input type="search">` + a single-select row of
  vendor-facing "phase" chips. Owns the phase selection, the search text AND
  the 300ms debounce, and hands the parent a resolved `{ phase, search }` via
  `onchange` (phase clicks fire immediately, search after typing stops). Because
  the debounce lives here, the parent's `load()` is never reached from a
  reactive `$effect` and needs no `untrack` (issue #168). Strings are passed in
  already-localized; `bind:this` exposes `reset()` for a "Clear filters" empty
  state. The phase→raw-status maps are `PORTAL_INVOICE_PHASES` /
  `PORTAL_PAYMENT_PHASES` in `$lib/types/portalStatus.ts`, both **derived from**
  the existing label maps so they can't drift.

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
- `vendor.ts` — `Vendor` (incl. `screening_status` / `last_screened_at` / `payments_blocked(+_reason)` / `risk_score` / `risk_level`), `SanctionsCheck`, `ScreeningReviewItem`, `VendorRisk`, `RiskSummaryBucket`, the `ScreeningStatus` / `RiskLevel` unions + label maps, and the dual-control change-request shapes — `VendorChangeRequest` / `VendorChangeRequestPage` / `VendorChangeRequestStatus` / `VendorChangeType` plus two pure formatters for the two views of `proposed_value`: `maskedProposalSummary` (the queue list's last-4-only line — it renders the backend's mask and never invents a full account number) and `revealedProposalFields` (the detail dialog's field list, returning `null` — so the caller falls back to raw JSON — whenever it can't flatten the payload without dropping a field)

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
- `isEmpty` + `empty` render the centred `td.empty` row, tagged
  `data-testid="table-empty"` so e2e specs can assert *which* empty state is
  showing.
- **`empty` must distinguish loading / errored / genuinely-empty.** A list that
  renders its "nothing here" copy while a fetch is in flight — or forever after
  a failed one — is asserting something it never established. Compose it:
  `empty={loading ? m('common.loading') : errored ? m('…empty.errored') : normalEmpty}`,
  and set both flags in the fetch's `try`/`catch`/`finally`. `/notifications`,
  `/exceptions` and the dashboard are the reference implementations; on
  `/exceptions` in particular the empty copy ("Everything looks good!") is a
  claim about open fraud flags and compliance holds, so getting this wrong is a
  correctness bug, not a polish one. **When the fetch lives in a store, the
  store owns the flag** — `invoiceStore` / `paymentStore` / `contractStore` /
  `expenseStore` each expose `errored`, set in the loader's `catch` under
  `isCurrentRequest` (the same rule the `loading` flag uses) and cleared on the
  next success. The loader still **re-throws**, so a caller that awaits a
  refresh keeps its own handling (`/invoices`' post-upload "Uploaded, but the
  list could not be refreshed" toast is exactly that, and
  `tests-e2e/invoices/upload-refetch-failure.spec.ts` guards it).
  `tests-e2e/reactivity/list-load-failure.spec.ts` stubs a 500 on all four
  lists and asserts the error copy, not the "nothing matched" copy.
- Opt-in `fixed` (`table-layout: fixed`, pair with `<th>` widths) and
  `stickyHeader`. These two MUST be props (they target DataTable-owned
  `<table>`/`<thead>`, which a page-scoped selector can't reach).

### Column sort (`SortableHeader`)

`$lib/components/ui/SortableHeader.svelte` renders one clickable, sortable
`<th>` for use inside a `DataTable`'s `header` snippet (see the note in
*Data tables* above). Pairs with the pure `$lib/utils/sort.ts::toggleSort`
helper — click an inactive column to sort it ascending, click the active
one again to flip direction:

```svelte
<script lang="ts">
    import SortableHeader from '$lib/components/ui/SortableHeader.svelte';
    import { toggleSort, type SortOrder } from '$lib/utils/sort';

    let sortField = $state<string | null>($page.url.searchParams.get('sort'));
    let sortOrder = $state<SortOrder>(($page.url.searchParams.get('order') as SortOrder) ?? 'desc');

    function handleSort(field: string) {
        const next = toggleSort({ field: sortField, order: sortOrder }, field);
        sortField = next.field;
        sortOrder = next.order;
        syncUrl(); // fold sort into the page's existing URL sync, or a dedicated syncSortUrl()
        store.fetch(buildParams());
    }
</script>

<SortableHeader field="amount" label={m('…col.amount')} active={sortField === 'amount'} order={sortOrder} onsort={handleSort} />
```

- The backend validates `sort=` against a per-endpoint allowlist
  (`backend/app/api/sorting.py`) — an out-of-list value is a 422, so only
  pass field keys the endpoint actually declares.
- `null` field = the backend's own default order; only send `sort`/`order`
  in `buildParams()` when `sortField` is set (`untrack()` the read the same
  way `search` is — `buildParams()` is called from filter effects too).
- Persist the choice to the URL the same way the page's other filter state
  is persisted (mirror `/expenses`' `syncUrl()`, or a page-local
  `syncSortUrl()` when the page has no existing filter→URL sync).
- Shipped on `/invoices`, `/vendors`, `/payments` (History tab), `/expenses`,
  and `/contracts` — the five primary list pages.

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
- **Never filter the loaded rows instead.** A client-side `.filter()` over
  what the page has already fetched silently hides every match living on a
  later page — the user searches and is told nothing matched. `/expenses` and
  `/requisitions` both shipped that way and needed an honest "searched only
  the N rows loaded so far" empty state to avoid lying; the fix was the
  backend `search` leg, not better copy. If the endpoint has no `search`
  parameter yet, add it — don't approximate it in the browser.
- **A term that hits the network needs the same discipline as a chip**:
  debounce, the page/store `createRequestSequencer` (see below), and a record
  of the term the newest issued request carried. The two pages above keep an
  `appliedSearch` `$state`, written where the request is issued and read by
  the debounce effect, which schedules nothing when the term already matches
  it. That is what stops the effect's FIRST run (mount, including a
  bookmarked `?search=`) firing a duplicate load 300ms behind the status
  effect's — and it cancels a pending debounce when a chip click has already
  loaded with the typed term.
- **Read `search` via `untrack(() => search)` inside the loader / params
  builder.** Any function the status-filter `$effect` calls *synchronously* is
  still inside that effect's tracking scope — Svelte registers reads
  transitively — so a plain read there makes the status effect depend on the
  term and every keystroke fires its own immediate request. `untrack` still
  reads the live value; it only stops the read becoming a dependency. This is
  issue #168, and it has now been reintroduced twice through a *different*
  function than the one previously fixed (`syncUrl` first, then the loader),
  so treat it as a property of the call site, not of one function:
  `routes/vendors/+page.svelte` is the reference. A `fill()`-based e2e cannot
  catch it — one state write, one term, and it passes either way. Guard it by
  typing: `pressSequentially` inside the debounce window, assert nothing
  fired, then exactly one request for the final term
  (`tests-e2e/{requisitions,expenses}/search-scope.spec.ts`, and the
  parameterized `tests-e2e/reactivity/search-debounce-race.spec.ts`).
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

Shipped on `/invoices`, `/expenses`, `/vendors` (bulk verify/reject via
`POST /api/vendors/bulk/status`, bulk re-screen via `.../bulk/screen`, CSV
export via `.../bulk/export`; gated to `vendor.manage`) and `/contracts`
(bulk activate/terminate/cancel via `POST /api/contracts/bulk/status`,
routed through the same `_transition` helper the single-row lifecycle
buttons use; CSV export) — the "select all N matching" affordance on each
resolves the whole filtered set via that resource's `GET .../ids` sibling
endpoint (`getVendorIds`/`getContractIds`/`getExpenseIds`) rather than only
the currently-loaded page.

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

- Append, don't replace. `loadMore` issues `page=N+1` and appends the new
  items **via `appendUnique` (`$lib/utils/pagination.ts`)** — never a raw
  `[...existing, ...res.items]` spread. Offset pagination can re-surface a
  row when the underlying set shifts between fetches (a new row inserted, a
  notification arriving), and a duplicated id crashes the keyed
  `{#each ... (id)}` with Svelte 5's `each_key_duplicate`. `appendUnique`
  drops incoming duplicates (existing row wins, order preserved); every
  load-more site — the list stores and the inline route/component loaders —
  uses it.
- "Showing all N" is the empty-string-of-pagination state — confirms
  for the user that they've reached the end. It is **only ever rendered
  behind `{:else if total > 0}`**, never on its own: `total` is the
  server's count of the whole filtered set, so a list that asks for one
  capped page and then states "Showing all {total}" is asserting that
  rows it never fetched do not exist. Six lists (budgets, intake,
  catalogs, requisitions, and the `/expenses` Reports + Cards sub-lists)
  shipped that way — 50 rows under a footer reading "Showing all 87",
  with no control to reach the other 37. `src/lib/utils/pagedListFooter.test.ts`
  is the guard: any file referencing a `<list>.showingAll` message must
  also reference the matching `<list>.loadMore`.
- Stores expose `total`, `page`, `hasMore`, and any mutating actions
  (create / delete / bulk-delete) keep `total` in sync without a
  refetch.

### Sequencing list fetches (`createRequestSequencer`)

Every list surface that can have a request in flight while something else
changes the list wires **`createRequestSequencer()`**
(`$lib/utils/requestSequence.ts`). This is now the whole app, not a handful of
pages: the list stores (`invoices`, `payments`, `contracts`, `expenses`,
`notifications`, `admin`, `workflows`), the list routes (`vendors`,
`vendors/screening`, `discounts`, `positive-pay`, `recurring`, `budgets`,
`intake`, `requisitions`, `catalogs`, `vendor-statements`, the four sub-lists
on `expenses`, the `workflows/[id]` builder canvas), `InvoiceModal`'s
line-item editor, and the three **analytics** surfaces whose loads are keyed
off a control rather than a filter — `/cfo` (its `$effect` fired three
unsequenced requests per keystroke on the two free-text money inputs, so the
cash-position curve and the "below minimum balance" breach banner could settle
on the figures for a *prefix* of what the field showed), `CfoMetrics` (the
30/90/180/365 horizon buttons) and `/tax` (the 1099 year selector). **A new
list surface wires it too** — don't hand-roll a second mechanism, and don't
leave it out because the page "only" edits a row after the first load has
landed (see the create/prepend note below). "Not a list" is not an exemption
either: any state written from a response that a control can re-issue needs
it, and a free-text control needs the debounce beside it (issue #168 /
`docs/decisions.md` §53 — anything an effect calls synchronously is inside its
tracking scope, so `load()` must `untrack` the free-text reads).

It answers two separate questions about a response — and takes one call that
retires in-flight requests. Conflating the two questions is a bug both ways:

```ts
const fetchSequence = createRequestSequencer();

async function fetch(params) {
    const token = fetchSequence.start();   // synchronously, before firing
    loading = true;
    try {
        const res = await api.get(`/api/things?${qs}`);
        if (!fetchSequence.canCommit(token)) return;   // stale → discard
        things = res.items;
    } finally {
        // NOT canCommit — see below.
        if (fetchSequence.isCurrentRequest(token)) loading = false;
    }
}
```

- **`canCommit(token)`** — may this response be written into state? False
  once a later `start()` has happened (the classic "search `acm` resolves
  after `acme`" race) **or** once a local edit superseded it.
- **`isCurrentRequest(token)`** — is this still the newest request I
  issued? Use it in the `finally` for the `loading` flag and for any
  load-error toast. Reading `canCommit` there leaves the spinner stuck on
  forever after a local edit, because no newer request exists to clear it.
- **`wasSupersededByEdit(token)`** — did a *local edit* retire this request?
  The third question, and only a **write** asks it. A save that PUTs the list
  and then re-reads it takes a token too, but its post-condition is narrower
  than a read's: only an edit invalidates what it sent. Reading `canCommit`
  there makes an unrelated newer read look like a conflict — which is how
  `InvoiceModal.saveLineItems` first shipped, leaving its dirty flag (and so
  the Save button) stuck on whenever an extraction poll's own reload landed
  mid-save.
- **`supersedeInFlight()`** — call it **immediately before** any helper
  that edits the list in place with no fetch of its own
  (`invoiceStore.update` / `patchLocal`, the vendors page's
  `applyVendorUpdate`). Without it the counter never moves, so an
  already-in-flight fetch resolves afterwards holding a pre-edit snapshot
  and silently reverts the edit — a user watching their approve, or the
  payment block they just lifted, undo itself. Requests issued *after* the
  edit are unaffected; they read server state that already includes it.

The superseded response is discarded, never merged — see
`docs/decisions.md` §23 for why re-applying the edit on top of it isn't
sound. A store with no local-mutation helper (every mutation re-fetches
through the sequencer, like `paymentStore`) needs no `supersedeInFlight`
call; say so in a comment rather than leaving the next reader to derive it.

Three things the sweep across the app settled, worth not re-deriving:

- **A create/prepend path needs no existing row.** "The mount fetch must have
  landed before there's a row to mutate" closes the race for edit and delete
  but *not* for New/Add, which is live while the first GET is still out. Every
  `upsert()` that can prepend an unseen row — `createUser`, `createFromTemplate`,
  a generated Positive Pay file — supersedes for that reason alone.
- **One sequencer per independent list, never one shared counter.** A page or
  store holding several lists (the `admin` store's users vs roles, the
  `notifications` store's list vs its 60s unread-count poll, `expenses`' four
  tabs, `discounts`' offers vs KPI dashboard) gives each its own. Sharing one
  would let an unrelated request mark another list's in-flight response
  un-committable and blank it. A local edit that writes state BOTH lists load
  (a mark-read, which moves `unread`) supersedes both.
- **An editor over a fetched list is the same surface.** The `workflows/[id]`
  canvas and `InvoiceModal`'s line-item table hold unsaved user edits, so a
  load resolving mid-edit doesn't just revert a row — it wipes work while the
  dirty flag stays set on something the user can no longer see. Both route
  every edit through a `markDirty()` that supersedes first.

**Related, and the other half of the same bug:** a filter `$effect` that calls
a `buildParams()` / `syncUrl()` helper reading `search` ends up depending on
`search` (Svelte tracks reads transitively through called functions), so every
keystroke fires an immediate un-debounced load *alongside* the debounced one.
Read `search` via `untrack(() => search)` in the params-builder, and untrack
`syncUrl()` wholesale — it is a writer of URL state, never a dependency
source. `tests-e2e/reactivity/search-debounce-race.spec.ts` is the guard.

**A debounce `$effect` must return its own teardown.** A `$effect` that arms a
timer and returns nothing leaves it armed when the component is destroyed, so
the callback runs against a page the user already left: `syncUrl()` rewrites
the address bar (SvelteKit's `replaceState` doesn't care which route is
mounted), and a list-store reload writes a pre-navigation snapshot into a
module-level store the *next* page shares. Always close the effect with

```ts
return () => clearTimeout(searchTimer);
```

Svelte also runs that teardown before each re-run, so it subsumes the
`clearTimeout` at the top of the body rather than fighting it. Enforced across
the tree by `src/lib/utils/effectTimerCleanup.test.ts`, a source scan that
fails any `$effect` body containing `setTimeout(` / `setInterval(` without a
matching `return () => clear…` — a deliberate static guard, because the
symptom only shows inside a sub-second window that no non-flaky e2e can pin.

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
- The "All" chip comes first; the active chip uses `var(--accent-strong)` +
  white — **not** `var(--accent)`, which is only 3.12:1 against white. See
  *Colour tokens and contrast* below.
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
| Status badge (invoice) | `<StatusBadge>` | `ui/StatusBadge.svelte` |
| Tinted badge (any tone) | `<Badge tone=…>` | `ui/Badge.svelte` |
| Money / currency | `<Money>` / `formatMoney` | `ui/Money.svelte` / `utils/money.ts` |
| Field-level advisory | `.field-warning` (`role="status"`) | `ui/FieldWarning.svelte` |
| Zero-data onboarding block | `.empty-state` (+ `data-testid`) | `ui/EmptyState.svelte` |
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

### Colour tokens and contrast (WCAG 1.4.3)

The palette in `src/app.css` `:root` is small and every colour token has a
**stated job**. They come in families, and picking the wrong member is the one
mistake this codebase kept making:

| Base token — text / icons / borders on a dark surface | `-strong` companion — the FILL behind white text | `-tint` / `-on-tint` pair — the status-badge recipe |
|---|---|---|
| `--accent` `#638cff` | `--accent-strong` `#3f5fd6` | `--accent-tint` + `--accent-on-tint` `#7d9bff` |
| `--success` `#1fa86a` | `--success-strong` `#177a4d` | `--success-tint` + `--success-on-tint` `#26b977` |
| `--danger` `#f87171` | `--danger-strong` `#c43535` | `--danger-tint` + `--danger-on-tint` `#f87171` |
| — | — | `--warning-tint` + `--warning-on-tint` `#dca014` |
| `--text-muted` `#8a8fa0` | — | `--muted-tint` + `--muted-on-tint` `#9aa0b2` |

- **Never put white text on a base token.** All three are mid-tones chosen to
  be legible *as text on the dark surfaces*; white on them is 3.06–3.12:1,
  well under the 4.5:1 bar. That is what the `-strong` half is for, and it is
  the only thing it is for — `--danger-strong` as *text* on `--surface` would
  be unreadable in the other direction.
- **A tinted badge takes the `-on-tint` text, never the base token.** A
  translucent tint lightens the dark surface *toward* text set in the same
  tone, so a base token — chosen to be legible on the BARE surface — lands
  just under the bar once composited. `--accent` on `--accent-tint` is 4.48:1:
  two hundredths short, which is why 29 badges shipped that way unnoticed.
  Write `background: var(--accent-tint); color: var(--accent-on-tint)` and
  nothing else; the pair is calibrated together and carries ≥0.7 of margin on
  both `--bg` and `--surface`. `--danger-on-tint` equals `--danger` on purpose
  — red needs no lift — so that the rule has no exception to remember.
- **`--surface-2` `#232b44` is the hostile surface.** Only `--text` clears
  4.5:1 on it (11.0:1). `--text-muted` is 4.34:1 there — the failure the axe
  guard originally caught. Muted text belongs on `--bg` or `--surface`.
- **Never write a `var(--token, fallback)` fallback.** Every token above is
  declared, so the fallback is dead code that becomes the *wrong colour* the
  day a token is renamed. `--surface-2` shipped for months as
  `var(--surface-2, #232b44)` with the token undefined, two call sites
  disagreeing about the value — that is the bug this rule prevents. Same for
  `--font-mono`, the canonical monospace stack.
- **Text colour comes from a token, not a literal.** A bare `color: #<hex>`
  with no background in the same rule renders on whatever the cascade supplies,
  so it has to be legible on `--bg` and `--surface` — and `#e04040` (the old
  status red) was 4.11:1 on `--surface`, failing in 106 places. `#fff` / `#000`
  are exempt: they're the deliberate on-a-coloured-fill choices. Decorative
  fills (a chart bar, a confidence dot, an SVG `fill`) carry no text and are
  not covered by this.

Two guards enforce it, and neither subsumes the other:

- **`src/lib/a11y/tokenPairing.test.ts`** (vitest, no browser) scans **every**
  stylesheet in `src/` — `app.css` plus every `<style>` block — for a rule that
  sets both `color` and a `background`, resolves both through the palette, and
  fails below 4.5:1 (3:1 when the rule itself declares a large-text size). It
  also fails a bare literal `color:` that can't clear the bar on `--bg` or
  `--surface`, a fallback that contradicts its token, a `var()` on a token
  nothing assigns, and asserts the table above directly. It also measures a
  rule that fades **itself** with `opacity`, because opacity composites text
  and its background down onto the backdrop — so a token that clears the bar at
  full strength can render under it (`--text-muted` at `.85` is 4.24:1 on
  `--surface`). **Don't dim already-muted text with `opacity`**: the token has
  done that job, and the fade only spends contrast. It measures a **translucent
  background** the same way and in the same pass, compositing the tint over each
  backdrop before judging the pair — the check that found all 29 badges. And it
  asserts each `-tint` / `-on-tint` pair directly, so a tone that drifts names
  one token instead of the dozen sites that spelled it. Pure scanners live in
  `a11y/cssAudit.ts`; the WCAG math in `a11y/contrast.ts`.
- **`tests-e2e/a11y/axe.spec.ts`** covers what the scanner deliberately can't:
  a rule setting only `color` inherits its background through the cascade at
  runtime, and an **ancestor's** `opacity` fades a descendant the scan reads as
  fine (a revoked-row fade put `/admin/api-keys`' status pill at 2.44:1). Add a
  route here when you add a page carrying dialogs or dense controls.

What is left is **consistency, not contrast**. 202 rules used to spell a tinted
badge as a hand-rolled `rgba()` plus a literal hex — 44 spellings of what the
five pairs above name. `ui/Badge.svelte` now owns the recipe and roughly half
the badge-shaped rules have moved onto it; **~62 remain**, concentrated in
`/expenses`, `/requisitions`, `/payments`, `InvoiceModal`, `RequisitionModal`,
`ExpenseModal` and `/admin/webhooks`. Every one of them passes the guard, so
this is design-system debt rather than a defect — but the tokens standardise on
alpha `.15`, so converting a `.1` or `.12` rule *visibly strengthens* that
badge's tint. That is why it moves in tranches you can attribute rather than
one sweep: check the rendered result, and check what a distinction was carrying
before collapsing it. Reach for `<Badge>` in new code and whenever you are
already editing one of these rules. Tracked in `docs/followups.md`.

**A failure means changing the colour, never relaxing the rule** — there is no
suppression mechanism, because the `-strong` companions mean a correct answer
always exists. Rationale + what was rejected: `docs/decisions.md` §28.

**The one runtime hole:** white-label theming lets a tenant overwrite
`--accent` / `--accent-strong` with any valid hex (`stores/brandTheme.ts`
`brandThemeVars`), which no static scan can see. `accentStrongContrast` /
`accentStrongMeetsAA` (same file, same WCAG primitive) drive a `FieldWarning`
advisory on both surfaces that edit it — `/organization` Branding and the
`/admin/partner` child-branding modal. Advisory, not a block: the backend
accepts any valid hex and the brand is the tenant's call.

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
