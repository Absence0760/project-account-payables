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
	import { formatMoney } from '$lib/utils/money';
	import {
		groupAmountsByCurrency,
		spansMultipleCurrencies,
		type CurrencyGroup
	} from '$lib/utils/currencyGroups';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
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
		// `Payment.amount` is INVOICE currency (the home-currency debit lives on
		// `source_amount`), so these totals used to be face-value sums across
		// currencies. The backend converts into the org's reporting currency now
		// and says which currency that is — render it, or the figure is a bare
		// number whose denomination the reader has to assume.
		currency?: string;
		// Payments it could not convert, excluded from the totals above.
		unconverted_payment_count?: number;
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
		// --- Payment-blocking exceptions -------------------------------------
		// OPTIONAL, and deliberately so: `GET /api/payments/queue` does not send
		// these yet (see `docs/followups.md` item 12 for the contract this is
		// written against). `services/payment_runs.create_payment_run_for_invoices`
		// refuses the WHOLE run with a 409 when any selected invoice carries an
		// unresolved `duplicate` / `fraud_flag` / `line_total_mismatch`
		// exception, so those rows must not be selectable — but until the field
		// ships, an absent `blocked` must leave this page behaving exactly as it
		// does today. Every read goes through `isBlocked()` below, which treats
		// "absent" as "not blocked".
		//
		// `blocked_reason` is a CODE from the backend's fixed, PII-free
		// `PAYMENT_BLOCKING_EXCEPTION_TYPES` vocabulary — never prose — so it
		// can be rendered in all six shipped locales.
		blocked?: boolean;
		blocked_reason?: string | null;
	}
	let queue = $state<QueueItem[]>([]);
	// Loading / errored are tracked separately from `queue.length === 0` because
	// the empty copy here is a claim about MONEY — "no invoices are ready for
	// payment". Rendered while the fetch is in flight, or left standing forever
	// after a failed one, that reads as "nothing is owed" when the truth is "we
	// have not looked". Same three-state rule the /exceptions and /notifications
	// lists follow; see `frontend/CLAUDE.md` § Data tables.
	let queueLoading = $state(true);
	let queueErrored = $state(false);
	// Queue rows the backend could not express in the reporting currency, so
	// its own roll-ups exclude them. Rows still render in their OWN currency.
	let queueUnconvertedCount = $state(0);

	// Queue selection and payment run creation
	let selectedQueue = $state<Set<string>>(new Set());
	let paymentMethods = $state<Record<string, string>>({});
	let creatingRun = $state(false);
	let showReview = $state(false);

	// Run detail modal — opened both after creating a draft from the queue
	// and when clicking a row in the Runs tab.
	let activeRunId = $state<string | null>(null);

	/** Does this row carry an unresolved payment-blocking exception?
	 *
	 *  The single read of `blocked`, so "the backend doesn't send the field
	 *  yet" is answered in exactly one place: an absent / non-`true` value is
	 *  NOT blocked, which is byte-for-byte today's behaviour. */
	function isBlocked(item: QueueItem): boolean {
		return item.blocked === true;
	}

	/** Localised sentence for why a row can't be paid.
	 *
	 *  Maps the backend's fixed `PAYMENT_BLOCKING_EXCEPTION_TYPES` codes; an
	 *  unrecognised or missing code falls back to the generic reason rather
	 *  than rendering a raw identifier at the operator. */
	function blockedReason(item: QueueItem): string {
		switch (item.blocked_reason) {
			case 'duplicate':
				return m('payments.queue.blocked.duplicate');
			case 'fraud_flag':
				return m('payments.queue.blocked.fraudFlag');
			case 'line_total_mismatch':
				return m('payments.queue.blocked.lineTotalMismatch');
			case 'payment_reconciliation':
				// The fourth member of PAYMENT_BLOCKING_EXCEPTION_TYPES, and the
				// one this switch was missing — so a row held because an earlier
				// payment's fate at the rail is unknown fell through to the
				// generic string and told the operator nothing actionable. The
				// default arm stays for codes this build genuinely doesn't know.
				return m('payments.queue.blocked.paymentReconciliation');
			default:
				return m('payments.queue.blocked.generic');
		}
	}

	// The rows a payment run could actually be built from. Everything that
	// counts, sums, selects-all or prunes reads THIS, never `queue` — a blocked
	// row that slipped into the selection would take the whole draft down with
	// a 409.
	let selectableQueue = $derived(queue.filter((q) => !isBlocked(q)));
	let blockedQueueCount = $derived(queue.length - selectableQueue.length);

	// Prune the queue selection to ids still SELECTABLE whenever the queue
	// reloads (after an execute/void via onRunChanged/commitVoid drops the
	// just-handled invoice — or after a refresh reveals that a selected row has
	// since picked up a blocking exception). Otherwise the pay-bar count
	// (`selectedQueue.size`) outruns the rows the money totals actually sum
	// over. No-op (same Set) when clean.
	$effect(() => {
		const pruned = pruneSelection(
			selectedQueue,
			selectableQueue.map((q) => q.id)
		);
		if (pruned !== selectedQueue) selectedQueue = pruned;
	});

	let allQueueSelected = $derived(
		selectableQueue.length > 0 && selectableQueue.every(q => selectedQueue.has(q.id))
	);

	let selectedRows = $derived(queue.filter((q) => selectedQueue.has(q.id)));

	// Money arrives as string-Decimal, and each row carries its OWN currency.
	// Reducing them into one figure was doubly wrong: it coerced exact decimals
	// through binary floats, and — worse — it added EUR to USD and rendered the
	// result in the org default, so a EUR 100 + USD 100 selection read as one
	// meaningless "200". `groupAmountsByCurrency` sums exactly *within* each
	// currency (BigInt-scaled, never a float reduce) and never across them; the
	// UI shows each currency's subtotal side by side. No FX conversion happens
	// on a read — see `backend/docs/multi-currency.md`.
	let selectedGroups = $derived(
		groupAmountsByCurrency(selectedRows, orgCurrency.currency)
	);

	// `.filter(total > 0)` preserves the old `selectedSavings > 0` guard: a
	// currency whose discounts sum to nothing must not add a "· save $0.00".
	// A comparison against zero is a predicate, not money arithmetic.
	let selectedSavingsGroups = $derived(
		groupAmountsByCurrency(
			selectedRows.filter((q) => q.discount_eligible && q.discount_amount)
				.map((q) => ({ amount: q.discount_amount, currency: q.currency })),
			orgCurrency.currency
		).filter((g) => g.total > 0)
	);

	// The whole queue's early-pay savings, grouped the same way. Derived from
	// the rows rather than read off the response's `total_savings`, which is a
	// naive cross-currency `SUM` on the backend (same defect, one level up).
	// `/api/payments/queue` is unpaginated, so the rows we hold ARE the queue.
	let queueSavingsGroups = $derived(
		groupAmountsByCurrency(
			queue.filter((q) => q.discount_eligible && q.discount_amount)
				.map((q) => ({ amount: q.discount_amount, currency: q.currency })),
			orgCurrency.currency
		).filter((g) => g.total > 0)
	);

	// `create_payment_run_for_invoices` 422s a run spanning more than one
	// currency ("All invoices in a payment run must share the same currency"),
	// because `PaymentRun.total_amount` is one bare Numeric with no currency of
	// its own. So a mixed selection isn't just unreadable — it can't be
	// submitted. Say so up front instead of letting the draft fail.
	let mixedCurrencySelection = $derived(spansMultipleCurrencies(selectedGroups));

	/** Render a set of per-currency subtotals as one honest label.
	 *
	 *  Single currency → exactly what the pay-bar always showed, but in the
	 *  row's OWN currency rather than the org default. Several → each subtotal
	 *  side by side, separated (never added). Empty → a zero in the org
	 *  currency, which is what "nothing selected" costs. */
	function formatGroups(groups: CurrencyGroup[]): string {
		if (groups.length === 0) return formatCurrency(0);
		return groups.map((g) => formatCurrency(g.total, g.currency)).join(' · ');
	}

	// The server's refusal, kept on screen. A 409 from
	// `create_payment_run_for_invoices` NAMES the offending invoice numbers
	// ("Invoice(s) have an unresolved duplicate/fraud/line-total exception and
	// can't be paid until it's cleared: INV-1001, INV-1002"), and `ApiError`
	// already carries that text — but a 5-second toast is the wrong home for
	// the one thing that says which of twenty selected rows is at fault. This
	// renders it in the review panel until the operator changes the selection.
	let createRunError = $state('');

	function toggleQueueSelect(item: QueueItem) {
		// Belt-and-braces: the checkbox is disabled for a blocked row, but the
		// guard lives with the state change so no future caller can bypass it.
		if (isBlocked(item)) return;
		createRunError = '';
		const next = new Set(selectedQueue);
		if (next.has(item.id)) next.delete(item.id);
		else next.add(item.id);
		selectedQueue = next;
		if (!paymentMethods[item.id]) paymentMethods[item.id] = 'ach';
	}

	function toggleQueueSelectAll() {
		createRunError = '';
		if (allQueueSelected) {
			selectedQueue = new Set();
		} else {
			selectedQueue = new Set(selectableQueue.map(q => q.id));
			for (const q of selectableQueue) {
				if (!paymentMethods[q.id]) paymentMethods[q.id] = 'ach';
			}
		}
	}

	function clearQueueSelection() {
		selectedQueue = new Set();
		showReview = false;
		createRunError = '';
	}

	async function createDraftRun() {
		if (selectedQueue.size === 0) return;
		if (mixedCurrencySelection) {
			// Advance signal for the backend's own 422 — never send a request we
			// already know is refused.
			createRunError = m('payments.queue.mixedCurrencyBlocked');
			toast(createRunError, 'error');
			return;
		}
		createRunError = '';
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
			// `ApiError.message` is `formatApiDetail(body.detail, …)`, so the
			// backend's 409 detail — which names the offending invoice numbers —
			// arrives intact. Surface it in BOTH places: the toast announces it
			// (assertive live region), the panel keeps it readable.
			const detail = err instanceof Error ? err.message : m('payments.queue.createFailed');
			createRunError = detail;
			toast(detail, 'error');
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

	// Three states, not two: a failed load must not read as "nothing matched".
	let historyEmptyMessage = $derived(
		paymentStore.errored ? m('payments.history.empty.errored') : m('payments.history.empty')
	);

	let searchTimer: ReturnType<typeof setTimeout>;
	function debouncedFetch() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			// `.catch`: the store re-throws so an awaiting caller keeps its own
			// handling, but this call is fire-and-forget — `paymentStore.errored`
			// is what renders the failure state, so swallow the rejection rather
			// than log an unhandled one.
			if (activeTab === 'history') paymentStore.fetch(buildParams()).catch(() => {}); // noqa: raw-fetch-in-component — store method; routes through api.get
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
			// `.catch`: fire-and-forget from an $effect — `paymentStore.errored`
			// renders the failure, so the re-thrown rejection has no other home.
			paymentStore.fetch(buildParams()).catch(() => {}); // noqa: raw-fetch-in-component — store method; routes through api.get
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
		// Cancel a pending debounce on teardown: without it the timer fires
		// after the page is gone and lands a stale list into the shared store.
		return () => clearTimeout(searchTimer);
	});

	// One sequencer PER independent list, never one shared counter — this page
	// holds four surfaces that reload on different triggers (a tab switch, an
	// execute, a void, a compliance release), and `onRunChanged` fires three of
	// them at once. A single counter would let the runs response retire the
	// queue's in-flight request and blank the list it was about to fill.
	// See `frontend/CLAUDE.md` § Sequencing list fetches.
	const summarySequence = createRequestSequencer();
	const queueSequence = createRequestSequencer();
	const runsSequence = createRequestSequencer();
	const cardsSequence = createRequestSequencer();

	async function loadSummary() {
		const token = summarySequence.start();
		try {
			const data = await api.get<Summary>('/api/payments/summary');
			if (!summarySequence.canCommit(token)) return; // superseded by a newer load
			summary = data;
		} catch (err) {
			if (summarySequence.isCurrentRequest(token)) {
				toast(err instanceof Error ? err.message : 'Failed to load summary', 'error');
			}
		}
	}

	async function loadQueue() {
		const token = queueSequence.start();
		queueLoading = true;
		try {
			const data = await api.get<{ items: QueueItem[]; unconverted_count?: number }>(
				'/api/payments/queue'
			);
			if (!queueSequence.canCommit(token)) return; // superseded by a newer load
			queue = data.items;
			queueUnconvertedCount = data.unconverted_count ?? 0;
			queueErrored = false;
		} catch (err) {
			if (queueSequence.isCurrentRequest(token)) {
				queueErrored = true;
				toast(err instanceof Error ? err.message : 'Failed to load payment queue', 'error');
			}
		} finally {
			// `isCurrentRequest`, not `canCommit`: a local edit that supersedes
			// this request must still clear the spinner, or the table sits on
			// "Loading…" forever with no request left to clear it.
			if (queueSequence.isCurrentRequest(token)) queueLoading = false;
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
		// Fetch and Load-More share one counter (latest-issued wins), exactly as
		// the payments store documents: a Load-More that resolves after a fresh
		// reload must not append a stale page onto the new list.
		const token = cardsSequence.start();
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
			if (!cardsSequence.canCommit(token)) return; // superseded by a newer load
			cards = opts.append ? appendUnique(cards, list.items) : list.items;
			cardsTotal = list.total;
			cardsPage = nextPage;
			cardDashboard = dash;
		} catch (err) {
			if (cardsSequence.isCurrentRequest(token)) {
				toast(err instanceof Error ? err.message : 'Failed to load cards', 'error');
			}
		} finally {
			// NOT canCommit — reading it here would leave the spinner stuck on
			// forever once a newer request superseded this one.
			if (cardsSequence.isCurrentRequest(token)) {
				loadingCards = false;
				loadingMoreCards = false;
			}
		}
	}

	async function loadMoreCards() {
		await loadCards({ append: true, nextPage: cardsPage + 1 });
	}

	// The AP-side reveal renders exactly what `CardDetailsResponse` declares
	// (`backend/app/schemas/virtual_card.py`): `{card_number, exp_month, exp_year,
	// cvv}`. FastAPI strips anything the response_model doesn't declare, so the
	// supplier-portal shape (`{pan, expires_at, last_four}`) that used to be typed
	// here arrived `undefined` — a blank card number and no expiry. Don't widen
	// the backend schema to suit the client; `backend/app/api/portal.py` records
	// that reading `details.pan` was a prior break.
	let revealedCard = $state<{ cardNumber: string; cvv: string; expires: string } | null>(null);

	/** `exp_month` / `exp_year` → the `MM/YYYY` printed on the card. Kept dumb on
	 *  purpose: these are card-face digits, not a date to localize. */
	function formatCardExpiry(month: number, year: number): string {
		if (!Number.isFinite(month) || !Number.isFinite(year)) return '—';
		return `${String(month).padStart(2, '0')}/${year}`;
	}

	async function revealCard(cardId: string) {
		try {
			const resp = await api.get<{
				card_number: string;
				exp_month: number;
				exp_year: number;
				cvv: string;
			}>(`/api/cards/${cardId}/details`);
			revealedCard = {
				cardNumber: resp.card_number,
				cvv: resp.cvv,
				expires: formatCardExpiry(resp.exp_month, resp.exp_year)
			};
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Card not viewable', 'error');
		}
	}

	async function loadRuns() {
		const token = runsSequence.start();
		try {
			const data = await api.get<{ items: RunItem[] }>('/api/payments/runs/?page_size=100');
			if (!runsSequence.canCommit(token)) return; // superseded by a newer load
			runs = data.items;
		} catch (err) {
			if (runsSequence.isCurrentRequest(token)) {
				toast(err instanceof Error ? err.message : 'Failed to load payment runs', 'error');
			}
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
				<span class="scard-value">
					{formatCurrency(summary.total_paid, summary.currency)}
				</span>
				<span class="scard-label">{m('payments.summary.totalPaid')}</span>
			</div>
			<div class="scard">
				<span class="scard-value">
					{formatCurrency(summary.total_pending, summary.currency)}
				</span>
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
		<!-- A payment with no rate into the reporting currency is left out of the
		     totals above, so the headline understates what actually moved. Same
		     notice pattern as /discounts' excluded foreign offers and the /cfo
		     cash-position card's unconverted outflows — all three read alike on
		     purpose. -->
		{#if (summary.unconverted_payment_count ?? 0) > 0}
			<p class="fx-skipped" role="alert" data-testid="unconverted-payments">
				{m('payments.summary.unconvertedPayments', {
					n: summary.unconverted_payment_count ?? 0,
					currency: summary.currency ?? ''
				})}
			</p>
		{/if}
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
		<!-- Rows the backend could not express in the reporting currency are
		     excluded from its own queue roll-ups. The rows themselves are still
		     listed and selectable — each renders in its own currency — so this
		     says the TOTALS are short, not that anything is missing. -->
		{#if queueUnconvertedCount > 0}
			<p class="fx-skipped" role="alert" data-testid="unconverted-queue">
				{m('payments.queue.unconvertedRows', { n: queueUnconvertedCount })}
			</p>
		{/if}
		{#if selectedQueue.size > 0}
			<div class="pay-bar">
				{#if mixedCurrencySelection}
					<!-- Inside the fixed bar, above the controls: the bar floats, so
					     a sibling in normal flow would render nowhere near it. -->
					<p class="pay-bar-warn" role="alert" data-testid="mixed-currency-warning">
						{m('payments.queue.mixedCurrency', { n: selectedGroups.length })}
					</p>
				{/if}
				<div class="pay-bar-row">
					<span class="pay-bar-count" data-testid="pay-bar-count">
						<!-- `formatGroups` renders ONE subtotal per currency, side by
						     side — it never adds EUR to USD into a single figure. -->
						{m('payments.queue.selected', { n: selectedQueue.size, total: formatGroups(selectedGroups) })}
						{#if selectedSavingsGroups.length > 0}
							<span class="pay-bar-savings">{m('payments.queue.save', { amount: formatGroups(selectedSavingsGroups) })}</span>
						{/if}
					</span>
					{#if !showReview}
						<button
							class="btn-pay"
							disabled={mixedCurrencySelection}
							title={mixedCurrencySelection ? m('payments.queue.mixedCurrencyBlocked') : ''}
							onclick={() => (showReview = true)}
						>
							{m('payments.queue.reviewAndPay')}
						</button>
					{/if}
					<button class="btn-clear" onclick={clearQueueSelection}>{m('common.clear')}</button>
				</div>
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
				{#if createRunError}
					<!-- The server's own refusal, kept on screen. On the
					     financial-integrity 409 this NAMES the invoice numbers
					     that blocked the run, which is the only thing that makes
					     a 20-row selection actionable. -->
					<p class="review-error" role="alert" data-testid="create-run-error">
						{createRunError}
					</p>
				{/if}
				<div class="review-footer">
					<span class="review-total">{m('payments.queue.total', { amount: formatGroups(selectedGroups) })}</span>
					<button
						class="btn-execute"
						disabled={creatingRun || mixedCurrencySelection}
						title={mixedCurrencySelection ? m('payments.queue.mixedCurrencyBlocked') : ''}
						onclick={createDraftRun}
					>
						{creatingRun
							? m('payments.queue.creatingDraft')
							: m('payments.queue.createDraftRun', { n: selectedQueue.size })}
					</button>
				</div>
			</div>
		{/if}

		{#if blockedQueueCount > 0}
			<p class="blocked-banner" role="status" data-testid="queue-blocked-banner">
				{m('payments.queue.blockedCount', { n: blockedQueueCount })}
			</p>
		{/if}

		{#if queueSavingsGroups.length > 0}
			<div class="savings-banner">
				<span class="savings-icon">💸</span>
				<span>
					{m('payments.queue.savingsBanner', { amount: formatGroups(queueSavingsGroups) })}
				</span>
			</div>
		{/if}

		<DataTable
			isEmpty={queue.length === 0}
			empty={queueLoading
				? m('common.loading')
				: queueErrored
					? m('payments.queue.empty.errored')
					: m('payments.queue.empty')}
			colspan={8}
		>
			{#snippet header()}
				<tr>
					<th class="checkbox-col"><input type="checkbox" aria-label={m('payments.selectAllPayableAria')} checked={allQueueSelected} disabled={selectableQueue.length === 0} onchange={toggleQueueSelectAll} /></th>
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
						class:row-blocked={isBlocked(item)}
					>
						<td class="checkbox-col">
							{#if isBlocked(item)}
								<!-- Disabled, not omitted: the column keeps its shape, and
								     the accessible name carries the REASON so a screen
								     reader learns why this row can't be paid. -->
								<input
									type="checkbox"
									disabled
									checked={false}
									data-testid="queue-blocked-checkbox"
									aria-label={m('payments.queue.blockedCheckboxAria', {
										invoice: item.invoice_number,
										reason: blockedReason(item)
									})}
								/>
							{:else}
								<input type="checkbox" aria-label={`Select invoice ${item.invoice_number}`} checked={selectedQueue.has(item.id)} onchange={() => toggleQueueSelect(item)} />
							{/if}
						</td>
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
						<td>
							<StatusBadge status={item.status as import('$lib/types/invoice').InvoiceStatus} />
							{#if isBlocked(item)}
								<span class="blocked-chip" data-testid="queue-blocked-chip">
									{blockedReason(item)}
								</span>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

	{:else if activeTab === 'history'}
		<DataTable
			columns={HISTORY_COLUMNS}
			isEmpty={paymentStore.all.length === 0}
			empty={historyEmptyMessage}
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
				<span class="card-value mono" data-testid="card-details-number">{revealedCard.cardNumber}</span>
			</div>
			<div class="card-row">
				<span class="card-label">{m('payments.cardDetails.cvv')}</span>
				<span class="card-value mono" data-testid="card-details-cvv">{revealedCard.cvv}</span>
			</div>
			<div class="card-row">
				<span class="card-label">{m('payments.cardDetails.expires')}</span>
				<span class="card-value mono" data-testid="card-details-expires">{revealedCard.expires}</span>
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
		flex-direction: column;
		gap: 8px;
		padding: 10px 16px;
		background: var(--surface);
		border: 1px solid var(--accent);
		border-radius: 8px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
		z-index: 50;
		max-width: calc(100vw - 48px);
	}

	.pay-bar-row {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	/* Amber, not red: nothing has failed — the selection simply can't be
	   submitted as one run until it is narrowed to a single currency. */
	/* Same look as /discounts' `.disc-skipped` and the /cfo unconverted-outflows
	   caveat: an FX exclusion notice reads alike everywhere it appears. */
	.fx-skipped {
		font-size: 0.85rem;
		margin: 0 0 12px;
		color: #d4940a;
		font-weight: 600;
	}

	.pay-bar-warn {
		margin: 0;
		max-width: 56ch;
		font-size: 0.8rem;
		line-height: 1.35;
		color: #d4940a;
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

	.btn-pay:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-pay:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* A queue row the backend would refuse: readable, visibly inert, never
	   hidden — an operator has to be able to see WHAT is blocked to go clear it.
	   No `tbody` prefix: these rows render inside a `{#snippet}` handed to
	   `DataTable`, so a descendant selector is pruned as unused at compile time
	   (which is why the sibling `tbody tr.discount-eligible` rules never
	   applied). */
	.row-blocked {
		opacity: 0.72;
	}

	.blocked-chip {
		display: inline-block;
		margin-left: 6px;
		padding: 2px 8px;
		border-radius: 10px;
		background: var(--danger-tint);
		color: var(--danger-on-tint);
		font-size: 0.72rem;
		font-weight: 600;
		white-space: normal;
	}

	.blocked-banner {
		margin: 0;
		padding: 10px 14px;
		border: 1px solid var(--danger);
		border-radius: 6px;
		background: var(--danger-tint);
		color: var(--text);
		font-size: 0.85rem;
	}

	.review-error {
		margin: 0 0 10px;
		padding: 10px 12px;
		border: 1px solid var(--danger);
		border-radius: 6px;
		background: var(--danger-tint);
		color: var(--text);
		font-size: 0.85rem;
		line-height: 1.4;
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
