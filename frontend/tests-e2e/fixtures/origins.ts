/**
 * The one place the e2e suite writes down where the web app is served.
 *
 * Why this file exists
 * --------------------
 * A concurrent session in this repo runs in its own git worktree
 * (root `CLAUDE.md` § Running concurrent sessions), and a worktree isolates
 * *files* — not ports. Two sessions each running `pnpm dev` both want `:7777`,
 * so the second one loses, and the port was written out as a literal in the
 * Playwright config, in `helpers.ts`, and in ~30 spec files besides. Making the
 * suite follow a different port therefore meant a find-and-replace across the
 * whole directory, which is why round-24 agents hit this and one ended up
 * borrowing another session's server instead.
 *
 * Everything here derives from `FEOH_E2E_WEB_PORT`, which defaults to the
 * documented `7777` — so a plain `pnpm test:e2e` is byte-for-byte unchanged,
 * and a worktree runs the suite against its own server with:
 *
 *     FEOH_E2E_WEB_PORT=7778 PUBLIC_API_URL=http://localhost:8001 pnpm test:e2e
 *
 * (`PUBLIC_API_URL` was already env-driven; the web port was the missing half.)
 *
 * Deliberately dependency-free — no `@playwright/test` import — because
 * `playwright.config.ts` reads it, and pulling the fixtures module into the
 * config would drag the whole worker-fixture graph into config evaluation.
 * `fixtures/helpers.ts` re-exports the spec-facing names from here, so specs
 * keep importing what they always did.
 */

/** Port the frontend under test is served on. */
export const WEB_PORT: number = Number(process.env.FEOH_E2E_WEB_PORT ?? 7777);

/** Origin for a tenant subdomain. `*.localhost` resolves to 127.0.0.1 in Chromium. */
export function tenantOrigin(slug: string): string {
	return `http://${slug}.localhost:${WEB_PORT}`;
}

/** The no-subdomain origin — the public marketing / signup surface. */
export const NO_TENANT_ORIGIN = `http://localhost:${WEB_PORT}`;

/**
 * "Signed in and landed on the app root", for any tenant.
 *
 * The host is deliberately unanchored (`[^/]+`): the specs using this are
 * asserting that the sign-in redirect completed, not *which* tenant it
 * completed on — the worker's tenant slug varies by `workerIndex`.
 */
export const APP_ROOT_URL: RegExp = new RegExp(`^http://[^/]+:${WEB_PORT}/?$`);

/** Same, pinned to one tenant — for the cross-tenant specs that assert identity. */
export function tenantRootUrl(slug: string): RegExp {
	return new RegExp(`^http://${slug.replace(/\./g, '\\.')}\\.localhost:${WEB_PORT}/?$`);
}

/** Same, pinned to one tenant, allowing any path below the root. */
export function tenantUrlPrefix(slug: string): RegExp {
	return new RegExp(`^http://${slug.replace(/\./g, '\\.')}\\.localhost:${WEB_PORT}/`);
}
