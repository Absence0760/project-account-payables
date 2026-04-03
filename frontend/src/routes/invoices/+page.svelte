<script lang="ts">
	import type { Invoice, InvoiceStatus, AdvancedSearchFilters } from '$lib/types/invoice';
	import { INVOICE_STATUSES, STATUS_LABELS, EMPTY_ADVANCED_FILTERS } from '$lib/types/invoice';
	import { invoiceStore } from '$lib/stores/invoices.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import InvoiceModal from '$lib/components/InvoiceModal.svelte';
	import AdvancedSearchModal from '$lib/components/AdvancedSearchModal.svelte';

	let search = $state('');
	let activeStatus = $state<InvoiceStatus | 'all'>('all');
	let editing = $state<Invoice | null>(null);
	let showAdvancedSearch = $state(false);
	let advancedFilters = $state<AdvancedSearchFilters>({ ...EMPTY_ADVANCED_FILTERS });

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

	let filtered = $derived.by(() => {
		let list = invoiceStore.all;

		if (activeStatus !== 'all') {
			list = list.filter((inv) => inv.status === activeStatus);
		}

		if (search.trim()) {
			const q = search.toLowerCase();
			list = list.filter(
				(inv) =>
					inv.vendor.toLowerCase().includes(q) ||
					inv.invoice_number.toLowerCase().includes(q) ||
					inv.po_number.toLowerCase().includes(q) ||
					inv.description.toLowerCase().includes(q)
			);
		}

		const af = advancedFilters;
		if (af.vendor) list = list.filter((inv) => inv.vendor.toLowerCase().includes(af.vendor.toLowerCase()));
		if (af.invoice_number) list = list.filter((inv) => inv.invoice_number.toLowerCase().includes(af.invoice_number.toLowerCase()));
		if (af.po_number) list = list.filter((inv) => inv.po_number.toLowerCase().includes(af.po_number.toLowerCase()));
		if (af.description) list = list.filter((inv) => inv.description.toLowerCase().includes(af.description.toLowerCase()));
		if (af.amount_min) list = list.filter((inv) => inv.amount >= Number(af.amount_min));
		if (af.amount_max) list = list.filter((inv) => inv.amount <= Number(af.amount_max));
		if (af.due_date_from) list = list.filter((inv) => inv.due_date >= af.due_date_from);
		if (af.due_date_to) list = list.filter((inv) => inv.due_date <= af.due_date_to);
		if (af.statuses.length > 0) list = list.filter((inv) => af.statuses.includes(inv.status));

		return list;
	});

	function statusCount(status: InvoiceStatus): number {
		return invoiceStore.all.filter((inv) => inv.status === status).length;
	}

	function formatCurrency(amount: number, currency: string): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);
	}
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Invoices</h1>
	</header>

	<nav class="filters">
		<button class="filter-chip" class:active={activeStatus === 'all'} onclick={() => (activeStatus = 'all')}>
			All <span class="count">{invoiceStore.all.length}</span>
		</button>
		{#each INVOICE_STATUSES as s}
			<button class="filter-chip" class:active={activeStatus === s} onclick={() => (activeStatus = s)}>
				{STATUS_LABELS[s]} <span class="count">{statusCount(s)}</span>
			</button>
		{/each}
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
				{#each filtered as invoice (invoice.id)}
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
		justify-content: space-between;
		align-items: center;
		gap: 16px;
	}

	h1 {
		margin: 0;
		font-size: 1.4rem;
		font-weight: 700;
		color: var(--text);
	}

	.search-group {
		display: flex;
		align-items: center;
		gap: 6px;
		margin-left: auto;
	}

	.search-box {
		display: flex;
		align-items: center;
		gap: 8px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 20px;
		padding: 6px 12px;
		width: min(300px, 40vw);
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
</style>
