<script lang="ts">
	import type { Payment, PaymentStatus, PaymentMethod } from '$lib/types/payment';
	import { PAYMENT_STATUSES, PAYMENT_STATUS_LABELS, PAYMENT_METHOD_LABELS } from '$lib/types/payment';
	import { paymentStore } from '$lib/stores/payments.svelte';
	import { appendUnique } from '$lib/utils/pagination';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import RunDetailModal from '$lib/components/modals/RunDetailModal.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { pruneSelection } from '$lib/utils/selection';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import StatusBadge from '$lib/components/ui/StatusBadge.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import { formatMoney, sumMoney } from '$lib/utils/money';
	import { formatDate } from '$lib/utils/time';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { PERM_PAYMENT_EXECUTE, PERM_PAYMENT_VOID } from '$lib/types/admin';
	import { m } from '$lib/i18n/store.svelte';
	import { untrack } from 'svelte';

	let HISTORY_COLUMNS = $derived([
		{ label: m('payments.col.invoiceNumber') },
		{ label: m('payments.col.vendor') },
		{ label: m('payments.col.method') },
		{ label: m('payments.col.amount'), class: 'right' },
		{ label: m('payments.col.status') },
		{ label: m('payments.col.reference') },
		{ label: m('payments.col.date') },
		{ class: 'actions-col' }
	]);

	let RUNS_COLUMNS = $derived([
		{ label: m('payments.col.run') },
		{ label: m('payments.col.status') },
		{ label: m('payments.col.total'), class: 'right' },
		{ label: m('payments.summary.payments') },
		{ label: m('payments.col.executed') },
		{ label: m('payments.col.created') }
	]);

	let CARDS_COLUMNS = $derived([
		{ label: m('payments.col.card') },
		{ label: m('payments.col.vendor') },
		{ label: m('payments.col.invoice') },
		{ label: m('payments.col.limit'), class: 'right' },
		{ label: m('payments.col.charged'), class: 'right' },
		{ label: m('payments.col.status') },
		{ label: m('payments.col.expires') },
		{ class: 'actions-col' }
	]);

	type Tab = 'queue' | 'history' | 'runs' | 'cards';
	let activeTab = $state<Tab>('queue');
	let search = $state('');
	let activeStatus = $state<PaymentStatus | 'all'>('all');

	// Summary
	// Money fields arrive as exact Decimal STRINGS from the backend (money
	// invariant — never float). formatMoney/Number() coerce at each use site.
	interface Summary {
		total_paid: string;
		total_pending: string;
		payment_count: number;
		total_rebates: string;
		queue_count: number;
	}
	let summary = $state<Summary | null>(null);

	// Queue
	interface QueueItem {
		id: string;
		invoice_number: string;
		vendor_name: string;
		// Exact Decimal STRING money (never float); coerce with Number() to sum.
		amount: string;
		currency: string;
		due_date: string | null;
		payment_terms: string | null;
		status: string;
		is_overdue: boolean;
		discount_eligible: boolean;
		discount_date: string | null;
		// discount_percent is a rate, not money — stays a JSON number.
		discount_percent: number | null;
		discount_amount: string | null;
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

	// Prune the queue selection to ids still in the queue whenever it reloads
	// (after an execute/void via onRunChanged/commitVoid drops the just-handled
	// invoice). Otherwise the pay-bar count (`selectedQueue.size`) outruns the
	// rows the money totals actually sum over. No-op (same Set) when clean.
	$effect(() => {
		const pruned = pruneSelection(
			selectedQueue,
			queue.map((q) => q.id)
		);
		if (pruned !== selectedQueue) selectedQueue = pruned;
	});

	let allQueueSelected = $derived(
		queue.length > 0 && queue.every(q => selectedQueue.has(q.id))
	);

	// Money arrives as string-Decimal. Summing via `Number(a) + Number(b)`
	// coerces each amount to a binary float before adding, which can drift
	// off the exact cent value (the classic 0.1 + 0.2 rounding bug) even
	// though every individual amount is exact — so this uses the
	// decimal-safe `sumMoney` (exact BigInt-scaled integer summation,
	// converted back to a float once at the end) instead of a float reduce.
	let selectedTotal = $derived(
		sumMoney(queue.filter(q => selectedQueue.has(q.id)).map(q => q.amount))
	);

	let selectedSavings = $derived(
		sumMoney(
			queue
				.filter(q => selectedQueue.has(q.id) && q.discount_eligible && q.discount_amount)
				.map(q => q.discount_amount)
		)
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
		if (activeTab === 'history') {
			await paymentStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method; routes through api.get
			await fetchPaymentCounts();
		}
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
				paymentStore.fetch(buildParams()), // noqa: raw-fetch-in-component — store method; routes through api.get
				fetchPaymentCounts(),
			]);
		} catch (err) {
			const e = err as { detail?: string; message?: string } | null;
			toast(e?.detail ?? e?.message ?? 'Void failed', 'error');
		} finally {
			voiding = false;
		}
	}

	function canVoid(p: Payment): boolean {
		// Server-side gate: require_permission(payment.void) — defaults to
		// admin/cfo, but a custom role can be granted it. Status: anything that
		// isn't already terminal-by-failure.
		//
		// `pending_compliance` is deliberately NOT offered here: nothing ever
		// reached the processor, so there is no rail to reverse. Its purpose-built
		// exits are Release / Dismiss below, which also close the
		// `payment_compliance_hold` exception the hold opened — a void would not.
		if (auth.user && auth.can(PERM_PAYMENT_VOID)) {
			return p.status === 'completed' || p.status === 'submitted' || p.status === 'processing';
		}
		return false;
	}

	// --- Compliance hold (pending_compliance) --------------------------------
	// A payment the sanctions/KYC gate parked at `pending_compliance` has two
	// server-side exits, each behind the permission that matches what it does:
	//   * release  → re-runs the SAME compliance-then-adapter path and can move
	//                money, so `payment.execute` (mirrors POST .../execute).
	//   * dismiss  → gives up, flips to `failed`, moves nothing, so
	//                `payment.void` (mirrors POST .../void).
	// Both 409 outside `pending_compliance`; hiding them elsewhere keeps the row
	// honest, and the backend enforces regardless.
	type ComplianceMode = 'release' | 'dismiss';
	let complianceTarget = $state<Payment | null>(null);
	let complianceMode = $state<ComplianceMode>('release');
	let complianceReason = $state('');
	let complianceBusy = $state(false);

	function canReleaseHold(p: Payment): boolean {
		return (
			p.status === 'pending_compliance' && !!auth.user && auth.can(PERM_PAYMENT_EXECUTE)
		);
	}

	function canDismissHold(p: Payment): boolean {
		return p.status === 'pending_compliance' && !!auth.user && auth.can(PERM_PAYMENT_VOID);
	}

	function openCompliance(p: Payment, mode: ComplianceMode) {
		complianceTarget = p;
		complianceMode = mode;
		complianceReason = '';
	}

	function closeCompliance() {
		complianceTarget = null;
		complianceReason = '';
	}

	async function refreshAfterCompliance() {
		await Promise.all([
			loadSummary(),
			loadQueue(),
			paymentStore.fetch(buildParams()), // noqa: raw-fetch-in-component — store method; routes through api.get
			fetchPaymentCounts()
		]);
	}

	async function commitCompliance() {
		if (!complianceTarget) return;
		const id = complianceTarget.id;
		const mode = complianceMode;
		const reason = complianceReason.trim();
		if (mode === 'dismiss' && !reason) {
			toast(m('payments.compliance.reasonRequired'), 'error');
			return;
		}
		complianceBusy = true;
		try {
			if (mode === 'release') {
				// The response carries the REAL post-release status: the backend
				// re-runs the gate, so a payment whose hold hasn't actually cleared
				// comes back still `pending_compliance`. Report what happened
				// instead of claiming success — this is never a bypass.
				const updated = await api.post<Payment>(`/api/payments/${id}/compliance/release`, {});
				toast(
					updated.status === 'pending_compliance'
						? m('payments.compliance.release.stillHeld')
						: m('payments.compliance.release.released'),
					updated.status === 'pending_compliance' ? 'error' : 'success'
				);
			} else {
				await api.post<Payment>(`/api/payments/${id}/compliance/dismiss`, { reason });
				toast(m('payments.compliance.dismiss.done'), 'success');
			}
			closeCompliance();
			await refreshAfterCompliance();
		} catch (err) {
			const fallback =
				mode === 'release'
					? m('payments.compliance.release.failed')
					: m('payments.compliance.dismiss.failed');
			toast(err instanceof Error ? err.message : fallback, 'error');
		} finally {
			complianceBusy = false;
		}
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
		// Paging (page / page_size) is owned by the store's Load-More; only
		// the filter params belong here.
		const params: Record<string, string> = {};
		if (activeStatus !== 'all') params.status = activeStatus;
		// `untrack`: `buildParams()` is also called from the tab-change `$effect`
		// below. A plain read of `search` here would make THAT effect depend on
		// `search` too (Svelte tracks reads transitively through called
		// functions), so every keystroke would re-fire it — an immediate,
		// un-debounced fetch racing the dedicated debounce timer further down.
		// `untrack` still reads the current value (the request still carries the
		// live search term); it just stops that read from registering as a
		// dependency of whichever effect happens to be calling this.
		const currentSearch = untrack(() => search);
		if (currentSearch.trim()) params.search = currentSearch.trim();
		return params;
	}

	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			if (activeTab === 'history') paymentStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method; routes through api.get
		}, 300);
	}

	$effect(() => {
		orgCurrency.ensureLoaded();
		loadSummary();
		loadQueue();
	});

	$effect(() => {
		if (activeTab === 'history') {
			activeStatus;
			paymentStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method; routes through api.get
			fetchPaymentCounts();
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
			const data = await api.get<{ items: QueueItem[]; total_savings: string }>(
				'/api/payments/queue'
			);
			queue = data.items;
			// total_savings is string-Decimal money — coerce for the numeric banner.
			queueTotalSavings = Number(data.total_savings ?? 0);
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
	const CARDS_PAGE_SIZE = 20;
	let cards = $state<CardItem[]>([]);
	let cardsTotal = $state(0);
	let cardsPage = $state(1);
	let loadingCards = $state(false);
	let loadingMoreCards = $state(false);
	let hasMoreCards = $derived(cards.length < cardsTotal);

	interface CardDashboard {
		active_cards: number;
		active_cards_value: number;
		spend_this_month: number;
		rebates_this_month: number;
		rebates_ytd: number;
		projected_annual_rebates: number;
	}
	let cardDashboard = $state<CardDashboard | null>(null);

	async function loadCards(opts: { append?: boolean; nextPage?: number } = {}) {
		const nextPage = opts.nextPage ?? 1;
		if (opts.append) loadingMoreCards = true;
		else loadingCards = true;
		try {
			const listReq = api.get<{ items: CardItem[]; total: number }>(
				`/api/cards?page=${nextPage}&page_size=${CARDS_PAGE_SIZE}`
			);
			// The dashboard is page-independent — only (re)fetch it on a fresh load.
			const dashReq = opts.append
				? Promise.resolve(cardDashboard)
				: api.get<CardDashboard>('/api/cards/dashboard').catch(() => null);
			const [list, dash] = await Promise.all([listReq, dashReq]);
			cards = opts.append ? appendUnique(cards, list.items) : list.items;
			cardsTotal = list.total;
			cardsPage = nextPage;
			cardDashboard = dash;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load cards', 'error');
		} finally {
			loadingCards = false;
			loadingMoreCards = false;
		}
	}

	async function loadMoreCards() {
		await loadCards({ append: true, nextPage: cardsPage + 1 });
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

	// Per-status tallies over the WHOLE payment set, from GET /api/payments/counts
	// — so the History chip counts (and "All") reflect every page, not just the
	// loaded one. Falls back to the loaded-page tally if the fetch fails.
	let paymentCounts = $state<Record<string, number>>({});
	let paymentCountsTotal = $state(0);

	async function fetchPaymentCounts() {
		try {
			const data = await api.get<{ total: number; by_status: Record<string, number> }>(
				'/api/payments/counts'
			);
			paymentCounts = data.by_status ?? {};
			paymentCountsTotal = data.total ?? 0;
		} catch {
			paymentCounts = {};
			paymentCountsTotal = 0;
		}
	}

	function statusCount(s: PaymentStatus): number {
		return paymentCounts[s] ?? paymentStore.all.filter((p) => p.status === s).length;
	}

	function formatCurrency(
		amount: number | string | null | undefined,
		currency?: string | null
	): string {
		// Per-row amounts pass their own currency; tenant-wide summary
		// totals omit it and fall back to the org's configured default.
		// Accepts string-Decimal money (formatMoney coerces) as well as numbers.
		return formatMoney(amount, { currency: currency ?? orgCurrency.currency });
	}


	function methodLabel(method: string | null): string {
		if (!method) return '—';
		return PAYMENT_METHOD_LABELS[method as PaymentMethod] ?? method;
	}

	let historyChips = $derived([
		{ key: 'all', label: m('common.all'), count: paymentCountsTotal || paymentStore.all.length },
		...PAYMENT_STATUSES.map((s) => ({
			key: s,
			label: PAYMENT_STATUS_LABELS[s],
			count: statusCount(s)
		}))
	]);
</script>

<PageHeader title={m('payments.title')}>
	{#if summary}
		<div class="summary-cards">
			<div class="scard">
				<span class="scard-value">{formatCurrency(summary.total_paid)}</span>
				<span class="scard-label">{m('payments.summary.totalPaid')}</span>
			</div>
			<div class="scard">
				<span class="scard-value">{formatCurrency(summary.total_pending)}</span>
				<span class="scard-label">{m('payments.summary.pending')}</span>
			</div>
			<div class="scard">
				<span class="scard-value">{summary.queue_count}</span>
				<span class="scard-label">{m('payments.summary.readyToPay')}</span>
			</div>
			<div class="scard">
				<span class="scard-value">{summary.payment_count}</span>
				<span class="scard-label">{m('payments.summary.payments')}</span>
			</div>
			{#if Number(summary.total_rebates) > 0}
				<div class="scard rebate">
					<span class="scard-value">{formatCurrency(summary.total_rebates)}</span>
					<span class="scard-label">{m('payments.summary.rebatesEarned')}</span>
				</div>
			{/if}
		</div>
	{/if}

	<nav class="tabs">
		<button class="tab" class:active={activeTab === 'queue'} onclick={() => (activeTab = 'queue')}>
			{m('payments.tab.queue')} {#if summary}<span class="tab-count">{summary.queue_count}</span>{/if}
		</button>
		<button class="tab" class:active={activeTab === 'history'} onclick={() => (activeTab = 'history')}>
			{m('payments.tab.history')}
		</button>
		<button class="tab" class:active={activeTab === 'cards'} onclick={() => (activeTab = 'cards')}>
			{m('payments.tab.cards')}
		</button>
		<button class="tab" class:active={activeTab === 'runs'} onclick={() => (activeTab = 'runs')}>
			{m('payments.tab.runs')}
		</button>
	</nav>

	{#if activeTab === 'history'}
		<div class="filter-row">
			<SearchBox
				bind:value={search}
				placeholder={m('payments.search.placeholder')}
				ariaLabel={m('payments.search.aria')}
			/>
			<FilterChips chips={historyChips} bind:active={activeStatus} />
		</div>
	{/if}

	{#if activeTab === 'queue'}
		{#if selectedQueue.size > 0}
			<div class="pay-bar">
				<span class="pay-bar-count">
				{m('payments.queue.selected', { n: selectedQueue.size, total: formatCurrency(selectedTotal) })}
				{#if selectedSavings > 0}
					<span class="pay-bar-savings">{m('payments.queue.save', { amount: formatCurrency(selectedSavings) })}</span>
				{/if}
			</span>
				{#if !showReview}
					<button class="btn-pay" onclick={() => (showReview = true)}>
						{m('payments.queue.reviewAndPay')}
					</button>
				{/if}
				<button class="btn-clear" onclick={() => { selectedQueue = new Set(); showReview = false; }}>{m('common.clear')}</button>
			</div>
		{/if}

		{#if showReview && selectedQueue.size > 0}
			<div class="review-panel">
				<div class="review-title">{m('payments.queue.reviewTitle')}</div>
				<table class="review-table">
					<thead>
						<tr>
							<th>{m('payments.col.invoice')}</th>
							<th>{m('payments.col.vendor')}</th>
							<th class="right">{m('payments.col.amount')}</th>
							<th>{m('payments.col.method')}</th>
						</tr>
					</thead>
					<tbody>
						{#each queue.filter(q => selectedQueue.has(q.id)) as item (item.id)}
							<tr>
								<td class="mono">{item.invoice_number}</td>
								<td>{item.vendor_name}</td>
								<td class="right mono">{formatCurrency(item.amount)}</td>
								<td>
									<select class="method-select" aria-label={`Payment method for ${item.invoice_number}`} value={paymentMethods[item.id] || 'ach'} onchange={(e) => (paymentMethods[item.id] = e.currentTarget.value)}>
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
					<span class="review-total">{m('payments.queue.total', { amount: formatCurrency(selectedTotal) })}</span>
					<button class="btn-execute" disabled={creatingRun} onclick={createDraftRun}>
						{creatingRun
							? m('payments.queue.creatingDraft')
							: m('payments.queue.createDraftRun', { n: selectedQueue.size })}
					</button>
				</div>
			</div>
		{/if}

		{#if queueTotalSavings > 0}
			<div class="savings-banner">
				<span class="savings-icon">💸</span>
				<span>
					{m('payments.queue.savingsBanner', { amount: formatCurrency(queueTotalSavings) })}
				</span>
			</div>
		{/if}

		<DataTable
			isEmpty={queue.length === 0}
			empty={m('payments.queue.empty')}
			colspan={8}
		>
			{#snippet header()}
				<tr>
					<th class="checkbox-col"><input type="checkbox" aria-label={m('payments.selectAllPayableAria')} checked={allQueueSelected} onchange={toggleQueueSelectAll} /></th>
					<th>{m('payments.col.invoiceNumber')}</th>
					<th>{m('payments.col.vendor')}</th>
					<th class="right">{m('payments.col.amount')}</th>
					<th>{m('payments.col.dueDate')}</th>
					<th>{m('payments.col.discount')}</th>
					<th>{m('payments.col.terms')}</th>
					<th>{m('payments.col.status')}</th>
				</tr>
			{/snippet}
			{#snippet body()}
				{#each queue as item (item.id)}
					<tr
						class:overdue={item.is_overdue}
						class:row-selected={selectedQueue.has(item.id)}
						class:discount-eligible={item.discount_eligible}
					>
						<td class="checkbox-col"><input type="checkbox" aria-label={`Select invoice ${item.invoice_number}`} checked={selectedQueue.has(item.id)} onchange={() => toggleQueueSelect(item.id)} /></td>
						<td class="mono">{item.invoice_number}</td>
						<td>{item.vendor_name}</td>
						<td class="right mono">{formatCurrency(item.amount, item.currency)}</td>
						<td class:overdue-text={item.is_overdue}>
							{formatDate(item.due_date)}
							{#if item.is_overdue}
								<span class="overdue-badge">{m('payments.overdue')}</span>
							{/if}
						</td>
						<td>
							{#if item.discount_eligible && item.discount_amount && item.discount_percent}
								<span
									class="discount-chip"
									title="{item.discount_percent}% discount expires {formatDate(item.discount_date)}"
								>
									{m('payments.queue.discountSave', { amount: formatCurrency(item.discount_amount, item.currency) })}
									<span class="discount-pct">{m('payments.queue.discountBy', { percent: item.discount_percent, date: formatDate(item.discount_date) })}</span>
								</span>
							{:else}
								<span class="muted">—</span>
							{/if}
						</td>
						<td class="muted">{item.payment_terms ?? '—'}</td>
						<td><StatusBadge status={item.status as import('$lib/types/invoice').InvoiceStatus} /></td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

	{:else if activeTab === 'history'}
		<DataTable
			columns={HISTORY_COLUMNS}
			isEmpty={paymentStore.all.length === 0}
			empty={m('payments.history.empty')}
			colspan={8}
		>
			{#snippet body()}
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
								<RowAction onclick={() => downloadRemittance(p)}>{m('payments.history.remittance')}</RowAction>
							{/if}
							{#if canReleaseHold(p)}
								<RowAction variant="accent" onclick={() => openCompliance(p, 'release')}>
									{m('payments.history.complianceRelease')}
								</RowAction>
							{/if}
							{#if canDismissHold(p)}
								<RowAction variant="danger" onclick={() => openCompliance(p, 'dismiss')}>
									{m('payments.history.complianceDismiss')}
								</RowAction>
							{/if}
							{#if canVoid(p)}
								<RowAction variant="danger" onclick={() => openVoid(p)}>{m('payments.history.void')}</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

		{#if paymentStore.hasMore}
			<div class="load-more-row">
				<button class="btn-load-more" onclick={() => paymentStore.loadMore()} disabled={paymentStore.loading}>
					{paymentStore.loading
						? m('common.loading')
						: m('payments.history.loadMore', { shown: paymentStore.all.length, total: paymentStore.total })}
				</button>
			</div>
		{:else if paymentStore.total > 0}
			<div class="load-more-row">
				<span class="load-more-end"
					>{m('payments.history.showingAll', { total: paymentStore.total })}</span
				>
			</div>
		{/if}

	{:else if activeTab === 'runs'}
		<DataTable
			columns={RUNS_COLUMNS}
			isEmpty={runs.length === 0}
			empty={m('payments.runs.empty')}
			colspan={6}
		>
			{#snippet body()}
				{#each runs as run (run.id)}
					<tr
						class="clickable"
						onclick={(e) => {
							if (isRowOpenClick(e)) activeRunId = run.id;
						}}
					>
						<td class="mono">
							<RowLink
								onclick={() => (activeRunId = run.id)}
								ariaLabel={`View payment run ${run.id.slice(0, 8)}`}
							>
								{run.id.slice(0, 8)}
							</RowLink>
						</td>
						<td><span class="badge {run.status}">{run.status}</span></td>
						<td class="right mono">{run.total_amount ? formatCurrency(run.total_amount) : '—'}</td>
						<td>{run.payment_count}</td>
						<td class="muted">{formatDate(run.executed_at)}</td>
						<td class="muted">{formatDate(run.created_at)}</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	{:else if activeTab === 'cards'}
		{#if cardDashboard}
			<div class="rebate-grid">
				<div class="rebate-card">
					<span class="rebate-label">{m('payments.cards.rebatesThisMonth')}</span>
					<span class="rebate-value">{formatCurrency(cardDashboard.rebates_this_month)}</span>
				</div>
				<div class="rebate-card">
					<span class="rebate-label">{m('payments.cards.rebatesYtd')}</span>
					<span class="rebate-value">{formatCurrency(cardDashboard.rebates_ytd)}</span>
				</div>
				<div class="rebate-card highlight">
					<span class="rebate-label">{m('payments.cards.projectedAnnual')}</span>
					<span class="rebate-value">{formatCurrency(cardDashboard.projected_annual_rebates)}</span>
					<span class="rebate-hint">{m('payments.cards.atRunRate')}</span>
				</div>
				<div class="rebate-card">
					<span class="rebate-label">{m('payments.cards.activeCards')}</span>
					<span class="rebate-value">
						{cardDashboard.active_cards}
						<span class="rebate-sub">{formatCurrency(cardDashboard.active_cards_value)}</span>
					</span>
				</div>
			</div>
		{/if}

		<DataTable
			columns={CARDS_COLUMNS}
			isEmpty={cards.length === 0}
			empty={loadingCards ? m('payments.cards.loading') : m('payments.cards.empty')}
			colspan={8}
		>
			{#snippet body()}
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
								<RowAction onclick={() => revealCard(card.id)}>{m('payments.cards.reveal')}</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

		{#if hasMoreCards}
			<div class="load-more-row">
				<button class="btn-load-more" onclick={loadMoreCards} disabled={loadingMoreCards}>
					{loadingMoreCards ? m('common.loading') : m('payments.cards.loadMore', { shown: cards.length, total: cardsTotal })}
				</button>
			</div>
		{:else if cardsTotal > 0}
			<div class="load-more-row">
				<span class="load-more-end">{m('payments.cards.showingAll', { total: cardsTotal })}</span>
			</div>
		{/if}
	{/if}
</PageHeader>

{#if activeRunId}
	<RunDetailModal
		runId={activeRunId}
		onclose={() => (activeRunId = null)}
		onchange={onRunChanged}
	/>
{/if}

<Modal
	open={revealedCard !== null}
	ariaLabel="Card details"
	title={m('payments.cardDetails.title')}
	onclose={() => (revealedCard = null)}
>
	{#if revealedCard}
		<p class="modal-hint">
			{m('payments.cardDetails.hint')}
		</p>
		<div class="card-details">
			<div class="card-row">
				<span class="card-label">{m('payments.cardDetails.cardNumber')}</span>
				<span class="card-value mono">{revealedCard.pan}</span>
			</div>
			<div class="card-row">
				<span class="card-label">{m('payments.cardDetails.cvv')}</span>
				<span class="card-value mono">{revealedCard.cvv}</span>
			</div>
			<div class="card-row">
				<span class="card-label">{m('payments.cardDetails.expires')}</span>
				<span class="card-value mono">{formatDate(revealedCard.expires)}</span>
			</div>
		</div>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (revealedCard = null)}>
				{m('payments.cardDetails.close')}
			</button>
		</div>
	{/if}
</Modal>

<Modal
	open={voidTarget !== null}
	ariaLabel="Void payment"
	title={m('payments.void.title')}
	onclose={() => (voidTarget = null)}
>
	{#if voidTarget}
		<p class="modal-hint">
			<strong>{voidTarget.invoice_number ?? voidTarget.id.slice(0, 8)}</strong>
			{#if voidTarget.vendor_name}· {voidTarget.vendor_name}{/if}
			· {formatCurrency(voidTarget.amount)}
		</p>
		<p class="modal-warn">
			{m('payments.void.warning')}
		</p>
		<form onsubmit={(e) => { e.preventDefault(); commitVoid(); }}>
			<label>
				<span>{m('payments.void.reason')}</span>
				<input
					type="text"
					bind:value={voidReason}
					placeholder={m('payments.void.reasonPlaceholder')}
					maxlength="500"
					autofocus
				/>
			</label>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (voidTarget = null)}>
					{m('common.cancel')}
				</button>
				<button
					type="submit"
					class="btn-danger"
					disabled={voiding || !voidReason.trim()}
				>
					{voiding ? m('payments.void.voiding') : m('payments.void.confirm')}
				</button>
			</div>
		</form>
	{/if}
</Modal>

<!-- Compliance-hold resolution. Confirm-then-act like the void dialog above:
     release re-runs the gate (and can dispatch money), dismiss gives up on the
     payment — neither should be a single stray click on a table row. -->
<Modal
	open={complianceTarget !== null}
	ariaLabel="Resolve compliance hold"
	title={complianceMode === 'release'
		? m('payments.compliance.release.title')
		: m('payments.compliance.dismiss.title')}
	onclose={closeCompliance}
>
	{#if complianceTarget}
		<p class="modal-hint">
			<strong>{complianceTarget.invoice_number ?? complianceTarget.id.slice(0, 8)}</strong>
			{#if complianceTarget.vendor_name}· {complianceTarget.vendor_name}{/if}
			· {formatCurrency(complianceTarget.amount)}
		</p>
		<p class="modal-warn">
			{complianceMode === 'release'
				? m('payments.compliance.release.warning')
				: m('payments.compliance.dismiss.warning')}
		</p>
		<form onsubmit={(e) => { e.preventDefault(); commitCompliance(); }}>
			{#if complianceMode === 'dismiss'}
				<label>
					<span>{m('payments.compliance.reason')}</span>
					<input
						type="text"
						bind:value={complianceReason}
						placeholder={m('payments.compliance.reasonPlaceholder')}
						maxlength="1000"
						autofocus
					/>
				</label>
			{/if}
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={closeCompliance}>
					{m('common.cancel')}
				</button>
				{#if complianceMode === 'release'}
					<button type="submit" class="btn-pay" disabled={complianceBusy}>
						{complianceBusy
							? m('payments.compliance.release.busy')
							: m('payments.compliance.release.confirm')}
					</button>
				{:else}
					<button
						type="submit"
						class="btn-danger"
						disabled={complianceBusy || !complianceReason.trim()}
					>
						{complianceBusy
							? m('payments.compliance.dismiss.busy')
							: m('payments.compliance.dismiss.confirm')}
					</button>
				{/if}
			</div>
		</form>
	{/if}
</Modal>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */

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
		/* WCAG 1.4.10: let the tab row wrap rather than push the page wider
		   than a narrow viewport. */
		flex-wrap: wrap;
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

	/* --- Queue --- */

	.overdue {
		background: rgba(224, 64, 64, 0.04);
	}

	.overdue-text {
		color: #f06464;
	}

	.overdue-badge {
		display: inline-block;
		font-size: 0.68rem;
		font-weight: 600;
		padding: 1px 6px;
		border-radius: 8px;
		background: rgba(224, 64, 64, 0.12);
		color: #f06464;
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
		background: var(--warning-tint);
		color: var(--warning-on-tint);
	}

	/* Held by the sanctions/KYC gate — an attention state, not a failure.
	   Amber so it reads as "needs a human", distinct from `failed`.

	   It shares the warning tone with `pending` above, which is correct — both
	   are waiting — so the RING, not the fill, is what separates a payment a
	   human must clear from one merely waiting its turn. Before the tokens the
	   two were told apart by two different hand-picked oranges; that read as a
	   distinction but was never a stated one, and it is the kind of thing a
	   later palette nudge silently collapses. `Compliance Hold` vs `Pending`
	   already carries it in text (SC 1.4.1) — this is scannability, which is
	   the whole reason the status is first-class rather than invisible.

	   An inset shadow, not a border: `.badge` declares none, so a real border
	   would grow this pill 2px and misalign it against every sibling badge. */
	.badge.pending_compliance {
		background: var(--warning-tint);
		color: var(--warning-on-tint);
		box-shadow: inset 0 0 0 1px var(--warning-on-tint);
	}

	.badge.processing {
		background: var(--accent-tint);
		color: var(--accent-on-tint);
	}

	.badge.completed {
		background: var(--success-tint);
		color: var(--success-on-tint);
	}

	.badge.failed {
		background: rgba(240, 70, 70, 0.15);
		color: #f06464;
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
		background: var(--accent-tint);
		color: var(--accent-on-tint);
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
		background: var(--success-strong);
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
		/* base look (border/radius/colour/font/chevron) from the global recipe */
		padding: 4px 30px 4px 8px;
		background-color: var(--surface);
		font-size: 0.82rem;
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
		background: var(--success-strong);
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

	/* --- Void modal --- */

	.modal-warn {
		font-size: 0.82rem;
		color: var(--text);
		margin: 0 0 14px;
		padding: 10px 12px;
		background: rgba(224, 64, 64, 0.08);
		border: 1px solid rgba(224, 64, 64, 0.3);
		border-radius: 4px;
	}

	.btn-danger {
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid var(--danger-strong);
		background: var(--danger-strong);
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
