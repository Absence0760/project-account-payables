import { describe, expect, it } from 'vitest';
import { contrastRatio, formatRatio, WCAG_AA_NORMAL } from './contrast';
import {
	auditStyles,
	collectAssignedTokens,
	describeFinding,
	extractStyleBlocks,
	parsePalette,
	type StyleSource
} from './cssAudit';

/**
 * Repo-wide token-pairing guard (WCAG 2.2 SC 1.4.3).
 *
 * The complement to `tests-e2e/a11y/axe.spec.ts`: axe checks what a listed
 * route happens to render, this checks every stylesheet in the app. The
 * contrast bug this initiative kept re-finding (`--text-muted` on
 * `--surface-2`, 4.34:1) recurs per *surface*, so it appeared identically on
 * pages inside the axe route list and pages outside it. A scan of the sources
 * finds all of them at once, including markup behind a modal, an empty state,
 * or a role the e2e user doesn't hold.
 *
 * **Fixing a failure means changing the colour, never relaxing the rule.**
 * The palette carries a "strong" companion for every text-bearing fill
 * (`--accent-strong`, `--success-strong`, `--danger-strong`) precisely so
 * there is always a correct answer.
 */

/**
 * Vite's `import.meta.glob` rather than a `node:fs` walk: it needs no
 * `@types/node` (the frontend has none, deliberately), it is resolved by the
 * same bundler that ships these files, and a pattern that matches nothing
 * fails the size assertion below rather than silently passing.
 */
const RAW = import.meta.glob('/src/**/*.{svelte,css}', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

const files = Object.entries(RAW)
	.map(([path, source]) => ({ path: path.replace(/^\/src\//, ''), source }))
	.sort((a, b) => a.path.localeCompare(b.path));

const appCss = files.find((f) => f.path === 'app.css');
const palette = parsePalette(appCss?.source ?? '');

const assignedTokens = new Set<string>();
for (const file of files) for (const t of collectAssignedTokens(file.source)) assignedTokens.add(t);

const sources: StyleSource[] = files.flatMap((f) => extractStyleBlocks(f.path, f.source));

describe('style token pairing', () => {
	it('scans a realistic number of stylesheets — the walk itself must not silently break', () => {
		expect(appCss, 'src/app.css must be discoverable by the walk').toBeDefined();
		expect(Object.keys(palette).length).toBeGreaterThanOrEqual(8);
		expect(sources.length).toBeGreaterThan(100);
	});

	it('has no colour pair below the WCAG AA bar, no stale fallback, and no dead token', () => {
		const findings = auditStyles(sources, {
			palette,
			assignedTokens,
			// The surfaces body text actually renders on. See `AuditOptions`
			// for why --surface-2 is not in this list.
			textSurfaces: ['--bg', '--surface']
		});
		const report = findings.map((f) => `  • ${describeFinding(f)}`).join('\n');
		expect(findings, `\n${report}\n`).toEqual([]);
	});
});

/**
 * The palette's own contract, asserted directly rather than inferred from
 * whichever rules happen to use it. Each "strong" token exists only to carry
 * white text; if one drifts light, every button using it fails at once and the
 * scan above would report dozens of sites for a single root cause.
 */
describe('palette contract', () => {
	for (const token of ['--accent-strong', '--success-strong', '--danger-strong']) {
		it(`${token} carries white text at AA`, () => {
			const value = palette[token];
			expect(value, `${token} must be declared in :root`).toBeDefined();
			const ratio = contrastRatio('#ffffff', value);
			expect(ratio, `white on ${token} (${value})`).not.toBeNull();
			expect(
				ratio as number,
				`white on ${token} (${value}) is ${formatRatio(ratio as number)}`
			).toBeGreaterThanOrEqual(WCAG_AA_NORMAL);
		});
	}

	for (const token of ['--text', '--text-muted', '--accent', '--success', '--danger']) {
		it(`${token} is legible on --bg and --surface`, () => {
			const value = palette[token];
			expect(value, `${token} must be declared in :root`).toBeDefined();
			for (const surface of ['--bg', '--surface'] as const) {
				const ratio = contrastRatio(value, palette[surface]);
				expect(
					ratio as number,
					`${token} (${value}) on ${surface} (${palette[surface]}) is ${formatRatio(
						ratio as number
					)}`
				).toBeGreaterThanOrEqual(WCAG_AA_NORMAL);
			}
		});
	}

	/**
	 * `--surface-2` is the app's most hostile surface: only `--text` clears
	 * 4.5:1 on it. Asserting that keeps the app.css note honest — if someone
	 * lightens the surface enough that `--text-muted` becomes legal there, the
	 * comment telling authors not to use it becomes wrong and this fails.
	 */
	it('--surface-2 admits --text and refuses --text-muted', () => {
		expect(contrastRatio(palette['--text'], palette['--surface-2']) as number).toBeGreaterThanOrEqual(
			WCAG_AA_NORMAL
		);
		expect(contrastRatio(palette['--text-muted'], palette['--surface-2']) as number).toBeLessThan(
			WCAG_AA_NORMAL
		);
	});
});
