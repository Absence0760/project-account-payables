import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright e2e config for the frontend.
 *
 * The frontend reads tenant context from the subdomain
 * (`<slug>.localhost:7777`), so specs target the seed tenant `acme`.
 * Chromium resolves `*.localhost` to 127.0.0.1 by default per RFC 6761,
 * so no /etc/hosts changes are needed.
 *
 * Prereqs (CI handles all of these):
 *   - Postgres running on :5432
 *   - Redis running on :6379
 *   - `python backend/scripts/seed.py` has run (creates `acme` tenant
 *     with user demo@acme.com / password "demo")
 *   - Backend running on :8000 (set PUBLIC_API_URL accordingly)
 *
 * Locally, `cd frontend && pnpm test:e2e` boots the dev server via the
 * webServer block below. The backend has to be running separately.
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
	workers: 1,
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
		// Tenant subdomain so the frontend's auto-extracted X-Tenant-Slug
		// header is "acme". Override per-test via page.goto with an
		// explicit URL when an anon-tenant or different-tenant flow is
		// the point of the test.
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
