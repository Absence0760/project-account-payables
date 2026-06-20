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
		include: ['src/**/*.{test,spec}.ts'],
	},
});
