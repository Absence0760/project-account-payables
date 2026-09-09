/**
 * Where the e2e suite points — the frontend origin and the backend API base.
 *
 * Both were hardcoded (`:7777` in the per-worker `baseURL` fixture, `:8000` in
 * a handful of `process.env.PUBLIC_API_URL ?? …` repeats), and
 * `playwright.config.ts` reuses an existing server. A session working in a git
 * **worktree** therefore either tested the *primary* checkout's build without
 * noticing, or had to take the port from another session — worktrees isolate
 * files, not ports. Two round-24 agents hit exactly that; one stopped and
 * restarted a server it did not own.
 *
 * So the two origins come from the environment, defaulting to today's values —
 * an existing run is bit-for-bit unchanged, and a worktree can serve on its own
 * port:
 *
 *   E2E_WEB_ORIGIN=http://localhost:7801 PUBLIC_API_URL=http://localhost:8001 \
 *     pnpm test:e2e
 *
 * Everything else is DERIVED from `E2E_WEB_ORIGIN` — the per-tenant subdomain
 * origins, the post-login landing-URL pattern, the dev/preview server's port —
 * so there is one knob for the whole web half, not a port here and a hostname
 * there. Deliberately dependency-free (no `@playwright/test` import) so
 * `playwright.config.ts` can import it at config-load time alongside the
 * fixtures.
 */

/** Today's values. Changing these changes the default for every run. */
const DEFAULT_WEB_ORIGIN = 'http://localhost:7777';
const DEFAULT_API_BASE = 'http://localhost:8000';

function trimTrailingSlashes(value: string): string {
	return value.replace(/\/+$/, '');
}

/** Origin the SvelteKit app is served on, WITHOUT a tenant subdomain. */
export const WEB_ORIGIN = trimTrailingSlashes(process.env.E2E_WEB_ORIGIN ?? DEFAULT_WEB_ORIGIN);

/** Origin the FastAPI backend is served on. Named `PUBLIC_API_URL` because that
 *  is the variable Vite already reads at dev/build time — one value configures
 *  the app under test and the specs that call the API directly. */
export const API_BASE = trimTrailingSlashes(process.env.PUBLIC_API_URL ?? DEFAULT_API_BASE);

const _web = new URL(WEB_ORIGIN);

/** `http:` / `https:` — includes the colon, as `URL.protocol` does. */
export const WEB_PROTOCOL = _web.protocol;
/** `localhost` — the registrable host every tenant subdomain hangs off. */
export const WEB_HOSTNAME = _web.hostname;
/** `7777`, or `''` when the origin uses the scheme's default port. */
export const WEB_PORT = _web.port;
/** `:7777`, or `''` — appendable to a hostname without a conditional. */
export const WEB_PORT_SUFFIX = WEB_PORT ? `:${WEB_PORT}` : '';

/**
 * The origin for one tenant. The frontend reads tenant context from the
 * subdomain, and Chromium resolves `*.localhost` to 127.0.0.1 per RFC 6761, so
 * no /etc/hosts entry is needed for the default host.
 */
export function tenantOrigin(slug: string): string {
	return `${WEB_PROTOCOL}//${slug}.${WEB_HOSTNAME}${WEB_PORT_SUFFIX}`;
}

/**
 * Matches a tenant ROOT url — where the login handler's `goto('/')` lands.
 *
 * The trailing-slash anchor is load-bearing: without it the pattern also
 * matches descendant paths like `/login/mfa`, so a spec would stop waiting
 * before the redirect it is waiting for. The host is left open (`[^/]+`)
 * because the worker's tenant slug varies.
 */
export const TENANT_ROOT_URL = new RegExp(`^${WEB_PROTOCOL}//[^/]+${WEB_PORT_SUFFIX}/?$`);
