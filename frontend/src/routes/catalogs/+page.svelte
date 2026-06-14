<script lang="ts">
	import type { Catalog, GuidedBuyingSuggestion } from '$lib/types/catalog';
	import { CATALOG_TYPE_LABELS, GUIDED_BUYING_REASON_LABELS } from '$lib/types/catalog';
	import { auth } from '$lib/stores/auth.svelte';
	import {
		listCatalogs,
		deleteCatalog as apiDeleteCatalog,
		guidedBuying,
		listGlAccounts,
		listVendors,
		type GlAccountOption,
		type VendorOption
	} from '$lib/api/catalogs';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import CatalogModal from '$lib/components/modals/CatalogModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';

	const canCreate = $derived(auth.isManager); // admin | ap_manager

	// --- List state ---
	let catalogs = $state<Catalog[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let search = $state('');
	let typeFilter = $state<string>('all'); // all | internal | punchout | preferred

	// --- Modal + selection state ---
	let showCreate = $state(false);
	let editing = $state<Catalog | null>(null);
	let confirmDeleteId = $state<string | null>(null);

	// --- Lookups (shared with the modal) ---
	let vendors = $state<VendorOption[]>([]);
	let glAccounts = $state<GlAccountOption[]>([]);

	// --- Guided buying panel ---
	let showGuided = $state(false);
	let guidedCategory = $state('');
	let guidedQuery = $state('');
	let guided = $state<GuidedBuyingSuggestion | null>(null);
	let guidedLoading = $state(false);

	const TYPE_CHIPS = [
		{ key: 'all', label: 'All' },
		{ key: 'internal', label: 'Internal' },
		{ key: 'punchout', label: 'Punch-out' },
		{ key: 'preferred', label: 'Preferred' }
	];

	const COLUMNS = [
		{ label: 'Name' },
		{ label: 'Type' },
		{ label: 'Items', class: 'right' },
		{ label: 'Status' },
		{ label: '', class: 'actions-col' }
	];

	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	async function load() {
		loading = true;
		try {
			const params: {
				search?: string;
				catalog_type?: string;
				is_preferred?: boolean;
				page_size: number;
			} = { page_size: 100 };
			if (search.trim()) params.search = search.trim();
			if (typeFilter === 'preferred') params.is_preferred = true;
			else if (typeFilter !== 'all') params.catalog_type = typeFilter;
			const res = await listCatalogs(params);
			catalogs = res.items;
			total = res.total;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Could not load catalogs', 'error');
		} finally {
			loading = false;
		}
	}

	// Debounce text search: re-fetch 280ms after the last keystroke. Reading
	// `search` registers the dependency so edits re-run this effect.
	$effect(() => {
		const q = search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(load, q === '' ? 0 : 280);
		return () => clearTimeout(searchTimer);
	});

	// Initial lookups (vendors + GL accounts) — once.
	$effect(() => {
		(async () => {
			vendors = await listVendors();
			try {
				glAccounts = await listGlAccounts();
			} catch {
				glAccounts = [];
			}
		})();
	});

	function onSaved(_c: Catalog) {
		load();
	}

	async function handleDelete(id: string) {
		if (confirmDeleteId !== id) {
			confirmDeleteId = id;
			return;
		}
		try {
			await apiDeleteCatalog(id);
			catalogs = catalogs.filter((c) => c.id !== id);
			total = Math.max(0, total - 1);
			toast('Catalog deleted', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Delete failed', 'error');
		} finally {
			confirmDeleteId = null;
		}
	}

	async function runGuided() {
		guidedLoading = true;
		try {
			guided = await guidedBuying({
				category: guidedCategory.trim() || undefined,
				q: guidedQuery.trim() || undefined
			});
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Guided buying failed', 'error');
		} finally {
			guidedLoading = false;
		}
	}

	function toggleGuided() {
		showGuided = !showGuided;
		if (showGuided && guided === null) runGuided();
	}
</script>

<PageHeader title="Catalogs">
	{#snippet actions()}
		<button class="btn-secondary" onclick={toggleGuided}>
			{showGuided ? 'Hide guided buying' : 'Guided buying'}
		</button>
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>+ New Catalog</button>
		{/if}
	{/snippet}

	{#if showGuided}
		<section class="guided-panel">
			<div class="guided-controls">
				<input type="text" placeholder="Category (e.g. office)" bind:value={guidedCategory} />
				<input type="text" placeholder="Search items…" bind:value={guidedQuery} />
				<button class="btn-primary" onclick={runGuided} disabled={guidedLoading}>
					{guidedLoading ? 'Finding…' : 'Find preferred sources'}
				</button>
			</div>

			{#if guided}
				<div class="guided-grid">
					<div class="guided-col">
						<h4>Preferred vendors</h4>
						{#if guided.preferred_vendors.length === 0}
							<p class="muted">No preferred vendors match.</p>
						{:else}
							{#each guided.preferred_vendors as v (v.vendor_id + (v.catalog_id ?? ''))}
								<div class="guided-card">
									<strong>{v.vendor_name}</strong>
									<div class="reasons">
										{#each v.reasons as r}
											<span class="reason">{GUIDED_BUYING_REASON_LABELS[r] ?? r}</span>
										{/each}
									</div>
									{#if v.catalog_name}<span class="sub">Catalog: {v.catalog_name}</span>{/if}
									{#if v.contract_number}<span class="sub">Contract: {v.contract_number}</span>{/if}
								</div>
							{/each}
						{/if}
					</div>

					<div class="guided-col">
						<h4>In-contract vendors</h4>
						{#if guided.in_contract_vendors.length === 0}
							<p class="muted">No vendors with active contracts.</p>
						{:else}
							{#each guided.in_contract_vendors as v (v.vendor_id)}
								<div class="guided-card">
									<strong>{v.vendor_name}</strong>
									{#if v.contract_number}<span class="sub">Contract: {v.contract_number}</span>{/if}
								</div>
							{/each}
						{/if}
					</div>

					<div class="guided-col">
						<h4>Matching items</h4>
						{#if guided.items.length === 0}
							<p class="muted">No matching catalog items.</p>
						{:else}
							{#each guided.items as it (it.catalog_item_id)}
								<div class="guided-card">
									<div class="item-head">
										<strong>{it.name}</strong>
										{#if it.is_preferred}<span class="reason">Preferred</span>{/if}
									</div>
									<span class="sub">
										{it.catalog_name}{#if it.unit_price != null} ·
											<Money amount={it.unit_price} currency={it.currency} />{/if}
									</span>
								</div>
							{/each}
						{/if}
					</div>
				</div>
			{/if}
		</section>
	{/if}

	<div class="toolbar-row">
		<SearchBox bind:value={search} placeholder="Search catalogs..." ariaLabel="Search catalogs" />
	</div>

	<FilterChips chips={TYPE_CHIPS} bind:active={typeFilter} onchange={() => load()} />

	<DataTable columns={COLUMNS} isEmpty={!loading && catalogs.length === 0} empty="No catalogs.">
		{#snippet body()}
			{#each catalogs as c (c.id)}
				<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) editing = c; }}>
					<td>
						<RowLink onclick={() => (editing = c)} ariaLabel={`Open catalog ${c.name}`}>
							{c.name}
							{#if c.is_preferred}<span class="pref-dot" title="Preferred">★</span>{/if}
						</RowLink>
					</td>
					<td>{CATALOG_TYPE_LABELS[c.catalog_type as keyof typeof CATALOG_TYPE_LABELS] ?? c.catalog_type}</td>
					<td class="right">{c.item_count}</td>
					<td>{c.is_active ? 'Active' : 'Inactive'}</td>
					<td class="actions">
						{#if canCreate}
							<RowAction
								variant="danger"
								armed={confirmDeleteId === c.id}
								onclick={() => handleDelete(c.id)}
							>
								{confirmDeleteId === c.id ? 'Confirm' : 'Delete'}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {total} catalogs</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<CatalogModal
		catalog={null}
		{vendors}
		{glAccounts}
		onclose={() => (showCreate = false)}
		onsaved={onSaved}
	/>
{/if}

{#if editing}
	<CatalogModal
		catalog={editing}
		{vendors}
		{glAccounts}
		onclose={() => (editing = null)}
		onsaved={onSaved}
	/>
{/if}

<style>
	.toolbar-row {
		display: flex;
		gap: 10px;
		align-items: center;
	}

	.pref-dot {
		color: #d4940a;
		margin-left: 4px;
	}

	.guided-panel {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 14px 16px;
		background: var(--surface);
	}
	.guided-controls {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		align-items: center;
		margin-bottom: 12px;
	}
	.guided-controls input {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.86rem;
		min-width: 180px;
	}
	.guided-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 14px;
	}
	.guided-col h4 {
		margin: 0 0 8px;
		font-size: 0.85rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
	}
	.guided-card {
		display: flex;
		flex-direction: column;
		gap: 3px;
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 6px;
		margin-bottom: 8px;
		font-size: 0.85rem;
	}
	.item-head {
		display: flex;
		align-items: center;
		gap: 8px;
		justify-content: space-between;
	}
	.reasons {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.reason {
		display: inline-block;
		padding: 1px 7px;
		border-radius: 10px;
		font-size: 0.7rem;
		font-weight: 600;
		background: rgba(31, 168, 106, 0.15);
		color: #1fa86a;
	}
	.sub {
		color: var(--text-muted);
		font-size: 0.78rem;
	}
	.muted {
		color: var(--text-muted);
		font-size: 0.82rem;
	}
</style>
