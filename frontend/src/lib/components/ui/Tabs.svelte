<script lang="ts">
	type Tab = {
		key: string;
		label: string;
		/** Optional count badge rendered in `.tab-count`. Omit for label-only tabs. */
		count?: number | string;
	};

	type Props = {
		tabs: Tab[];
		/** The active tab key. Two-way bindable. */
		active: string;
		/** Fired in addition to updating `active`. */
		onchange?: (key: string) => void;
		/** Accessible name for the tablist. */
		ariaLabel?: string;
		/** Prefix for the generated tab `id` + `aria-controls`. The caller gives
		    the visible panel `id="<idPrefix>-panel-<activeKey>"` + `role="tabpanel"`
		    + `aria-labelledby="<idPrefix>-tab-<activeKey>"` to complete the pairing. */
		idPrefix?: string;
	};

	let { tabs, active = $bindable(), onchange, ariaLabel, idPrefix = 'tabs' }: Props = $props();

	function select(key: string) {
		if (key === active) return;
		active = key;
		onchange?.(key);
	}
</script>

<div class="tab-row" role="tablist" aria-label={ariaLabel}>
	{#each tabs as tab (tab.key)}
		<button
			class="tab"
			class:active={active === tab.key}
			type="button"
			role="tab"
			id={`${idPrefix}-tab-${tab.key}`}
			aria-controls={`${idPrefix}-panel-${tab.key}`}
			aria-selected={active === tab.key}
			onclick={() => select(tab.key)}
		>
			{tab.label}{#if tab.count !== undefined}{' '}<span class="tab-count">{tab.count}</span>{/if}
		</button>
	{/each}
</div>

<style>
	/* Underline tab bar. Lifted from the per-route copies in
	   /expenses, /payments, /audit so new pages share one source. */
	.tab-row {
		display: flex;
		gap: 4px;
		border-bottom: 1px solid var(--border);
	}
	.tab {
		padding: 8px 16px;
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.active {
		color: var(--accent);
		border-bottom-color: var(--accent);
	}
	.tab-count {
		font-size: 0.78rem;
		color: var(--text-muted);
	}
</style>
