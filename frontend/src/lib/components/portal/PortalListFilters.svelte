<script lang="ts">
	/**
	 * Shared filter bar for the supplier-portal list pages (invoices, payments):
	 * a debounced substring search + a row of single-select "phase" chips.
	 *
	 * The component owns the phase selection, the search text AND the debounce,
	 * and hands the caller a resolved `{ phase, search }` through `onchange` —
	 * phase clicks fire immediately, search fires 300ms after typing stops. The
	 * caller never sees a keystroke, so its own `load()` is never reached from a
	 * reactive `$effect` and needs no `untrack` gymnastics (frontend/CLAUDE.md
	 * § Sequencing list fetches / issue #168).
	 *
	 * Every string is passed in already-localized — the component is
	 * i18n-agnostic, like `SecretReveal` / `FieldWarning`.
	 */
	interface Chip {
		key: string;
		label: string;
	}

	export interface PortalListFilterState {
		phase: string | null;
		search: string;
		dateFrom: string;
		dateTo: string;
	}

	let {
		chips,
		allLabel,
		groupLabel,
		searchLabel,
		searchPlaceholder,
		dateFromLabel,
		dateToLabel,
		initial,
		onchange,
	}: {
		chips: Chip[];
		allLabel: string;
		groupLabel: string;
		searchLabel: string;
		searchPlaceholder: string;
		dateFromLabel: string;
		dateToLabel: string;
		/** Seed state, for a page that restores its filters from the URL. Read
		 *  once at construction — this component owns the state afterwards, so a
		 *  later change here does not (and must not) reset what the user typed. */
		initial?: Partial<PortalListFilterState>;
		onchange: (f: PortalListFilterState) => void;
	} = $props();

	/* eslint-disable svelte/state-referenced-locally -- seed read once by design */
	let phase = $state<string | null>(initial?.phase ?? null);
	let search = $state(initial?.search ?? '');
	let dateFrom = $state(initial?.dateFrom ?? '');
	let dateTo = $state(initial?.dateTo ?? '');
	/* eslint-enable svelte/state-referenced-locally */
	// The search term the newest `onchange` carried — so the debounce effect
	// schedules nothing when a phase click already emitted with this term.
	let lastEmitted = $state(initial?.search ?? '');

	const current = (): PortalListFilterState => ({ phase, search, dateFrom, dateTo });

	/** Caller-invokable (`bind:this`) — used by a "Clear filters" affordance in
	 *  the empty state. No-ops when nothing is set. */
	export function reset() {
		if (phase === null && search === '' && dateFrom === '' && dateTo === '') return;
		phase = null;
		search = '';
		dateFrom = '';
		dateTo = '';
		lastEmitted = '';
		onchange(current());
	}

	function pick(key: string | null) {
		if (phase === key) return;
		phase = key;
		lastEmitted = search;
		onchange(current());
	}

	// Date changes are discrete (not keystroke-y) — emit immediately, no debounce.
	function onDateChange() {
		lastEmitted = search;
		onchange(current());
	}

	let timer: ReturnType<typeof setTimeout>;
	let effectRan = false;
	$effect(() => {
		search;
		if (!effectRan) {
			effectRan = true;
			return;
		}
		if (search === lastEmitted) return;
		clearTimeout(timer);
		timer = setTimeout(() => {
			lastEmitted = search;
			onchange(current());
		}, 300);
		return () => clearTimeout(timer);
	});
</script>

<div class="filter-bar">
	<input
		type="search"
		class="search"
		bind:value={search}
		placeholder={searchPlaceholder}
		aria-label={searchLabel}
	/>
	<input
		type="date"
		class="date"
		bind:value={dateFrom}
		aria-label={dateFromLabel}
		max={dateTo || undefined}
		onchange={onDateChange}
	/>
	<input
		type="date"
		class="date"
		bind:value={dateTo}
		aria-label={dateToLabel}
		min={dateFrom || undefined}
		onchange={onDateChange}
	/>
	<div class="phases" role="group" aria-label={groupLabel}>
		<button
			type="button"
			class="phase-chip"
			class:active={phase === null}
			aria-pressed={phase === null}
			onclick={() => pick(null)}
		>
			{allLabel}
		</button>
		{#each chips as chip (chip.key)}
			<button
				type="button"
				class="phase-chip"
				class:active={phase === chip.key}
				aria-pressed={phase === chip.key}
				onclick={() => pick(chip.key)}
			>
				{chip.label}
			</button>
		{/each}
	</div>
</div>

<style>
	.filter-bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		margin-bottom: 16px;
	}
	.search {
		flex: 0 1 260px;
		padding: 7px 10px;
		font-size: 0.85rem;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--surface);
		color: var(--text);
	}
	.date {
		padding: 6px 8px;
		font-size: 0.82rem;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--surface);
		color: var(--text);
	}
	.phases {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.phase-chip {
		padding: 5px 10px;
		font-size: 0.78rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
	}
	.phase-chip:hover {
		color: var(--text);
	}
	.phase-chip.active {
		background: var(--accent-strong);
		border-color: var(--accent-strong);
		color: #fff;
	}
</style>
