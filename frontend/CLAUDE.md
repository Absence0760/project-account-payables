# Project Overview

This is the SvelteKit frontend for the Account Payables application. It connects to a FastAPI backend for all data.

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
    api.ts              # API client — fetch wrapper with JWT auth
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
    +layout.svelte      # App shell — sidebar + auth guard
    +page.svelte        # Dashboard — fetches GET /api/dashboard
    invoices/
      +page.svelte      # Invoice list — fetches GET /api/invoices
    login/
      +layout.svelte    # Bare layout (no sidebar)
      +page.svelte      # Login form — POSTs to /api/auth/login
  app.css               # Global styles
  app.d.ts              # App-level TypeScript declarations
.env                    # PUBLIC_API_URL (backend URL)
.env.example            # Documented env var template
```

## Backend Connection

The frontend communicates with the FastAPI backend via a single API client (`src/lib/api.ts`).

### API URL configuration

The backend URL is set via the `PUBLIC_API_URL` environment variable in `.env`:

```
PUBLIC_API_URL=http://localhost:8000
```

This uses SvelteKit's `$env/static/public`, which embeds the value at **build time**. To deploy to different environments:

```bash
# Dev (default)
pnpm dev

# QA build
PUBLIC_API_URL=https://api-qa.example.com pnpm build

# Prod build
PUBLIC_API_URL=https://api.example.com pnpm build
```

### Authentication

- JWT-based auth; token stored in `localStorage`
- `src/lib/api.ts` automatically attaches `Authorization: Bearer <token>` to all requests
- On 401 responses, the token is cleared and the user is redirected to `/login`
- The root `+layout.svelte` guards all routes — unauthenticated users are redirected to `/login`
- Login page (`/login`) has its own layout without the sidebar

### API endpoints used

| Frontend page  | API endpoint          | Method |
|----------------|-----------------------|--------|
| Login          | `/api/auth/login`     | POST   |
| Layout (user)  | `/api/auth/me`        | GET    |
| Dashboard      | `/api/dashboard`      | GET    |
| Invoice list   | `/api/invoices`       | GET    |
| Invoice edit   | `/api/invoices/{id}`  | PATCH  |

### Stores

- **`auth.svelte.ts`** — manages login/logout, stores current user, exposes `loggedIn` state
- **`invoices.svelte.ts`** — fetches invoices from the API with query params for filtering; `update()` PATCHes changes to the backend
- **`sidebar.svelte.ts`** — UI-only, tracks sidebar collapsed state

## Development

```bash
pnpm i                    # Install dependencies
cp .env.example .env      # First time — set API URL
pnpm dev                  # Dev server on :7777
pnpm build      # Production build
pnpm preview    # Preview build on :8888
pnpm check      # Type-check
```

## Conventions

- Use Svelte 5 runes syntax (`$state`, `$derived`, `$effect`, `$props`) — not the legacy options API
- TypeScript throughout; `lang="ts"` on all `<script>` blocks
- Prefer `@sveltejs/adapter-static` for GitHub Pages output (output dir: `build/`)
- `BASE_PATH` env var is set to `/<repo-name>` during CI builds for correct asset paths
- All data fetching goes through `src/lib/api.ts` — never call `fetch()` directly for API requests

## Deployment

- **GitHub Pages**: push to `main` triggers `.github/workflows/deploy.yml`, which builds and deploys automatically
- The `build/.nojekyll` file is created at build time to bypass Jekyll processing

## Pull Request Guidelines

- Target branch: `main`
- Keep PRs focused; one feature or fix per PR
- Draft PRs are fine for work-in-progress
