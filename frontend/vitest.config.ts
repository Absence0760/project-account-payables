import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

// Unit-test config, kept separate from vite.config.ts (which carries the
// SvelteKit plugin). The i18n unit tests cover the *pure* runtime modules
// (locale negotiation, interpolation, message-catalogue parity) — none of
// which import `$app/*` or the Svelte compiler — so plain Node ESM + the
// dynamic-import() catalogue loaders are all that's needed. The reactive
// rune runtime (store.svelte.ts) isn't unit-tested here; its behaviour is
// exercised through the components in the e2e suite.
//
// The `$lib` alias is mapped manually (SvelteKit normally injects it) so the
// pure helpers in `src/lib/utils/` that import a sibling via `$lib/...` — e.g.
// `utils/time.ts` reading `$lib/i18n/formatLocale` — resolve under Node.
export default defineConfig({
	resolve: {
		alias: {
			$lib: fileURLToPath(new URL('./src/lib', import.meta.url)),
		},
	},
	test: {
		environment: 'node',
		// `tests-e2e/**/*.test.ts` is deliberately narrower than the `src` entry:
		// the e2e directory's `*.spec.ts` files are Playwright's and must never
		// be collected here. The one file it picks up is
		// `tests-e2e/fixtures/origins.test.ts`, the static guard keeping the web
		// port out of the specs — a source scan, so it belongs in the fast unit
		// job rather than costing a browser worker. Playwright ignores it in
		// turn via `testIgnore: ['**/fixtures/**']`.
		include: ['src/**/*.{test,spec}.ts', 'tests-e2e/**/*.test.ts'],
		// Vitest's default (`css: false`) short-circuits every CSS module to an
		// empty string — including one imported `?raw`. The token-pairing guard
		// (`lib/a11y/tokenPairing.test.ts`) reads `app.css` as text to extract
		// the palette, and a silently-empty read would make it pass by scanning
		// nothing. No test imports CSS for its styles, so turning processing on
		// costs nothing else.
		css: true,
	},
});
