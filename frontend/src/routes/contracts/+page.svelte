<script lang="ts">
	import type { Contract, ContractStatus, ContractType } from '$lib/types/contract';
	import {
		CONTRACT_STATUSES,
		STATUS_LABELS,
		STATUS_TONES,
		CONTRACT_TYPE_LABELS
	} from '$lib/types/contract';
	import Badge from '$lib/components/ui/Badge.svelte';
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
	import { untrack } from 'svelte';
	import { formatDate } from '$lib/utils/time';

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
	// Three states, not two: a failed load must not read as "nothing matched".
	let emptyMessage = $derived(
		contractStore.loading
			? m('common.loading')
			: contractStore.errored
				? m('contracts.empty.errored')
				: m('contracts.empty')
	);

	let showCreate = $state(false);
	let editing = $state<Contract | null>(null);

	// `untrack` on the `search` read: buildParams() is called from `load()`,
	// which the filter `$effect` below calls directly — and Svelte tracks reads
	// transitively through called functions, so a plain read here would make
	// that effect depend on `search` and re-fire it on every keystroke,
	// un-debounced, racing the dedicated 300ms timer (issue #168). `untrack`
	// still reads the CURRENT value, so the request carries the live search
	// term; it just stops the read registering as the caller's dependency.
	function buildParams() {
		const params: { status?: string; search?: string } = {};
		if (statusFilter !== 'all') params.status = statusFilter as ContractStatus;
		const currentSearch = untrack(() => search);
		if (currentSearch.trim()) params.search = currentSearch.trim();
		return params;
	}

	// Reflect the live filter state into the URL. EVERY read in here is
	// untracked, `$page.url` included, because syncUrl() is a WRITER called
	// from the filter `$effect`s below — not a source of dependencies:
	//   - the URL read would self-trigger the effect that writes it via
	//     replaceState (Svelte effect_update_depth_exceeded);
	//   - a tracked `search` read would make every filter effect depend on
	//     `search`, so each keystroke re-fired it: an immediate, un-debounced
	//     load racing the dedicated 300ms debounce timer. That is issue #168,
	//     fixed on /invoices, /payments and /vendors but never carried to this
	//     page. Each effect declares the filters it actually depends on by
	//     reading them directly, so nothing here needs to be tracked.
	function syncUrl() {
		untrack(() => {
			const url = new URL($page.url);
			if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
			else url.searchParams.delete('status');
			if (search.trim()) url.searchParams.set('search', search.trim());
			else url.searchParams.delete('search');
			replaceState(`${url.pathname}${url.search}`, {});
		});
	}

	// `searchEffectRan` skips this effect's own mount-time run: a Svelte
	// `$effect` always fires once immediately regardless of whether its
	// tracked value actually changed, so without the guard this queued a
	// SECOND, redundant fetch ~300ms after the statusFilter effect below
	// already loaded the page once. `contractStore.fetch()` replaces the
	// list wholesale, so if a create/edit lands in that window, the
	// delayed duplicate can resolve afterward and silently clobber it
	// with a stale snapshot — same class of bug fixed in UsersPanel.svelte.
	let searchTimer: ReturnType<typeof setTimeout>;
	let searchEffectRan = false;
	$effect(() => {
		search;
		if (!searchEffectRan) {
			searchEffectRan = true;
			return;
		}
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			syncUrl();
			// Fire-and-forget: the store re-throws so an awaiting caller keeps its
			// own handling, but nothing awaits here — `contractStore.errored` is what
			// the table's empty state reads. Swallow so a failed load isn't an
			// unhandled rejection in the console.
			contractStore.fetch(buildParams()).catch(() => {}); // noqa: raw-fetch-in-component — store method, routes through api client
		}, 300);
		// Cancel a pending debounce on teardown: without it the timer fires
		// after the page is gone, running syncUrl()/a list fetch against a route
		// the user already left.
		return () => clearTimeout(searchTimer);
	});

	$effect(() => {
		statusFilter;
		syncUrl();
		// See the debounce effect above: swallow the rejection, the store's
		// `errored` flag is what the UI reads.
		contractStore.fetch(buildParams()).catch(() => {}); // noqa: raw-fetch-in-component — store method, routes through api client
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
		empty={emptyMessage}
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
					<td>
						<Badge tone={STATUS_TONES[contract.status]} variant={contract.status}>
							{STATUS_LABELS[contract.status]}
						</Badge>
					</td>
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

	/* The status pill is `<Badge>` now. This file and `ContractModal` used to
	   tint the same five statuses at two different alphas (.12 here, .15
	   there) — one owner, one answer. */

	.over-limit {
		color: var(--danger);
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
		background: var(--danger-strong);
		color: #fff;
		font-size: 0.7rem;
		font-weight: 700;
	}
</style>
