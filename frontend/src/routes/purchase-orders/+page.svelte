<script lang="ts">
	import { api } from '$lib/api';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';

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
			pos = opts.append ? [...pos, ...data.items] : data.items;
			total = data.total;
			page = nextPage;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load purchase orders', 'error');
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
			toast(err instanceof Error ? err.message : 'Failed to load PO', 'error');
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
			toast(err instanceof Error ? err.message : 'ERP sync failed', 'error');
		} finally {
			syncing = false;
		}
	}

	function formatCurrency(n: number | null): string {
		if (n === null) return '—';
		return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
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

<div class="workspace">
	<header class="toolbar">
		<h1>Purchase Orders</h1>
		<button class="btn-outline" disabled={syncing} onclick={syncFromErp}>
			{syncing ? 'Syncing…' : 'Sync from ERP'}
		</button>
	</header>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder="Search PO number..." ariaLabel="Search purchase orders" />
		<nav class="filters">
			<button class="filter-chip" class:active={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>
				All <span class="count">{total}</span>
			</button>
			<button class="filter-chip" class:active={statusFilter === 'open'} onclick={() => (statusFilter = 'open')}>
				Open
			</button>
			<button class="filter-chip" class:active={statusFilter === 'closed'} onclick={() => (statusFilter = 'closed')}>
				Closed
			</button>
			<button class="filter-chip" class:active={statusFilter === 'cancelled'} onclick={() => (statusFilter = 'cancelled')}>
				Cancelled
			</button>
		</nav>
	</div>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th>PO #</th>
					<th>Vendor</th>
					<th class="right">Total</th>
					<th>Status</th>
					<th>Lines</th>
					<th>Created</th>
				</tr>
			</thead>
			<tbody>
				{#each pos as po (po.id)}
					<tr class="clickable" onclick={() => (detailId = po.id)}>
						<td class="mono">{po.po_number}</td>
						<td>{po.vendor_name ?? '—'}</td>
						<td class="right mono">{formatCurrency(po.total)}</td>
						<td><span class="badge {po.status}">{po.status}</span></td>
						<td class="muted">{po.line_items.length}</td>
						<td class="muted">{formatDate(po.created_at)}</td>
					</tr>
				{:else}
					<tr><td colspan="6" class="empty">{loading ? 'Loading…' : 'No purchase orders.'}</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMore} disabled={loadingMore}>
				{loadingMore ? 'Loading…' : `Load more (${pos.length} of ${total})`}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {total} PO{total === 1 ? '' : 's'}</span>
		</div>
	{/if}
</div>

{#if detailId}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) detailId = null; }}>
		<div class="modal" role="dialog" aria-label="Purchase order">
			<header class="modal-header">
				<div class="title-block">
					<h2>Purchase Order</h2>
					{#if detail}
						<span class="po-number-badge">{detail.po_number}</span>
						<span class="badge {detail.status}">{detail.status}</span>
					{/if}
				</div>
				<button class="close-btn" onclick={() => (detailId = null)} aria-label="Close">&times;</button>
			</header>

			<div class="modal-body">
				{#if detailLoading}
					<div class="loading">Loading…</div>
				{:else if detail}
					<dl class="meta">
						<dt>Vendor</dt><dd>{detail.vendor_name ?? '—'}</dd>
						<dt>Total</dt><dd class="mono">{formatCurrency(detail.total)}</dd>
						<dt>Created</dt><dd>{formatDate(detail.created_at)}</dd>
					</dl>

					<h3>Line Items</h3>
					<table class="line-table">
						<thead>
							<tr>
								<th>Description</th>
								<th class="right">Qty</th>
								<th class="right">Unit Price</th>
								<th class="right">Total</th>
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
								<tr><td colspan="4" class="empty">No line items.</td></tr>
							{/each}
						</tbody>
					</table>

					<h3>Linked Invoices ({detail.linked_invoices.length})</h3>
					<table class="line-table">
						<thead>
							<tr>
								<th>Invoice #</th>
								<th>Vendor</th>
								<th class="right">Amount</th>
								<th>Status</th>
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
								<tr><td colspan="4" class="empty">No invoices reference this PO.</td></tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	.workspace {
		max-width: 1800px;
		margin: 0 auto;
		padding: 24px 20px;
		display: flex;
		flex-direction: column;
		gap: 16px;
		min-height: 100vh;
	}
	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	h1 {
		font-size: 1.3rem;
		font-weight: 700;
		margin: 0;
	}
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
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.filters {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-wrap: wrap;
	}
	.filter-chip {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 14px;
		border-radius: 20px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}
	.filter-chip:hover {
		border-color: var(--accent);
		color: var(--text);
	}
	.filter-chip.active {
		background: var(--accent);
		color: #fff;
		border-color: var(--accent);
	}
	.filter-chip .count {
		font-size: 0.72rem;
		opacity: 0.7;
	}
	.grid-container {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.875rem;
	}
	th {
		background: var(--bg);
		text-align: left;
		padding: 10px 14px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
	}
	td {
		padding: 10px 14px;
		border-bottom: 1px solid var(--border);
	}
	tr.clickable {
		cursor: pointer;
	}
	tr.clickable:hover {
		background: rgba(99, 140, 255, 0.04);
	}
	.mono {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.82rem;
	}
	.right {
		text-align: right;
	}
	.muted {
		color: var(--text-muted);
	}
	.empty {
		text-align: center;
		padding: 40px;
		color: var(--text-muted);
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
	.load-more-row {
		display: flex;
		justify-content: center;
		padding: 8px 0 4px;
	}
	.btn-load-more {
		padding: 8px 18px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-load-more:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn-load-more:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.load-more-end {
		font-size: 0.78rem;
		color: var(--text-muted);
	}
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: grid;
		place-items: center;
		z-index: 100;
		backdrop-filter: blur(2px);
	}
	.modal {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		width: min(820px, 95vw);
		max-height: 90vh;
		display: flex;
		flex-direction: column;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
	}
	.modal-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px 20px;
		border-bottom: 1px solid var(--border);
	}
	.title-block {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.modal h2 {
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
		padding: 20px;
		overflow-y: auto;
		flex: 1;
	}
	.modal h3 {
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
	.line-table {
		font-size: 0.85rem;
	}
	.line-table th {
		padding: 6px 10px;
		font-size: 0.7rem;
	}
	.line-table td {
		padding: 6px 10px;
	}
	.loading {
		padding: 40px;
		text-align: center;
		color: var(--text-muted);
	}
</style>
