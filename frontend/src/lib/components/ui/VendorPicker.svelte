<script lang="ts">
	/**
	 * The shared vendor picker — a searchable, SERVER-PAGED combobox.
	 *
	 * Replaces the native `<select>` that five surfaces each built over a
	 * client-side vendor list: `/catalogs` (CatalogModal, twice), `/contracts`
	 * (ContractModal), `/recurring` (RecurringModal), `/vendor-statements`
	 * (VendorStatementReconModal) and `/discounts` (BulkNegotiationModal). Two
	 * of them asked for `page_size=100` and rendered page 1; the other three
	 * walked every page on mount. A `<select>` has no search, so on the capped
	 * pair a tenant past 100 vendors could not select the rest — anywhere — and
	 * nothing on screen said so. Fixing one modal would have left four wrong,
	 * which is why this is a component rather than five patches.
	 *
	 * Three properties it must keep:
	 *
	 * 1. **Reach.** Filtering happens on the server (`GET /api/vendors?search=`,
	 *    which matches name / code / email), so a vendor on page 40 is one
	 *    query away. Filtering a truncated client page would not be.
	 * 2. **Honesty.** The count line says how many of the matching set is on
	 *    screen. A subset is never presented as the whole, and a load failure
	 *    is never presented as an empty tenant — `/api/vendors` is
	 *    admin/ap_manager/cfo and an `ap_clerk` gets a 403.
	 * 3. **Keyboard + AT parity with the `<select>` it replaced.** WAI-ARIA 1.2
	 *    combobox: roving `aria-activedescendant` over a real `role="listbox"`,
	 *    Arrow/Home/End/Enter/Escape, an accessible name from a real `<label>`,
	 *    and the count line wired in as the input's description.
	 *
	 * The rules live in `$lib/utils/vendorPicker.ts` so they are testable
	 * without a DOM; this file owns fetching, focus and markup.
	 */
	import { searchVendorOptions, type VendorOption } from '$lib/api/vendors';
	import { m } from '$lib/i18n/store.svelte';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import {
		VENDOR_PICKER_PAGE_SIZE,
		hasMoreVendors,
		nextActiveIndex,
		resolveEnterSelection,
		revertedQuery,
		vendorOptionLabel,
		vendorPickerCount,
		vendorSearchDelay
	} from '$lib/utils/vendorPicker';

	let {
		value = $bindable(''),
		label,
		ariaLabel,
		/** The chosen vendor's name when the form opened on an existing row. The
		 *  vendor may sit on any page of the tenant's set, so the picker cannot
		 *  look it up from the first page — the parent already has it on the row
		 *  it is editing (`contract.vendor_name`, `template.vendor_name`, …). */
		selectedLabel = null,
		placeholder,
		required = false,
		disabled = false,
		fullWidth = false,
		compact = false,
		testid,
		onselect
	}: {
		/** The chosen vendor's uuid — `''` for none. This is what the form submits. */
		value?: string;
		/** Visible field label. Omit for an inline control and pass `ariaLabel`. */
		label?: string;
		/** Accessible name when there is no visible label. */
		ariaLabel?: string;
		selectedLabel?: string | null;
		placeholder?: string;
		required?: boolean;
		disabled?: boolean;
		/** Span both columns of a `.form-grid`. */
		fullWidth?: boolean;
		/** Narrow sizing for an inline row of controls. */
		compact?: boolean;
		testid?: string;
		/** Fired on every commit — the chosen option, or null when cleared. */
		onselect?: (option: VendorOption | null) => void;
	} = $props();

	const uid = $props.id();
	const inputId = `${uid}-input`;
	const labelId = `${uid}-label`;
	const listId = `${uid}-list`;
	const countId = `${uid}-count`;
	const optionId = (i: number) => `${uid}-opt-${i}`;

	let open = $state(false);
	let options = $state<VendorOption[]>([]);
	let total = $state(0);
	let page = $state(1);
	let loading = $state(false);
	/**
	 * Which fetch failed, if any — the LIST or just the NEXT page.
	 *
	 * They call for different copy over different content. A failed list leaves
	 * nothing on screen, so the count line must say so rather than let an empty
	 * popup read as an empty tenant. A failed "load more" leaves the page that
	 * did land still rendered and still valid, so the count line keeps reporting
	 * it and only the footer says what could not be added.
	 */
	let failure = $state<'none' | 'list' | 'more'>('none');
	let activeIndex = $state(-1);
	let inputEl = $state<HTMLInputElement | null>(null);

	/** The option the user picked in THIS session of the control. Kept so the
	 *  committed label survives the next search replacing `options`. */
	let picked = $state<VendorOption | null>(null);

	/**
	 * What the user has typed, or `null` when they have not typed since the box
	 * last settled. `''` (they cleared it) is a THIRD state and must not collapse
	 * into `null` — that is why this is nullable rather than a companion boolean.
	 *
	 * It also keeps the search term separate from the display text. At rest the
	 * box shows the committed vendor's NAME, which is not a search term: issuing
	 * it as one returns no matches and reads as "this tenant has none".
	 */
	let typed = $state<string | null>(null);

	const committedLabel = $derived.by(() => {
		if (!value) return null;
		if (picked?.id === value) return vendorOptionLabel(picked);
		const inPage = options.find((o) => o.id === value);
		if (inPage) return vendorOptionLabel(inPage);
		return selectedLabel;
	});

	/** The input's text: what was typed, else the reverted (committed) label. */
	const query = $derived(typed ?? revertedQuery(committedLabel));
	const searchTerm = $derived(typed ?? '');

	const count = $derived(vendorPickerCount(options.length, total, loading));
	const showMore = $derived(hasMoreVendors(options.length, total));
	/** The listbox exists only when it has options. With none, the count line
	 *  under the input already says why (loading / no matches / load failed), so
	 *  a floating empty box would only restate it — and `aria-expanded` must
	 *  agree with whether a listbox is actually there. */
	const expanded = $derived(open && options.length > 0);

	/**
	 * A vendor is committed but nothing on this screen can name it.
	 *
	 * Only reachable when the consuming row's API shape carries no vendor name
	 * (today: `Catalog`, unlike `Contract` / `RecurringTemplate` /
	 * `Reconciliation`, which all do). The native `<select>` this replaced hit
	 * the same case and fell back to rendering its empty first option — reading
	 * as "no vendor" while the form still held one. Saying it is the fix; the
	 * value is never dropped either way.
	 */
	const unresolvedSelection = $derived(!!value && committedLabel === null);

	const countText = $derived.by(() => {
		// Resting state: silent, unless the field is holding a vendor it cannot
		// name. Once the popup is open the count is the more urgent thing to say.
		if (!open) return unresolvedSelection ? m('vendors.picker.unresolvedSelection') : '';
		// Loading outranks the previous failure: a retry that is genuinely in
		// flight must not still read as "couldn't load".
		if (count.kind === 'loading') return m('vendors.picker.loading');
		if (failure === 'list') return m('vendors.picker.loadFailed');
		// `loading` is handled above, so the compiler has narrowed it out here.
		switch (count.kind) {
			case 'none':
				return searchTerm
					? m('vendors.picker.noMatches', { query: searchTerm })
					: m('vendors.picker.noVendors');
			case 'all':
				return m('vendors.picker.showingAll', { total: count.total });
			case 'partial':
				return m('vendors.picker.showingPartial', {
					shown: count.shown,
					total: count.total
				});
		}
	});

	const fetchSequence = createRequestSequencer();

	async function load(nextPage: number, replace: boolean) {
		const token = fetchSequence.start();
		loading = true;
		try {
			const res = await searchVendorOptions({
				search: searchTerm,
				page: nextPage,
				page_size: VENDOR_PICKER_PAGE_SIZE
			});
			if (!fetchSequence.canCommit(token)) return;
			// `appendUnique`, not a bare concat: offset pagination can re-surface
			// a row when the set shifts between fetches, and a duplicate id
			// crashes the keyed `{#each}` (Svelte `each_key_duplicate`).
			options = replace ? res.items : appendUnique(options, res.items);
			total = res.total;
			page = nextPage;
			failure = 'none';
			// A replaced result set drops the highlight: carrying it onto
			// whichever row now occupies that position would let Enter commit a
			// vendor the user never looked at.
			if (replace) activeIndex = -1;
		} catch {
			if (!fetchSequence.canCommit(token)) return;
			// Surfaced, never swallowed. "You may not list vendors" (403) and
			// "this tenant has none" are different answers and the picker must
			// not present the first as the second.
			failure = replace ? 'list' : 'more';
			if (replace) {
				options = [];
				total = 0;
				activeIndex = -1;
			}
		} finally {
			// `isCurrentRequest`, not `canCommit` — see `utils/requestSequence.ts`.
			if (fetchSequence.isCurrentRequest(token)) loading = false;
		}
	}

	// Re-query on open and on every search edit, debounced. A cleared box fires
	// immediately; typing waits (`vendorSearchDelay`, matching /catalogs).
	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		if (!open) return;
		const q = searchTerm;
		// Set synchronously, not inside the debounced `load()`. Until the timer
		// fires, `count` would otherwise be recomputed off the PREVIOUS result
		// set — 0 of 0 on a first open — and the count line would announce "No
		// vendors" through its live region before replacing it with "Loading".
		// With options already on screen this changes nothing that is rendered.
		loading = true;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => void load(1, true), vendorSearchDelay(q));
		return () => clearTimeout(searchTimer);
	});

	function commit(option: VendorOption) {
		picked = option;
		value = option.id;
		// Back to "not typing" — the box now displays this option's label via
		// `committedLabel`, so the text and the committed value cannot disagree.
		typed = null;
		open = false;
		activeIndex = -1;
		onselect?.(option);
	}

	function clear() {
		picked = null;
		value = '';
		typed = null;
		activeIndex = -1;
		onselect?.(null);
		inputEl?.focus();
	}

	function closeAndRevert() {
		open = false;
		activeIndex = -1;
		// Dropping back to "not typing" reverts the text through `query`'s
		// `revertedQuery(committedLabel)`, so the box can never be left showing a
		// search term that disagrees with the value the form will submit.
		typed = null;
	}

	/**
	 * Bound with `onkeydownCAPTURE`, deliberately.
	 *
	 * `ui/Modal` traps focus with `actions/focusTrap`, which registers a REAL
	 * `keydown` listener on the dialog box — while Svelte 5 delegates a plain
	 * `onkeydown` to one listener at the app root. Real bubbling reaches the
	 * dialog first, so a bubble-phase handler here would run AFTER the trap had
	 * already seen Escape and shut the whole modal, and stopping propagation
	 * from it would be too late to matter. A capture listener is attached to the
	 * input directly and fires in the TARGET phase, before the bubble phase
	 * reaches any ancestor — which is what lets the Escape branch below close
	 * the popup and keep the dialog open.
	 */
	function onKeydown(e: KeyboardEvent) {
		if (disabled) return;

		if (e.key === 'Escape') {
			if (!open) return; // let it through — the enclosing Modal closes
			// Close the popup, not the dialog around it. See the CAPTURE note on
			// the binding below for why stopping propagation here actually works.
			e.stopPropagation();
			e.preventDefault();
			closeAndRevert();
			return;
		}

		if (e.key === 'Tab') {
			if (open) closeAndRevert();
			return;
		}

		if (e.key === 'Enter') {
			if (!open) return; // closed: let the form submit as normal
			e.preventDefault();
			const selection = resolveEnterSelection(activeIndex, options);
			if (selection) commit(selection);
			return;
		}

		const next = nextActiveIndex(e.key, activeIndex, options.length);
		if (next === null) return;
		e.preventDefault();
		if (!open) {
			open = true;
			return;
		}
		activeIndex = next;
	}

	function onInput(e: Event) {
		open = true;
		typed = (e.currentTarget as HTMLInputElement).value;
	}

	function onFocus() {
		if (disabled) return;
		open = true;
		// Select the committed label so the first keystroke replaces it rather
		// than appending to a name the user is not trying to extend.
		inputEl?.select();
	}

	/**
	 * Re-open on a click into a field that already holds focus.
	 *
	 * Committing a choice closes the popup WITHOUT moving focus, so from then on
	 * the input is the active element — and clicking an already-focused element
	 * fires no `focus` event. Without this the control was one-shot: choose a
	 * vendor, then clicking the field to change it did nothing at all.
	 * Deliberately does NOT re-`select()`; a second click is usually the user
	 * placing a cursor, not restarting the search.
	 */
	function onClick() {
		if (!disabled) open = true;
	}

	function onFocusOut(e: FocusEvent) {
		// Ignore focus moving WITHIN the control (the clear button, Load more).
		const next = e.relatedTarget as Node | null;
		if (next && (e.currentTarget as HTMLElement).contains(next)) return;
		closeAndRevert();
	}
</script>

<div
	class="vendor-picker"
	class:full-width={fullWidth}
	class:compact
	onfocusout={onFocusOut}
	data-testid={testid ? `${testid}-field` : undefined}
>
	{#if label}
		<label class="vp-label" id={labelId} for={inputId}>
			{label}{#if required}<em class="required">*</em>{/if}
		</label>
	{/if}
	<div class="vp-control">
		<input
			bind:this={inputEl}
			id={inputId}
			type="text"
			role="combobox"
			class="vp-input"
			autocomplete="off"
			aria-expanded={expanded}
			aria-controls={expanded ? listId : undefined}
			aria-haspopup="listbox"
			aria-autocomplete="list"
			aria-activedescendant={expanded && activeIndex >= 0 ? optionId(activeIndex) : undefined}
			aria-describedby={countId}
			aria-labelledby={label ? labelId : undefined}
			aria-label={label ? undefined : ariaLabel}
			{placeholder}
			{required}
			{disabled}
			value={query}
			oninput={onInput}
			onfocus={onFocus}
			onclick={onClick}
			onkeydowncapture={onKeydown}
			data-testid={testid}
		/>
		{#if value && !disabled}
			<button
				type="button"
				class="vp-clear"
				aria-label={m('vendors.picker.clearAria')}
				onclick={clear}
			>
				<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
			</button>
		{/if}

		<!-- Always in the DOM so the live region exists before its text changes;
		     `:empty` hides it when the popup is closed. It is also the input's
		     `aria-describedby` target, so "showing 25 of 137" is part of the
		     field's description rather than a sighted-only footnote. -->
		<p class="vp-count" id={countId} aria-live="polite">{countText}</p>

		{#if expanded}
			<div class="vp-popup">
				<ul
					class="vp-list"
					id={listId}
					role="listbox"
					aria-label={label ?? ariaLabel ?? m('vendors.picker.listAria')}
				>
					{#each options as option, i (option.id)}
						<li
							class="vp-option"
							class:active={i === activeIndex}
							class:selected={option.id === value}
							id={optionId(i)}
							role="option"
							aria-selected={option.id === value}
							onmousedown={(e) => {
								// Keep focus on the input: a blur here would close the
								// popup before the click landed.
								e.preventDefault();
								commit(option);
							}}
						>
							<span class="vp-option-name">{option.name}</span>
							{#if option.code?.trim()}
								<span class="vp-option-code">{option.code}</span>
							{/if}
						</li>
					{/each}
				</ul>
				{#if showMore}
					<div class="vp-foot">
						<span class="vp-foot-note">
							{failure === 'more'
								? m('vendors.picker.loadMoreFailed')
								: m('vendors.picker.refineHint')}
						</span>
						<button
							type="button"
							class="vp-more"
							onmousedown={(e) => e.preventDefault()}
							onclick={() => void load(page + 1, false)}
							disabled={loading}
							data-testid={testid ? `${testid}-more` : undefined}
						>
							{loading ? m('vendors.picker.loading') : m('vendors.picker.loadMore')}
						</button>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	/* The control replicates the `.form-grid label` recipe its consumers use
	   (column flex, 4px gap, 0.82rem muted label) because Svelte scopes that
	   rule to the PARENT component — `label:where(.svelte-parent)` never
	   matches a child component's root. Owning it here also means the five
	   modals now render an identical field instead of five near-copies. */
	.vendor-picker {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.82rem;
		color: var(--text-muted);
		min-width: 0;
	}

	.vendor-picker.full-width {
		grid-column: 1 / -1;
	}

	.vendor-picker.compact {
		flex: 0 1 190px;
		min-width: 150px;
	}

	.vp-label {
		color: inherit;
	}

	.vp-control {
		position: relative;
	}

	.vp-input {
		width: 100%;
		padding: 7px 30px 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
	}

	.compact .vp-input {
		padding: 6px 26px 6px 8px;
		font-size: 0.82rem;
	}

	.vp-input:disabled {
		opacity: 0.7;
		cursor: not-allowed;
	}

	.vp-clear {
		position: absolute;
		top: 50%;
		right: 6px;
		transform: translateY(-50%);
		display: grid;
		place-items: center;
		width: 20px;
		height: 20px;
		padding: 0;
		border: none;
		border-radius: 4px;
		background: none;
		/* `--text-muted` is 5.38:1 on `--bg` — an icon-only control still has to
		   meet 1.4.11 against its own background, and dimming with `opacity`
		   would drag it under (app.css § .row-muted). */
		color: var(--text-muted);
		cursor: pointer;
	}

	.vp-clear:hover {
		color: var(--text);
	}

	.vp-count {
		margin: 0;
		font-size: 0.72rem;
		line-height: 1.4;
		color: var(--text-muted);
	}

	.vp-count:empty {
		display: none;
	}

	.vp-popup {
		position: absolute;
		top: 100%;
		left: 0;
		right: 0;
		z-index: 61;
		margin-top: 4px;
		border: 1px solid var(--border);
		border-radius: 8px;
		background: var(--surface);
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
		overflow: hidden;
	}

	.vp-list {
		list-style: none;
		margin: 0;
		padding: 4px;
		max-height: 240px;
		overflow-y: auto;
	}

	.vp-option {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 8px;
		padding: 7px 9px;
		border-radius: 5px;
		font-size: 0.85rem;
		color: var(--text);
		cursor: pointer;
	}

	.vp-option:hover,
	.vp-option.active {
		background: rgba(99, 140, 255, 0.12);
	}

	.vp-option.selected {
		color: var(--accent);
		font-weight: 600;
	}

	.vp-option-name {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.vp-option-code {
		flex-shrink: 0;
		font-size: 0.7rem;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.vp-foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 7px 9px;
		border-top: 1px solid var(--border);
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.vp-foot-note {
		min-width: 0;
	}

	.vp-more {
		flex-shrink: 0;
		padding: 4px 10px;
		border: 1px solid var(--border);
		border-radius: 5px;
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.72rem;
		cursor: pointer;
	}

	.vp-more:hover:not(:disabled) {
		border-color: var(--accent);
	}

	.vp-more:disabled {
		cursor: default;
	}
</style>
