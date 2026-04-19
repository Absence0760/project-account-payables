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
| `/invoices` | `routes/invoices/+page.svelte` | `GET /api/invoices` (returns `priors_summary`), `POST /api/invoices/upload`, `PATCH /api/invoices/{id}`, `GET /api/invoices/{id}/priors`, bulk ops |
| `/vendors` | `routes/vendors/+page.svelte` | `GET /api/vendors` |
| `/payments` | `routes/payments/+page.svelte` | `GET /api/payments`, `GET/POST /api/payments/runs`, `POST /api/payments/runs/{id}/execute` |
| `/exceptions` | `routes/exceptions/+page.svelte` | `GET /api/exceptions`, `PATCH /api/exceptions/{id}` |
| `/workflows` | `routes/workflows/+page.svelte` | `GET /api/workflows`, `POST /api/workflows` |
| `/workflows/[id]` | `routes/workflows/[id]/+page.svelte` | `GET/PATCH /api/workflows/{id}`, `GET /api/organization` |
| `/organization` | `routes/organization/+page.svelte` | `GET/PATCH /api/organization` |
| `/admin` | `routes/admin/+page.svelte` | `GET/POST/PATCH/DELETE /api/admin/users`, `GET /api/admin/roles` |

Root layout (`+layout.svelte`) routing logic:
- No tenant subdomain → Landing component (public) or `<slot />` for `/signup` / `/verify`
- Tenant present, not logged in → redirect to `/login`
- Tenant present, logged in, `must_change_password=true` → redirect to `/change-password`
- Tenant present, logged in, flag clear → app shell with sidebar

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
| `auth` | `auth.svelte.ts` | `user`, `loggedIn`, role checks (`isAdmin`, `isManager`, `isCfo`, `isClerkOnly`) | `login()`, `logout()`, `fetchUser()`, `hasRole()` |
| `invoiceStore` | `invoices.svelte.ts` | `all`, `loading`, `total`, `statusCounts` | `fetch(params)`, `fetchCounts()`, `update(id, changes)` |
| `paymentStore` | `payments.svelte.ts` | `all`, `loading`, `total` | `fetch(params)` |
| `workflowStore` | `workflows.svelte.ts` | `all`, `loading`, `activeSteps` | `fetch()`, `fetchActiveSteps()`, `getById()`, `create()`, `update()` |
| `adminStore` | `admin.svelte.ts` | `users`, `roles`, `loading` | `fetchUsers()`, `fetchRoles()`, `createUser()`, `updateUser()`, `deleteUser()` |
| `sidebar` | `sidebar.svelte.ts` | `collapsed` | `toggle()` |

### Components (`src/lib/components/`)

- `Sidebar.svelte` — nav sidebar (collapsed/expanded)
- `StatusBadge.svelte` — invoice status display
- `InvoiceModal.svelte` — invoice detail/edit modal
- `AdvancedSearchModal.svelte` — invoice search filters
- `Toast.svelte` — toast notifications

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
