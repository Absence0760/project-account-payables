import { describe, expect, it } from 'vitest';

/**
 * Source-scan guard: every `$effect` that starts a timer must stop it.
 *
 * A `$effect` that does `searchTimer = setTimeout(…)` and returns nothing
 * leaves the timer armed when the component is destroyed. The callback then
 * runs on a page the user has already left — the debounce bodies in this app
 * call `syncUrl()` (which `replaceState`s the address bar) and list-store
 * reloads, which write a stale snapshot into a module-level store shared with
 * the next page. Svelte gives the effect a teardown for exactly this;
 * `routes/catalogs/+page.svelte` was the only site that used it, so the rule
 * is enforced here rather than left to the next author to remember.
 *
 * The check is textual and deliberately blunt: an `$effect(…)` body containing
 * `setTimeout(` / `setInterval(` must also contain a `return () => clear…`.
 * It mirrors the backend's own source-scan drift guards
 * (`backend/tests/test_payment_methods.py`), and reads the tree through Vite's
 * `import.meta.glob` for the same reason `a11y/tokenPairing.test.ts` does —
 * the frontend deliberately carries no `@types/node`.
 */

const RAW = import.meta.glob('/src/**/*.svelte', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

/** Index of the `}` matching the `{` at `open`. -1 when unbalanced. */
function matchingBrace(s: string, open: number): number {
	let depth = 0;
	for (let i = open; i < s.length; i++) {
		if (s[i] === '{') depth++;
		else if (s[i] === '}') {
			depth--;
			if (depth === 0) return i;
		}
	}
	return -1;
}

/** Every `$effect(...)` body in a source file, as raw text. */
function effectBodies(source: string): string[] {
	const bodies: string[] = [];
	const marker = '$effect(';
	let from = 0;
	for (;;) {
		const at = source.indexOf(marker, from);
		if (at === -1) break;
		const open = source.indexOf('{', at);
		if (open === -1) break;
		const close = matchingBrace(source, open);
		if (close === -1) break;
		bodies.push(source.slice(open, close + 1));
		from = close + 1;
	}
	return bodies;
}

describe('$effect timer cleanup', () => {
	it('reads the component tree', () => {
		expect(Object.keys(RAW).length).toBeGreaterThan(50);
	});

	it('every $effect that starts a timer returns a cleanup that clears it', () => {
		const offenders: string[] = [];
		for (const [path, source] of Object.entries(RAW)) {
			for (const body of effectBodies(source)) {
				if (!/\b(setTimeout|setInterval)\s*\(/.test(body)) continue;
				const clears =
					/return\s*\(\s*\)\s*=>[\s\S]*?\b(clearTimeout|clearInterval)\s*\(/.test(body);
				if (!clears) offenders.push(path);
			}
		}
		expect(offenders, 'these $effect blocks arm a timer they never disarm').toEqual([]);
	});
});
