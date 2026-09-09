/**
 * What a KPI card shows where a figure would go when there is no figure.
 *
 * The problem this owns: an aggregate KPI is rendered from a response that has
 * not arrived yet. Coercing the absent figure to `0` — `formatMoney(x ?? 0)`,
 * `count ?? 0` — renders a number nobody computed as though somebody had, and
 * it renders it as the one value a reader acts on: "we captured nothing", "no
 * offers are open". That is `docs/decisions.md` §34 in a display layer — an
 * attestation nobody made must not report the reassuring (or alarming) answer.
 *
 * The convention, decided once here so every page inherits it rather than each
 * one choosing:
 *
 *   - **A figure that exists renders exactly as the caller formatted it**,
 *     including a genuine zero. A computed `0` is a fact and must stay
 *     indistinguishable from any other computed figure.
 *   - **A figure that does not exist renders {@link KPI_NO_FIGURE}** — the same
 *     em dash `formatMoney` already returns for a null amount, and the same
 *     glyph the CFO cash-conversion-cycle, fraud-rate and discount capture-rate
 *     cards already show for an unknown. Reusing it keeps one vocabulary for
 *     "no number here" instead of adding a second (a shimmer skeleton would
 *     have been a second, would have needed its own reduced-motion handling,
 *     and would have read as a *different kind* of nothing from the dash on the
 *     card beside it).
 *   - **Whether it is missing because it is still loading is announced, not
 *     drawn.** The dash is identical either way — a reader who cannot yet see a
 *     number does not need two dashes to tell apart — but the pending card sets
 *     `aria-busy` and substitutes a "Loading…" label for the dash, so assistive
 *     tech hears a state rather than a punctuation mark. That is the accessible
 *     half of the same decision: a placeholder must never be read as a value.
 *
 * Pure — no `$state`, no `fetch`, no DOM — so it lives in `utils/` beside
 * `discountPartialSet.ts` and unit-tests under the plain-Node vitest config,
 * which cannot compile a `.svelte` file.
 */

/**
 * The glyph a KPI shows in place of a figure it does not have.
 *
 * Deliberately the same character `formatMoney`'s `placeholder` parameter
 * defaults to, so a money KPI reading `—` and a `formatMoney(null)` elsewhere
 * on the page say the same thing. `kpiValue.test.ts` asserts the two agree, so
 * changing one without the other fails rather than drifts.
 */
export const KPI_NO_FIGURE = '—';

/**
 * Why a KPI does or does not have a figure.
 *
 * `pending` and `unavailable` render identically; they differ only in what is
 * announced, because "still arriving" and "did not arrive" are different facts
 * to a reader who cannot see the card settle.
 */
export type KpiFigureState = 'value' | 'pending' | 'unavailable';

/**
 * Classify what a card is holding.
 *
 * A landed figure always wins: `pending` is a statement about a *missing*
 * figure, so a caller that leaves the flag set while data is on screen cannot
 * blank a real number. Only `null` / `undefined` count as missing — an empty
 * string, a `0`, and the string `'0'` are all figures somebody computed.
 */
export function kpiFigureState(
	value: string | number | null | undefined,
	pending = false
): KpiFigureState {
	if (value !== null && value !== undefined) return 'value';
	return pending ? 'pending' : 'unavailable';
}

/**
 * The text a KPI card renders for `value`.
 *
 * Returns the caller's own formatting untouched when there is a figure — this
 * never re-formats money — and {@link KPI_NO_FIGURE} when there is not.
 */
export function kpiDisplayValue(
	value: string | number | null | undefined,
	pending = false
): string {
	return kpiFigureState(value, pending) === 'value' ? String(value) : KPI_NO_FIGURE;
}
