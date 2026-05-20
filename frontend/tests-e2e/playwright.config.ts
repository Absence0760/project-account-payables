import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright e2e config for the frontend.
 *
 * The frontend reads tenant context from the subdomain
 * (`<slug>.localhost:7777`). Chromium resolves `*.localhost` to
 * 127.0.0.1 by default per RFC 6761, so no /etc/hosts changes are
 * needed.
 *
 * Parallelism + isolation: each worker is pinned to its own
 * `e2e<workerIndex+1>` tenant via the worker-scoped `tenantSlug`
 * fixture in `fixtures/helpers.ts`. The seed
 * (`backend/scripts/seed.py`) provisions `AP_E2E_TENANT_COUNT`
 * (default 4) such tenants. A spec that creates / deletes / mutates
 * data in worker N's DB cannot collide with worker M's.
 *
 * The seeded `acme` + `techflow` tenants stay around for the
 * cross-tenant-isolation specs that need fixed slugs
 * (`tenant-isolation.spec.ts`, `cross-tenant-writes.spec.ts`).
 *
 * Prereqs (CI handles all of these):
 *   - Postgres running on :5432
 *   - Redis running on :6379
 *   - `python backend/scripts/seed.py` has run (creates acme +
 *     techflow + the e2e tenants)
 *   - Backend running on :8000 (set PUBLIC_API_URL accordingly)
 *
 * Locally, `cd frontend && pnpm test:e2e` boots the dev server via
 * the webServer block below. The backend has to be running
 * separately.
 */
export default defineConfig({
	testDir: '.',
	testIgnore: ['**/node_modules/**', '**/.auth/**', '**/fixtures/**'],

	timeout: 30_000,
	expect: { timeout: 10_000 },

	// One retry on CI absorbs incidental flake (dev-server cold start,
	// HMR transient errors). No retries locally so flakes are visible
	// during development.
	retries: process.env.CI ? 1 : 0,

	// Fail fast in CI; locally, run the whole suite even if the seed is
	// stale so the developer can see the full damage.
	forbidOnly: !!process.env.CI,
	// Workers run in parallel, each pinned to its own `e2e<N>` tenant
	// via the worker-scoped `tenantSlug` fixture in
	// `fixtures/helpers.ts`. The seed (`backend/scripts/seed.py`)
	// provisions `AP_E2E_TENANT_COUNT` (default 4) such tenants —
	// bump both at once if you raise this. Setting either var to a
	// value higher than `AP_E2E_TENANT_COUNT` makes workers wrap
	// modulo the tenant count, so the architecture stays correct but
	// you lose isolation between the wrapping workers.
	//
	// `fullyParallel: false` keeps tests *within* a file serial — each
	// file's tests share the worker's tenant, so a file that mutates
	// state (creates a user, voids a payment) needs them ordered.
	// Files across workers still run in parallel.
	workers: parseInt(
		process.env.PLAYWRIGHT_WORKERS ?? process.env.AP_E2E_TENANT_COUNT ?? '4',
		10
	),
	fullyParallel: false,

	reporter: process.env.CI ? [['github'], ['list']] : 'list',

	// Auto-start the frontend dev server. PUBLIC_API_URL has to be
	// passed through because vite reads it from process.env at build /
	// dev time. Default to localhost:8000 so a developer's locally-run
	// backend works without extra config.
	webServer: {
		command: 'pnpm dev',
		url: 'http://localhost:7777',
		reuseExistingServer: !process.env.CI,
		timeout: 60_000,
		stdout: 'ignore',
		stderr: 'pipe',
		env: {
			PUBLIC_API_URL: process.env.PUBLIC_API_URL ?? 'http://localhost:8000'
		}
	},

	use: {
		// Fallback baseURL. The per-worker `baseURL` fixture in
		// `fixtures/helpers.ts` routes each worker to its own
		// `http://e2e<N>.localhost:7777`, so this value mostly ends up
		// in cross-tenant specs that pin via `test.use({ baseURL: … })`
		// (e.g. `auth/tenant-isolation.spec.ts`) and in any direct
		// `@playwright/test` import path.
		baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://acme.localhost:7777',
		trace: 'on-first-retry',
		screenshot: 'only-on-failure',
		video: 'retain-on-failure',
		locale: 'en-US',
		timezoneId: 'UTC'
	},

	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
