/**
 * Pure logic behind `ui/VendorPicker.svelte` — the one searchable, server-paged
 * vendor combobox every vendor-choosing surface uses.
 *
 * It lives outside the component for the usual reason: the parts that decide
 * what the user can reach (which option Enter commits, whether the list on
 * screen is the whole match set) are exactly the parts worth testing without a
 * DOM, and they are what the picker exists to get right. The component owns
 * fetching, focus and markup; this module owns the rules.
 *
 * Background: five surfaces (`/catalogs`, `ContractModal`, `RecurringModal`,
 * `VendorStatementReconModal`, `BulkNegotiationModal`) each rendered a native
 * `<select>` over a client-side vendor list — two of them capped at the first
 * 100 rows, three of them walking every page on mount. A native `<select>` has
 * no search, so on the capped ones a tenant past 100 vendors simply could not
 * pick the rest, anywhere, with nothing on screen saying so.
 */

/** Vendor picker option — the `id` is what the consuming form submits. */
export interface VendorPickerOption {
	id: string;
	name: string;
	code?: string | null;
}

/**
 * How many options one page of the picker asks the server for.
 *
 * Deliberately well under the server's `MAX_PAGE_SIZE` of 100: the popup is a
 * browsing aid, not the reach mechanism — `search=` is. A bigger first page
 * only makes the popup longer to scan while moving the same cliff further out.
 */
export const VENDOR_PICKER_PAGE_SIZE = 25;

/**
 * Debounce for the search box, in ms. Matches the `/catalogs` and `/vendors`
 * list surfaces: a cleared box fires immediately (the user is asking for the
 * unfiltered list back and should not wait for it), typing waits.
 */
export function vendorSearchDelay(query: string): number {
	return query.trim() === '' ? 0 : 280;
}

/** The text shown for an option, and the input's text once it is chosen. */
export function vendorOptionLabel(option: VendorPickerOption): string {
	const code = option.code?.trim();
	return code ? `${option.name} (${code})` : option.name;
}

/**
 * What the count line under the input says.
 *
 * `partial` is the whole point of this module: the user must never be shown a
 * subset of the matches without being told it is a subset. `loading` is
 * distinct from `none` — "we have not looked yet" and "there is nothing" are
 * different answers, and reading one as the other is how a truncated list reads
 * as an empty tenant.
 */
export type VendorPickerCount =
	| { kind: 'loading' }
	| { kind: 'none' }
	| { kind: 'all'; total: number }
	| { kind: 'partial'; shown: number; total: number };

export function vendorPickerCount(
	shown: number,
	total: number,
	loading: boolean
): VendorPickerCount {
	if (loading && shown === 0) return { kind: 'loading' };
	if (total <= 0) return { kind: 'none' };
	// `shown > total` is not impossible — the set can shrink between the page
	// fetch and the `total` that came with it. Report that as complete rather
	// than as a negative remainder, which would render "-2 more".
	if (shown >= total) return { kind: 'all', total };
	return { kind: 'partial', shown, total };
}

/** True when another page of matches exists beyond what is loaded. */
export function hasMoreVendors(shown: number, total: number): boolean {
	return vendorPickerCount(shown, total, false).kind === 'partial';
}

/**
 * The roving `aria-activedescendant` index after a navigation key.
 *
 * `current` is -1 when no option is active — the state the list resets to on
 * every new result set, so that Enter can never commit a row the user has not
 * looked at. Returns `null` when the key is not a navigation key, so the caller
 * leaves that event alone.
 */
export function nextActiveIndex(key: string, current: number, count: number): number | null {
	if (key !== 'ArrowDown' && key !== 'ArrowUp' && key !== 'Home' && key !== 'End') return null;
	if (count <= 0) return -1;
	switch (key) {
		case 'ArrowDown':
			// From "nothing active" the first press lands on the FIRST option.
			// `(-1 + 1) % count` happens to give that too, but say it explicitly
			// so the wrap arithmetic can't quietly change it later.
			return current < 0 ? 0 : (current + 1) % count;
		case 'ArrowUp':
			return current < 0 ? count - 1 : (current - 1 + count) % count;
		case 'Home':
			return 0;
		default:
			return count - 1;
	}
}

/**
 * Which option Enter commits.
 *
 * The active one when the user has arrowed to a row. Otherwise the sole option,
 * when the search has narrowed to exactly one — typing a vendor's full name and
 * pressing Enter is the fastest path through this control, and refusing it
 * because no row was arrowed to would cost a pointless keystroke. With two or
 * more candidates and nothing active there is no defensible pick, so: nothing.
 */
export function resolveEnterSelection(
	activeIndex: number,
	options: readonly VendorPickerOption[]
): VendorPickerOption | null {
	if (activeIndex >= 0 && activeIndex < options.length) return options[activeIndex];
	if (activeIndex < 0 && options.length === 1) return options[0];
	return null;
}

/**
 * The text the input reverts to when it loses focus.
 *
 * A combobox whose text can disagree with its committed value is a lie: the
 * field would read "Acme Manufact…" while the form submits whichever vendor was
 * picked before that. Reverting on blur is also what keeps a native `required`
 * on the input honest — non-empty text then means exactly "a vendor is
 * committed", which is the condition the consuming form actually gates on.
 */
export function revertedQuery(selectedLabel: string | null): string {
	return selectedLabel ?? '';
}
