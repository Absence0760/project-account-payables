import { describe, expect, it } from 'vitest';

/**
 * Source-scan guard: a fire-and-forget store load must swallow its rejection.
 *
 * The list-store loaders re-throw on purpose — an awaiting caller keeps its own
 * handling, and `/invoices`' post-upload toast depends on it. But the mount and
 * filter `$effect`s call them WITHOUT `await`, so a failed load surfaces as an
 * `[Unhandled rejection] ApiError` in the console. Harmless in the UI (the
 * stores' `errored` flag is what renders the failure state) but noisy, and
 * noise is how a real unhandled rejection goes unnoticed.
 *
 * The rule: inside a `$effect`, a call to a store loader that is neither
 * `await`ed, `return`ed, nor handed to `Promise.all(...)` must carry a
 * `.catch(...)`. The `.catch` should be empty with a comment pointing at the
 * store's `errored` flag — the point is to declare "the store already renders
 * this", not to add a second error path.
 *
 * Textual and deliberately blunt, mirroring
 * `effectTimerCleanup.test.ts` (and the backend's own source-scan drift guards
 * in `tests/test_payment_methods.py`). It reads the tree through Vite's
 * `import.meta.glob` for the same reason `a11y/tokenPairing.test.ts` does — the
 * frontend deliberately carries no `@types/node`.
 */

const RAW = import.meta.glob('/src/**/*.svelte', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

/** The store objects whose loaders re-throw. */
const STORES = [
	'invoiceStore',
	'paymentStore',
	'expenseStore',
	'contractStore',
	'workflowStore',
	'notificationStore',
	'adminStore'
];

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

/**
 * Statement lines inside `body` that call a store loader in a fire-and-forget
 * position. A line is exempt when it `await`s, `return`s, or is an argument
 * inside a `Promise.all([...])` / `Promise.allSettled([...])` (those are
 * awaited by the enclosing statement), or already has a `.catch`.
 */
function unhandledLoads(body: string): string[] {
	const call = new RegExp(`\\b(?:${STORES.join('|')})\\.(?:fetch|load|refresh)[A-Za-z]*\\s*\\(`);
	const offenders: string[] = [];
	const lines = body.split('\n');
	let inPromiseAll = 0;
	for (const raw of lines) {
		const line = raw.trim();
		// A comment naming a loader is prose, not a call site.
		if (line.startsWith('//') || line.startsWith('*') || line.startsWith('/*')) continue;
		if (/Promise\.(all|allSettled)\s*\(\s*\[/.test(line)) inPromiseAll++;
		const hit = call.test(line);
		if (hit && inPromiseAll === 0) {
			const exempt =
				/\bawait\b/.test(line) || /^return\b/.test(line) || /\.catch\s*\(/.test(line);
			if (!exempt) offenders.push(line);
		}
		if (inPromiseAll > 0 && /\]\s*\)/.test(line)) inPromiseAll--;
	}
	return offenders;
}

describe('fire-and-forget store loads are caught', () => {
	it('no $effect calls a re-throwing store loader without await or .catch', () => {
		const found: string[] = [];
		for (const [path, source] of Object.entries(RAW)) {
			for (const body of effectBodies(source)) {
				for (const line of unhandledLoads(body)) {
					found.push(`${path}: ${line}`);
				}
			}
		}
		expect(found, found.join('\n')).toEqual([]);
	});

	// The fixtures below are STRING LITERALS fed to the scanner, not calls.
	it('recognises the exempt shapes', () => {
		expect(unhandledLoads('{\n\tawait invoiceStore.fetch(p);\n}')).toEqual([]); // noqa: raw-fetch-in-component — scanner fixture string, not a call
		expect(unhandledLoads('{\n\treturn invoiceStore.fetch(p);\n}')).toEqual([]); // noqa: raw-fetch-in-component — scanner fixture string, not a call
		expect(unhandledLoads('{\n\tinvoiceStore.fetch(p).catch(() => {});\n}')).toEqual([]); // noqa: raw-fetch-in-component — scanner fixture string, not a call
		expect(
			unhandledLoads('{\nawait Promise.all([\npaymentStore.fetch(p),\nx(),\n]);\n}') // noqa: raw-fetch-in-component — scanner fixture string, not a call
		).toEqual([]);
	});

	it('flags a bare fire-and-forget call', () => {
		expect(unhandledLoads('{\n\tinvoiceStore.fetch(buildParams());\n}')).toEqual([ // noqa: raw-fetch-in-component — scanner fixture string, not a call
			'invoiceStore.fetch(buildParams());' // noqa: raw-fetch-in-component — scanner fixture string, not a call
		]);
	});
});

/**
 * The other half of the same convention.
 *
 * The `.catch(() => {})` above is only safe because — in the words of the
 * comment every one of those call sites carries — "the store's `errored` flag
 * is what the UI renders". Two stores carried that exact comment at their call
 * sites while exposing no such flag: `adminStore` and `workflowStore`. A failed
 * load was swallowed with no toast, no banner and no retry, and the table
 * asserted "No users found." / "No workflows configured." An outage read as an
 * empty tenant, on the two pages you open when access or routing is already
 * misbehaving.
 *
 * So: any store a swallowing call site CITES must actually expose the flag.
 * Derived from the comments rather than a hardcoded list, so a new surface
 * making the same claim is covered for free — and a store whose consumer owns
 * its own error state instead (the `/notifications` route does) is correctly
 * out of scope, because it never makes the claim.
 */
const STORE_SOURCES = import.meta.glob('/src/lib/stores/*.svelte.ts', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

/** Store object names cited by a swallowing call site that names the flag. */
function citedStores(): Set<string> {
	const cited = new Set<string>();
	for (const source of Object.values(RAW)) {
		for (const body of effectBodies(source)) {
			if (!body.includes('`errored` flag')) continue;
			for (const [, name] of body.matchAll(/(\w+Store)\.\w+\([^;]*\)\.catch/g)) {
				cited.add(name);
			}
		}
	}
	return cited;
}

/** `adminStore` → `/src/lib/stores/admin.svelte.ts`. */
function moduleFor(storeName: string): string | undefined {
	const stem = storeName.replace(/Store$/, '').toLowerCase();
	return Object.keys(STORE_SOURCES).find((p) => {
		const file = p.split('/').pop()!.replace('.svelte.ts', '').toLowerCase();
		return file === stem || file === `${stem}s`;
	});
}

describe('a store cited as owning the failure state actually owns it', () => {
	it('finds the citing call sites at all (the scan is not vacuous)', () => {
		expect(citedStores().size).toBeGreaterThan(0);
	});

	it('every cited store exposes a real `errored` flag', () => {
		const missing: string[] = [];
		for (const storeName of citedStores()) {
			const path = moduleFor(storeName);
			if (!path) {
				missing.push(`${storeName}: no store module found`);
				continue;
			}
			const source = STORE_SOURCES[path];
			if (!/get errored\(\)/.test(source)) missing.push(`${storeName}: no \`errored\` getter`);
			// …and it is real state, not a hardcoded false.
			else if (!/let errored = \$state\(false\)/.test(source)) {
				missing.push(`${storeName}: \`errored\` is not $state`);
			}
		}
		expect(missing, missing.join('\n')).toEqual([]);
	});
});
