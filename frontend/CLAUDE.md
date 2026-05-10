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
| `/` (no-tenant) | `lib/components/Landing.svelte` (inline in `+layout.svelte`) | Marketing landing page with features, pricing, signup CTA |
| `/signup` | `routes/signup/+page.svelte` | `GET /api/public-config`, `GET /api/signup/slug-check`, `POST /api/signup/start` |
| `/verify` | `routes/verify/+page.svelte` | `POST /api/signup/complete` |
| `/login` | `routes/login/+page.svelte` | `POST /api/auth/login`, `GET /api/auth/sso/config` (renders SSO button when enabled) |
| `/login/mfa` | `routes/login/mfa/+page.svelte` | `POST /api/auth/mfa/challenge/email`, `POST /api/auth/mfa/verify` — second-factor step after password |
| `/login/sso-callback` | `routes/login/sso-callback/+page.svelte` | `POST /api/auth/sso/callback` — exchanges OIDC code+state for our JWT after IdP redirect |
| `/profile` | `routes/profile/+page.svelte` | `POST /api/auth/mfa/enroll`, `POST /api/auth/mfa/enroll/verify`, `POST /api/auth/mfa/disable` — manage two-factor |
| `/change-password` | `routes/change-password/+page.svelte` | `POST /api/auth/change-password` |
| `/invoices` | `routes/invoices/+page.svelte` | `GET /api/invoices` (returns `priors_summary`), `POST /api/invoices/upload` (supports multi-file; frontend batches 5 at a time via `Promise.allSettled`), `PATCH /api/invoices/{id}`, `GET /api/invoices/{id}/priors`, bulk ops |
| `/vendors` | `routes/vendors/+page.svelte` | `GET /api/vendors` |
| `/payments` | `routes/payments/+page.svelte` | `GET /api/payments/{queue,summary,runs/}`, `GET /api/payments`, `POST /api/payments/runs` (creates draft), `GET /api/payments/runs/{id}` + `POST .../execute` (via `RunDetailModal`) |
| `/exceptions` | `routes/exceptions/+page.svelte` | `GET /api/exceptions`, `PATCH /api/exceptions/{id}` |
| `/workflows` | `routes/workflows/+page.svelte` | `GET /api/workflows`, `POST /api/workflows` |
| `/workflows/[id]` | `routes/workflows/[id]/+page.svelte` | `GET/PATCH /api/workflows/{id}`, `GET /api/organization` |
| `/organization` | `routes/organization/+page.svelte` | `GET/PATCH /api/organization` |
| `/admin` | `routes/admin/+page.svelte` | `GET/POST/PATCH/DELETE /api/admin/users`, `GET /api/admin/roles` |

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

### Tenant — `src/lib/tenant.ts`

`getTenantSlug()` extracts subdomain: `acme.localhost:7777` → `"acme"`, plain `localhost` → `null`.

### Stores (`src/lib/stores/`) — Svelte 5 rune stores

| Store | File | State | Key methods |
|-------|------|-------|-------------|
| `auth` | `auth.svelte.ts` | `user` (incl. `mfa_enabled`, `mfa_required_by_org`), `loggedIn`, role checks (`isAdmin`, `isManager`, `isCfo`, `isClerkOnly`) | `login()` (returns `{kind:'ok'} \| {kind:'mfa', challenge}` — MFA branch routes to `/login/mfa`), `completeMfa(token, code, method)`, `requestEmailMfa(token)`, `logout()`, `fetchUser()`, `hasRole()`, `hasAnyRole()` |
| `invoiceStore` | `invoices.svelte.ts` | `all`, `loading`, `total`, `statusCounts` | `fetch(params)`, `fetchCounts()`, `update(id, changes)` |
| `paymentStore` | `payments.svelte.ts` | `all`, `loading`, `total` | `fetch(params)` |
| `workflowStore` | `workflows.svelte.ts` | `all`, `loading`, `activeSteps` | `fetch()`, `fetchActiveSteps()`, `getById()`, `create()`, `update()` |
| `adminStore` | `admin.svelte.ts` | `users`, `roles`, `loading` | `fetchUsers()`, `fetchRoles()`, `createUser()`, `updateUser()`, `deleteUser()` |
| `sidebar` | `sidebar.svelte.ts` | `collapsed` | `toggle()` |

### Components (`src/lib/components/`)

- `Sidebar.svelte` — nav sidebar (collapsed/expanded), profile popover with link to `/profile`
- `StatusBadge.svelte` — invoice status display
- `InvoiceModal.svelte` — invoice detail/edit modal
- `AdvancedSearchModal.svelte` — invoice search filters
- `RunDetailModal.svelte` — payment run detail; shows status, total, payments table; Execute button when run is `draft`
- `Toast.svelte` — toast notifications
- `Landing.svelte` + `Pricing.svelte` — public marketing landing page (no-tenant route)

### Types (`src/lib/types/`)

- `invoice.ts` — `Invoice`, `InvoiceStatus` (12 statuses), `VALID_TRANSITIONS`, `AdvancedSearchFilters`
- `payment.ts` — `Payment`, `PaymentRun`, `PaymentStatus`, `PaymentMethod` (ach, wire, check, virtual_card)
- `workflow.ts` — `WorkflowDefinition`, `WorkflowStep`, step configs (extraction, approval, erp_export)
- `admin.ts` — `AdminUser`, `Role` (admin, ap_manager, ap_clerk, cfo)

## Multi-tenant routing

- `src/lib/tenant.ts` extracts subdomain → `acme.localhost` becomes `"acme"`
- `src/lib/api.ts` sends `X-Tenant-Slug` header on every request
- `+layout.svelte` shows "no tenant" page if accessed without a subdomain

Access via: http://acme.localhost:7777 or http://techflow.localhost:7777

## Design system & UI patterns

Reuse these patterns instead of inventing new ones. Reach for the
existing component first; only deviate with a written justification.

### Page layout

Every authenticated route is wrapped in `<div class="workspace">`:

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

The page title goes in `<header class="toolbar">`, with primary
actions (e.g. `+ Invite User`, `+ Upload Invoices`) right-aligned in
a `<div class="toolbar-actions">`.

### Search (`SearchBox`)

Pill-shaped search input with a magnifier-glass SVG. Single component:

```svelte
<script lang="ts">
    import SearchBox from '$lib/components/SearchBox.svelte';
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
    import BulkBar from '$lib/components/BulkBar.svelte';
    import BulkDeleteButton from '$lib/components/BulkDeleteButton.svelte';

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

Inline, pill-shaped buttons above the table:

```svelte
<nav class="filters">
    <button class="filter-chip" class:active={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>
        All <span class="count">{total}</span>
    </button>
    {#each STATUSES as s}
        <button class="filter-chip" class:active={statusFilter === s} onclick={() => (statusFilter = s)}>
            {STATUS_LABELS[s]} <span class="count">{statusCount(s)}</span>
        </button>
    {/each}
</nav>
```

Active chip uses `var(--accent)` background + white text. The "All"
chip always comes first.

### Modals

Backdrop + centred dialog:

```svelte
<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) onclose(); }}>
    <div class="modal" role="dialog" aria-label="<Action>">
        <h2><Heading></h2>
        <form onsubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
            <!-- labelled fields -->
            <div class="modal-footer">
                <button type="button" class="btn-cancel" onclick={onclose}>Cancel</button>
                <button type="submit" class="btn-primary" disabled={saving}>
                    {saving ? 'Saving…' : 'Save'}
                </button>
            </div>
        </form>
    </div>
</div>
```

- Backdrop click on its own element (not propagated from children) closes.
- Cancel sits left of the primary action in the footer.
- Required-field markers use `<em class="required">*</em>`.

### Per-row actions

Use the shared `<RowAction>` component (`$lib/components/RowAction.svelte`)
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

### Class-name conventions

| Pattern | Class | Source |
|---|---|---|
| Page wrapper | `.workspace` | every route's `+page.svelte` |
| Page header | `.toolbar` | each route |
| Search input | `.search-box` | `$lib/components/SearchBox.svelte` |
| Bulk bar | `.bulk-bar` | `$lib/components/BulkBar.svelte` |
| Bulk delete | `.bulk-delete-btn` (+ `.armed`) | `$lib/components/BulkDeleteButton.svelte` |
| Bulk action | `.bulk-action-btn` | per-route, but always inside a BulkBar |
| Per-row action | `<RowAction>` (variant + armed) | `$lib/components/RowAction.svelte` |
| Filter pill | `.filter-chip` (+ `.active`) | per-route, copy /invoices |
| Load more | `.btn-load-more` / `.load-more-row` / `.load-more-end` | per-route, copy /admin |
| Modal dialog | `.modal[role="dialog"]` + `.backdrop` | per-route |
| Status badge | `<StatusBadge>` | `$lib/components/StatusBadge.svelte` |

If you need a new pattern, add the component to `$lib/components/`
and document it here. **Do not** invent a new class name for an
existing pattern.

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

- **GitHub Pages**: push to `main` triggers `.github/workflows/deploy.yml`
- `build/.nojekyll` created at build time to bypass Jekyll processing
