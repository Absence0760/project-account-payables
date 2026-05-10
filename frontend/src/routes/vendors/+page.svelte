<script lang="ts">
	import { api } from '$lib/api';
	import SearchBox from '$lib/components/SearchBox.svelte';
	import { toast } from '$lib/components/Toast.svelte';

	interface Vendor {
		id: string;
		name: string;
		code: string | null;
		email: string | null;
		phone: string | null;
		address: string | null;
		tax_id: string | null;
		payment_terms: string | null;
		accepts_virtual_cards: boolean;
		status: string;
		source: string;
		verified_by: string | null;
		erp_vendor_id: string | null;
		erp_synced_at: string | null;
		invoice_count: number;
		created_at: string;
	}

	let vendors = $state<Vendor[]>([]);
	let search = $state('');
	let statusFilter = $state('all');
	let syncing = $state(false);

	const PAGE_SIZE = 20;
	let total = $state(0);
	let page = $state(1);
	let loadingMore = $state(false);
	let hasMore = $derived(vendors.length < total);

	const STATUS_LABELS: Record<string, string> = {
		active: 'Active',
		unverified: 'Unverified',
		inactive: 'Inactive',
		rejected: 'Rejected',
	};

	const SOURCE_LABELS: Record<string, string> = {
		manual: 'Manual',
		erp_sync: 'ERP Sync',
		ai_extracted: 'AI Extracted',
	};

	$effect(() => {
		fetchVendors();
	});

	$effect(() => {
		search;
		statusFilter;
		fetchVendors();
	});

	async function fetchVendors(opts: { append?: boolean; nextPage?: number } = {}) {
		try {
			const nextPage = opts.nextPage ?? 1;
			const params = new URLSearchParams({
				page: String(nextPage),
				page_size: String(PAGE_SIZE)
			});
			if (search.trim()) params.set('search', search.trim());
			if (statusFilter !== 'all') params.set('status', statusFilter);
			const data = await api.get<{ items: Vendor[]; total: number }>(
				`/api/vendors?${params}`
			);
			vendors = opts.append ? [...vendors, ...data.items] : data.items;
			total = data.total;
			page = nextPage;
		} catch {
			toast('Failed to load vendors', 'error');
		}
	}

	async function loadMoreVendors() {
		loadingMore = true;
		try {
			await fetchVendors({ append: true, nextPage: page + 1 });
		} finally {
			loadingMore = false;
		}
	}

	async function verifyVendor(id: string) {
		try {
			await api.post(`/api/vendors/${id}/verify`, {});
			await fetchVendors();
			toast('Vendor verified', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Verify failed', 'error');
		}
	}

	async function rejectVendor(id: string) {
		try {
			await api.post(`/api/vendors/${id}/reject`, {});
			await fetchVendors();
			toast('Vendor rejected', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Reject failed', 'error');
		}
	}

	async function syncFromErp() {
		syncing = true;
		try {
			const result = await api.post<{ message: string }>('/api/vendors/sync-erp', {});
			await fetchVendors();
			toast(result.message, 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Sync failed', 'error');
		} finally {
			syncing = false;
		}
	}

	let unverifiedCount = $derived(vendors.filter(v => v.status === 'unverified').length);
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Vendors</h1>
		<div class="toolbar-actions">
			<button class="btn-outline" disabled={syncing} onclick={syncFromErp}>
				{syncing ? 'Syncing...' : 'Sync from ERP'}
			</button>
		</div>
	</header>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder="Search vendors..." ariaLabel="Search vendors" />
		<nav class="filters">
			<button class="filter-chip" class:active={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>
				All <span class="count">{vendors.length}</span>
			</button>
			<button class="filter-chip" class:active={statusFilter === 'unverified'} onclick={() => (statusFilter = 'unverified')}>
				Unverified {#if unverifiedCount > 0}<span class="count alert">{unverifiedCount}</span>{/if}
			</button>
			<button class="filter-chip" class:active={statusFilter === 'active'} onclick={() => (statusFilter = 'active')}>
				Active
			</button>
			<button class="filter-chip" class:active={statusFilter === 'rejected'} onclick={() => (statusFilter = 'rejected')}>
				Rejected
			</button>
		</nav>
	</div>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th>Vendor</th>
					<th>Code</th>
					<th>Email</th>
					<th>Status</th>
					<th>Source</th>
					<th>Invoices</th>
					<th>ERP</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each vendors as v (v.id)}
					<tr class:unverified={v.status === 'unverified'} class:rejected={v.status === 'rejected'}>
						<td class="vendor-name">{v.name}</td>
						<td class="mono muted">{v.code ?? '—'}</td>
						<td class="muted">{v.email ?? '—'}</td>
						<td>
							<span class="status-badge {v.status}">{STATUS_LABELS[v.status] ?? v.status}</span>
						</td>
						<td>
							<span class="source-badge">{SOURCE_LABELS[v.source] ?? v.source}</span>
						</td>
						<td class="mono">{v.invoice_count}</td>
						<td>
							{#if v.erp_vendor_id}
								<span class="erp-linked" title="Linked to ERP: {v.erp_vendor_id}">
									<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
								</span>
							{:else}
								<span class="muted">—</span>
							{/if}
						</td>
						<td class="actions">
							{#if v.status === 'unverified'}
								<button class="btn-verify" onclick={() => verifyVendor(v.id)}>Verify</button>
								<button class="btn-reject-sm" onclick={() => rejectVendor(v.id)}>Reject</button>
							{/if}
						</td>
					</tr>
				{:else}
					<tr><td colspan="8" class="empty">No vendors found.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMoreVendors} disabled={loadingMore}>
				{loadingMore ? 'Loading…' : `Load more (${vendors.length} of ${total})`}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {total} vendor{total === 1 ? '' : 's'}</span>
		</div>
	{/if}
</div>

<style>
	.workspace {
		max-width: 1280px;
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

	.toolbar-actions {
		display: flex;
		gap: 8px;
	}

	.btn-outline {
		padding: 8px 18px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
	}

	.btn-outline:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.btn-outline:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* --- Filters --- */

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
		transition: all 0.15s;
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

	.count {
		font-size: 0.72rem;
		opacity: 0.7;
	}

	.count.alert {
		background: #e04040;
		color: #fff;
		padding: 0 5px;
		border-radius: 8px;
		opacity: 1;
	}

	/* --- Table --- */

	.grid-container {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow-x: auto;
		min-width: 0;
		max-width: 100%;
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
		letter-spacing: 0.04em;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
		white-space: nowrap;
	}

	td {
		padding: 10px 14px;
		border-bottom: 1px solid var(--border);
		color: var(--text);
		white-space: nowrap;
	}

	tr:last-child td {
		border-bottom: none;
	}

	tbody tr:hover {
		background: rgba(99, 140, 255, 0.04);
	}

	.unverified {
		background: rgba(212, 148, 10, 0.04);
	}

	.rejected td {
		opacity: 0.5;
	}

	.vendor-name {
		font-weight: 500;
	}

	.mono {
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
		font-size: 0.82rem;
	}

	.muted {
		color: var(--text-muted);
	}

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
	}

	/* --- Badges --- */

	.status-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.75rem;
		font-weight: 500;
	}

	.status-badge.active {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}

	.status-badge.unverified {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}

	.status-badge.inactive {
		background: var(--bg);
		color: var(--text-muted);
	}

	.status-badge.rejected {
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
	}

	.source-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 500;
		background: var(--bg);
		color: var(--text-muted);
	}

	.erp-linked {
		color: #1fa86a;
		display: inline-flex;
	}

	/* --- Actions --- */

	.actions {
		display: flex;
		gap: 6px;
	}

	.btn-verify {
		padding: 4px 12px;
		border-radius: 4px;
		border: 1px solid #1fa86a;
		background: var(--surface);
		color: #1fa86a;
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-verify:hover {
		background: rgba(31, 168, 106, 0.1);
	}

	.btn-reject-sm {
		padding: 4px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-reject-sm:hover {
		border-color: #e04040;
		color: #e04040;
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
</style>
