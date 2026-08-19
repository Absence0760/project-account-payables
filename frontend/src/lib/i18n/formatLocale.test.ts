import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { getActiveFormatLocale, setActiveFormatLocale } from './formatLocale';

// `formatLocale.ts` is the holder every `Intl` formatter in the app reads. It
// carries two contracts that are easy to break in opposite directions:
//
//   1. **It must stay importable outside a browser.** `money.ts` / `time.ts`
//      import it and are pure, unit-tested modules — turning it into a
//      `.svelte.ts` rune module (or importing `$app/*` here) would make every
//      money/date helper a Svelte-compiler dependency and take these tests
//      down with it. Merely running this file proves the import resolves under
//      vitest's node environment.
//   2. **A locale change must be observable to its readers.** Before the
//      `createSubscriber` wiring, a switch moved every `m()` label (the `dict`
//      rune) while every money cell and date stayed in the browser locale
//      until its component remounted.
//
// The re-render half of (2) is Svelte-runtime behaviour: under node,
// `svelte/reactivity` resolves to `index-server.js`, where `createSubscriber`
// is a no-op — which is exactly what keeps (1) true, and exactly why the
// re-render cannot be asserted here. The value half is tested directly below;
// the wiring that turns it into a re-render is pinned by a source scan, the
// same technique `utils/effectTimerCleanup.test.ts` and
// `utils/pagedListFooter.test.ts` use for behaviour no non-flaky runtime test
// can reach. A browser-level guard would be a Playwright spec that switches
// the locale on a money-bearing page and asserts the figures move without a
// reload.

const SOURCE = readFileSync(fileURLToPath(new URL('./formatLocale.ts', import.meta.url)), 'utf8');

afterEach(() => {
	setActiveFormatLocale(undefined);
});

describe('active format locale holder', () => {
	it('starts undefined (defer to the runtime/browser locale)', () => {
		expect(getActiveFormatLocale()).toBeUndefined();
	});

	it('round-trips a locale tag', () => {
		setActiveFormatLocale('de-DE');
		expect(getActiveFormatLocale()).toBe('de-DE');
		setActiveFormatLocale('ja');
		expect(getActiveFormatLocale()).toBe('ja');
	});

	it('trims surrounding whitespace', () => {
		setActiveFormatLocale('  pt-BR  ');
		expect(getActiveFormatLocale()).toBe('pt-BR');
	});

	it('resets to undefined for every empty / nullish shape', () => {
		for (const empty of ['', '   ', null, undefined]) {
			setActiveFormatLocale('de-DE');
			setActiveFormatLocale(empty);
			expect(getActiveFormatLocale()).toBeUndefined();
		}
	});

	it('is idempotent — re-setting the same locale keeps the value', () => {
		// `setActiveFormatLocale` short-circuits a no-change write so it can't
		// re-render every money cell in the app for nothing; that must not
		// leave the stored value behind.
		setActiveFormatLocale('fr');
		setActiveFormatLocale('fr');
		expect(getActiveFormatLocale()).toBe('fr');
	});
});

describe('reactive wiring (source guard)', () => {
	it('builds its invalidation on svelte/reactivity::createSubscriber', () => {
		expect(SOURCE).toMatch(/import\s*\{\s*createSubscriber\s*\}\s*from\s*'svelte\/reactivity'/);
		expect(SOURCE).toMatch(/createSubscriber\(/);
	});

	it('imports nothing that would break the node-importable contract', () => {
		// `$app/*` has no vitest alias, and a `.svelte.ts` import drags in the
		// compiler — either one breaks `money.ts` / `time.ts` unit tests.
		const imports = [...SOURCE.matchAll(/from\s*'([^']+)'/g)].map((m) => m[1]);
		expect(imports).toEqual(['svelte/reactivity']);
	});

	it('subscribes inside the getter, so a read registers a dependency', () => {
		const getter = /export function getActiveFormatLocale\(\)[^}]*\}/.exec(SOURCE)?.[0] ?? '';
		expect(getter, 'getActiveFormatLocale not found').not.toBe('');
		expect(getter, 'the getter must call subscribe() before returning').toContain('subscribe()');
	});

	it('notifies subscribers from the setter', () => {
		const setter =
			/export function setActiveFormatLocale\([\s\S]*?\n\}/.exec(SOURCE)?.[0] ?? '';
		expect(setter, 'setActiveFormatLocale not found').not.toBe('');
		expect(setter, 'the setter must invalidate reactive readers').toMatch(
			/notifyLocaleChanged\?\.\(\)/
		);
	});
});
