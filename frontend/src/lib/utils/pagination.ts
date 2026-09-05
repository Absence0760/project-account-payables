/**
 * Load-more append for offset-paginated lists.
 *
 * Append the next page onto the already-loaded rows, skipping any id already
 * present. Offset pagination can re-surface a row when the underlying set
 * shifts between fetches (e.g. a new invoice inserted, a notification arriving
 * between page loads); a duplicate id would crash the keyed
 * `{#each ... (item.id)}` rendering the list (Svelte 5 `each_key_duplicate`).
 *
 * Semantics (matching the original invoice-store inline guard):
 * - The existing row wins — an incoming duplicate is dropped, never replacing
 *   the already-rendered row.
 * - Order is preserved: existing rows first (in order), then the incoming
 *   page's new rows in their server order.
 */
export function appendUnique<T extends { id: string }>(existing: T[], incoming: T[]): T[] {
	const seen = new Set(existing.map((item) => item.id));
	return [...existing, ...incoming.filter((item) => !seen.has(item.id))];
}

/**
 * Response shape of every list endpoint's `/ids` sibling (`GET
 * /api/invoices/ids`, `/api/exceptions/ids`, `/api/expenses/ids` —
 * `backend/app/api/pagination.py::MatchingIdsResponse`).
 *
 * Backs the "select all N matching" affordance: a list page's header
 * checkbox only ever selects the currently-LOADED page of rows (the bulk
 * endpoints take an explicit id list, and the page only has what it fetched
 * client-side) — this is the whole filtered set instead, resolved
 * server-side so a bulk action can't silently skip rows past the first page.
 * `truncated` is true when the match count exceeded the server's cap
 * (`MAX_SELECT_ALL_IDS`), so a partial selection is never presented as
 * complete.
 */
export interface MatchingIdsResponse {
	ids: string[];
	total: number;
	truncated: boolean;
}

/** Envelope every paginated list endpoint returns (`backend/app/api/pagination.py`). */
export interface PagedResponse<T> {
	items: T[];
	total: number;
}

/**
 * The server's `MAX_PAGE_SIZE` (`backend/app/api/pagination.py`). A request for
 * more is clamped, so asking for more is not a way to skip paging.
 */
export const MAX_PAGE_SIZE = 100;

/**
 * Fetch EVERY page of an offset-paginated list, not just the first.
 *
 * A bare `api.get('/api/vendors')` returns the server's DEFAULT_PAGE_SIZE (20)
 * rows and nothing says so. That is right for a table with a Load-more control
 * and wrong for a `<select>` whose options ARE the set of valid choices: on a
 * tenant with more suppliers than one page, the dropdown silently omits most of
 * them and — with no search inside a native `<select>` — they are unreachable.
 *
 * Raising `page_size` is not the fix; the server caps it at
 * {@link MAX_PAGE_SIZE}, so it only moves the cliff. This walks pages until the
 * envelope's own `total` is satisfied.
 *
 * `maxPages` is a runaway bound, not a product limit: it stops a mis-specified
 * endpoint (a `total` that never converges) from looping forever. Reaching it
 * means the list is far past what any `<select>` should hold, and the caller
 * should have a searchable picker instead.
 */
export async function fetchAllPages<T>(
	fetchPage: (page: number, pageSize: number) => Promise<PagedResponse<T>>,
	{ pageSize = MAX_PAGE_SIZE, maxPages = 50 }: { pageSize?: number; maxPages?: number } = {}
): Promise<T[]> {
	let items: T[] = [];
	for (let page = 1; page <= maxPages; page++) {
		const data = await fetchPage(page, pageSize);
		items = items.concat(data.items ?? []);
		// Stop on the envelope's own count, and on a short/empty page so a
		// `total` that disagrees with reality can't spin.
		if (items.length >= (data.total ?? 0) || (data.items?.length ?? 0) < pageSize) break;
	}
	return items;
}
