# Project Overview

This is the SvelteKit frontend for the Account Payables application. It connects to a FastAPI backend via subdomain-based multi-tenant routing.

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
.env.example            # PUBLIC_API_URL config template
```

## Multi-Tenant Routing

- `src/lib/tenant.ts` extracts the subdomain → `acme.localhost` becomes slug `acme`
- `src/lib/api.ts` sends `X-Tenant-Slug` header on every request
- `+layout.svelte` shows "no tenant" page if accessed without a subdomain

## Backend Connection

The backend URL is set via `PUBLIC_API_URL` in `.env` (build-time). All tenants share one backend — tenant routing is via the header, not separate URLs.

### API endpoints used

| Frontend page  | API endpoint          | Method | Database      |
|----------------|-----------------------|--------|---------------|
| Login          | `/api/auth/login`     | POST   | Control plane |
| Layout (user)  | `/api/auth/me`        | GET    | Control plane |
| Dashboard      | `/api/dashboard`      | GET    | Tenant DB     |
| Invoice list   | `/api/invoices`       | GET    | Tenant DB     |
| Invoice edit   | `/api/invoices/{id}`  | PATCH  | Tenant DB     |

## Development

```bash
pnpm i                    # Install dependencies
cp .env.example .env      # First time — set API URL
pnpm dev                  # Dev server on :7777
pnpm build                # Production build
pnpm preview              # Preview build on :8888
pnpm check                # Type-check
```

Access via: http://acme.localhost:7777 or http://techflow.localhost:7777

## Conventions

- Use Svelte 5 runes syntax (`$state`, `$derived`, `$effect`, `$props`) — not the legacy options API
- TypeScript throughout; `lang="ts"` on all `<script>` blocks
- All data fetching goes through `src/lib/api.ts` — never call `fetch()` directly for API requests
- `BASE_PATH` env var is set to `/<repo-name>` during CI builds for correct asset paths

## Deployment

- **GitHub Pages**: push to `main` triggers `.github/workflows/deploy.yml`
- The `build/.nojekyll` file is created at build time to bypass Jekyll processing

## Pull Request Guidelines

- Target branch: `main`
- Keep PRs focused; one feature or fix per PR
- Draft PRs are fine for work-in-progress
