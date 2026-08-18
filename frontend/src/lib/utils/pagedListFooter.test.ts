import { describe, expect, it } from 'vitest';

/**
 * Source-scan guard: a list that can say "Showing all N" must also be able to
 * load the rest of the N.
 *
 * Six lists (budgets, intake, catalogs, requisitions, and the /expenses
 * Reports + Cards sub-lists) asked the API for one capped page and then
 * rendered `m('…​.showingAll', { total })` unconditionally, where `total` is the
 * server's count of the *whole* filtered set. With 87 budgets the page showed
 * 50 rows and asserted "Showing all 87 budgets" — the remaining 37 were
 * unreachable, and the footer said they did not exist.
 *
 * The rule: any file referencing a `<list>.showingAll` message must also
 * reference the matching `<list>.loadMore`, which is what renders the button
 * that appends the next page. Per-key, not per-file, because `/expenses` holds
 * four independent sub-lists in one component.
 *
 * Reads the tree through Vite's `import.meta.glob` for the same reason
 * `a11y/tokenPairing.test.ts` does — the frontend deliberately carries no
 * `@types/node`.
 */

const RAW = import.meta.glob('/src/**/*.svelte', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

const SHOWING_ALL = /m\(\s*'([A-Za-z0-9.]+)\.showingAll'/g;

describe('paged list footers', () => {
	it('reads the component tree', () => {
		expect(Object.keys(RAW).length).toBeGreaterThan(50);
	});

	it('every "Showing all N" footer is paired with a Load more control', () => {
		const offenders: string[] = [];
		for (const [path, source] of Object.entries(RAW)) {
			SHOWING_ALL.lastIndex = 0;
			for (const match of source.matchAll(SHOWING_ALL)) {
				const list = match[1];
				if (!source.includes(`'${list}.loadMore'`)) offenders.push(`${path} → ${list}`);
			}
		}
		expect(
			offenders,
			'these lists claim to show everything but offer no way to load the rest'
		).toEqual([]);
	});
});
