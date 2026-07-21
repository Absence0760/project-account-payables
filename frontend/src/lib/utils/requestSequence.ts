/**
 * Guards a list-loader against out-of-order async responses clobbering newer
 * state — the classic "search 'acm' resolves after 'acme'" race.
 *
 * Call `start()` right before firing a request to get a token; when the
 * response resolves, only commit it to state if `isLatest(token)` is still
 * true. A response whose token has been superseded by a later `start()` call
 * is stale and must be discarded (not applied), even if it's the one that
 * happens to resolve last-in-wins by arrival order.
 *
 * Shared by every list loader that can have two fetches in flight at once
 * (rapid search typing, a debounced fetch racing a filter-chip fetch, a
 * load-more racing a fresh fetch) — see `stores/invoices.svelte.ts`,
 * `stores/payments.svelte.ts`, and `routes/vendors/+page.svelte`.
 */
export function createRequestSequencer() {
	let latest = 0;

	return {
		/** Call synchronously right before firing the request. Returns this
		 *  call's token. */
		start(): number {
			return ++latest;
		},
		/** Call when the response resolves. True only if no later `start()`
		 *  call has happened since — i.e. this is still the most recent
		 *  in-flight request. */
		isLatest(token: number): boolean {
			return token === latest;
		}
	};
}
