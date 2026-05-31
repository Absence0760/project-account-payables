<script lang="ts">
	import type { Payment, PaymentStatus, PaymentMethod } from '$lib/types/payment';
	import { PAYMENT_STATUSES, PAYMENT_STATUS_LABELS, PAYMENT_METHOD_LABELS } from '$lib/types/payment';
	import { paymentStore } from '$lib/stores/payments.svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import RunDetailModal from '$lib/components/modals/RunDetailModal.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import StatusBadge from '$lib/components/ui/StatusBadge.svelte';
	import { auth } from '$lib/stores/auth.svelte';

	type Tab = 'queue' | 'history' | 'runs' | 'cards';
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
		discount_eligible: boolean;
		discount_date: string | null;
		discount_percent: number | null;
		discount_amount: number | null;
	}
	let queue = $state<QueueItem[]>([]);
	let queueTotalSavings = $state(0);

	// Queue selection and payment run creation
	let selectedQueue = $state<Set<string>>(new Set());
	let paymentMethods = $state<Record<string, string>>({});
	let creatingRun = $state(false);
	let showReview = $state(false);

	// Run detail modal — opened both after creating a draft from the queue
	// and when clicking a row in the Runs tab.
	let activeRunId = $state<string | null>(null);

	let allQueueSelected = $derived(
		queue.length > 0 && queue.every(q => selectedQueue.has(q.id))
	);

	let selectedTotal = $derived(
		queue.filter(q => selectedQueue.has(q.id)).reduce((sum, q) => sum + q.amount, 0)
	);

	let selectedSavings = $derived(
		queue
			.filter(q => selectedQueue.has(q.id) && q.discount_eligible && q.discount_amount)
			.reduce((sum, q) => sum + (q.discount_amount ?? 0), 0)
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

	async function createDraftRun() {
		if (selectedQueue.size === 0) return;
		creatingRun = true;
		try {
			const items = [...selectedQueue].map((id) => ({
				invoice_id: id,
				method: paymentMethods[id] || 'ach',
			}));

			// Create as a draft. The backend always creates with status='draft';
			// a separate /execute call moves money. The modal lets the user
			// review what's about to be paid before triggering it.
			const run = await api.post<{ id: string; message: string }>(
				'/api/payments/runs',
				{ items }
			);

			toast('Draft payment run created — review and execute', 'success');
			selectedQueue = new Set();
			showReview = false;
			activeRunId = run.id;

			// Refresh runs list so the new draft shows up immediately.
			await loadRuns();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Payment run failed', 'error');
		} finally {
			creatingRun = false;
		}
	}

	async function onRunChanged() {
		// Called by the modal after a successful execute or cancel.
		// Refresh everything the user might be looking at.
		await loadSummary();
		await loadQueue();
		await loadRuns();
		if (activeTab === 'history') await paymentStore.fetch(buildParams());
	}

	// Void modal — cfo/admin can void a completed or in-flight payment.
	let voidTarget = $state<Payment | null>(null);
	let voidReason = $state('');
	let voiding = $state(false);

	function openVoid(p: Payment) {
		voidTarget = p;
		voidReason = '';
	}

	async function commitVoid() {
		if (!voidTarget) return;
		const reason = voidReason.trim();
		if (!reason) {
			toast('Reason is required for a void', 'error');
			return;
		}
		voiding = true;
		try {
			await api.post(`/api/payments/${voidTarget.id}/void`, { reason });
			toast('Payment voided', 'success');
			voidTarget = null;
			voidReason = '';
			await Promise.all([
				loadSummary(),
				loadQueue(),
				paymentStore.fetch(buildParams()),
			]);
		} catch (err) {
			const e = err as { detail?: string; message?: string } | null;
			toast(e?.detail ?? e?.message ?? 'Void failed', 'error');
		} finally {
			voiding = false;
		}
	}

	function canVoid(p: Payment): boolean {
		// Server-side gate: ROLE_ADMIN | ROLE_CFO. Status: anything that
		// isn't already terminal-by-failure.
		if (auth.user && (auth.isAdmin || auth.isCfo)) {
			return p.status === 'completed' || p.status === 'submitted' || p.status === 'processing';
		}
		return false;
	}

	async function downloadRemittance(p: Payment) {
		try {
			const blob = await api.downloadBlob(`/api/payments/${p.id}/remittance`);
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `remittance-${p.reference ?? p.id.slice(0, 8)}.pdf`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Could not download remittance', 'error');
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
		} else if (activeTab === 'cards') {
			loadCards();
		}
	});

	$effect(() => {
		search;
		debouncedFetch();
	});

	async function loadSummary() {
		try {
			summary = await api.get<Summary>('/api/payments/summary');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load summary', 'error');
		}
	}

	async function loadQueue() {
		try {
			const data = await api.get<{ items: QueueItem[]; total_savings: number }>(
				'/api/payments/queue'
			);
			queue = data.items;
			queueTotalSavings = data.total_savings ?? 0;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load payment queue', 'error');
		}
	}

	// --- Virtual cards tab ---
	interface CardItem {
		id: string;
		invoice_id: string;
		card_provider: string;
		last_four: string | null;
		amount_limit: number;
		amount_charged: number | null;
		currency: string;
		status: string;
		expires_at: string | null;
		created_at: string;
		vendor_name: string | null;
		invoice_number: string | null;
	}
	let cards = $state<CardItem[]>([]);
	let loadingCards = $state(false);

	interface CardDashboard {
		active_cards: number;
		active_cards_value: number;
		spend_this_month: number;
		rebates_this_month: number;
		rebates_ytd: number;
		projected_annual_rebates: number;
	}
	let cardDashboard = $state<CardDashboard | null>(null);

	async function loadCards() {
		loadingCards = true;
		try {
			const [list, dash] = await Promise.all([
				api.get<{ items: CardItem[] }>('/api/cards'),
				api.get<CardDashboard>('/api/cards/dashboard').catch(() => null)
			]);
			cards = list.items;
			cardDashboard = dash;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load cards', 'error');
		} finally {
			loadingCards = false;
		}
	}

	let revealedCard = $state<{ pan: string; cvv: string; expires: string; last_four: string } | null>(
		null
	);

	async function revealCard(cardId: string) {
		try {
			const resp = await api.get<{
				pan: string;
				cvv: string;
				expires_at: string;
				last_four: string;
			}>(`/api/cards/${cardId}/details`);
			revealedCard = {
				pan: resp.pan,
				cvv: resp.cvv,
				expires: resp.expires_at,
				last_four: resp.last_four
			};
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Card not viewable', 'error');
		}
	}

	async function loadRuns() {
		try {
			const data = await api.get<{ items: RunItem[] }>('/api/payments/runs/?page_size=100');
			runs = data.items;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load payment runs', 'error');
		}
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
		<button class="tab" class:active={activeTab === 'cards'} onclick={() => (activeTab = 'cards')}>
			Cards
		</button>
		<button class="tab" class:active={activeTab === 'runs'} onclick={() => (activeTab = 'runs')}>
			Runs
		</button>
	</nav>

	{#if activeTab === 'history'}
		<div class="filter-row">
			<SearchBox
				bind:value={search}
				placeholder="Search payments..."
				ariaLabel="Search payments"
			/>
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
					<span class="pay-bar-count">
					{selectedQueue.size} selected — {formatCurrency(selectedTotal)}
					{#if selectedSavings > 0}
						<span class="pay-bar-savings">· save {formatCurrency(selectedSavings)}</span>
					{/if}
				</span>
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
						<button class="btn-execute" disabled={creatingRun} onclick={createDraftRun}>
							{creatingRun
								? 'Creating draft...'
								: `Create Draft Run · ${selectedQueue.size} Invoice${selectedQueue.size > 1 ? 's' : ''}`}
						</button>
					</div>
				</div>
			{/if}

			{#if queueTotalSavings > 0}
				<div class="savings-banner">
					<span class="savings-icon">💸</span>
					<span>
						<strong>{formatCurrency(queueTotalSavings)}</strong> in early-pay discounts
						available — pay the highlighted invoices before their discount date to capture them.
					</span>
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
						<th>Discount</th>
						<th>Terms</th>
						<th>Status</th>
					</tr>
				</thead>
				<tbody>
					{#each queue as item (item.id)}
						<tr
							class:overdue={item.is_overdue}
							class:row-selected={selectedQueue.has(item.id)}
							class:discount-eligible={item.discount_eligible}
						>
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
							<td>
								{#if item.discount_eligible && item.discount_amount && item.discount_percent}
									<span
										class="discount-chip"
										title="{item.discount_percent}% discount expires {formatDate(item.discount_date)}"
									>
										Save {formatCurrency(item.discount_amount, item.currency)}
										<span class="discount-pct">{item.discount_percent}% by {formatDate(item.discount_date)}</span>
									</span>
								{:else}
									<span class="muted">—</span>
								{/if}
							</td>
							<td class="muted">{item.payment_terms ?? '—'}</td>
							<td><StatusBadge status={item.status as import('$lib/types/invoice').InvoiceStatus} /></td>
						</tr>
					{:else}
						<tr><td colspan="8" class="empty">No invoices ready for payment.</td></tr>
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
						<th class="actions-col"></th>
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
									{#if p.method === 'virtual_card' && p.card_last_four}
										<span class="card-meta">•••• {p.card_last_four}</span>
									{/if}
								</span>
							</td>
							<td class="right mono">{formatCurrency(p.amount)}</td>
							<td><span class="badge {p.status}">{PAYMENT_STATUS_LABELS[p.status]}</span></td>
							<td class="mono muted">{p.reference ?? '—'}</td>
							<td class="muted">{formatDate(p.created_at)}</td>
							<td class="actions">
								{#if p.status === 'completed'}
									<RowAction onclick={() => downloadRemittance(p)}>Remittance</RowAction>
								{/if}
								{#if canVoid(p)}
									<RowAction variant="danger" onclick={() => openVoid(p)}>Void</RowAction>
								{/if}
							</td>
						</tr>
					{:else}
						<tr><td colspan="8" class="empty">No payments match your filters.</td></tr>
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
						<tr class="clickable" onclick={() => (activeRunId = run.id)}>
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
		{:else if activeTab === 'cards'}
			{#if cardDashboard}
				<div class="rebate-grid">
					<div class="rebate-card">
						<span class="rebate-label">Rebates this month</span>
						<span class="rebate-value">{formatCurrency(cardDashboard.rebates_this_month)}</span>
					</div>
					<div class="rebate-card">
						<span class="rebate-label">Rebates YTD</span>
						<span class="rebate-value">{formatCurrency(cardDashboard.rebates_ytd)}</span>
					</div>
					<div class="rebate-card highlight">
						<span class="rebate-label">Projected annual</span>
						<span class="rebate-value">{formatCurrency(cardDashboard.projected_annual_rebates)}</span>
						<span class="rebate-hint">at current run rate</span>
					</div>
					<div class="rebate-card">
						<span class="rebate-label">Active cards</span>
						<span class="rebate-value">
							{cardDashboard.active_cards}
							<span class="rebate-sub">{formatCurrency(cardDashboard.active_cards_value)}</span>
						</span>
					</div>
				</div>
			{/if}

			<table>
				<thead>
					<tr>
						<th>Card</th>
						<th>Vendor</th>
						<th>Invoice</th>
						<th class="right">Limit</th>
						<th class="right">Charged</th>
						<th>Status</th>
						<th>Expires</th>
						<th class="actions-col"></th>
					</tr>
				</thead>
				<tbody>
					{#each cards as card (card.id)}
						<tr>
							<td>
								<span class="card-badge">
									{card.card_provider}
									{#if card.last_four}
										<span class="card-meta">•••• {card.last_four}</span>
									{/if}
								</span>
							</td>
							<td>{card.vendor_name ?? '—'}</td>
							<td class="mono">{card.invoice_number ?? '—'}</td>
							<td class="right mono">{formatCurrency(card.amount_limit, card.currency)}</td>
							<td class="right mono muted">
								{card.amount_charged
									? formatCurrency(card.amount_charged, card.currency)
									: '—'}
							</td>
							<td><span class="badge {card.status}">{card.status}</span></td>
							<td class="muted">{formatDate(card.expires_at)}</td>
							<td class="actions">
								{#if card.status === 'created' || card.status === 'sent' || card.status === 'active'}
									<RowAction onclick={() => revealCard(card.id)}>Reveal</RowAction>
								{/if}
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="8" class="empty">
								{loadingCards ? 'Loading cards…' : 'No virtual cards issued yet.'}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
</div>

{#if activeRunId}
	<RunDetailModal
		runId={activeRunId}
		onclose={() => (activeRunId = null)}
		onchange={onRunChanged}
	/>
{/if}

{#if revealedCard}
	<div
		class="backdrop"
		onclick={(e) => { if (e.target === e.currentTarget) (revealedCard = null); }}
	>
		<div class="modal" role="dialog" aria-label="Card details">
			<h2>Virtual card details</h2>
			<p class="modal-hint">
				These values are fetched on demand and the access is audit-logged. Treat them like
				a credit card number — paste into the vendor's portal and close this dialog when
				you're done.
			</p>
			<div class="card-details">
				<div class="card-row">
					<span class="card-label">Card number</span>
					<span class="card-value mono">{revealedCard.pan}</span>
				</div>
				<div class="card-row">
					<span class="card-label">CVV</span>
					<span class="card-value mono">{revealedCard.cvv}</span>
				</div>
				<div class="card-row">
					<span class="card-label">Expires</span>
					<span class="card-value mono">{formatDate(revealedCard.expires)}</span>
				</div>
			</div>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (revealedCard = null)}>
					Close
				</button>
			</div>
		</div>
	</div>
{/if}

{#if voidTarget}
	<div
		class="backdrop"
		onclick={(e) => { if (e.target === e.currentTarget) (voidTarget = null); }}
	>
		<div class="modal" role="dialog" aria-label="Void payment">
			<h2>Void payment</h2>
			<p class="modal-hint">
				<strong>{voidTarget.invoice_number ?? voidTarget.id.slice(0, 8)}</strong>
				{#if voidTarget.vendor_name}· {voidTarget.vendor_name}{/if}
				· {formatCurrency(voidTarget.amount)}
			</p>
			<p class="modal-warn">
				This flips the payment to <strong>voided</strong> and re-opens the invoice for
				re-payment. If the processor supports it, we'll attempt an upstream reversal too.
				Audit-logged.
			</p>
			<form onsubmit={(e) => { e.preventDefault(); commitVoid(); }}>
				<label>
					<span>Reason</span>
					<input
						type="text"
						bind:value={voidReason}
						placeholder="Why is this being voided?"
						maxlength="500"
						autofocus
					/>
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={() => (voidTarget = null)}>
						Cancel
					</button>
					<button
						type="submit"
						class="btn-danger"
						disabled={voiding || !voidReason.trim()}
					>
						{voiding ? 'Voiding…' : 'Void payment'}
					</button>
				</div>
			</form>
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

	tbody tr.clickable {
		cursor: pointer;
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

	/* Floats above the page so selecting rows doesn't shove the queue down.
	   Same treatment as the invoices bulk-bar. */
	.pay-bar {
		position: fixed;
		left: 50%;
		bottom: 24px;
		transform: translateX(-50%);
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 10px 16px;
		background: var(--surface);
		border: 1px solid var(--accent);
		border-radius: 8px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
		z-index: 50;
		max-width: calc(100vw - 48px);
	}

	.pay-bar-count {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--accent);
		flex: 1;
	}

	.pay-bar-savings {
		color: #1fa86a;
		font-weight: 600;
	}

	.savings-banner {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 14px;
		background: rgba(31, 168, 106, 0.08);
		border: 1px solid rgba(31, 168, 106, 0.3);
		border-radius: 6px;
		font-size: 0.85rem;
		color: var(--text);
	}

	.savings-icon {
		font-size: 1.1rem;
	}

	tbody tr.discount-eligible {
		background: rgba(31, 168, 106, 0.04);
	}

	tbody tr.discount-eligible:hover {
		background: rgba(31, 168, 106, 0.08);
	}

	.discount-chip {
		display: inline-flex;
		flex-direction: column;
		gap: 2px;
		padding: 4px 8px;
		border-radius: 6px;
		background: rgba(31, 168, 106, 0.1);
		color: #1fa86a;
		font-size: 0.78rem;
		font-weight: 600;
		line-height: 1.2;
	}

	.discount-pct {
		font-size: 0.7rem;
		font-weight: 400;
		color: var(--text-muted);
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

	/* --- Actions / void modal --- */

	.actions-col {
		width: 90px;
	}

	.actions {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
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
		width: min(480px, 92vw);
		padding: 24px;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
	}

	.modal h2 {
		margin: 0 0 4px;
		font-size: 1.1rem;
		font-weight: 600;
		color: var(--text);
	}

	.modal-hint {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0 0 8px;
	}

	.modal-warn {
		font-size: 0.82rem;
		color: var(--text);
		margin: 0 0 14px;
		padding: 10px 12px;
		background: rgba(224, 64, 64, 0.08);
		border: 1px solid rgba(224, 64, 64, 0.3);
		border-radius: 4px;
	}

	.modal form {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}

	.modal label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.modal label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.modal input[type='text'] {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
	}

	.modal input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding-top: 8px;
		border-top: 1px solid var(--border);
	}

	.btn-cancel {
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-cancel:hover {
		background: var(--bg);
		color: var(--text);
	}

	.btn-danger {
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid #e04040;
		background: #e04040;
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-danger:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	.btn-danger:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.rebate-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 12px;
		margin-bottom: 16px;
	}

	.rebate-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 14px 16px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.rebate-card.highlight {
		border-color: rgba(31, 168, 106, 0.4);
		background: rgba(31, 168, 106, 0.05);
	}

	.rebate-label {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.rebate-value {
		font-size: 1.4rem;
		font-weight: 700;
		color: var(--text);
		line-height: 1.1;
	}

	.rebate-card.highlight .rebate-value {
		color: #1fa86a;
	}

	.rebate-sub {
		display: block;
		font-size: 0.78rem;
		font-weight: 400;
		color: var(--text-muted);
		margin-top: 2px;
	}

	.rebate-hint {
		font-size: 0.7rem;
		color: var(--text-muted);
	}

	.card-badge {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 3px 10px;
		border-radius: 10px;
		background: rgba(99, 140, 255, 0.1);
		color: var(--accent);
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: capitalize;
	}

	.card-meta {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.72rem;
		font-weight: 400;
		color: var(--text-muted);
	}

	.card-details {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 14px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 6px;
	}

	.card-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
	}

	.card-label {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.card-value {
		font-size: 1rem;
		font-weight: 600;
		color: var(--text);
		letter-spacing: 0.06em;
	}

	@media (max-width: 768px) {
		.summary-cards {
			grid-template-columns: repeat(2, 1fr);
		}

		.filter-row {
			flex-direction: column;
			align-items: stretch;
		}
	}
</style>
