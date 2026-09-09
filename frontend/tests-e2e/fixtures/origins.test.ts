import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
	APP_ROOT_URL,
	escapeRegExp,
	NO_TENANT_ORIGIN,
	tenantOrigin,
	tenantRootUrl,
	WEB_PORT
} from './origins';

/**
 * The ratchet behind `fixtures/origins.ts`.
 *
 * The port was a literal in ~30 spec files, which is why "run the e2e suite in
 * a worktree" meant a find-and-replace across the directory instead of setting
 * one env var. Centralising it fixes today; this test is what stops the next
 * spec re-introducing a literal and quietly re-breaking it — the same static
 * source-scan shape `src/lib/utils/effectTimerCleanup.test.ts` and
 * `src/lib/a11y/opacityAudit.test.ts` use, and for the same reason: the symptom
 * (a suite that only runs on one machine's free port) is not something a
 * browser test can observe about itself.
 *
 * Playwright never runs this file — `playwright.config.ts` sets
 * `testIgnore: ['**\/fixtures/**']` — and vitest picks it up via the
 * `tests-e2e/**` entry in `vitest.config.ts`'s `include`.
 */

const E2E_DIR = join(fileURLToPath(new URL('.', import.meta.url)), '..');

/** Every `.ts` under `tests-e2e/`, except this file and the module it guards. */
function specSources(): Array<{ path: string; source: string }> {
	const out: Array<{ path: string; source: string }> = [];
	const walk = (dir: string) => {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			const full = join(dir, entry.name);
			if (entry.isDirectory()) {
				if (entry.name === 'node_modules' || entry.name === '.auth') continue;
				walk(full);
				continue;
			}
			if (!entry.name.endsWith('.ts')) continue;
			// origins.ts owns the number; this file quotes it in assertions.
			if (entry.name === 'origins.ts' || entry.name === 'origins.test.ts') continue;
			out.push({ path: full.slice(E2E_DIR.length + 1), source: readFileSync(full, 'utf8') });
		}
	};
	walk(E2E_DIR);
	return out;
}

/** Strip comments so a doc-comment mentioning the default port isn't a finding. */
function code(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

describe('the e2e suite derives its origins from one configurable port', () => {
	it('defaults to the documented 7777', () => {
		// A default that drifted would silently change what `pnpm test:e2e`
		// targets for everyone who sets nothing.
		expect(WEB_PORT).toBe(Number(process.env.FEOH_E2E_WEB_PORT ?? 7777));
	});

	it('builds every origin shape from that port', () => {
		expect(tenantOrigin('e2e1')).toBe(`http://e2e1.localhost:${WEB_PORT}`);
		expect(NO_TENANT_ORIGIN).toBe(`http://localhost:${WEB_PORT}`);
		expect(APP_ROOT_URL.test(`http://e2e1.localhost:${WEB_PORT}/`)).toBe(true);
		expect(APP_ROOT_URL.test(`http://e2e1.localhost:${WEB_PORT}`)).toBe(true);
		expect(tenantRootUrl('acme').test(`http://acme.localhost:${WEB_PORT}/`)).toBe(true);
	});

	it('APP_ROOT_URL means the app ROOT, not any page on the app', () => {
		// The specs using it assert that a sign-in redirect *completed*. An
		// unanchored regex (the old `/:7777\/?$/`) also matched, e.g., a URL
		// that had merely stopped at the port — so the anchors are the point.
		expect(APP_ROOT_URL.test(`http://e2e1.localhost:${WEB_PORT}/login`)).toBe(false);
		expect(APP_ROOT_URL.test(`http://e2e1.localhost:${WEB_PORT}/invoices`)).toBe(false);
	});

	it('tenantRootUrl does not let one tenant satisfy another', () => {
		// `.` is a regex metacharacter; unescaped, `acme.localhost` would match
		// `acmeXlocalhost` — and these are the cross-tenant isolation specs.
		expect(tenantRootUrl('acme').test(`http://techflow.localhost:${WEB_PORT}/`)).toBe(false);
		expect(tenantRootUrl('acme').test(`http://acmeXlocalhost:${WEB_PORT}/`)).toBe(false);
	});

	it('escapes every metacharacter in a slug, backslash included', () => {
		// The first version of `tenantRootUrl` escaped only dots, which CodeQL
		// flagged as incomplete sanitization — correctly: `\` is itself a
		// metacharacter, so a dots-only escape passes it through and builds a
		// DIFFERENT pattern rather than an escaped literal. `\d` is the sharp
		// case: left alone it matches any digit, so `1ocalhost` would satisfy a
		// regex meant to pin one exact tenant.
		expect(escapeRegExp(String.raw`a\db.c`)).toBe(String.raw`a\\db\.c`);
		expect(tenantRootUrl(String.raw`a\d`).test(`http://a1.localhost:${WEB_PORT}/`)).toBe(false);
		expect(tenantRootUrl(String.raw`a\d`).test(`http://a\\d.localhost:${WEB_PORT}/`)).toBe(true);

		// And the ordinary slug still round-trips untouched.
		expect(escapeRegExp('e2e1')).toBe('e2e1');
	});

	it('no spec or fixture hardcodes the port', () => {
		const offenders = specSources()
			.filter(({ source }) => /:7777\b/.test(code(source)))
			.map(({ path }) => path);
		expect(offenders, `use tenantOrigin() / APP_ROOT_URL from fixtures/origins.ts`).toEqual([]);
	});

	it('no spec rebuilds a tenant origin by hand', () => {
		// The port is only half of it — a spec spelling out its own
		// `http://<slug>.localhost` pins the scheme and host shape too, and it
		// is the shape that carries the port when someone re-adds one.
		//
		// Anchored on `http://` on purpose: the seeded logins are
		// `demo+<role>@<slug>.localhost` (see `_credsFor`), which share the
		// suffix and are not origins. Flagging those would push the guard
		// towards being switched off rather than obeyed.
		const offenders = specSources()
			.filter(({ source }) => /https?:\/\/\$\{[^}]+\}\.localhost/.test(code(source)))
			.map(({ path }) => path);
		expect(offenders, 'use tenantOrigin(slug) from fixtures/origins.ts').toEqual([]);
	});
});
