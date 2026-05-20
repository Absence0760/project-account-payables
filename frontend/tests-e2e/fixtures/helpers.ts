import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { expect, test as base, type Browser, type Page } from '@playwright/test';

/**
 * Per-worker tenant isolation for parallel Playwright execution.
 *
 * `backend/scripts/seed.py` provisions `AP_E2E_TENANT_COUNT` (default 4)
 * `e2e<N>` tenants. Each Playwright worker maps to one tenant via
 * `workerIndex`, so a worker that creates / deletes data in
 * `e2e1` can't collide with another worker working in `e2e2`. Spec
 * files import `test` from this module (not `@playwright/test`); the
 * fixture below overrides `baseURL` and injects role-specific creds
 * so most specs need no further changes.
 *
 * Auth storage state: a worker-scoped `storageState` fixture lazy-
 * creates `.auth/<tenantSlug>-admin.json` on first use by signing the
 * worker's admin into a temporary context. Every subsequent test in
 * that worker boots the page with the JWT already in localStorage —
 * no `signInAndWait` needed in `beforeEach`. Specs that need a fresh
 * unauthenticated browser (login UI, auth-wall, signup) opt out via
 * `test.use({ storageState: { cookies: [], origins: [] } })` at the
 * top of the file or describe block.
 */

const AUTH_DIR = path.resolve(__dirname, '../.auth');

const E2E_TENANT_COUNT = parseInt(
	process.env.E2E_TENANT_COUNT ?? process.env.AP_E2E_TENANT_COUNT ?? '4',
	10
);

type TenantCreds = { email: string; password: string };

type WorkerFixtures = {
	tenantSlug: string;
	tenantAdmin: TenantCreds;
	tenantManager: TenantCreds;
	tenantClerk: TenantCreds;
	tenantCfo: TenantCreds;
};

function _tenantSlugFor(workerIndex: number): string {
	return `e2e${(workerIndex % Math.max(E2E_TENANT_COUNT, 1)) + 1}`;
}

function _credsFor(slug: string, role: 'admin' | 'manager' | 'clerk' | 'cfo'): TenantCreds {
	return { email: `demo+${role}@${slug}.localhost`, password: 'demo' };
}

export const test = base.extend<object, WorkerFixtures>({
	tenantSlug: [
		async ({}, use, workerInfo) => {
			await use(_tenantSlugFor(workerInfo.workerIndex));
		},
		{ scope: 'worker' }
	],
	tenantAdmin: [
		async ({ tenantSlug }, use) => {
			await use(_credsFor(tenantSlug, 'admin'));
		},
		{ scope: 'worker' }
	],
	tenantManager: [
		async ({ tenantSlug }, use) => {
			await use(_credsFor(tenantSlug, 'manager'));
		},
		{ scope: 'worker' }
	],
	tenantClerk: [
		async ({ tenantSlug }, use) => {
			await use(_credsFor(tenantSlug, 'clerk'));
		},
		{ scope: 'worker' }
	],
	tenantCfo: [
		async ({ tenantSlug }, use) => {
			await use(_credsFor(tenantSlug, 'cfo'));
		},
		{ scope: 'worker' }
	],
	baseURL: async ({ tenantSlug }, use) => {
		await use(`http://${tenantSlug}.localhost:7777`);
	},
	// Default storage state for every test: the worker's tenant admin
	// is already signed in. First test per worker pays the ~1–2 s login
	// cost once and persists the resulting localStorage to disk; every
	// subsequent test in the worker loads the file in <100 ms.
	//
	// Specs that need to test the login UI itself, the auth wall, or
	// signup must opt out:
	//
	//   test.use({ storageState: { cookies: [], origins: [] } });
	//
	// Specs that need a different role keep their explicit
	// `signInAndWait(page, tenantClerk)` — the storage-state preload is
	// only the *default*, not a hard contract.
	storageState: async ({ browser, tenantSlug, tenantAdmin }, use) => {
		await use(await _ensureAdminStorageState(browser, tenantSlug, tenantAdmin));
	}
});

/** Worker-scoped lazy creator for the per-tenant admin storage-state
 *  file. The first test in a worker signs the admin into a throwaway
 *  context, persists the localStorage to disk, and closes the
 *  context. Subsequent tests just read the file path. */
async function _ensureAdminStorageState(
	browser: Browser,
	tenantSlug: string,
	creds: TenantCreds
): Promise<string> {
	const file = path.join(AUTH_DIR, `${tenantSlug}-admin.json`);
	if (fs.existsSync(file)) return file;

	fs.mkdirSync(AUTH_DIR, { recursive: true });
	const context = await browser.newContext({
		baseURL: `http://${tenantSlug}.localhost:7777`
	});
	try {
		const page = await context.newPage();
		await page.goto('/login');
		await page.waitForLoadState('networkidle');
		await page.locator('input[type="email"]').fill(creds.email);
		await page.locator('input[type="password"]').fill(creds.password);
		await page.locator('form button[type="submit"]').click();
		// Mirror signInAndWait's success contract — land on the tenant
		// dashboard URL before snapshotting storage. If the redirect
		// hasn't happened, the localStorage hasn't been written yet.
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
		await context.storageState({ path: file });
	} finally {
		await context.close();
	}
	return file;
}

export { expect };

/**
 * Resolve a per-worker admin from `test.info()` for callers (signIn helpers)
 * that don't take the fixture as an argument. Falls back to ACME_ADMIN when
 * called outside a Playwright test context (e.g. globalSetup, unit tests).
 */
function _currentWorkerAdmin(): TenantCreds {
	try {
		const wi = base.info().workerIndex;
		return _credsFor(_tenantSlugFor(wi), 'admin');
	} catch {
		return ACME_ADMIN;
	}
}

/**
 * Seeded credentials for the two non-e2e demo tenants (acme + techflow).
 * These stay seeded so the cross-tenant isolation specs have a stable
 * pair of distinct tenants to exercise. New parallel specs should prefer
 * the `tenantAdmin` / `tenantClerk` worker fixtures above instead.
 */
export const ACME_ADMIN = {
	email: 'demo@acme.com',
	password: 'demo'
} as const;

export const ACME_CLERK = {
	email: 'demo+apclerk@acme.com',
	password: 'demo'
} as const;

export const ACME_MANAGER = {
	email: 'demo+apmanager@acme.com',
	password: 'demo'
} as const;

export const ACME_CFO = {
	email: 'demo+cfo@acme.com',
	password: 'demo'
} as const;

export const TECHFLOW_ADMIN = {
	email: 'admin@techflow.com',
	password: 'demo'
} as const;

/** Tenant origins. `*.localhost` resolves to 127.0.0.1 in Chromium. */
export const ACME_BASE = 'http://acme.localhost:7777';
export const TECHFLOW_BASE = 'http://techflow.localhost:7777';
export const NO_TENANT_BASE = 'http://localhost:7777';

/** Build a tenant origin from a slug — used by specs that want to address
 *  the current worker's tenant explicitly (e.g. when overriding baseURL
 *  on a specific page.goto). */
export function tenantBase(slug: string): string {
	return `http://${slug}.localhost:7777`;
}

/** Escape every regex metacharacter so a literal URL can be embedded in a RegExp. */
export function escapeRegExp(input: string): string {
	return input.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
}

/**
 * Drive the email-password sign-in form on the seeded `acme` tenant.
 * The frontend's tenant resolution requires hitting an `<slug>.localhost`
 * URL, so the playwright.config.ts baseURL is `acme.localhost:7777`.
 *
 * Returns once the submit click has fired. Callers assert the
 * destination URL.
 *
 * Why `waitForLoadState('networkidle')`: Svelte 5 binds the form's
 * `onsubmit` only after hydration. A click before that fires the
 * native GET submit, which navigates to /login?email=…&password=…
 * (visually identical to "still on /login" but with no auth POST
 * attempted). Waiting for networkidle covers Vite HMR + the dynamic
 * imports for `auth.svelte.ts` and `api.ts`.
 */
export async function signIn(
	page: Page,
	creds?: { email: string; password: string }
) {
	// Default to the current worker's tenant admin so a spec running in
	// worker N signs into e2eN rather than acme. Specs that need a
	// specific tenant (cross-tenant isolation tests, etc.) pass `creds`
	// explicitly.
	const resolved = creds ?? _currentWorkerAdmin();
	await page.goto('/login');
	await page.waitForLoadState('networkidle');

	await page.locator('input[type="email"]').fill(resolved.email);
	await page.locator('input[type="password"]').fill(resolved.password);
	await page.locator('form button[type="submit"]').click();
}

/**
 * Sign in and wait for the post-login redirect to land on the tenant
 * root (`goto('/')` is what the login handler runs on success). Use
 * this when subsequent assertions need the authed app shell.
 */
export async function signInAndWait(
	page: Page,
	creds?: { email: string; password: string }
) {
	await signIn(page, creds);
	// The tenant root is the dashboard. URL must end in just '/' — using
	// a trailing-slash regex anchors the match against descendant paths
	// like '/login/mfa'.
	await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
}

/**
 * Click the sidebar profile button → Log Out and assert the redirect
 * to /login.
 */
export async function signOut(page: Page) {
	await page.locator('.profile-btn').click();
	await page.locator('.profile-logout').click();
	await expect(page).toHaveURL(/\/login/);
}

/** Read the current worker's tenant slug. Falls back to `acme` outside a
 *  Playwright context (e.g. unit-test imports). Use in API-request blocks
 *  that need `X-Tenant-Slug` so a spec running in worker N targets e2eN
 *  rather than always acme. */
export function currentTenantSlug(): string {
	try {
		return _tenantSlugFor(base.info().workerIndex);
	} catch {
		return 'acme';
	}
}

/** Read the JWT the frontend stored after login. Throws when called
 *  before a successful sign-in. */
export async function authToken(page: Page): Promise<string> {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

/** Build the `Authorization` + `X-Tenant-Slug` headers for an
 *  authenticated, tenant-scoped API request. Defaults to the current
 *  worker's tenant; pass `slug` to target a specific one (cross-tenant
 *  isolation tests). */
export function tenantHeaders(token: string, slug?: string): Record<string, string> {
	return {
		Authorization: `Bearer ${token}`,
		'X-Tenant-Slug': slug ?? currentTenantSlug()
	};
}

/** Resolve an authenticated API request's headers in one shot. Reads
 *  the token from `localStorage`, then composes the tenant headers. */
export async function authedTenantHeaders(
	page: Page,
	slug?: string
): Promise<Record<string, string>> {
	return tenantHeaders(await authToken(page), slug);
}

/** Run a synchronous `psql -c <query>` against a tenant DB. Defaults to
 *  the current worker's tenant. Used by specs that need to set up /
 *  inspect state the API doesn't expose (e.g. clobbering
 *  `assigned_to_id` to provoke a blocked-delete branch). */
export function tenantPsql(query: string, slug?: string): string {
	const db = `ap_${slug ?? currentTenantSlug()}`;
	const out = execFileSync(
		'psql',
		['-h', 'localhost', '-U', 'postgres', '-p', '5432', '-d', db, '-tAc', query],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: ['ignore', 'pipe', 'pipe'] }
	);
	return out.toString();
}

/** Per-worker API origin. Specs that hit `${API_BASE}/api/...` directly
 *  can import this instead of redeclaring `process.env.PUBLIC_API_URL ?? …`. */
export const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';
