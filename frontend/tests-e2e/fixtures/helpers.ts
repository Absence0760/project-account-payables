import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test as base, type Browser, type Page } from '@playwright/test';

// `frontend/package.json` is `"type": "module"`, so the CommonJS
// `__dirname` global isn't defined here. Recover it from
// `import.meta.url` so the AUTH_DIR resolves relative to this file's
// location regardless of the test runner's cwd.
const _thisFile = fileURLToPath(import.meta.url);
const _thisDir = path.dirname(_thisFile);

/**
 * Per-worker tenant isolation for parallel Playwright execution.
 *
 * `backend/scripts/seed.py` provisions `FEOH_E2E_TENANT_COUNT` (default 4)
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

const AUTH_DIR = path.resolve(_thisDir, '../.auth');

const E2E_TENANT_COUNT = parseInt(
	process.env.E2E_TENANT_COUNT ?? process.env.FEOH_E2E_TENANT_COUNT ?? '4',
	10
);

// Optional fixed offset added to every worker's tenant index. Defaults to
// 0 (no change to normal single- or multi-worker runs). It exists so that
// several *independent* Playwright processes (e.g. parallel authoring
// sessions, each running with PLAYWRIGHT_WORKERS=1) can each be pinned to a
// distinct `e2e<N>` tenant instead of all colliding on `e2e1`: process k
// sets `E2E_TENANT_OFFSET=k` and its lone worker resolves to `e2e<k+1>`.
const E2E_TENANT_OFFSET = parseInt(process.env.E2E_TENANT_OFFSET ?? '0', 10);

type TenantCreds = { email: string; password: string };

type WorkerFixtures = {
	tenantSlug: string;
	tenantAdmin: TenantCreds;
	tenantManager: TenantCreds;
	tenantClerk: TenantCreds;
	tenantCfo: TenantCreds;
};

function _tenantSlugFor(workerIndex: number): string {
	return `e2e${((workerIndex + E2E_TENANT_OFFSET) % Math.max(E2E_TENANT_COUNT, 1)) + 1}`;
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
	},
	// Pre-navigate the page to the worker's tenant root before yielding
	// to the test. Why this exists:
	//
	// `storageState` populates the browser context's localStorage for
	// the tenant origin, but a freshly-created `page` starts at
	// `about:blank`. Reading localStorage on `about:blank` throws
	// `SecurityError: Failed to read the 'localStorage' property` —
	// browsers refuse storage access on the empty-document scheme.
	//
	// Specs that already start with a `page.goto(...)` in `beforeEach`
	// pay one redundant navigation, which is cheap. Specs that go
	// straight from arg destructuring to an API call (like
	// `admin/delete-safety.spec.ts`'s `await createUser(page, …)`)
	// would otherwise fail at the first `authToken(page)` because the
	// page is still on `about:blank`. The pre-nav lifts the SecurityError
	// while keeping the storage-state speed-up.
	//
	// Opt-out specs (`storageState: { cookies: [], origins: [] }`)
	// still get the pre-nav; for them the worker's tenant root
	// redirects to `/login` (no auth) which is the same place those
	// specs were going to navigate next anyway.
	page: async ({ page, baseURL }, use) => {
		if (baseURL) {
			await page.goto(baseURL);
		}
		await use(page);
	}
});

/**
 * Read the auth_token value out of a persisted storageState JSON
 * (format: `{origins:[{localStorage:[{name,value}]}]}`).
 * Returns null when the file is missing, malformed, or holds no token.
 */
function _readStoredToken(file: string): string | null {
	try {
		const raw = fs.readFileSync(file, 'utf8');
		const parsed = JSON.parse(raw) as {
			origins?: Array<{ localStorage?: Array<{ name: string; value: string }> }>;
		};
		for (const origin of parsed.origins ?? []) {
			for (const entry of origin.localStorage ?? []) {
				if (entry.name === 'auth_token') return entry.value;
			}
		}
	} catch {
		/* missing or corrupt file — treat as invalid */
	}
	return null;
}

/**
 * Probe the API with a stored JWT to decide if the storageState file is
 * still usable. Returns true only when `/api/auth/me` responds 2xx.
 * A 401 (expired, blocklisted, or otherwise revoked) returns false.
 * Network errors also return false so the caller re-logs in.
 */
async function _isStoredTokenValid(token: string, tenantSlug: string): Promise<boolean> {
	const apiBase = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';
	try {
		const res = await fetch(`${apiBase}/api/auth/me`, {
			headers: {
				Authorization: `Bearer ${token}`,
				'X-Tenant-Slug': tenantSlug
			}
		});
		return res.ok;
	} catch {
		return false;
	}
}

/** Worker-scoped lazy creator for the per-tenant admin storage-state
 *  file. The first test in a worker signs the admin into a throwaway
 *  context, persists the localStorage to disk, and closes the
 *  context. Subsequent tests just read the file path.
 *
 *  The file is validated on every use: the stored JWT is probed against
 *  `/api/auth/me`. If it returns 401 (expired or blocklisted by the
 *  session-management eviction system) the stale file is deleted and
 *  a fresh login regenerates it. This prevents the "not signed in" flake
 *  that occurs when another test's `signInAndWait` call pushes the cached
 *  JTI out of the active-sessions set and into the Redis blocklist. */
async function _ensureAdminStorageState(
	browser: Browser,
	tenantSlug: string,
	creds: TenantCreds
): Promise<string> {
	const file = path.join(AUTH_DIR, `${tenantSlug}-admin.json`);
	if (fs.existsSync(file)) {
		const token = _readStoredToken(file);
		if (token && (await _isStoredTokenValid(token, tenantSlug))) {
			return file;
		}
		// Token missing, expired, or blocklisted — remove the stale file so
		// we fall through to re-login below.
		fs.unlinkSync(file);
	}

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
		// Bake a recorded cookie-consent choice into the persisted state so the
		// GDPR consent banner (position:fixed, bottom-centre, z-index 10000) is
		// hidden for every authenticated spec. The banner otherwise overlaps the
		// app's bottom-anchored controls (BulkBar, modal footers, Load-more) and
		// intercepts their clicks — the systemic cause of the e2e shard failures.
		// This is the post-consent steady state every real session is in after
		// the first visit; it is NOT an init script, so consent-banner.spec.ts
		// (which removes the key and reloads to assert the banner) still works.
		await page.evaluate(() => localStorage.setItem('ap_consent_choice', 'accepted'));
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
	// Record a cookie-consent choice before the first paint so the GDPR consent
	// banner (position:fixed, bottom-centre, z-index 10000) stays hidden for
	// specs that sign in fresh via this path (non-admin roles, cross-tenant
	// flows) — it otherwise overlaps the app's bottom-anchored controls and
	// intercepts their clicks. Mirrors the persisted-state injection in
	// _ensureAdminStorageState. consent-banner.spec.ts uses neither path, so the
	// banner still shows there.
	await page.addInitScript(() => {
		try {
			localStorage.setItem('ap_consent_choice', 'accepted');
		} catch {
			/* about:blank — ignore */
		}
	});
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
