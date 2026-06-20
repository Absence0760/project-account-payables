import { defineConfig } from 'vitest/config';

// Unit-test config, kept separate from vite.config.ts (which carries the
// SvelteKit plugin). The i18n unit tests cover the *pure* runtime modules
// (locale negotiation, interpolation, message-catalogue parity) — none of
// which import `$app/*` or the Svelte compiler — so plain Node ESM + the
// dynamic-import() catalogue loaders are all that's needed. The reactive
// rune runtime (store.svelte.ts) isn't unit-tested here; its behaviour is
// exercised through the components in the e2e suite.
export default defineConfig({
	test: {
		environment: 'node',
		include: ['src/**/*.{test,spec}.ts'],
	},
});
