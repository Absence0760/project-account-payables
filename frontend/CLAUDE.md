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
```

## Routes → API mappings

| Route | File | API calls |
|-------|------|-----------|
| `/` (tenant) | `routes/+page.svelte` | `GET /api/dashboard` |
| `/` (no-tenant) | `lib/components/marketing/Landing.svelte` (inline in `+layout.svelte`) | Marketing landing page with features, pricing, signup CTA |
| `/signup` | `routes/signup/+page.svelte` | `GET /api/public-config`, `GET /api/signup/slug-check`, `POST /api/signup/start` |
| `/verify` | `routes/verify/+page.svelte` | `POST /api/signup/complete` |
| `/login` | `routes/login/+page.svelte` | `POST /api/auth/login`, `GET /api/auth/sso/config` (renders SSO button when enabled) |
| `/login/mfa` | `routes/login/mfa/+page.svelte` | `POST /api/auth/mfa/challenge/email`, `POST /api/auth/mfa/verify` — second-factor step after password |
| `/login/sso-callback` | `routes/login/sso-callback/+page.svelte` | `POST /api/auth/sso/callback` — exchanges OIDC code+state for our JWT after IdP redirect |
| `/profile` | `routes/profile/+page.svelte` | `POST /api/auth/mfa/enroll`, `POST /api/auth/mfa/enroll/verify`, `POST /api/auth/mfa/disable` — manage two-factor |
| `/change-password` | `routes/change-password/+page.svelte` | `POST /api/auth/change-password` |
| `/invoices` | `routes/invoices/+page.svelte` | `GET /api/invoices` (returns `priors_summary`), `GET /api/invoices/counts` (status-chip tallies), `GET /api/invoices/{id}` (`?id=` deep-link opens the detail modal), `POST /api/invoices/upload` (supports multi-file; frontend batches 5 at a time via `Promise.allSettled`), `PATCH /api/invoices/{id}`, `GET /api/invoices/{id}/priors`, `GET /api/invoices/{id}/summary` (audit-log summary; `POST .../summary/regenerate` for admins/managers), bulk ops |
| `/vendors` | `routes/vendors/+page.svelte` | `GET /api/vendors` |
| `/payments` | `routes/payments/+page.svelte` | `GET /api/payments/{queue,summary,runs/}`, `GET /api/payments`, `POST /api/payments/runs` (creates draft), `GET /api/payments/runs/{id}` + `POST .../execute` (via `RunDetailModal`) |
| `/exceptions` | `routes/exceptions/+page.svelte` | `GET /api/exceptions`, `PATCH /api/exceptions/{id}` |
| `/workflows` | `routes/workflows/+page.svelte` | `GET /api/workflows`, `POST /api/workflows` |
| `/workflows/[id]` | `routes/workflows/[id]/+page.svelte` | `GET/PATCH /api/workflows/{id}`, `GET /api/organization` |
| `/audit` | `routes/audit/+page.svelte` | `GET /api/audit/export` (JSON + CSV) — SOX auditor console, admin/CFO only (content-gated on `auth.isCfo`; backend 403s otherwise). Date-range or by-invoice query + CSV download. |
| `/organization` | `routes/organization/+page.svelte` | `GET/PATCH /api/organization` |
| `/admin` | `routes/admin/+page.svelte` | `GET/POST/PATCH/DELETE /api/admin/users`, `GET /api/admin/roles` |
| `/cfo` | `routes/cfo/+page.svelte` | `GET /api/analytics/{cashflow_forecast,cashflow_whatif,cash_position}`, `GET /api/analytics/export/cashflow_forecast` (admin + cfo) — predictive cash-flow dashboard |
| `/tax` | `routes/tax/+page.svelte` | `GET /api/tax/1099-report?year=` (via `lib/api/tax.ts`) — 1099 vendor reporting dashboard. KPI summary, year selector, per-vendor 1099-eligible / W-9-on-file / TIN-verified chips, >$600 threshold flags, vendor search + filter chips (all / reportable / missing W-9 / over threshold). admin/ap_manager/cfo. The report outer-joins vendors→payments, so the row set is the tenant's vendor list and the year only re-aggregates each vendor's YTD; the table is empty only when there are no vendors. |
| `/notifications` | `routes/notifications/+page.svelte` | `GET /api/notifications` (list, `?unread_only=`), `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all` — clickable rows open the related invoice (`/invoices?id=`) |
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
- Typed feature helpers wrap `api` per domain — e.g. `src/lib/api/audit.ts` (`getInvoiceAuditLog`, `getAuditExport`, `downloadAuditExportCsv`) over the SOX audit endpoints, with `AuditEntry` / `AuditFieldChange` types in `src/lib/types/audit.ts`. The invoice-modal Activity timeline renders `details.changes` (per-field before/after) from these. `src/lib/api/tax.ts` (`get1099Report`) wraps the 1099 endpoint, with `Report1099` / `Vendor1099Row` types in `src/lib/types/tax.ts`.

### Money formatting — `src/lib/utils/money.ts` + `ui/Money.svelte`

**Never hand-roll `Intl.NumberFormat` for currency.** `formatMoney(amount, opts?, placeholder?)` is the single locale-aware formatter (currency-code driven via `Intl.NumberFormat`, browser locale), and `<Money amount currency? whole? accounting? mono? />` is the component over it. Each amount renders with its **own** ISO 4217 code — never a hardcoded `$`.

- Per-row amounts pass their own `currency` (invoices, credit memos, POs, cards, payments).
- Tenant-wide roll-ups (dashboard KPIs, payment-summary totals, aging, CFO forecast) have no per-row currency, so they use the **org default** from the `orgCurrency` store (`src/lib/stores/orgSettings.svelte.ts`). It lazy-loads `Organization.settings.invoice_defaults.currency` from `GET /api/organization` once per session and falls back to USD for non-admin roles (403) or any error. Call `orgCurrency.ensureLoaded()` from the page's init `$effect` and read `orgCurrency.currency`.
- `formatMoney` accepts `number | string-Decimal | null` and returns the placeholder (`—` by default) for null/empty/non-finite; a bad currency code falls back to USD rather than throwing.

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
| `notificationStore` | `notifications.svelte.ts` | `items`, `unread`, `total`, `loading`, `hasMore`, `prefs` | `fetchList({unreadOnly})`, `loadMore()`, `fetchUnreadCount()`, `markRead(id)`, `markAllRead()`, `fetchPrefs()`, `updatePrefs()`, `startPolling()`/`stopPolling()` (60s unread-count poll for the sidebar badge; started from `+layout` when signed in) |

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
- `Money.svelte` — locale-aware currency display. `<Money amount={row.amount} currency={row.currency} />`. Opt-in `whole` (no decimals), `accounting` (parenthesised negatives), `mono` (tabular-nums). Over `utils/money.ts::formatMoney`; see *Money formatting* above. Use this (or `formatMoney` in script) for every currency value — don't write `Intl.NumberFormat` inline.

The visual styling for all of the above lives **globally in `src/app.css`** (class-scoped: `.workspace`, `.grid-container td`, `.filter-chip`, `.modal`, `.kpi`, …) so route pages carry no duplicated `<style>`. Feature components below keep their own scoped CSS (Svelte's `.svelte-<hash>` outranks the bare-class globals).

**`modals/` — feature dialogs:**
- `InvoiceModal.svelte` — invoice detail/edit modal
- `AdvancedSearchModal.svelte` — invoice search filters
- `BulkRecodeGLModal.svelte` — admin bulk GL re-code preview/apply
- `ApprovalMatrixEditor.svelte` — approval-chain matrix builder
- `RunDetailModal.svelte` — payment run detail; status, total, payments table; Execute button when run is `draft`

**`marketing/`** — `Landing.svelte` + `Pricing.svelte` (public no-tenant route).
**`layout/`** — `Sidebar.svelte` (collapsed/expanded nav, profile popover).

### Types (`src/lib/types/`)

- `invoice.ts` — `Invoice`, `InvoiceStatus` (12 statuses), `VALID_TRANSITIONS`, `AdvancedSearchFilters`
- `payment.ts` — `Payment`, `PaymentRun`, `PaymentStatus`, `PaymentMethod` (ach, wire, check, virtual_card)
- `workflow.ts` — `WorkflowDefinition`, `WorkflowStep`, step configs (extraction, approval, erp_export)
- `admin.ts` — `AdminUser`, `Role` (admin, ap_manager, ap_clerk, cfo)
- `tax.ts` — `Report1099`, `Vendor1099Row` (1099 reporting dashboard)

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
| Load more | `.btn-load-more` / `.load-more-row` / `.load-more-end` | per-route, copy /admin |
| Modal dialog | `.modal[role="dialog"]` + `.backdrop` | `ui/Modal.svelte` |
| KPI card | `.kpi` / `.kpi-value` / `.kpi-label` | `ui/KpiCard.svelte` |
| Status badge | `<StatusBadge>` | `ui/StatusBadge.svelte` |
| Money / currency | `<Money>` / `formatMoney` | `ui/Money.svelte` / `utils/money.ts` |

(All Source paths are under `$lib/components/`.) If a shared style is
missing, add it to `src/app.css` (class-scoped) — not a per-route
`<style>`. If you need a brand-new pattern, add a component under
`$lib/components/ui/` and document it here. **Do not** invent a new
class name for an existing pattern, and **do not** re-introduce a
per-route copy of the table/modal/chip/shell CSS.

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
