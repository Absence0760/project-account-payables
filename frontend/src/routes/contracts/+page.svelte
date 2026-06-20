<script lang="ts">
	import type { Contract, ContractStatus, ContractType } from '$lib/types/contract';
	import { CONTRACT_STATUSES, STATUS_LABELS, CONTRACT_TYPE_LABELS } from '$lib/types/contract';
	import { contractStore } from '$lib/stores/contracts.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import { getContract } from '$lib/api/contracts';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import StatusBadge from '$lib/components/ui/StatusBadge.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import ContractModal from '$lib/components/modals/ContractModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { m } from '$lib/i18n/store.svelte';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';

	const canCreate = $derived(auth.isManager);

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...CONTRACT_STATUSES.map((s) => ({ key: s, label: STATUS_LABELS[s] }))
	]);

	const COLUMNS = $derived([
		{ label: m('contracts.col.contractNumber') },
		{ label: m('contracts.col.vendor') },
		{ label: m('contracts.col.type') },
		{ label: m('contracts.col.status') },
		{ label: m('contracts.col.endDate') },
		{ label: m('contracts.col.value'), class: 'right' },
		{ label: m('contracts.col.spend'), class: 'right' }
	]);

	interface VendorOption {
		id: string;
		name: string;
	}

	// URL-backed filter state (mirrors the invoices page convention).
	let search = $state($page.url.searchParams.get('search') ?? '');
	let statusFilter = $state<string>($page.url.searchParams.get('status') ?? 'all');
	let vendors = $state<VendorOption[]>([]);

	// Modal state: null = create; a Contract = detail/edit.
	let showCreate = $state(false);
	let editing = $state<Contract | null>(null);

	function buildParams() {
		const params: { status?: string; search?: string } = {};
		if (statusFilter !== 'all') params.status = statusFilter as ContractStatus;
		if (search.trim()) params.search = search.trim();
		return params;
	}

	// Reflect filter state into the URL so it survives reload / back-forward.
	function syncUrl() {
		const url = new URL($page.url);
		if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
		else url.searchParams.delete('status');
		if (search.trim()) url.searchParams.set('search', search.trim());
		else url.searchParams.delete('search');
		replaceState(`${url.pathname}${url.search}`, {});
	}

	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			syncUrl();
			contractStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method, routes through api client
		}, 300);
	});

	$effect(() => {
		statusFilter;
		syncUrl();
		contractStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method, routes through api client
	});

	$effect(() => {
		loadVendors();
	});

	async function loadVendors() {
		try {
			const data = await api.get<{ items: VendorOption[] }>('/api/vendors');
			vendors = data.items;
		} catch {
			/* non-critical for the list view */
		}
	}

	// Deep-link: `/contracts?id=<uuid>` opens that contract's detail modal.
	let deepLinkLoaded = $state<string | null>(null);
	$effect(() => {
		const id = $page.url.searchParams.get('id');
		if (!id || deepLinkLoaded === id) return;
		deepLinkLoaded = id;
		getContract(id)
			.then((c) => (editing = c))
			.catch(() => toast(m('contracts.notFound'), 'error'));
	});

	async function openDetail(c: Contract) {
		// Fetch the full record so the spend summary + line items are present
		// (the list payload omits the heavier `spend` block on some backends).
		try {
			editing = await getContract(c.id);
		} catch {
			editing = c;
		}
	}

	function closeModal() {
		editing = null;
		showCreate = false;
		const url = new URL($page.url);
		if (url.searchParams.has('id')) {
			url.searchParams.delete('id');
			replaceState(`${url.pathname}${url.search}`, {});
			deepLinkLoaded = null;
		}
	}

	function onSaved(c: Contract) {
		contractStore.upsert(c);
		// Keep the open detail modal in sync after a lifecycle action.
		if (editing && editing.id === c.id) editing = c;
	}

	function formatDate(s: string | null): string {
		if (!s) return '—';
		return new Date(s).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
	}

	function typeLabel(t: ContractType): string {
		return CONTRACT_TYPE_LABELS[t] ?? t;
	}
</script>

<PageHeader title={m('contracts.title')}>
	{#snippet actions()}
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>{m('contracts.action.new')}</button>
		{/if}
	{/snippet}

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('contracts.search.placeholder')} ariaLabel={m('contracts.search.aria')} />
		<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={contractStore.all.length === 0}
		empty={contractStore.loading ? m('common.loading') : m('contracts.empty')}
	>
		{#snippet body()}
			{#each contractStore.all as contract (contract.id)}
				<tr
					class="clickable"
					onclick={(e) => {
						if (isRowOpenClick(e)) openDetail(contract);
					}}
				>
					<td class="mono">
						<RowLink
							onclick={() => openDetail(contract)}
							ariaLabel={m('contracts.row.open', { number: contract.contract_number })}
						>
							{contract.contract_number}
						</RowLink>
					</td>
					<td>{contract.vendor_name ?? '—'}</td>
					<td>{typeLabel(contract.contract_type)}</td>
					<td><span class="badge {contract.status}">{STATUS_LABELS[contract.status]}</span></td>
					<td class="muted">{formatDate(contract.end_date)}</td>
					<td class="right mono"><Money amount={contract.total_value} currency={contract.currency} /></td>
					<td class="right mono">
						{#if contract.spend}
							<span class:over-limit={contract.spend.over_limit}>
								<Money amount={contract.spend.invoiced_total} currency={contract.currency} />
							</span>
							{#if contract.spend.over_limit}
								<span class="over-tag" title={m('contracts.overSpendLimit')}>!</span>
							{/if}
						{:else}
							—
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if contractStore.hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={() => contractStore.loadMore()} disabled={contractStore.loading}>
				{contractStore.loading
					? m('common.loading')
					: m('contracts.loadMore', { shown: contractStore.all.length, total: contractStore.total })}
			</button>
		</div>
	{:else if contractStore.total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('contracts.showingAll', { total: contractStore.total })}</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<ContractModal contract={null} {vendors} onclose={closeModal} onsaved={onSaved} />
{/if}

{#if editing}
	<ContractModal contract={editing} {vendors} onclose={closeModal} onsaved={onSaved} />
{/if}

<style>
	/* Page-specific bits; shared design-system CSS lives in app.css. */
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
	}
	.badge.draft { background: rgba(99, 140, 255, 0.12); color: #638cff; }
	.badge.active { background: rgba(31, 168, 106, 0.12); color: #1fa86a; }
	.badge.expired { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	.badge.terminated { background: rgba(224, 64, 64, 0.12); color: #e04040; }
	.badge.cancelled { background: var(--bg); color: var(--text-muted); }

	.over-limit {
		color: #e04040;
		font-weight: 600;
	}

	.over-tag {
		display: inline-block;
		margin-left: 4px;
		width: 16px;
		height: 16px;
		line-height: 16px;
		text-align: center;
		border-radius: 50%;
		background: #e04040;
		color: #fff;
		font-size: 0.7rem;
		font-weight: 700;
	}
</style>
