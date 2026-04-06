<script lang="ts">
	import type { Payment, PaymentStatus, PaymentMethod } from '$lib/types/payment';
	import { PAYMENT_STATUSES, PAYMENT_STATUS_LABELS, PAYMENT_METHOD_LABELS } from '$lib/types/payment';
	import { paymentStore } from '$lib/stores/payments.svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/Toast.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';

	type Tab = 'queue' | 'history' | 'runs';
	let activeTab = $state<Tab>('queue');
	let search = $state('');
	let activeStatus = $state<PaymentStatus | 'all'>('all');

	// Summary
	interface Summary {
		total_paid: number;
		total_pending: number;
		payment_count: number;
		total_rebates: number;
		queue_count: number;
	}
	let summary = $state<Summary | null>(null);

	// Queue
	interface QueueItem {
		id: string;
		invoice_number: string;
		vendor_name: string;
		amount: number;
		currency: string;
		due_date: string | null;
		payment_terms: string | null;
		status: string;
		is_overdue: boolean;
	}
	let queue = $state<QueueItem[]>([]);

	// Queue selection and payment run creation
	let selectedQueue = $state<Set<string>>(new Set());
	let paymentMethods = $state<Record<string, string>>({});
	let creatingRun = $state(false);
	let showReview = $state(false);

	let allQueueSelected = $derived(
		queue.length > 0 && queue.every(q => selectedQueue.has(q.id))
	);

	let selectedTotal = $derived(
		queue.filter(q => selectedQueue.has(q.id)).reduce((sum, q) => sum + q.amount, 0)
	);

	function toggleQueueSelect(id: string) {
		const next = new Set(selectedQueue);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selectedQueue = next;
		if (!paymentMethods[id]) paymentMethods[id] = 'ach';
	}

	function toggleQueueSelectAll() {
		if (allQueueSelected) {
			selectedQueue = new Set();
		} else {
			selectedQueue = new Set(queue.map(q => q.id));
			for (const q of queue) {
				if (!paymentMethods[q.id]) paymentMethods[q.id] = 'ach';
			}
		}
	}

	async function createAndExecuteRun() {
		if (selectedQueue.size === 0) return;
		creatingRun = true;
		try {
			const items = [...selectedQueue].map(id => ({
				invoice_id: id,
				method: paymentMethods[id] || 'ach',
			}));

			// Create the run
			const run = await api.post<{ id: string; message: string }>('/api/payments/runs', { items });

			// Execute immediately
			const result = await api.post<{ message: string }>(`/api/payments/runs/${run.id}/execute`, {});

			toast(result.message, 'success');
			selectedQueue = new Set();
			showReview = false;

			// Refresh all data
			await loadSummary();
			await loadQueue();
			await loadRuns();
			if (activeTab === 'history') await paymentStore.fetch(buildParams());
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Payment run failed', 'error');
		} finally {
			creatingRun = false;
		}
	}

	// Runs
	interface RunItem {
		id: string;
		status: string;
		total_amount: number | null;
		executed_at: string | null;
		created_at: string;
		payment_count: number;
	}
	let runs = $state<RunItem[]>([]);

	function buildParams(): Record<string, string> {
		const params: Record<string, string> = { page_size: '100' };
		if (activeStatus !== 'all') params.status = activeStatus;
		if (search.trim()) params.search = search.trim();
		return params;
	}

	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			if (activeTab === 'history') paymentStore.fetch(buildParams());
		}, 300);
	}

	$effect(() => {
		loadSummary();
		loadQueue();
	});

	$effect(() => {
		if (activeTab === 'history') {
			activeStatus;
			paymentStore.fetch(buildParams());
		} else if (activeTab === 'queue') {
			loadQueue();
		} else if (activeTab === 'runs') {
			loadRuns();
		}
	});

	$effect(() => {
		search;
		debouncedFetch();
	});

	async function loadSummary() {
		try {
			summary = await api.get<Summary>('/api/payments/summary');
		} catch { /* non-critical */ }
	}

	async function loadQueue() {
		try {
			const data = await api.get<{ items: QueueItem[] }>('/api/payments/queue');
			queue = data.items;
		} catch { /* non-critical */ }
	}

	async function loadRuns() {
		try {
			const data = await api.get<{ items: RunItem[] }>('/api/payments/runs/?page_size=100');
			runs = data.items;
		} catch { /* non-critical */ }
	}

	function statusCount(s: PaymentStatus): number {
		return paymentStore.all.filter((p) => p.status === s).length;
	}

	function formatCurrency(amount: number, currency: string = 'USD'): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);
	}

	function formatDate(dateStr: string | null): string {
		if (!dateStr) return '—';
		return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
	}

	function methodLabel(method: string | null): string {
		if (!method) return '—';
		return PAYMENT_METHOD_LABELS[method as PaymentMethod] ?? method;
	}
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Payments</h1>
	</header>

	{#if summary}
		<div class="summary-cards">
			<div class="scard">
				<span class="scard-value">{formatCurrency(summary.total_paid)}</span>
				<span class="scard-label">Total Paid</span>
			</div>
			<div class="scard">
				<span class="scard-value">{formatCurrency(summary.total_pending)}</span>
				<span class="scard-label">Pending</span>
			</div>
			<div class="scard">
				<span class="scard-value">{summary.queue_count}</span>
				<span class="scard-label">Ready to Pay</span>
			</div>
			<div class="scard">
				<span class="scard-value">{summary.payment_count}</span>
				<span class="scard-label">Payments</span>
			</div>
			{#if summary.total_rebates > 0}
				<div class="scard rebate">
					<span class="scard-value">{formatCurrency(summary.total_rebates)}</span>
					<span class="scard-label">Rebates Earned</span>
				</div>
			{/if}
		</div>
	{/if}

	<nav class="tabs">
		<button class="tab" class:active={activeTab === 'queue'} onclick={() => (activeTab = 'queue')}>
			Queue {#if summary}<span class="tab-count">{summary.queue_count}</span>{/if}
		</button>
		<button class="tab" class:active={activeTab === 'history'} onclick={() => (activeTab = 'history')}>
			History
		</button>
		<button class="tab" class:active={activeTab === 'runs'} onclick={() => (activeTab = 'runs')}>
			Runs
		</button>
	</nav>

	{#if activeTab === 'history'}
		<div class="filter-row">
			<div class="search-box">
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
				</svg>
				<input type="text" placeholder="Search payments..." bind:value={search} />
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
			</nav>
		</div>
	{/if}

	<div class="grid-container">
		{#if activeTab === 'queue'}
			{#if selectedQueue.size > 0}
				<div class="pay-bar">
					<span class="pay-bar-count">{selectedQueue.size} selected — {formatCurrency(selectedTotal)}</span>
					{#if !showReview}
						<button class="btn-pay" onclick={() => (showReview = true)}>
							Review & Pay
						</button>
					{/if}
					<button class="btn-clear" onclick={() => { selectedQueue = new Set(); showReview = false; }}>Clear</button>
				</div>
			{/if}

			{#if showReview && selectedQueue.size > 0}
				<div class="review-panel">
					<div class="review-title">Payment Review</div>
					<table class="review-table">
						<thead>
							<tr>
								<th>Invoice</th>
								<th>Vendor</th>
								<th class="right">Amount</th>
								<th>Method</th>
							</tr>
						</thead>
						<tbody>
							{#each queue.filter(q => selectedQueue.has(q.id)) as item (item.id)}
								<tr>
									<td class="mono">{item.invoice_number}</td>
									<td>{item.vendor_name}</td>
									<td class="right mono">{formatCurrency(item.amount)}</td>
									<td>
										<select class="method-select" value={paymentMethods[item.id] || 'ach'} onchange={(e) => (paymentMethods[item.id] = e.currentTarget.value)}>
											<option value="ach">ACH</option>
											<option value="wire">Wire</option>
											<option value="check">Check</option>
											<option value="virtual_card">Virtual Card</option>
										</select>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
					<div class="review-footer">
						<span class="review-total">Total: {formatCurrency(selectedTotal)}</span>
						<button class="btn-execute" disabled={creatingRun} onclick={createAndExecuteRun}>
							{creatingRun ? 'Processing...' : `Pay ${selectedQueue.size} Invoice${selectedQueue.size > 1 ? 's' : ''}`}
						</button>
					</div>
				</div>
			{/if}

			<table>
				<thead>
					<tr>
						<th class="checkbox-col"><input type="checkbox" checked={allQueueSelected} onchange={toggleQueueSelectAll} /></th>
						<th>Invoice #</th>
						<th>Vendor</th>
						<th class="right">Amount</th>
						<th>Due Date</th>
						<th>Terms</th>
						<th>Status</th>
					</tr>
				</thead>
				<tbody>
					{#each queue as item (item.id)}
						<tr class:overdue={item.is_overdue} class:row-selected={selectedQueue.has(item.id)}>
							<td class="checkbox-col"><input type="checkbox" checked={selectedQueue.has(item.id)} onchange={() => toggleQueueSelect(item.id)} /></td>
							<td class="mono">{item.invoice_number}</td>
							<td>{item.vendor_name}</td>
							<td class="right mono">{formatCurrency(item.amount, item.currency)}</td>
							<td class:overdue-text={item.is_overdue}>
								{formatDate(item.due_date)}
								{#if item.is_overdue}
									<span class="overdue-badge">Overdue</span>
								{/if}
							</td>
							<td class="muted">{item.payment_terms ?? '—'}</td>
							<td><StatusBadge status={item.status as import('$lib/types/invoice').InvoiceStatus} /></td>
						</tr>
					{:else}
						<tr><td colspan="7" class="empty">No invoices ready for payment.</td></tr>
					{/each}
				</tbody>
			</table>

		{:else if activeTab === 'history'}
			<table>
				<thead>
					<tr>
						<th>Invoice #</th>
						<th>Vendor</th>
						<th>Method</th>
						<th class="right">Amount</th>
						<th>Status</th>
						<th>Reference</th>
						<th>Date</th>
					</tr>
				</thead>
				<tbody>
					{#each paymentStore.all as p (p.id)}
						<tr>
							<td class="mono">{p.invoice_number ?? '—'}</td>
							<td>{p.vendor_name ?? '—'}</td>
							<td>
								<span class="method-badge" class:card-method={p.method === 'virtual_card'}>
									{methodLabel(p.method)}
								</span>
							</td>
							<td class="right mono">{formatCurrency(p.amount)}</td>
							<td><span class="badge {p.status}">{PAYMENT_STATUS_LABELS[p.status]}</span></td>
							<td class="mono muted">{p.reference ?? '—'}</td>
							<td class="muted">{formatDate(p.created_at)}</td>
						</tr>
					{:else}
						<tr><td colspan="7" class="empty">No payments match your filters.</td></tr>
					{/each}
				</tbody>
			</table>

		{:else if activeTab === 'runs'}
			<table>
				<thead>
					<tr>
						<th>Run</th>
						<th>Status</th>
						<th class="right">Total</th>
						<th>Payments</th>
						<th>Executed</th>
						<th>Created</th>
					</tr>
				</thead>
				<tbody>
					{#each runs as run (run.id)}
						<tr>
							<td class="mono">{run.id.slice(0, 8)}</td>
							<td><span class="badge {run.status}">{run.status}</span></td>
							<td class="right mono">{run.total_amount ? formatCurrency(run.total_amount) : '—'}</td>
							<td>{run.payment_count}</td>
							<td class="muted">{formatDate(run.executed_at)}</td>
							<td class="muted">{formatDate(run.created_at)}</td>
						</tr>
					{:else}
						<tr><td colspan="6" class="empty">No payment runs yet.</td></tr>
					{/each}
				</tbody>
			</table>
		{/if}
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
		align-items: center;
		justify-content: space-between;
	}

	h1 {
		font-size: 1.3rem;
		font-weight: 700;
		margin: 0;
	}

	/* --- Summary --- */

	.summary-cards {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
	}

	.scard {
		flex: 1;
		min-width: 140px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 14px 16px;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.scard.rebate {
		border-color: rgba(31, 168, 106, 0.3);
		background: rgba(31, 168, 106, 0.04);
	}

	.scard-value {
		font-size: 1.2rem;
		font-weight: 700;
		color: var(--text);
	}

	.scard.rebate .scard-value {
		color: #1fa86a;
	}

	.scard-label {
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	/* --- Tabs --- */

	.tabs {
		display: flex;
		gap: 0;
		border-bottom: 1px solid var(--border);
	}

	.tab {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 10px 20px;
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 0.88rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		transition: all 0.12s;
	}

	.tab:hover {
		color: var(--text);
	}

	.tab.active {
		color: var(--accent);
		border-bottom-color: var(--accent);
	}

	.tab-count {
		font-size: 0.72rem;
		padding: 1px 6px;
		border-radius: 8px;
		background: rgba(99, 140, 255, 0.12);
		color: var(--accent);
		font-weight: 600;
	}

	/* --- Filter row (history tab) --- */

	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.search-box {
		display: flex;
		align-items: center;
		gap: 8px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 20px;
		padding: 8px 14px;
		max-width: 320px;
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

	/* --- Table --- */

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

	.muted {
		color: var(--text-muted);
	}

	.empty {
		text-align: center;
		padding: 40px 14px;
		color: var(--text-muted);
	}

	/* --- Queue --- */

	.overdue {
		background: rgba(224, 64, 64, 0.04);
	}

	.overdue-text {
		color: #e04040;
	}

	.overdue-badge {
		display: inline-block;
		font-size: 0.68rem;
		font-weight: 600;
		padding: 1px 6px;
		border-radius: 8px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
		margin-left: 6px;
	}

	/* --- Method badge --- */

	.method-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		background: var(--bg);
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
	}

	.method-badge.card-method {
		background: rgba(99, 140, 255, 0.1);
		color: var(--accent);
	}

	/* --- Status badges --- */

	.badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: capitalize;
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

	.badge.draft {
		background: var(--bg);
		color: var(--text-muted);
	}

	.badge.submitted {
		background: rgba(99, 140, 255, 0.15);
		color: #638cff;
	}

	/* --- Queue selection & payment --- */

	.checkbox-col {
		width: 36px;
		text-align: center;
		padding-left: 10px;
		padding-right: 4px;
	}

	.checkbox-col input[type='checkbox'] {
		cursor: pointer;
		accent-color: var(--accent);
	}

	.row-selected {
		background: rgba(99, 140, 255, 0.08);
	}

	.pay-bar {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 10px 14px;
		background: var(--surface);
		border-bottom: 1px solid var(--border);
	}

	.pay-bar-count {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--accent);
		flex: 1;
	}

	.btn-pay {
		padding: 6px 16px;
		border-radius: 4px;
		border: none;
		background: #1fa86a;
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-pay:hover {
		opacity: 0.9;
	}

	.btn-clear {
		padding: 6px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-clear:hover {
		color: var(--text);
	}

	/* --- Review panel --- */

	.review-panel {
		padding: 14px;
		background: var(--bg);
		border-bottom: 1px solid var(--border);
	}

	.review-title {
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin-bottom: 10px;
	}

	.review-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.82rem;
		margin-bottom: 12px;
	}

	.review-table th {
		padding: 6px 10px;
		text-align: left;
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
	}

	.review-table td {
		padding: 6px 10px;
		border-bottom: 1px solid var(--border);
	}

	.method-select {
		padding: 4px 8px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.82rem;
		font-family: inherit;
	}

	.review-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.review-total {
		font-size: 0.9rem;
		font-weight: 700;
		color: var(--text);
	}

	.btn-execute {
		padding: 8px 20px;
		border-radius: 6px;
		border: none;
		background: #1fa86a;
		color: #fff;
		font-size: 0.88rem;
		font-weight: 600;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-execute:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-execute:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	@media (max-width: 768px) {
		.summary-cards {
			grid-template-columns: repeat(2, 1fr);
		}

		.filter-row {
			flex-direction: column;
			align-items: stretch;
		}

		.search-box {
			max-width: none;
		}
	}
</style>
