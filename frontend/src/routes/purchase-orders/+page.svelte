<script lang="ts">
	import { api } from '$lib/api';
	import { appendUnique } from '$lib/utils/pagination';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';

	const COLUMNS = $derived([
		{ label: m('purchaseOrders.col.poNumber') },
		{ label: m('purchaseOrders.col.vendor') },
		{ label: m('purchaseOrders.col.total'), class: 'right' },
		{ label: m('purchaseOrders.col.status') },
		{ label: m('purchaseOrders.col.lines') },
		{ label: m('purchaseOrders.col.created') }
	]);

	interface POLineItem {
		id: string;
		description: string | null;
		quantity: number | null;
		unit_price: number | null;
		total: number | null;
	}

	interface POListItem {
		id: string;
		po_number: string;
		vendor_id: string | null;
		vendor_name: string | null;
		total: number;
		status: string;
		line_items: POLineItem[];
		created_at: string;
	}

	interface POLinkedInvoice {
		id: string;
		invoice_number: string;
		vendor_name: string | null;
		amount: number;
		status: string;
	}

	interface PODetail extends POListItem {
		linked_invoices: POLinkedInvoice[];
	}

	const PAGE_SIZE = 20;

	let pos = $state<POListItem[]>([]);
	let total = $state(0);
	let page = $state(1);
	let loading = $state(true);
	let loadingMore = $state(false);
	let search = $state('');
	let statusFilter = $state<'all' | 'open' | 'closed' | 'cancelled'>('all');
	let syncing = $state(false);

	let detailId = $state<string | null>(null);
	let detail = $state<PODetail | null>(null);
	let detailLoading = $state(false);

	let searchTimer: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		orgCurrency.ensureLoaded();
		void loadPos();
	});

	$effect(() => {
		const q = search;
		const s = statusFilter;
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			void loadPos({ search: q, status: s });
		}, 250);
	});

	$effect(() => {
		if (!detailId) {
			detail = null;
			return;
		}
		void loadDetail(detailId);
	});

	async function loadPos(
		opts: { search?: string; status?: string; append?: boolean; nextPage?: number } = {}
	) {
		loading = !opts.append;
		try {
			const nextPage = opts.nextPage ?? 1;
			const params = new URLSearchParams({
				page: String(nextPage),
				page_size: String(PAGE_SIZE)
			});
			if (opts.search) params.set('search', opts.search);
			if (opts.status && opts.status !== 'all') params.set('status', opts.status);
			const data = await api.get<{ items: POListItem[]; total: number }>(
				`/api/purchase-orders?${params}`
			);
			pos = opts.append ? appendUnique(pos, data.items) : data.items;
			total = data.total;
			page = nextPage;
		} catch (err) {
			toast(err instanceof Error ? err.message : m('purchaseOrders.toast.loadListFailed'), 'error');
		} finally {
			loading = false;
		}
	}

	async function loadMore() {
		loadingMore = true;
		try {
			await loadPos({ search, status: statusFilter, append: true, nextPage: page + 1 });
		} finally {
			loadingMore = false;
		}
	}

	async function loadDetail(id: string) {
		detailLoading = true;
		try {
			detail = await api.get<PODetail>(`/api/purchase-orders/${id}`);
		} catch (err) {
			toast(err instanceof Error ? err.message : m('purchaseOrders.toast.loadFailed'), 'error');
			detailId = null;
		} finally {
			detailLoading = false;
		}
	}

	async function syncFromErp() {
		syncing = true;
		try {
			const result = await api.post<{ message: string }>('/api/purchase-orders/sync-erp', {});
			toast(result.message, 'success');
			await loadPos({ search, status: statusFilter });
		} catch (err) {
			toast(err instanceof Error ? err.message : m('purchaseOrders.toast.syncFailed'), 'error');
		} finally {
			syncing = false;
		}
	}

	function formatCurrency(n: number | null): string {
		return formatMoney(n, { currency: orgCurrency.currency });
	}

	function formatDate(s: string | null): string {
		if (!s) return '—';
		return new Date(s).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
	}

	let hasMore = $derived(pos.length < total);
</script>

<PageHeader title={m('purchaseOrders.title')}>
	{#snippet actions()}
		<button class="btn-outline" disabled={syncing} onclick={syncFromErp}>
			{syncing ? m('purchaseOrders.action.syncing') : m('purchaseOrders.action.syncErp')}
		</button>
	{/snippet}

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('purchaseOrders.search.placeholder')} ariaLabel={m('purchaseOrders.search.aria')} />
		<FilterChips
			chips={[
				{ key: 'all', label: m('common.all'), count: total },
				{ key: 'open', label: m('purchaseOrders.filter.open') },
				{ key: 'closed', label: m('purchaseOrders.filter.closed') },
				{ key: 'cancelled', label: m('purchaseOrders.filter.cancelled') }
			]}
			bind:active={statusFilter}
		/>
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={pos.length === 0}
		empty={loading ? m('common.loading') : m('purchaseOrders.empty')}
	>
		{#snippet body()}
			{#each pos as po (po.id)}
				<tr
					class="clickable"
					onclick={(e) => {
						if (isRowOpenClick(e)) detailId = po.id;
					}}
				>
					<td class="mono">
						<RowLink
							onclick={() => (detailId = po.id)}
							ariaLabel={m('purchaseOrders.row.view', { number: po.po_number })}
						>
							{po.po_number}
						</RowLink>
					</td>
					<td>{po.vendor_name ?? '—'}</td>
					<td class="right mono">{formatCurrency(po.total)}</td>
					<td><span class="badge {po.status}">{po.status}</span></td>
					<td class="muted">{po.line_items.length}</td>
					<td class="muted">{formatDate(po.created_at)}</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMore} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('purchaseOrders.loadMore', { shown: pos.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('purchaseOrders.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

<Modal
	open={detailId !== null}
	ariaLabel={m('purchaseOrders.modal.aria')}
	width="lg"
	onclose={() => (detailId = null)}
>
	{#snippet header()}
		<header class="modal-header">
			<div class="title-block">
				<h2>{m('purchaseOrders.modal.title')}</h2>
				{#if detail}
					<span class="po-number-badge">{detail.po_number}</span>
					<span class="badge {detail.status}">{detail.status}</span>
				{/if}
			</div>
			<button class="close-btn" onclick={() => (detailId = null)} aria-label={m('purchaseOrders.modal.close')}>&times;</button>
		</header>
	{/snippet}

	<div class="modal-body">
		{#if detailLoading}
			<div class="loading">{m('common.loading')}</div>
		{:else if detail}
			<dl class="meta">
				<dt>{m('purchaseOrders.modal.vendor')}</dt><dd>{detail.vendor_name ?? '—'}</dd>
				<dt>{m('purchaseOrders.modal.total')}</dt><dd class="mono">{formatCurrency(detail.total)}</dd>
				<dt>{m('purchaseOrders.modal.created')}</dt><dd>{formatDate(detail.created_at)}</dd>
			</dl>

			<h3>{m('purchaseOrders.modal.lineItems')}</h3>
			<table class="line-table">
				<thead>
					<tr>
						<th>{m('purchaseOrders.modal.description')}</th>
						<th class="right">{m('purchaseOrders.modal.qty')}</th>
						<th class="right">{m('purchaseOrders.modal.unitPrice')}</th>
						<th class="right">{m('purchaseOrders.modal.lineTotal')}</th>
					</tr>
				</thead>
				<tbody>
					{#each detail.line_items as li (li.id)}
						<tr>
							<td>{li.description ?? '—'}</td>
							<td class="right mono">{li.quantity ?? '—'}</td>
							<td class="right mono">{formatCurrency(li.unit_price)}</td>
							<td class="right mono">{formatCurrency(li.total)}</td>
						</tr>
					{:else}
						<tr><td colspan="4" class="empty">{m('purchaseOrders.modal.noLineItems')}</td></tr>
					{/each}
				</tbody>
			</table>

			<h3>{m('purchaseOrders.modal.linkedInvoices', { count: detail.linked_invoices.length })}</h3>
			<table class="line-table">
				<thead>
					<tr>
						<th>{m('purchaseOrders.modal.invoiceNumber')}</th>
						<th>{m('purchaseOrders.modal.invoiceVendor')}</th>
						<th class="right">{m('purchaseOrders.modal.amount')}</th>
						<th>{m('purchaseOrders.modal.invoiceStatus')}</th>
					</tr>
				</thead>
				<tbody>
					{#each detail.linked_invoices as inv (inv.id)}
						<tr>
							<td class="mono">{inv.invoice_number}</td>
							<td>{inv.vendor_name ?? '—'}</td>
							<td class="right mono">{formatCurrency(inv.amount)}</td>
							<td>{inv.status}</td>
						</tr>
					{:else}
						<tr><td colspan="4" class="empty">{m('purchaseOrders.modal.noLinkedInvoices')}</td></tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
</Modal>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	.btn-outline {
		padding: 8px 16px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-outline:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn-outline:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
		text-transform: capitalize;
		background: var(--bg);
		color: var(--text-muted);
	}
	.badge.open {
		background: rgba(99, 140, 255, 0.15);
		color: var(--accent);
	}
	.badge.closed {
		background: rgba(31, 168, 106, 0.15);
		color: #1fa86a;
	}
	.badge.cancelled {
		background: rgba(150, 150, 150, 0.15);
		color: #999;
	}

	/* Detail modal — custom header + body + nested line tables. */
	.modal-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px 20px;
		border-bottom: 1px solid var(--border);
		margin: -24px -24px 0;
	}
	.title-block {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.modal-header h2 {
		margin: 0;
		font-size: 1.1rem;
		font-weight: 600;
	}
	.po-number-badge {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.85rem;
		color: var(--text-muted);
	}
	.close-btn {
		background: none;
		border: none;
		font-size: 1.5rem;
		cursor: pointer;
		color: var(--text-muted);
		line-height: 1;
		padding: 0 4px;
	}
	.close-btn:hover {
		color: var(--text);
	}
	.modal-body {
		padding: 20px 0 0;
		overflow-y: auto;
		flex: 1;
	}
	.modal-body h3 {
		margin: 18px 0 8px;
		font-size: 0.85rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}
	dl.meta {
		display: grid;
		grid-template-columns: 90px 1fr 90px 1fr;
		gap: 8px 14px;
		margin: 0 0 18px;
		padding-bottom: 14px;
		border-bottom: 1px solid var(--border);
	}
	dt {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
		align-self: center;
	}
	dd {
		margin: 0;
		font-size: 0.9rem;
	}
	dd.mono {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.82rem;
	}
	.line-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	.line-table th {
		background: var(--bg);
		text-align: left;
		padding: 6px 10px;
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
	}
	.line-table td {
		padding: 6px 10px;
		border-bottom: 1px solid var(--border);
	}
	.line-table .right {
		text-align: right;
	}
	.line-table .mono {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.82rem;
	}
	.line-table .empty {
		text-align: center;
		padding: 40px;
		color: var(--text-muted);
	}
	.loading {
		padding: 40px;
		text-align: center;
		color: var(--text-muted);
	}
</style>
