/**
 * Selection ↔ visible-row reconciliation for paginated/filtered list pages.
 *
 * A list page keeps row selection in a `Set<string>` of ids. When the list
 * refetches under a new filter/search/page, ids that fell off the list must be
 * dropped from the selection — otherwise the bulk-bar count (and the id set fed
 * to bulk delete/status/export) goes stale, acting on rows the user can no
 * longer see. Both the invoices list and the exceptions queue use this.
 */

/**
 * Return a selection pruned to only the ids present in `visibleIds`.
 *
 * Identity-preserving: returns the **same** Set instance when nothing is stale,
 * so a caller can guard a reactive reassignment on `result !== selected` and
 * avoid a redundant write (which, in a Svelte `$effect` that both reads and
 * writes the selection, would otherwise loop).
 */
export function pruneSelection(
	selected: Set<string>,
	visibleIds: Iterable<string>
): Set<string> {
	const visible = visibleIds instanceof Set ? visibleIds : new Set(visibleIds);
	let stale = false;
	for (const id of selected) {
		if (!visible.has(id)) {
			stale = true;
			break;
		}
	}
	if (!stale) return selected;
	return new Set([...selected].filter((id) => visible.has(id)));
}
