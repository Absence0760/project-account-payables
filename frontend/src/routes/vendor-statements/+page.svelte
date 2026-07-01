<script lang="ts">
	import type { Reconciliation, ReconStatus } from '$lib/types/vendorStatementRecon';
	import { RECON_STATUSES, RECON_STATUS_LABELS } from '$lib/types/vendorStatementRecon';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { api } from '$lib/api';
	import {
		listReconciliations,
		getReconciliation,
		getCloseReadiness,
		deleteReconciliation
	} from '$lib/api/vendorStatementRecon';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import VendorStatementReconModal from '$lib/components/modals/VendorStatementReconModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { formatDate } from '$lib/utils/time';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';

	const canCreate = $derived(auth.isManager);

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...RECON_STATUSES.map((s) => ({ key: s, label: RECON_STATUS_LABELS[s] }))
	]);

	const COLUMNS = $derived([
		{ label: m('vendorStatements.col.vendor') },
		{ label: m('vendorStatements.col.statementDate') },
		{ label: m('vendorStatements.col.reference') },
		{ label: m('vendorStatements.col.discrepancies'), class: 'right' },
		{ label: m('vendorStatements.col.statementTotal'), class: 'right' },
		{ label: m('vendorStatements.col.ledgerTotal'), class: 'right' },
		{ label: m('vendorStatements.col.status') },
		{ class: 'actions-col' }
	]);

	const PAGE_SIZE = 20;

	interface VendorOption {
		id: string;
		name: string;
	}

	// URL-backed filter state (mirrors the recurring/contracts page convention).
	let search = $state($page.url.searchParams.get('search') ?? '');
	let statusFilter = $state<string>($page.url.searchParams.get('status') ?? 'all');
	let vendors = $state<VendorOption[]>([]);

	let recons = $state<Reconciliation[]>([]);
	let total = $state(0);
	let pageNum = $state(1);
	let loading = $state(true);
	let loadingMore = $state(false);

	let closeReady = $state<boolean | null>(null);
	let blockingCount = $state(0);

	let hasMore = $derived(recons.length < total);

	// Modal state: showCreate = create; a Reconciliation = detail/diff.
	let showCreate = $state(false);
	let detail = $state<Reconciliation | null>(null);

	// Client-side search filter (the list endpoint filters by vendor_id/status).
	const visibleRecons = $derived.by(() => {
		const q = search.trim().toLowerCase();
		if (!q) return recons;
		return recons.filter(
			(r) =>
				(r.vendor_name ?? '').toLowerCase().includes(q) ||
				(r.statement_reference ?? '').toLowerCase().includes(q)
		);
	});

	function buildParams() {
		const params: { status?: string } = {};
		if (statusFilter !== 'all') params.status = statusFilter;
		return params;
	}

	// Reflect filter state into the URL so it survives reload / back-forward.
	// Read the current URL untracked: syncUrl() runs synchronously inside the
	// status-filter $effect and writes the URL via replaceState — a tracked
	// $page.url read here would make that effect depend on the state it mutates
	// (Svelte effect_update_depth_exceeded loop).
	function syncUrl() {
		const url = new URL(untrack(() => $page.url));
		if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
		else url.searchParams.delete('status');
		if (search.trim()) url.searchParams.set('search', search.trim());
		else url.searchParams.delete('search');
		replaceState(`${url.pathname}${url.search}`, {});
	}

	async function load(opts: { append?: boolean } = {}) {
		const nextPage = opts.append ? pageNum + 1 : 1;
		if (opts.append) loadingMore = true;
		else loading = true;
		try {
			const data = await listReconciliations({
				...buildParams(),
				page: nextPage,
				page_size: PAGE_SIZE
			});
			recons = opts.append ? [...recons, ...data.items] : data.items;
			total = data.total;
			pageNum = nextPage;
		} catch (e) {
			if (!opts.append) recons = [];
			toast(e instanceof Error ? e.message : m('vendorStatements.toast.loadFailed'), 'error');
		} finally {
			loading = false;
			loadingMore = false;
		}
	}

	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(syncUrl, 300);
	});

	$effect(() => {
		statusFilter;
		syncUrl();
		load();
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
		loadVendors();
		loadCloseReadiness();
	});

	async function loadVendors() {
		try {
			const data = await api.get<{ items: VendorOption[] }>('/api/vendors');
			vendors = data.items;
		} catch {
			/* non-critical for the list view */
		}
	}

	async function loadCloseReadiness() {
		try {
			const data = await getCloseReadiness();
			closeReady = data.is_close_ready;
			blockingCount = data.blocking_vendors.length;
		} catch {
			closeReady = null;
			blockingCount = 0;
		}
	}

	// Deep-link: `/vendor-statements?id=<uuid>` opens that run's detail modal.
	let deepLinkLoaded = $state<string | null>(null);
	$effect(() => {
		const id = $page.url.searchParams.get('id');
		if (!id || deepLinkLoaded === id) return;
		deepLinkLoaded = id;
		getReconciliation(id)
			.then((r) => (detail = r))
			.catch(() => toast(m('vendorStatements.toast.notFound'), 'error'));
	});

	async function openDetail(r: Reconciliation) {
		try {
			detail = await getReconciliation(r.id);
		} catch {
			detail = r;
		}
	}

	function closeModal() {
		detail = null;
		showCreate = false;
		const url = new URL($page.url);
		if (url.searchParams.has('id')) {
			url.searchParams.delete('id');
			replaceState(`${url.pathname}${url.search}`, {});
			deepLinkLoaded = null;
		}
	}

	function upsert(r: Reconciliation) {
		const idx = recons.findIndex((x) => x.id === r.id);
		if (idx === -1) {
			recons = [r, ...recons];
			total += 1;
		} else {
			recons = recons.map((x) => (x.id === r.id ? r : x));
		}
		loadCloseReadiness();
	}

	function onSaved(r: Reconciliation) {
		upsert(r);
		if (detail && detail.id === r.id) detail = r;
	}

	// --- Delete (armed two-click confirm) ---
	let busyId = $state<string | null>(null);
	let confirmDeleteId = $state<string | null>(null);

	async function deleteRecon(r: Reconciliation) {
		if (confirmDeleteId !== r.id) {
			confirmDeleteId = r.id;
			return;
		}
		confirmDeleteId = null;
		busyId = r.id;
		try {
			await deleteReconciliation(r.id);
			recons = recons.filter((x) => x.id !== r.id);
			total = Math.max(0, total - 1);
			toast(m('vendorStatements.toast.deleted'), 'success');
			loadCloseReadiness();
		} catch (e) {
			toast(e instanceof Error ? e.message : m('vendorStatements.toast.deleteFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	function discrepancyCount(r: Reconciliation): number {
		return (
			r.summary.amount_mismatch_count +
			r.summary.missing_our_side_count +
			r.summary.missing_their_side_count
		);
	}


	// --- KPI math (derived from the loaded rows) ---
	const openCount = $derived(recons.filter((r) => r.status === 'open').length);
	const totalDiscrepancies = $derived(recons.reduce((sum, r) => sum + discrepancyCount(r), 0));
</script>

<svelte:window
	onclick={(e) => {
		if (confirmDeleteId && !(e.target as HTMLElement)?.closest?.('.row-action')) {
			confirmDeleteId = null;
		}
	}}
/>

<PageHeader title={m('vendorStatements.title')}>
	{#snippet actions()}
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>{m('vendorStatements.new')}</button>
		{/if}
	{/snippet}

	<!-- KPI row -->
	<div class="kpi-row">
		<KpiCard value={openCount} label={m('vendorStatements.kpi.openRecons')} />
		<KpiCard value={totalDiscrepancies} label={m('vendorStatements.kpi.openDiscrepancies')} highlight={totalDiscrepancies > 0 ? 'red' : null} />
		<KpiCard
			value={closeReady === null ? '—' : closeReady ? m('vendorStatements.kpi.ready') : m('vendorStatements.kpi.blocking', { n: blockingCount })}
			label={m('vendorStatements.kpi.closeReadiness')}
			highlight={closeReady === false ? 'red' : closeReady === true ? 'green' : null}
		/>
	</div>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('vendorStatements.searchPlaceholder')} ariaLabel={m('vendorStatements.searchAria')} />
		<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={!loading && visibleRecons.length === 0}
		empty={loading ? m('common.loading') : m('vendorStatements.empty')}
		colspan={8}
	>
		{#snippet body()}
			{#each visibleRecons as recon (recon.id)}
				<tr
					class="clickable"
					onclick={(e) => {
						if (isRowOpenClick(e)) openDetail(recon);
					}}
				>
					<td>
						<RowLink
							onclick={() => openDetail(recon)}
							ariaLabel={m('vendorStatements.row.openAria', { vendor: recon.vendor_name ?? m('vendorStatements.vendorFallback'), date: recon.statement_date })}
						>
							{recon.vendor_name ?? '—'}
						</RowLink>
					</td>
					<td class="muted">{formatDate(recon.statement_date)}</td>
					<td class="muted">{recon.statement_reference ?? '—'}</td>
					<td class="right mono">
						{#if discrepancyCount(recon) > 0}
							<span class="disc-badge">{discrepancyCount(recon)}</span>
						{:else}
							<span class="disc-clean">0</span>
						{/if}
					</td>
					<td class="right mono"><Money amount={recon.summary.statement_total} currency={recon.currency} /></td>
					<td class="right mono"><Money amount={recon.summary.ledger_total} currency={recon.currency} /></td>
					<td><span class="badge {recon.status}">{RECON_STATUS_LABELS[recon.status]}</span></td>
					<td class="actions">
						{#if canCreate}
							<RowAction
								variant="danger"
								armed={confirmDeleteId === recon.id}
								disabled={busyId === recon.id}
								onclick={() => deleteRecon(recon)}
								ariaLabel={m('vendorStatements.row.deleteAria', { vendor: recon.vendor_name ?? m('vendorStatements.vendorFallback'), date: recon.statement_date })}
							>
								{confirmDeleteId === recon.id ? m('vendorStatements.row.confirm') : m('vendorStatements.row.delete')}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={() => load({ append: true })} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('vendorStatements.loadMore', { shown: recons.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('vendorStatements.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<VendorStatementReconModal recon={null} {vendors} onclose={closeModal} onsaved={onSaved} />
{/if}

{#if detail}
	<VendorStatementReconModal recon={detail} {vendors} onclose={closeModal} onsaved={onSaved} />
{/if}

<style>
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.mono {
		font-variant-numeric: tabular-nums;
		font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
	}
	.muted {
		color: var(--text-muted);
	}

	.disc-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 700;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
	}
	.disc-clean {
		color: var(--text-muted);
	}

	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
	}
	.badge.open {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
	.badge.resolved {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
</style>
