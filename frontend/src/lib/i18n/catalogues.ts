import { en } from './locales/en';
import type { Messages } from './messages';
import type { Locale } from './locale';

// One loader per supported locale, typed `Record<Locale, …>` so adding a
// locale to SUPPORTED_LOCALES without a catalogue here is a compile error.
// English resolves synchronously (it is the static fallback dict + the
// prerender default); every other locale is a dynamic import() so it
// splits into its own chunk and only downloads when actually selected —
// the i18n layer adds ~nothing to the initial payload.
//
// Used by the runtime (store.svelte.ts) to switch locale and by
// messages_parity.test.ts to validate every shipped catalogue without
// hard-coding the locale list.
export const CATALOGUE_LOADERS: Record<Locale, () => Promise<Messages>> = {
	en: () => Promise.resolve(en),
	de: () => import('./locales/de').then((m) => m.messages),
};
