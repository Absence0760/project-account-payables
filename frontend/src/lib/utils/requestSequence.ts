/**
 * Guards a list-loader's state against being overwritten by a response that no
 * longer reflects it.
 *
 * Two things can make an in-flight response stale, and both are handled here:
 *
 * 1. **A newer request.** The classic "search 'acm' resolves after 'acme'"
 *    race. Call `start()` synchronously right before firing a request to get a
 *    token; when the response resolves, only write it into state if
 *    `canCommit(token)` is still true.
 *
 * 2. **A local mutation.** A page that edits one row in place — an approve, a
 *    bank-detail save, a file attach — never issues a request of its own, so
 *    the counter above never moves and a fetch that was already in flight
 *    happily lands afterwards with a pre-edit snapshot and reverts the edit.
 *    Call `supersedeInFlight()` immediately BEFORE applying such an edit: every
 *    request issued up to that moment is marked un-committable, so none of them
 *    can clobber it. Requests issued after the edit are unaffected (they read a
 *    server state that already includes it).
 *
 * A superseded response is **discarded, not merged**. Overlaying the local edit
 * back onto it isn't sound: the response is a whole-row server snapshot taken
 * before the edit, so re-applying the edit's fields would still publish stale
 * values for every other field on that row. Dropping it leaves the list showing
 * the pre-fetch data plus the edit — the next sequenced fetch (a filter change,
 * a modal close, a reload) reconciles. See `docs/decisions.md` §23.
 *
 * `canCommit` answers "may this response be written into state?"; the separate
 * `isCurrentRequest` answers "is this still the newest request I issued?" and
 * is what a `finally` block should use to clear a `loading` flag or surface a
 * load error. Using `canCommit` there would leave the spinner stuck on forever
 * after a local mutation superseded the fetch that owns it.
 *
 * Shared by every list loader that can have a request in flight while something
 * else changes the list — see `stores/invoices.svelte.ts`,
 * `stores/payments.svelte.ts`, and `routes/vendors/+page.svelte`.
 */
export function createRequestSequencer() {
	let latest = 0;
	// Tokens at or below this were issued before a local mutation was applied,
	// so their responses predate it and must not be committed.
	let staleThrough = 0;

	return {
		/** Call synchronously right before firing the request. Returns this
		 *  call's token. */
		start(): number {
			return ++latest;
		},
		/** Call when the response resolves. True only if nothing has superseded
		 *  it since it was issued — no later `start()`, and no local mutation
		 *  via `supersedeInFlight()`. False means: discard the response. */
		canCommit(token: number): boolean {
			return token === latest && token > staleThrough;
		},
		/** True while this is the most recently issued request, regardless of
		 *  any local mutation. Use for `loading`-flag and error-surface
		 *  bookkeeping in a `finally` — NOT to decide whether to commit data. */
		isCurrentRequest(token: number): boolean {
			return token === latest;
		},
		/** Mark every already-issued request un-committable. Call immediately
		 *  before applying a local mutation to the list. */
		supersedeInFlight(): void {
			staleThrough = latest;
		}
	};
}
