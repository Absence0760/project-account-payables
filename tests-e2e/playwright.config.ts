import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright e2e config for project-account-payables.
 *
 * The fixtures/auth.ts globalSetup signs each seeded user in once via
 * the UI and saves their storage state to .auth/<user>.json. Spec
 * files attach the storage state via:
 *
 *   test.use({ storageState: USER_A.storageStatePath });
 *
 * to skip the form submit on every test. .auth/ is gitignored.
 *
 * Adapt the `webServer` block to whatever the app's dev server
 * eventually is (vite, next dev, express, etc.) — the URL just has to
 * answer 200 by the time globalSetup runs.
 */
export default defineConfig({
	testDir: '.',
	// Don't recurse into node_modules / .auth / fixtures from the testDir glob.
	testIgnore: ['**/node_modules/**', '**/.auth/**', '**/fixtures/**'],

	timeout: 30_000,
	expect: { timeout: 10_000 },

	// One retry on CI absorbs incidental flake; no retries locally so
	// flakes are visible during development.
	retries: process.env.CI ? 1 : 0,

	// Fail fast in CI — a failure usually means the seed is mis-stated
	// and every dependent test will fail the same way. Locally, run them all.
	forbidOnly: !!process.env.CI,
	// Single worker to start. Bump after the suite is stable and the
	// seed / fixture layer is concurrency-safe.
	workers: 1,
	fullyParallel: false,

	reporter: process.env.CI ? [['github'], ['list']] : 'list',

	// Auto-start the dev server. `reuseExistingServer` lets a manually
	// started server (e.g. for `playwright test --ui`) take precedence.
	// Replace `npm run dev` and the port with whatever the project's
	// dev surface ends up being.
	webServer: {
		command: 'npm run dev',
		url: 'http://localhost:5173',
		reuseExistingServer: !process.env.CI,
		timeout: 60_000,
		stdout: 'ignore',
		stderr: 'pipe'
	},

	use: {
		baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173',
		trace: 'on-first-retry',
		screenshot: 'only-on-failure',
		video: 'retain-on-failure',
		locale: 'en-US',
		timezoneId: 'UTC'
	},

	globalSetup: './fixtures/auth.ts',

	// Chromium-only on purpose. Webkit + Firefox would 3× the runtime
	// for marginal bug-yield on a typical SPA. Add projects later if a
	// bug ever shows up that's browser-specific.
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
