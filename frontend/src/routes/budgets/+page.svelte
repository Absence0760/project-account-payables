<script lang="ts">
	import type { Budget } from '$lib/types/budget';
	import {
		BUDGET_DIMENSIONS,
		BUDGET_DIMENSION_LABELS,
		type BudgetDimension
	} from '$lib/types/budget';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { listBudgets, deleteBudget as apiDeleteBudget } from '$lib/api/budgets';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import BudgetModal from '$lib/components/modals/BudgetModal.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';

	// Mutations are admin / cfo only (financial config).
	const canManage = $derived(auth.hasAnyRole('admin', 'cfo'));

	// --- Filter state (URL-backed) ---
	let search = $state($page.url.searchParams.get('search') ?? '');
	let dimensionFilter = $state<string>($page.url.searchParams.get('dimension') ?? 'all');

	// --- Data ---
	let budgets = $state<Budget[]>([]);
	let total = $state(0);
	let loading = $state(false);

	// --- Modal state ---
	let showCreate = $state(false);
	let editing = $state<Budget | null>(null);
	let confirmDeleteId = $state<string | null>(null);

	const DIMENSION_CHIPS = [
		{ key: 'all', label: 'All' },
		...BUDGET_DIMENSIONS.map((d) => ({ key: d, label: BUDGET_DIMENSION_LABELS[d] }))
	];

	const COLUMNS = [
		{ label: 'Name' },
		{ label: 'Dimension' },
		{ label: 'Value' },
		{ label: 'Period' },
		{ label: 'Allocation', class: 'right' },
		{ label: '', class: 'actions-col' }
	];

	// Client-side text search over the loaded page (the list endpoint already
	// filters by name/value server-side via ?search=; this keeps it responsive).
	const visibleBudgets = $derived.by(() => {
		const q = search.trim().toLowerCase();
		if (!q) return budgets;
		return budgets.filter(
			(b) =>
				b.name.toLowerCase().includes(q) || b.dimension_value.toLowerCase().includes(q)
		);
	});

	// KPI: total allocation across the loaded budgets (org default currency, since
	// per-budget currencies may differ).
	const totalAllocated = $derived(budgets.reduce((sum, b) => sum + (Number.isFinite(b.amount) ? b.amount : 0), 0));

	function buildParams() {
		const params: { dimension?: string; search?: string } = {};
		if (dimensionFilter !== 'all') params.dimension = dimensionFilter;
		if (search.trim()) params.search = search.trim();
		return params;
	}

	function syncUrl() {
		const url = new URL($page.url);
		if (dimensionFilter !== 'all') url.searchParams.set('dimension', dimensionFilter);
		else url.searchParams.delete('dimension');
		if (search.trim()) url.searchParams.set('search', search.trim());
		else url.searchParams.delete('search');
		replaceState(`${url.pathname}${url.search}`, {});
	}

	async function load() {
		loading = true;
		try {
			const res = await listBudgets({ ...buildParams(), page_size: 50 });
			budgets = res.items;
			total = res.total;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load budgets', 'error');
		} finally {
			loading = false;
		}
	}

	// Dimension filter → server refetch. Debounced search re-syncs the URL +
	// refetch (server-side ILIKE keeps cross-page matches in scope).
	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			syncUrl();
			load();
		}, 300);
	});

	$effect(() => {
		dimensionFilter;
		syncUrl();
		load();
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
	});

	function onSaved(b: Budget) {
		const idx = budgets.findIndex((x) => x.id === b.id);
		if (idx >= 0) budgets = budgets.map((x) => (x.id === b.id ? b : x));
		else {
			budgets = [b, ...budgets];
			total += 1;
		}
		if (editing && editing.id === b.id) editing = b;
	}

	async function deleteBudget(id: string) {
		try {
			await apiDeleteBudget(id);
			budgets = budgets.filter((b) => b.id !== id);
			total = Math.max(0, total - 1);
			toast('Budget deleted', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Delete failed', 'error');
		} finally {
			confirmDeleteId = null;
		}
	}

	function onWindowClick(e: MouseEvent) {
		const target = e.target as Element | null;
		if (!target?.closest('.row-action') && confirmDeleteId) confirmDeleteId = null;
	}

	function dimLabel(d: string): string {
		return BUDGET_DIMENSION_LABELS[d as BudgetDimension] ?? d;
	}
</script>

<svelte:window onclick={onWindowClick} />

<PageHeader title="Budgets">
	{#snippet actions()}
		{#if canManage}
			<button class="btn-primary" onclick={() => (showCreate = true)}>+ New Budget</button>
		{/if}
	{/snippet}

	<div class="kpi-row">
		<KpiCard
			value={formatMoney(totalAllocated, { currency: orgCurrency.currency })}
			label="Total allocated"
		/>
		<KpiCard value={total} label="Budgets" />
		<KpiCard value={DIMENSION_CHIPS.length - 1} label="Dimensions" />
	</div>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder="Search budgets..." ariaLabel="Search budgets" />
		<FilterChips chips={DIMENSION_CHIPS} bind:active={dimensionFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={visibleBudgets.length === 0}
		empty={loading ? 'Loading…' : 'No budgets match your filters.'}
	>
		{#snippet body()}
			{#each visibleBudgets as b (b.id)}
				<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) editing = b; }}>
					<td>
						<RowLink onclick={() => (editing = b)} ariaLabel={`Open budget ${b.name}`}>
							{b.name}
						</RowLink>
					</td>
					<td>{dimLabel(b.dimension)}</td>
					<td>{b.dimension_value}</td>
					<td class="muted">{b.period ?? '—'}</td>
					<td class="right mono"><Money amount={b.amount} currency={b.currency} /></td>
					<td class="actions">
						{#if canManage}
							<RowAction
								variant="danger"
								armed={confirmDeleteId === b.id}
								onclick={(e) => {
									e.stopPropagation();
									if (confirmDeleteId === b.id) deleteBudget(b.id);
									else confirmDeleteId = b.id;
								}}
							>
								{confirmDeleteId === b.id ? 'Confirm' : 'Delete'}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {total} budget{total === 1 ? '' : 's'}</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<BudgetModal budget={null} onclose={() => (showCreate = false)} onsaved={onSaved} />
{/if}

{#if editing}
	<BudgetModal budget={editing} onclose={() => (editing = null)} onsaved={onSaved} />
{/if}

<style>
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
</style>
