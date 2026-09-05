import { expect, test } from '../fixtures/helpers';

/**
 * Host-derived tenant + API-origin resolution (`$lib/tenant.ts` over
 * `$lib/hostRouting.ts`).
 *
 * ## What this spec covers, and what it deliberately doesn't
 *
 * The SPA is now platform-domain aware: a host under a domain listed in
 * `PUBLIC_PLATFORM_DOMAINS` carries the tenant slug as its first label and
 * calls the build-time `PUBLIC_API_URL`; ANY OTHER host is a customer's
 * white-label vanity domain, where the SPA must send **no** `X-Tenant-Slug`
 * (a present header suppresses the backend's `Host` lookup) and must call
 * `/api` **same-origin** (only a same-origin request carries the vanity
 * hostname in `Host` for the backend to resolve the tenant from).
 *
 * **The vanity half is covered at the unit level (`src/lib/tenant.test.ts`),
 * not here, and that is a harness limit rather than a choice.** Two things
 * would have to be true for an honest end-to-end vanity assertion:
 *
 *   1. `PUBLIC_PLATFORM_DOMAINS` reaching BOTH run modes. Locally the suite
 *      boots `pnpm dev`, which loads `frontend/.env.development` (where the
 *      var is set); CI sets `FEOH_E2E_USE_PREVIEW=true` and serves a
 *      `vite build` bundle, and a production-mode build does not read
 *      `.env.development`. Unset means "replay the pre-change rule", under
 *      which no host is a vanity host at all — so the same navigation would
 *      assert opposite things locally and in CI. Unlocking it is one line:
 *      add `PUBLIC_PLATFORM_DOMAINS` beside `PUBLIC_API_URL` in
 *      `playwright.config.ts`'s `webServer.env` AND in the CI build step.
 *   2. A second hostname that actually serves both the SPA and `/api`.
 *      Chromium can be pointed at one (`--host-resolver-rules`), but the dev
 *      server / `vite preview` does not proxy `/api`, so a same-origin call
 *      from a vanity host 404s at the static server rather than reaching the
 *      backend. A real vanity deployment terminates both on one origin (see
 *      `docs/white-label.md` § Custom domains); the harness has no such origin.
 *
 * Faking either — stubbing the responses, or asserting whichever answer the
 * current mode happens to give — would test the mock, not the fix. So what
 * this spec locks down is the other half, which IS reachable and is the half a
 * regression would be catastrophic in: on a platform host, the slug header and
 * the cross-origin API base are exactly what they were before the change.
 */

interface SeenRequest {
	url: string;
	tenantHeader: string | undefined;
}

test.describe('platform host resolution', () => {
	test('sends the subdomain slug and calls the build-time API origin', async ({
		page,
		tenantSlug,
		baseURL
	}) => {
		const seen: SeenRequest[] = [];
		page.on('request', (req) => {
			// Match on the PATH, and only for an XHR/fetch: the dev server
			// serves module source over HTTP too, and `src/lib/api/audit.ts`
			// contains `/api/` as a substring.
			if (req.resourceType() !== 'xhr' && req.resourceType() !== 'fetch') return;
			if (!new URL(req.url()).pathname.startsWith('/api/')) return;
			seen.push({ url: req.url(), tenantHeader: req.headers()['x-tenant-slug'] });
		});

		await page.goto('/invoices');
		await expect(page.locator('aside.sidebar')).toBeVisible();
		await expect
			.poll(() => seen.length, { message: 'expected at least one /api request' })
			.toBeGreaterThan(0);

		const pageOrigin = new URL(baseURL!).origin;

		for (const req of seen) {
			// The slug comes off the hostname's first label — `e2e1.localhost`
			// → `e2e1`. Regression guard on `getTenantSlug()`: dropping the
			// header here would push every request onto the backend's
			// custom-domain `Host` lookup, which has no entry for a platform
			// subdomain, and 400 with "Missing X-Tenant-Slug header".
			expect(req.tenantHeader, `X-Tenant-Slug on ${req.url}`).toBe(tenantSlug);

			// `getApiBase()` must still resolve to the build-time
			// `PUBLIC_API_URL` (a separate origin from the SPA) on a platform
			// host. Collapsing to same-origin here is the failure mode of
			// mis-classifying a platform subdomain as a vanity host.
			expect(new URL(req.url).origin, `API origin of ${req.url}`).not.toBe(pageOrigin);
		}
	});
});
