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

	let {
		chips,
		allLabel,
		groupLabel,
		searchLabel,
		searchPlaceholder,
		onchange,
	}: {
		chips: Chip[];
		allLabel: string;
		groupLabel: string;
		searchLabel: string;
		searchPlaceholder: string;
		onchange: (f: { phase: string | null; search: string }) => void;
	} = $props();

	let phase = $state<string | null>(null);
	let search = $state('');
	// The search term the newest `onchange` carried — so the debounce effect
	// schedules nothing when a phase click already emitted with this term.
	let lastEmitted = $state('');

	/** Caller-invokable (`bind:this`) — used by a "Clear filters" affordance in
	 *  the empty state. No-ops when nothing is set. */
	export function reset() {
		if (phase === null && search === '') return;
		phase = null;
		search = '';
		lastEmitted = '';
		onchange({ phase: null, search: '' });
	}

	function pick(key: string | null) {
		if (phase === key) return;
		phase = key;
		lastEmitted = search;
		onchange({ phase, search });
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
			onchange({ phase, search });
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
