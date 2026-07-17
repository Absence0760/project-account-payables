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
