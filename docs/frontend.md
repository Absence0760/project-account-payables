# Frontend

SvelteKit 2 + Svelte 5 single-page application with multi-tenant subdomain routing.

## Stack

- **Framework**: SvelteKit 2 with Svelte 5 (runes)
- **Language**: TypeScript
- **Package manager**: pnpm
- **Adapters**: `@sveltejs/adapter-static` (GitHub Pages), `@sveltejs/adapter-vercel` (Vercel)
- **Styling**: normalize.css + custom CSS in `src/app.css`
- **Icons**: unplugin-icons with `@iconify-json/material-symbols`
- **Markdown**: mdsvex

## Folder Structure

```
src/
  lib/
    api.ts              # API client — fetch wrapper with JWT auth + tenant header
    tenant.ts           # Subdomain extraction (getTenantSlug)
    components/         # Svelte components
      Sidebar.svelte
      StatusBadge.svelte
      InvoiceModal.svelte
      AdvancedSearchModal.svelte
    stores/             # Svelte 5 rune stores
      auth.svelte.ts    # Auth state (login, logout, current user)
      invoices.svelte.ts # Invoice data fetched from backend API
      sidebar.svelte.ts  # Sidebar collapsed/expanded state
    types/
      invoice.ts        # Invoice interface, status types, filter types
  routes/
    +layout.svelte      # App shell — sidebar + auth guard + tenant check
    +page.svelte        # Dashboard — fetches GET /api/dashboard
    invoices/
      +page.svelte      # Invoice list — fetches GET /api/invoices
    login/
      +layout.svelte    # Bare layout (no sidebar)
      +page.svelte      # Login form — POSTs to /api/auth/login
  app.css               # Global styles
  app.d.ts              # App-level TypeScript declarations
```

## Multi-Tenant Routing

The frontend uses **subdomain-based tenant routing**:

1. `src/lib/tenant.ts` extracts the subdomain from `window.location.hostname`:
   - `acme.localhost:7777` → `"acme"`
   - `techflow.app.com` → `"techflow"`
   - `localhost:7777` → `null` (no tenant)

2. `src/lib/api.ts` includes the `X-Tenant-Slug` header on every API request

3. `src/routes/+layout.svelte` checks for a tenant:
   - No tenant → shows a "no tenant" page with links to available tenants
   - Has tenant, not logged in → redirects to `/login`
   - Has tenant, logged in → renders the app shell with sidebar

## API Connection

The backend URL is set via the `PUBLIC_API_URL` environment variable in `.env`:

```
PUBLIC_API_URL=http://localhost:8000
```

This uses SvelteKit's `$env/static/public`, which embeds the value at **build time**. All tenants share the same backend URL — tenant routing is handled via the `X-Tenant-Slug` header, not separate backend URLs.

## API Endpoints Used

| Frontend page  | API endpoint          | Method | Database      |
|----------------|-----------------------|--------|---------------|
| Login          | `/api/auth/login`     | POST   | Control plane |
| Layout (user)  | `/api/auth/me`        | GET    | Control plane |
| Dashboard      | `/api/dashboard`      | GET    | Tenant DB     |
| Invoice list   | `/api/invoices`       | GET    | Tenant DB     |
| Invoice edit   | `/api/invoices/{id}`  | PATCH  | Tenant DB     |

## Stores

- **`auth.svelte.ts`** — manages login/logout, stores current user, exposes `loggedIn` state
- **`invoices.svelte.ts`** — fetches invoices from the API with query params for filtering; `update()` PATCHes changes to the backend
- **`sidebar.svelte.ts`** — UI-only, tracks sidebar collapsed state

## Development Commands

```bash
pnpm i                    # Install dependencies
cp .env.example .env      # First time — set API URL
pnpm dev                  # Dev server on :7777
pnpm build                # Production build
pnpm preview              # Preview build on :8888
pnpm check                # Type-check
```

## Local Dev URLs

Access the app via tenant subdomains:

| Tenant    | URL                            | Login                        |
|-----------|--------------------------------|------------------------------|
| Acme      | http://acme.localhost:7777     | `demo@acme.com` / `demo`    |
| TechFlow  | http://techflow.localhost:7777 | `admin@techflow.com` / `demo`|

`*.localhost` resolves natively in Chrome, Firefox, and Edge. See [multi-tenancy.md](multi-tenancy.md) for Safari setup.

## Conventions

- Use Svelte 5 runes syntax (`$state`, `$derived`, `$effect`, `$props`) — not the legacy options API
- TypeScript throughout; `lang="ts"` on all `<script>` blocks
- All data fetching goes through `src/lib/api.ts` — never call `fetch()` directly for API requests
- `BASE_PATH` env var is set to `/<repo-name>` during CI builds for correct asset paths

## Deployment

- **GitHub Pages**: push to `main` triggers `.github/workflows/deploy.yml`, which builds and deploys automatically
- The `build/.nojekyll` file is created at build time to bypass Jekyll processing
