<script lang="ts">
	import type { Payment, PaymentStatus, PaymentMethod } from '$lib/types/payment';
	import { PAYMENT_STATUSES, PAYMENT_STATUS_LABELS, PAYMENT_METHOD_LABELS } from '$lib/types/payment';
	import { paymentStore } from '$lib/stores/payments.svelte';

	let search = $state('');
	let activeStatus = $state<PaymentStatus | 'all'>('all');
	let activeMethod = $state<PaymentMethod | 'all'>('all');

	function buildParams(): Record<string, string> {
		const params: Record<string, string> = { page_size: '100' };
		if (activeStatus !== 'all') params.status = activeStatus;
		if (activeMethod !== 'all') params.method = activeMethod;
		if (search.trim()) params.search = search.trim();
		return params;
	}

	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => paymentStore.fetch(buildParams()), 300);
	}

	$effect(() => {
		activeStatus;
		activeMethod;
		paymentStore.fetch(buildParams());
	});

	$effect(() => {
		search;
		debouncedFetch();
	});

	function statusCount(status: PaymentStatus): number {
		return paymentStore.all.filter((p) => p.status === status).length;
	}

	function formatCurrency(amount: number): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount);
	}

	function formatDate(dateStr: string | null): string {
		if (!dateStr) return '—';
		return new Date(dateStr).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
	}

	function methodLabel(method: string | null): string {
		if (!method) return '—';
		return PAYMENT_METHOD_LABELS[method as PaymentMethod] ?? method;
	}

	// Summary stats
	let totalAmount = $derived(
		paymentStore.all.reduce((sum, p) => sum + p.amount, 0)
	);
	let completedCount = $derived(
		paymentStore.all.filter((p) => p.status === 'completed').length
	);
	let pendingAmount = $derived(
		paymentStore.all
			.filter((p) => p.status === 'pending' || p.status === 'processing')
			.reduce((sum, p) => sum + p.amount, 0)
	);
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Payments</h1>
	</header>

	<div class="summary-cards">
		<div class="card">
			<span class="card-label">Total Payments</span>
			<span class="card-value">{paymentStore.all.length}</span>
		</div>
		<div class="card">
			<span class="card-label">Total Amount</span>
			<span class="card-value">{formatCurrency(totalAmount)}</span>
		</div>
		<div class="card">
			<span class="card-label">Completed</span>
			<span class="card-value">{completedCount}</span>
		</div>
		<div class="card">
			<span class="card-label">Pending Amount</span>
			<span class="card-value">{formatCurrency(pendingAmount)}</span>
		</div>
	</div>

	<nav class="filters">
		<button class="filter-chip" class:active={activeStatus === 'all'} onclick={() => (activeStatus = 'all')}>
			All <span class="count">{paymentStore.all.length}</span>
		</button>
		{#each PAYMENT_STATUSES as s}
			<button class="filter-chip" class:active={activeStatus === s} onclick={() => (activeStatus = s)}>
				{PAYMENT_STATUS_LABELS[s]} <span class="count">{statusCount(s)}</span>
			</button>
		{/each}
		<div class="search-group">
			<div class="search-box">
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
				</svg>
				<input type="text" placeholder="Search payments..." bind:value={search} />
			</div>
		</div>
	</nav>

	<div class="grid-container">
		<table>
			<thead>
				<tr>
					<th>Reference</th>
					<th>Vendor</th>
					<th>Invoice #</th>
					<th>Method</th>
					<th class="right">Amount</th>
					<th>Date</th>
					<th>Status</th>
				</tr>
			</thead>
			<tbody>
				{#each paymentStore.all as payment (payment.id)}
					<tr>
						<td class="mono">{payment.reference ?? '—'}</td>
						<td>{payment.vendor_name ?? '—'}</td>
						<td class="mono">{payment.invoice_number ?? '—'}</td>
						<td>{methodLabel(payment.method)}</td>
						<td class="right mono">{formatCurrency(payment.amount)}</td>
						<td>{formatDate(payment.created_at)}</td>
						<td><span class="badge {payment.status}">{PAYMENT_STATUS_LABELS[payment.status]}</span></td>
					</tr>
				{:else}
					<tr>
						<td colspan="7" class="empty">No payments match your filters.</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
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

	/* Summary cards */
	.summary-cards {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 12px;
	}

	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.card-label {
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.card-value {
		font-size: 1.25rem;
		font-weight: 700;
		color: var(--text);
	}

	/* Filters */
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

	/* Table */
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

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
	}

	/* Status badges */
	.badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		white-space: nowrap;
	}

	.badge.pending {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}

	.badge.processing {
		background: rgba(99, 140, 255, 0.15);
		color: #638cff;
	}

	.badge.completed {
		background: rgba(50, 200, 130, 0.15);
		color: #1fa86a;
	}

	.badge.failed {
		background: rgba(240, 70, 70, 0.15);
		color: #e04040;
	}

	.badge.cancelled {
		background: rgba(150, 150, 150, 0.15);
		color: #999;
	}

	@media (max-width: 768px) {
		.summary-cards {
			grid-template-columns: repeat(2, 1fr);
		}
	}
</style>
