<script lang="ts">
	import type { Invoice, InvoiceStatus, AdvancedSearchFilters } from '$lib/types/invoice';
	import { INVOICE_STATUSES, STATUS_LABELS, EMPTY_ADVANCED_FILTERS } from '$lib/types/invoice';
	import { invoiceStore } from '$lib/stores/invoices.svelte';
	import { api } from '$lib/api';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import InvoiceModal from '$lib/components/InvoiceModal.svelte';
	import AdvancedSearchModal from '$lib/components/AdvancedSearchModal.svelte';

	let search = $state('');
	let activeStatus = $state<InvoiceStatus | 'all'>('all');
	let editing = $state<Invoice | null>(null);
	let showAdvancedSearch = $state(false);
	let advancedFilters = $state<AdvancedSearchFilters>({ ...EMPTY_ADVANCED_FILTERS });
	let uploading = $state(false);
	let uploadError = $state('');
	let fileInput: HTMLInputElement;

	async function handleUpload(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		uploading = true;
		uploadError = '';
		try {
			await api.upload('/api/invoices/upload', file);
			await invoiceStore.fetch(buildParams());
		} catch (err: unknown) {
			uploadError = err instanceof Error ? err.message : 'Upload failed';
		} finally {
			uploading = false;
			input.value = '';
		}
	}

	// Build query params from current filters and fetch from API
	function buildParams(): Record<string, string> {
		const params: Record<string, string> = { page_size: '100' };
		if (activeStatus !== 'all') params.status = activeStatus;
		if (search.trim()) params.search = search.trim();
		const af = advancedFilters;
		if (af.vendor) params.vendor = af.vendor;
		if (af.invoice_number) params.invoice_number = af.invoice_number;
		if (af.po_number) params.po_number = af.po_number;
		if (af.description) params.description = af.description;
		if (af.amount_min) params.amount_min = af.amount_min;
		if (af.amount_max) params.amount_max = af.amount_max;
		if (af.due_date_from) params.due_date_from = af.due_date_from;
		if (af.due_date_to) params.due_date_to = af.due_date_to;
		if (af.statuses.length > 0) params.status = af.statuses.join(',');
		return params;
	}

	// Debounce timer for search input
	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => invoiceStore.fetch(buildParams()), 300);
	}

	// Re-fetch when status tab or advanced filters change
	$effect(() => {
		// Touch reactive deps
		activeStatus;
		advancedFilters;
		invoiceStore.fetch(buildParams());
	});

	// Re-fetch on search input (debounced)
	$effect(() => {
		search;
		debouncedFetch();
	});

	let hasAdvancedFilters = $derived(
		advancedFilters.vendor !== '' ||
		advancedFilters.invoice_number !== '' ||
		advancedFilters.po_number !== '' ||
		advancedFilters.description !== '' ||
		advancedFilters.amount_min !== '' ||
		advancedFilters.amount_max !== '' ||
		advancedFilters.due_date_from !== '' ||
		advancedFilters.due_date_to !== '' ||
		advancedFilters.statuses.length > 0
	);

	function statusCount(status: InvoiceStatus): number {
		return invoiceStore.all.filter((inv) => inv.status === status).length;
	}

	function formatCurrency(amount: number, currency: string): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);
	}
</script>

<div class="workspace">
	<header class="toolbar">
		<div class="search-group">
			<div class="search-box">
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
				</svg>
				<input type="text" placeholder="Search invoices..." bind:value={search} />
			</div>
			<button
				class="advanced-btn"
				class:has-filters={hasAdvancedFilters}
				onclick={() => (showAdvancedSearch = true)}
				aria-label="Advanced search"
			>
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<line x1="4" y1="6" x2="20" y2="6" />
					<line x1="8" y1="12" x2="20" y2="12" />
					<line x1="12" y1="18" x2="20" y2="18" />
					<circle cx="6" cy="12" r="2" /><circle cx="10" cy="18" r="2" /><circle cx="8" cy="6" r="2" />
				</svg>
				{#if hasAdvancedFilters}
					<span class="dot"></span>
				{/if}
			</button>
		</div>
		<input type="file" accept=".pdf,.png,.jpg,.jpeg,.tiff" bind:this={fileInput} onchange={handleUpload} hidden />
		<button class="btn-upload" disabled={uploading} onclick={() => fileInput.click()}>
			{uploading ? 'Uploading...' : '+ Upload Invoice'}
		</button>
	</header>
	{#if uploadError}
		<div class="upload-error">{uploadError}</div>
	{/if}

	<nav class="filters">
		<button class="filter-chip" class:active={activeStatus === 'all'} onclick={() => (activeStatus = 'all')}>
			All <span class="count">{invoiceStore.all.length}</span>
		</button>
		{#each INVOICE_STATUSES as s}
			<button class="filter-chip" class:active={activeStatus === s} onclick={() => (activeStatus = s)}>
				{STATUS_LABELS[s]} <span class="count">{statusCount(s)}</span>
			</button>
		{/each}
	</nav>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th>Invoice #</th>
					<th>Vendor</th>
					<th>Description</th>
					<th>PO #</th>
					<th class="right">Amount</th>
					<th>Due Date</th>
					<th>Status</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each invoiceStore.all as invoice (invoice.id)}
					<tr>
						<td class="mono">{invoice.invoice_number}</td>
						<td>{invoice.vendor}</td>
						<td class="description">{invoice.description}</td>
						<td class="mono">{invoice.po_number}</td>
						<td class="right mono">{formatCurrency(invoice.amount, invoice.currency)}</td>
						<td>{invoice.due_date}</td>
						<td><StatusBadge status={invoice.status} /></td>
						<td>
							<button class="edit-btn" onclick={() => (editing = invoice)}>Edit</button>
						</td>
					</tr>
				{:else}
					<tr>
						<td colspan="8" class="empty">No invoices match your filters.</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>

{#if editing}
	<InvoiceModal invoice={editing} onclose={() => (editing = null)} />
{/if}

{#if showAdvancedSearch}
	<AdvancedSearchModal
		filters={advancedFilters}
		onclose={() => (showAdvancedSearch = false)}
		onapply={(f) => (advancedFilters = f)}
	/>
{/if}

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
		gap: 16px;
	}

	.search-group {
		display: flex;
		align-items: center;
		gap: 6px;
		flex: 1;
	}

	.search-box {
		display: flex;
		align-items: center;
		gap: 8px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 20px;
		padding: 8px 14px;
		flex: 1;
		max-width: 480px;
		color: var(--text-muted);
	}

	.advanced-btn {
		position: relative;
		display: grid;
		place-items: center;
		width: 34px;
		height: 34px;
		border-radius: 50%;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
		transition: all 0.15s;
		flex-shrink: 0;
	}

	.advanced-btn:hover {
		border-color: var(--accent);
		color: var(--text);
	}

	.advanced-btn.has-filters {
		border-color: var(--accent);
		color: var(--accent);
	}

	.advanced-btn .dot {
		position: absolute;
		top: 4px;
		right: 4px;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--accent);
	}

	.search-box input {
		border: none;
		background: none;
		outline: none;
		font-size: 0.9rem;
		width: 100%;
		color: var(--text);
		font-family: inherit;
	}

	.search-box input::placeholder {
		color: var(--text-muted);
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

	thead {
		position: sticky;
		top: 0;
		z-index: 1;
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

	.description {
		white-space: normal;
		max-width: 220px;
	}

	tr:last-child td {
		border-bottom: none;
	}

	tbody tr:hover {
		background: rgba(99, 140, 255, 0.04);
	}

	.mono {
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
		font-size: 0.82rem;
	}

	.right {
		text-align: right;
	}

	.edit-btn {
		padding: 4px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.edit-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
	}

	.btn-upload {
		padding: 8px 18px;
		border-radius: 6px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		transition: opacity 0.15s;
		white-space: nowrap;
		flex-shrink: 0;
	}

	.btn-upload:hover {
		opacity: 0.85;
	}

	.btn-upload:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.upload-error {
		padding: 10px 14px;
		border-radius: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
		font-size: 0.85rem;
	}
</style>
