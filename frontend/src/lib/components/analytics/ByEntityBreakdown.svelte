<script lang="ts">
	import { api } from '$lib/api';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { entityStore } from '$lib/stores/entity.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import type { AnalyticsByEntity } from '$lib/types/analytics';

	// Consolidated reporting ACROSS entities — a side-by-side per-entity AP
	// rollup plus a consolidated total (the cross-check). Renders only when the
	// tenant has more than one entity, mirroring the entity switcher's
	// single-entity hide rule. `GET /api/analytics/by-entity` ignores the
	// X-Entity-ID selection by design, so the table is the same regardless of
	// which entity is currently selected in the switcher.

	interface Props {
		/** Trailing window in days (matches the rest of the CFO surface). */
		periodDays?: number;
	}

	let { periodDays = 365 }: Props = $props();

	let data = $state<AnalyticsByEntity | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);

	// Per-entity currency falls back to the org reporting currency for the
	// consolidated row (which mixes entities and has no single currency).
	function rowCurrency(c: string | null): string | null {
		return c ?? orgCurrency.currency;
	}

	$effect(() => {
		// Register deps so a period change re-fetches.
		void periodDays;
		entityStore.ensureLoaded();
		orgCurrency.ensureLoaded();
		// Only fetch once we know the tenant is multi-entity — single-entity
		// tenants don't render this section at all.
		if (!entityStore.multiEntity) {
			data = null;
			return;
		}
		load();
	});

	async function load() {
		loading = true;
		error = null;
		try {
			data = await api.get<AnalyticsByEntity>(
				`/api/analytics/by-entity?period_days=${periodDays}`
			);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load per-entity breakdown';
		} finally {
			loading = false;
		}
	}
</script>

{#if entityStore.multiEntity}
	<div class="chart-card" data-testid="by-entity-section">
		<h2>By entity</h2>
		{#if error}
			<p class="be-error" role="alert">{error}</p>
		{:else if loading && !data}
			<p class="empty">Loading…</p>
		{:else if data}
			<DataTable
				columns={[
					{ label: 'Entity' },
					{ label: 'Spend (period)', class: 'num' },
					{ label: 'Outstanding', class: 'num' },
					{ label: 'Invoices', class: 'num' },
					{ label: 'Open exceptions', class: 'num' },
					{ label: 'Open POs', class: 'num' }
				]}
			>
				{#snippet body()}
					{#each data?.entities ?? [] as e (e.entity_id)}
						<tr>
							<td>
								{e.entity_name}
								{#if e.is_default}<span class="be-tag">default</span>{/if}
							</td>
							<td class="num"><Money amount={e.total_spend} currency={rowCurrency(e.currency)} mono /></td>
							<td class="num"><Money amount={e.outstanding_amount} currency={rowCurrency(e.currency)} mono /></td>
							<td class="num">{e.invoice_count}</td>
							<td class="num" class:be-alert={e.open_exceptions > 0}>{e.open_exceptions}</td>
							<td class="num"><Money amount={e.open_po_amount} currency={rowCurrency(e.currency)} mono /></td>
						</tr>
					{/each}
					{#if data?.consolidated}
						{@const c = data.consolidated}
						<tr class="be-total">
							<td>Consolidated</td>
							<td class="num"><Money amount={c.total_spend} currency={orgCurrency.currency} mono /></td>
							<td class="num"><Money amount={c.outstanding_amount} currency={orgCurrency.currency} mono /></td>
							<td class="num">{c.invoice_count}</td>
							<td class="num" class:be-alert={c.open_exceptions > 0}>{c.open_exceptions}</td>
							<td class="num"><Money amount={c.open_po_amount} currency={orgCurrency.currency} mono /></td>
						</tr>
					{/if}
				{/snippet}
			</DataTable>
		{/if}
	</div>
{/if}

<style>
	.be-error {
		color: var(--danger, #f87171);
	}

	.be-tag {
		margin-left: 6px;
		font-size: 0.72rem;
		color: var(--muted, #9ca3af);
		border: 1px solid var(--border, #374151);
		border-radius: 4px;
		padding: 0 5px;
		vertical-align: middle;
	}

	.be-alert {
		color: var(--danger, #f87171);
		font-weight: 600;
	}

	/* The consolidated cross-check row — visually separated as the total. */
	.be-total td {
		border-top: 2px solid var(--border, #374151);
		font-weight: 700;
	}
</style>
