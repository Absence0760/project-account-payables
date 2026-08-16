<script lang="ts">
	import type {
		Expense,
		ExpenseReport,
		ExpenseReportSummary,
		ExpensePolicy,
		ExpensePreapproval,
		PolicyViolation,
		CorporateCardTransaction,
		CardMatchSuggestion
	} from '$lib/types/expense';
	import {
		EXPENSE_STATUSES,
		EXPENSE_STATUS_LABELS,
		EXPENSE_REPORT_STATUS_LABELS,
		EXPENSE_PREAPPROVAL_STATUSES,
		EXPENSE_PREAPPROVAL_STATUS_LABELS,
		RECONCILIATION_STATUSES,
		RECONCILIATION_STATUS_LABELS
	} from '$lib/types/expense';
	import { expenseStore } from '$lib/stores/expenses.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import {
		listGlAccounts,
		exportExpensesCsv,
		bulkGlCode,
		listExpenseReports,
		createExpenseReport,
		getExpenseReport,
		attachExpenses,
		expenseReportSummary,
		deleteExpense as apiDeleteExpense,
		listPolicies,
		deletePolicy as apiDeletePolicy,
		listPreapprovals,
		createPreapproval,
		approvePreapproval,
		rejectPreapproval,
		submitReport as apiSubmitReport,
		approveReport,
		rejectReport,
		listCardTransactions,
		importCardCsv,
		syncVirtualCards,
		cardMatchSuggestions,
		matchCardTxn,
		unmatchCardTxn,
		ignoreCardTxn,
		createExpenseFromCard,
		type GlAccountOption
	} from '$lib/api/expenses';
	import Modal from '$lib/components/ui/Modal.svelte';
	import PolicyModal from '$lib/components/modals/PolicyModal.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import BulkBar from '$lib/components/ui/BulkBar.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { formatDate } from '$lib/utils/time';
	import ExpenseModal from '$lib/components/modals/ExpenseModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { pruneSelection } from '$lib/utils/selection';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { m } from '$lib/i18n/store.svelte';

	const canCreate = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk'));
	// Policy CRUD + report/pre-approval approve/reject = admin | ap_manager.
	const canManagePolicies = $derived(auth.isManager);

	// --- Tabs ---
	type Tab = 'expenses' | 'reports' | 'policies' | 'preapprovals' | 'cards';
	let tab = $state<Tab>(($page.url.searchParams.get('tab') as Tab) ?? 'expenses');

	// --- Expenses tab filter state (URL-backed) ---
	let search = $state($page.url.searchParams.get('search') ?? '');
	let statusFilter = $state<string>($page.url.searchParams.get('status') ?? 'all');

	// --- Modal + selection state ---
	let showCreate = $state(false);
	let editing = $state<Expense | null>(null);
	let selected = $state<Set<string>>(new Set());
	let confirmDeleteId = $state<string | null>(null);

	let glAccounts = $state<GlAccountOption[]>([]);
	let bulkGl = $state('');
	let bulkBusy = $state(false);

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...EXPENSE_STATUSES.map((s) => ({ key: s, label: EXPENSE_STATUS_LABELS[s] }))
	]);

	const COLUMNS = $derived([
		{ label: '', class: 'checkbox-col' },
		{ label: m('expenses.col.date') },
		{ label: m('expenses.col.merchant') },
		{ label: m('expenses.col.category') },
		{ label: m('expenses.col.gl') },
		{ label: m('expenses.col.amount'), class: 'right' },
		{ label: m('expenses.col.status') },
		{ label: '', class: 'actions-col' }
	]);

	// Client-side search: the list endpoint filters by status server-side; the
	// merchant/category text search is applied to the loaded page here.
	const visibleExpenses = $derived.by(() => {
		const q = search.trim().toLowerCase();
		if (!q) return expenseStore.all;
		return expenseStore.all.filter(
			(e) =>
				(e.merchant ?? '').toLowerCase().includes(q) ||
				(e.category ?? '').toLowerCase().includes(q)
		);
	});

	// KPIs (period rollup uses the org default currency — mixed per-row
	// currencies have no single code, so a deterministic base is used).
	const periodTotal = $derived(
		expenseStore.all.reduce((sum, e) => sum + (Number.isFinite(e.amount) ? e.amount : 0), 0)
	);
	const pendingCount = $derived(
		expenseStore.all.filter((e) => e.status === 'draft' || e.status === 'submitted').length
	);

	function buildParams() {
		const params: { status?: string } = {};
		if (statusFilter !== 'all') params.status = statusFilter;
		return params;
	}

	// Read the URL untracked — syncUrl() writes it via replaceState inside a
	// filter $effect; a tracked $page.url read would self-trigger the effect
	// (Svelte effect_update_depth_exceeded loop).
	function syncUrl() {
		const url = new URL(untrack(() => $page.url));
		if (tab !== 'expenses') url.searchParams.set('tab', tab);
		else url.searchParams.delete('tab');
		if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
		else url.searchParams.delete('status');
		if (search.trim()) url.searchParams.set('search', search.trim());
		else url.searchParams.delete('search');
		// Pre-approval status filter only belongs in the URL while on that tab.
		if (tab === 'preapprovals' && preapprovalStatus !== 'all')
			url.searchParams.set('pa_status', preapprovalStatus);
		else url.searchParams.delete('pa_status');
		// Reconciliation status filter only belongs in the URL while on the cards tab.
		if (tab === 'cards' && reconFilter !== 'all') url.searchParams.set('recon', reconFilter);
		else url.searchParams.delete('recon');
		replaceState(`${url.pathname}${url.search}`, {});
	}

	// Status filter → server refetch (debounced search only re-syncs the URL,
	// search is client-side so no refetch needed).
	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => syncUrl(), 300);
	});

	$effect(() => {
		statusFilter;
		syncUrl();
		expenseStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method, routes through api client
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
		loadGlAccounts();
	});

	async function loadGlAccounts() {
		try {
			glAccounts = await listGlAccounts();
		} catch {
			/* non-critical for the list view */
		}
	}

	function glLabel(id: string | null): string {
		if (!id) return '—';
		const g = glAccounts.find((a) => a.id === id);
		return g ? g.code : '—';
	}

	// --- Selection ---
	// Keep the selection ⊆ the rows actually visible (status refetch OR the
	// client-side merchant/category search). Otherwise stale ids inflate the
	// bulk-bar count, break the select-all `size === length` comparison, and
	// feed invisible ids into the bulk GL re-code.
	$effect(() => {
		const pruned = pruneSelection(
			selected,
			visibleExpenses.map((e) => e.id)
		);
		if (pruned !== selected) selected = pruned;
	});

	function toggleSelect(id: string) {
		const next = new Set(selected);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selected = next;
	}

	function toggleSelectAll() {
		if (selected.size === visibleExpenses.length) {
			selected = new Set();
		} else {
			selected = new Set(visibleExpenses.map((e) => e.id));
		}
	}

	async function applyBulkGl() {
		if (selected.size === 0) return;
		bulkBusy = true;
		try {
			const res = await bulkGlCode([...selected], bulkGl || null);
			toast(m('expenses.toast.glCoded', { n: res.updated }), 'success');
			selected = new Set();
			bulkGl = '';
			await expenseStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method, routes through api client
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.toast.bulkGlFailed'), 'error');
		} finally {
			bulkBusy = false;
		}
	}

	// --- Per-row delete ---
	async function deleteExpense(id: string) {
		try {
			await apiDeleteExpense(id);
			expenseStore.remove(id);
			toast(m('expenses.toast.deleted'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.toast.deleteFailed'), 'error');
		} finally {
			confirmDeleteId = null;
		}
	}

	function onSaved(e: Expense) {
		expenseStore.upsert(e);
		if (editing && editing.id === e.id) editing = e;
	}

	// Outside-click un-arms any pending armed-confirm (expense delete, policy
	// delete, pre-approval reject) when the click lands outside a row action.
	function onWindowClick(e: MouseEvent) {
		const target = e.target as Element | null;
		if (!target?.closest('.row-action')) {
			if (confirmDeleteId) confirmDeleteId = null;
			if (confirmDeletePolicyId) confirmDeletePolicyId = null;
			if (paRejectArmedId) paRejectArmedId = null;
		}
	}

	// ============================ Reports tab ============================
	let reports = $state<ExpenseReport[]>([]);
	let reportsLoading = $state(false);
	let reportsTotal = $state(0);
	let activeReport = $state<ExpenseReport | null>(null);
	let activeSummary = $state<ExpenseReportSummary | null>(null);
	let showNewReport = $state(false);
	let newReportNumber = $state('');
	let newReportTitle = $state('');
	let reportBusy = $state(false);
	let attachId = $state('');

	// One sequencer per sub-list on this page (reports / policies / pre-approvals
	// / card transactions), never one shared counter: they are four independent
	// requests, and a shared counter would let a tab switch mark another list's
	// in-flight response un-committable and blank it. The expenses tab itself is
	// sequenced inside `expenseStore`. See `frontend/CLAUDE.md` § Sequencing list
	// fetches.
	//
	// Reports has no local-mutation helper — every submit/approve/reject/attach
	// re-fetches through `loadReports()` — so it needs no `supersedeInFlight()`;
	// the sequencer here only stops two of those refreshes landing out of order.
	const reportsSequence = createRequestSequencer();

	async function loadReports() {
		const token = reportsSequence.start();
		reportsLoading = true;
		try {
			const res = await listExpenseReports({ page_size: 50 });
			if (!reportsSequence.canCommit(token)) return;
			reports = res.items;
			reportsTotal = res.total;
		} finally {
			if (reportsSequence.isCurrentRequest(token)) reportsLoading = false;
		}
	}

	// Load reports whenever the Reports tab is active.
	$effect(() => {
		if (tab === 'reports') loadReports();
	});

	async function openReport(r: ExpenseReport) {
		try {
			activeReport = await getExpenseReport(r.id);
		} catch {
			activeReport = r;
		}
		await refreshSummary();
	}

	async function refreshSummary() {
		if (!activeReport) return;
		try {
			activeSummary = await expenseReportSummary(activeReport.id);
		} catch {
			activeSummary = null;
		}
	}

	function closeReport() {
		activeReport = null;
		activeSummary = null;
	}

	async function handleNewReport() {
		if (!newReportNumber.trim()) return;
		reportBusy = true;
		try {
			const created = await createExpenseReport({
				report_number: newReportNumber.trim(),
				title: newReportTitle.trim() || null,
				currency: orgCurrency.currency,
				notes: null
			});
			toast(m('expenses.reports.toast.created'), 'success');
			showNewReport = false;
			newReportNumber = '';
			newReportTitle = '';
			await loadReports();
			await openReport(created);
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.createFailed'), 'error');
		} finally {
			reportBusy = false;
		}
	}

	async function attachToReport() {
		if (!activeReport || !attachId.trim()) return;
		reportBusy = true;
		try {
			const updated = await attachExpenses(activeReport.id, [attachId.trim()], false);
			activeReport = updated;
			attachId = '';
			toast(m('expenses.reports.toast.attached'), 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.attachFailed'), 'error');
		} finally {
			reportBusy = false;
		}
	}

	async function detachFromReport(expenseId: string) {
		if (!activeReport) return;
		reportBusy = true;
		try {
			const updated = await attachExpenses(activeReport.id, [expenseId], true);
			activeReport = updated;
			toast(m('expenses.reports.toast.detached'), 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.detachFailed'), 'error');
		} finally {
			reportBusy = false;
		}
	}

	// Blocking policy violations returned by the WF3 submit route (422). Rendered
	// as an inline panel above the report detail; cleared on a clean submit.
	let submitViolations = $state<PolicyViolation[]>([]);

	async function submitReport() {
		if (!activeReport) return;
		reportBusy = true;
		submitViolations = [];
		try {
			// draft → submitted via the real WF3 transition. A 422 returns the
			// blocking policy-violation list (missing required receipt, absent
			// pre-approval) without transitioning — surfaced inline + as a toast.
			const result = await apiSubmitReport(activeReport.id);
			if (result.ok) {
				activeReport = result.report;
				toast(m('expenses.reports.toast.submitted'), 'success');
				await refreshSummary();
				await loadReports();
			} else {
				submitViolations = result.violations;
				toast(result.message || m('expenses.reports.toast.submitBlocked'), 'warning');
			}
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.submitFailed'), 'error');
		} finally {
			reportBusy = false;
		}
	}

	async function approveActiveReport() {
		if (!activeReport) return;
		reportBusy = true;
		try {
			const updated = await approveReport(activeReport.id);
			activeReport = updated;
			toast(m('expenses.reports.toast.approved'), 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.approveFailed'), 'error');
		} finally {
			reportBusy = false;
		}
	}

	let rejectReason = $state('');
	let showReject = $state(false);

	async function rejectActiveReport() {
		if (!activeReport) return;
		reportBusy = true;
		try {
			const updated = await rejectReport(activeReport.id, rejectReason.trim());
			activeReport = updated;
			showReject = false;
			rejectReason = '';
			toast(m('expenses.reports.toast.rejected'), 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.rejectFailed'), 'error');
		} finally {
			reportBusy = false;
		}
	}

	// True when the signed-in user can approve/reject this report: a manager who
	// is NOT the report owner (segregation of duties — approver != submitter).
	function canDecideReport(r: ExpenseReport): boolean {
		return canManagePolicies && r.status === 'submitted' && auth.user?.id !== r.employee_user_id;
	}

	function exportReportCsv() {
		if (!activeReport) return;
		exportExpensesCsv({ report_id: activeReport.id });
	}

	// --- Violation badge helpers (shared by expenses + report-detail rows) ---
	function violationTitle(list: PolicyViolation[]): string {
		return list.map((v) => v.message).join('; ');
	}

	function switchTab(next: Tab) {
		tab = next;
		closeReport();
		closeMatchPicker();
		showReject = false;
		submitViolations = [];
		syncUrl();
	}

	// ========================== Policies tab ==========================
	// A policy's thresholds are denominated in its own threshold_currency; an
	// unset one means the org's reporting currency (what the backend engine
	// falls back to), so the table must never render them in a different unit.
	function policyCurrency(p: ExpensePolicy): string {
		return p.threshold_currency ?? orgCurrency.currency;
	}

	let policies = $state<ExpensePolicy[]>([]);
	let policiesLoading = $state(false);
	let showPolicyCreate = $state(false);
	let editingPolicy = $state<ExpensePolicy | null>(null);
	let confirmDeletePolicyId = $state<string | null>(null);

	// `onPolicySaved` / `deletePolicy` edit the list in place with no fetch of
	// their own — and a newly created policy needs no pre-existing row, so it
	// races even the tab's first load.
	const policiesSequence = createRequestSequencer();

	async function loadPolicies() {
		const token = policiesSequence.start();
		policiesLoading = true;
		try {
			const loaded = await listPolicies();
			// Superseded by a newer load, or by a local create/edit/delete.
			if (!policiesSequence.canCommit(token)) return;
			policies = loaded;
		} catch (err) {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!policiesSequence.isCurrentRequest(token)) return;
			toast(err instanceof Error ? err.message : m('expenses.policies.toast.loadFailed'), 'error');
		} finally {
			if (policiesSequence.isCurrentRequest(token)) policiesLoading = false;
		}
	}

	$effect(() => {
		if (tab === 'policies') loadPolicies();
	});

	function onPolicySaved(p: ExpensePolicy) {
		policiesSequence.supersedeInFlight();
		const idx = policies.findIndex((x) => x.id === p.id);
		if (idx >= 0) policies = policies.map((x) => (x.id === p.id ? p : x));
		else policies = [p, ...policies];
	}

	async function deletePolicy(id: string) {
		try {
			await apiDeletePolicy(id);
			policiesSequence.supersedeInFlight();
			policies = policies.filter((p) => p.id !== id);
			toast(m('expenses.policies.toast.deleted'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.toast.deleteFailed'), 'error');
		} finally {
			confirmDeletePolicyId = null;
		}
	}

	// ======================== Pre-approvals tab ========================
	let preapprovals = $state<ExpensePreapproval[]>([]);
	let preapprovalsLoading = $state(false);
	let preapprovalStatus = $state<string>($page.url.searchParams.get('pa_status') ?? 'all');
	let showNewPreapproval = $state(false);
	let paBusy = $state(false);
	let paTitle = $state('');
	let paAmount = $state<number | null>(null);
	let paCategory = $state('');
	let paJustification = $state('');
	let paRejectArmedId = $state<string | null>(null);

	const PREAPPROVAL_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...EXPENSE_PREAPPROVAL_STATUSES.map((s) => ({
			key: s,
			label: EXPENSE_PREAPPROVAL_STATUS_LABELS[s]
		}))
	]);

	// `approvePa` / `rejectPa` rewrite a row in place with no fetch of their own.
	const preapprovalsSequence = createRequestSequencer();

	async function loadPreapprovals() {
		const token = preapprovalsSequence.start();
		preapprovalsLoading = true;
		try {
			const params = preapprovalStatus !== 'all' ? { status: preapprovalStatus } : {};
			const loaded = await listPreapprovals(params);
			// Superseded by a newer load, or by a local approve/reject.
			if (!preapprovalsSequence.canCommit(token)) return;
			preapprovals = loaded;
		} catch (err) {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!preapprovalsSequence.isCurrentRequest(token)) return;
			toast(err instanceof Error ? err.message : m('expenses.preapprovals.toast.loadFailed'), 'error');
		} finally {
			if (preapprovalsSequence.isCurrentRequest(token)) preapprovalsLoading = false;
		}
	}

	$effect(() => {
		if (tab === 'preapprovals') {
			preapprovalStatus;
			loadPreapprovals();
			syncUrl();
		}
	});

	function paNumOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	async function handleNewPreapproval() {
		if (!paTitle.trim() || paAmount == null) return;
		paBusy = true;
		try {
			await createPreapproval({
				title: paTitle.trim(),
				estimated_amount: paAmount,
				currency: orgCurrency.currency,
				category: paCategory.trim() || null,
				justification: paJustification.trim() || null
			});
			toast(m('expenses.preapprovals.toast.created'), 'success');
			showNewPreapproval = false;
			paTitle = '';
			paAmount = null;
			paCategory = '';
			paJustification = '';
			await loadPreapprovals();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.createFailed'), 'error');
		} finally {
			paBusy = false;
		}
	}

	function canDecidePreapproval(pa: ExpensePreapproval): boolean {
		return (
			canManagePolicies &&
			pa.status === 'pending' &&
			auth.user?.id !== pa.requester_user_id
		);
	}

	async function approvePa(pa: ExpensePreapproval) {
		try {
			const updated = await approvePreapproval(pa.id);
			preapprovalsSequence.supersedeInFlight();
			preapprovals = preapprovals.map((p) => (p.id === pa.id ? updated : p));
			toast(m('expenses.preapprovals.toast.approved'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.approveFailed'), 'error');
		}
	}

	async function rejectPa(pa: ExpensePreapproval) {
		try {
			const updated = await rejectPreapproval(pa.id);
			preapprovalsSequence.supersedeInFlight();
			preapprovals = preapprovals.map((p) => (p.id === pa.id ? updated : p));
			toast(m('expenses.preapprovals.toast.rejected'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.reports.toast.rejectFailed'), 'error');
		} finally {
			paRejectArmedId = null;
		}
	}

	// ============================ Cards tab (WF4) ============================
	let cardTxns = $state<CorporateCardTransaction[]>([]);
	let cardsLoading = $state(false);
	let cardsTotal = $state(0);
	let reconFilter = $state<string>($page.url.searchParams.get('recon') ?? 'all');
	let cardBusy = $state(false);
	let cardFileInput = $state<HTMLInputElement>();

	// Match-suggestion picker modal state.
	let matchTxn = $state<CorporateCardTransaction | null>(null);
	let matchSuggestions = $state<CardMatchSuggestion[]>([]);
	let matchLoading = $state(false);

	const RECON_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...RECONCILIATION_STATUSES.map((s) => ({ key: s, label: RECONCILIATION_STATUS_LABELS[s] }))
	]);

	const unmatchedCount = $derived(
		cardTxns.filter((t) => t.reconciliation_status === 'unmatched').length
	);
	const matchedCount = $derived(
		cardTxns.filter((t) => t.reconciliation_status === 'matched').length
	);

	// Like reports, the cards tab has no local-mutation helper — import / sync /
	// match / unmatch / ignore all re-fetch through `loadCardTxns()` — so it
	// needs no `supersedeInFlight()`; the sequencer stops a filter flip and a
	// post-mutation refresh landing out of order.
	const cardsSequence = createRequestSequencer();

	async function loadCardTxns() {
		const token = cardsSequence.start();
		cardsLoading = true;
		try {
			const params = reconFilter !== 'all' ? { reconciliation_status: reconFilter } : {};
			const res = await listCardTransactions({ ...params, page_size: 50 });
			if (!cardsSequence.canCommit(token)) return;
			cardTxns = res.items;
			cardsTotal = res.total;
		} catch (err) {
			if (!cardsSequence.isCurrentRequest(token)) return;
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.loadFailed'), 'error');
		} finally {
			if (cardsSequence.isCurrentRequest(token)) cardsLoading = false;
		}
	}

	// Load whenever the Cards tab is active or the recon filter changes (mirrors
	// the Reports / Pre-approvals tab effects).
	$effect(() => {
		if (tab === 'cards') {
			reconFilter;
			loadCardTxns();
			syncUrl();
		}
	});

	async function handleCardCsv(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		cardBusy = true;
		try {
			const result = await importCardCsv(file);
			toast(
				m('expenses.cards.toast.imported', { n: result.imported }) +
					(result.skipped
						? m('expenses.cards.toast.importedSkipped', { n: result.skipped })
						: ''),
				result.imported > 0 ? 'success' : 'info'
			);
			await loadCardTxns();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.importFailed'), 'error');
		} finally {
			cardBusy = false;
			input.value = ''; // allow re-pick of the same file
		}
	}

	async function handleSyncVirtualCards() {
		cardBusy = true;
		try {
			const res = await syncVirtualCards();
			toast(
				m('expenses.cards.toast.synced', { n: res.created }) +
					(res.skipped ? m('expenses.cards.toast.syncedSkipped', { n: res.skipped }) : ''),
				'success'
			);
			await loadCardTxns();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.syncFailed'), 'error');
		} finally {
			cardBusy = false;
		}
	}

	async function openMatchPicker(txn: CorporateCardTransaction) {
		matchTxn = txn;
		matchSuggestions = [];
		matchLoading = true;
		try {
			matchSuggestions = await cardMatchSuggestions(txn.id);
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.suggestionsFailed'), 'error');
		} finally {
			matchLoading = false;
		}
	}

	function closeMatchPicker() {
		matchTxn = null;
		matchSuggestions = [];
	}

	async function confirmMatch(expenseId: string) {
		if (!matchTxn) return;
		cardBusy = true;
		try {
			await matchCardTxn(matchTxn.id, expenseId);
			toast(m('expenses.cards.toast.matched'), 'success');
			closeMatchPicker();
			await loadCardTxns();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.matchFailed'), 'error');
		} finally {
			cardBusy = false;
		}
	}

	async function createExpenseForCard(txn: CorporateCardTransaction) {
		cardBusy = true;
		try {
			await createExpenseFromCard(txn.id);
			toast(m('expenses.cards.toast.expenseCreated'), 'success');
			await loadCardTxns();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.createExpenseFailed'), 'error');
		} finally {
			cardBusy = false;
		}
	}

	async function ignoreCard(txn: CorporateCardTransaction) {
		cardBusy = true;
		try {
			await ignoreCardTxn(txn.id);
			toast(m('expenses.cards.toast.ignored'), 'success');
			await loadCardTxns();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.ignoreFailed'), 'error');
		} finally {
			cardBusy = false;
		}
	}

	async function unmatchCard(txn: CorporateCardTransaction) {
		cardBusy = true;
		try {
			await unmatchCardTxn(txn.id);
			toast(m('expenses.cards.toast.unmatched'), 'success');
			await loadCardTxns();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('expenses.cards.toast.unmatchFailed'), 'error');
		} finally {
			cardBusy = false;
		}
	}
</script>

<svelte:window onclick={onWindowClick} />

<PageHeader title={m('expenses.title')}>
	{#snippet actions()}
		{#if tab === 'expenses'}
			<button class="btn-secondary" onclick={() => exportExpensesCsv(buildParams())}>{m('expenses.action.exportCsv')}</button>
			{#if canCreate}
				<button class="btn-primary" onclick={() => (showCreate = true)}>{m('expenses.action.newExpense')}</button>
			{/if}
		{:else if tab === 'reports'}
			{#if canCreate}
				<button class="btn-primary" onclick={() => (showNewReport = true)}>{m('expenses.action.newReport')}</button>
			{/if}
		{:else if tab === 'policies'}
			{#if canManagePolicies}
				<button class="btn-primary" onclick={() => (showPolicyCreate = true)}>{m('expenses.action.newPolicy')}</button>
			{/if}
		{:else if tab === 'preapprovals'}
			{#if canCreate}
				<button class="btn-primary" onclick={() => (showNewPreapproval = true)}>{m('expenses.action.newRequest')}</button>
			{/if}
		{:else if tab === 'cards'}
			{#if canManagePolicies}
				<input type="file" accept=".csv" bind:this={cardFileInput} onchange={handleCardCsv} hidden />
				<button class="btn-secondary" disabled={cardBusy} onclick={() => cardFileInput?.click()}>{m('expenses.action.importCsv')}</button>
				<button class="btn-secondary" disabled={cardBusy} onclick={handleSyncVirtualCards}>{m('expenses.action.syncCards')}</button>
			{/if}
		{/if}
	{/snippet}

	<div class="tab-row">
		<button class="tab" class:active={tab === 'expenses'} onclick={() => switchTab('expenses')}>{m('expenses.tab.expenses')}</button>
		<button class="tab" class:active={tab === 'reports'} onclick={() => switchTab('reports')}>{m('expenses.tab.reports')}</button>
		{#if canManagePolicies}
			<button class="tab" class:active={tab === 'policies'} onclick={() => switchTab('policies')}>{m('expenses.tab.policies')}</button>
		{/if}
		<button class="tab" class:active={tab === 'preapprovals'} onclick={() => switchTab('preapprovals')}>{m('expenses.tab.preapprovals')}</button>
		<button class="tab" class:active={tab === 'cards'} onclick={() => switchTab('cards')}>{m('expenses.tab.cards')}</button>
	</div>

	{#if tab === 'expenses'}
		<div class="kpi-row">
			<KpiCard value={formatMoney(periodTotal, { currency: orgCurrency.currency })} label={m('expenses.kpi.periodTotal')} />
			<KpiCard value={expenseStore.total} label={m('expenses.kpi.expenses')} />
			<KpiCard value={pendingCount} label={m('expenses.kpi.pending')} highlight={pendingCount ? 'red' : null} />
		</div>

		<div class="filter-row">
			<SearchBox bind:value={search} placeholder={m('expenses.search.placeholder')} ariaLabel={m('expenses.search.aria')} />
			<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
		</div>

		<DataTable
			columns={COLUMNS}
			isEmpty={visibleExpenses.length === 0}
			empty={expenseStore.loading ? m('expenses.loading') : m('expenses.empty')}
		>
			{#snippet header()}
				<tr>
					<th class="checkbox-col">
						<input
							type="checkbox"
							aria-label={m('expenses.selectAllAria')}
							checked={visibleExpenses.length > 0 && selected.size === visibleExpenses.length}
							onchange={toggleSelectAll}
						/>
					</th>
					<th>{m('expenses.col.date')}</th>
					<th>{m('expenses.col.merchant')}</th>
					<th>{m('expenses.col.category')}</th>
					<th>{m('expenses.col.method')}</th>
					<th class="right">{m('expenses.col.amount')}</th>
					<th>{m('expenses.col.status')}</th>
					<th class="actions-col"></th>
				</tr>
			{/snippet}
			{#snippet body()}
				{#each visibleExpenses as exp (exp.id)}
					<tr
						class="clickable"
						class:row-selected={selected.has(exp.id)}
						onclick={(e) => {
							if (isRowOpenClick(e)) editing = exp;
						}}
					>
						<td class="checkbox-col">
							<input
								type="checkbox"
								aria-label={m('expenses.selectAria', { name: exp.merchant ?? exp.id })}
								checked={selected.has(exp.id)}
								onchange={() => toggleSelect(exp.id)}
							/>
						</td>
						<td class="muted">{formatDate(exp.expense_date)}</td>
						<td>
							<RowLink onclick={() => (editing = exp)} ariaLabel={m('expenses.row.editAria', { name: exp.merchant ?? exp.id })}>
								{exp.merchant ?? '—'}
							</RowLink>
						</td>
						<td>{exp.category ?? '—'}</td>
						<td class="muted">{glLabel(exp.gl_account_id)}</td>
						<td class="right mono"><Money amount={exp.amount} currency={exp.currency} /></td>
						<td>
							<span class="badge {exp.status}">{EXPENSE_STATUS_LABELS[exp.status as keyof typeof EXPENSE_STATUS_LABELS] ?? exp.status}</span>
							{#if exp.policy_violations && exp.policy_violations.length}
								<span class="badge violation" title={violationTitle(exp.policy_violations)}>⚠ {exp.policy_violations.length}</span>
							{/if}
						</td>
						<td class="actions">
							{#if canCreate}
								<RowAction
									variant="danger"
									armed={confirmDeleteId === exp.id}
									onclick={(e) => {
										e.stopPropagation();
										if (confirmDeleteId === exp.id) deleteExpense(exp.id);
										else confirmDeleteId = exp.id;
									}}
								>
									{confirmDeleteId === exp.id ? m('expenses.row.confirm') : m('expenses.row.delete')}
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

		{#if expenseStore.hasMore}
			<div class="load-more-row">
				<button class="btn-load-more" onclick={() => expenseStore.loadMore()} disabled={expenseStore.loading}>
					{expenseStore.loading ? m('expenses.loading') : m('expenses.loadMore', { shown: expenseStore.all.length, total: expenseStore.total })}
				</button>
			</div>
		{:else if expenseStore.total > 0}
			<div class="load-more-row">
				<span class="load-more-end">{m('expenses.showingAll', { total: expenseStore.total })}</span>
			</div>
		{/if}
	{:else if tab === 'reports'}
		<!-- ===================== Reports tab ===================== -->
		{#if activeReport}
			<div class="report-detail">
				<div class="report-detail-head">
					<button class="btn-back" onclick={closeReport}>{m('expenses.reports.back')}</button>
					<div class="report-title-block">
						<h2>{activeReport.report_number}</h2>
						<span class="badge {activeReport.status}">{EXPENSE_REPORT_STATUS_LABELS[activeReport.status as keyof typeof EXPENSE_REPORT_STATUS_LABELS] ?? activeReport.status}</span>
					</div>
					<div class="report-detail-actions">
						<button class="btn-secondary" onclick={exportReportCsv}>{m('expenses.reports.exportCsv')}</button>
						{#if canCreate && activeReport.status === 'draft'}
							<button class="btn-primary" disabled={reportBusy} onclick={submitReport}>{m('expenses.reports.submit')}</button>
						{/if}
						{#if canDecideReport(activeReport)}
							<button class="btn-primary" disabled={reportBusy} onclick={approveActiveReport}>{m('expenses.reports.approve')}</button>
							<button class="btn-secondary danger" disabled={reportBusy} onclick={() => (showReject = true)}>{m('expenses.reports.reject')}</button>
						{/if}
					</div>
				</div>

				{#if submitViolations.length}
					<div class="violation-panel">
						<strong>{m('expenses.reports.submitBlocked')}</strong>
						<ul>
							{#each submitViolations as v (v.code + (v.policy_id ?? ''))}
								<li>{v.message}</li>
							{/each}
						</ul>
					</div>
				{/if}

				{#if showReject && activeReport}
					<div class="reject-row">
						<input
							type="text"
							placeholder={m('expenses.reports.rejectPlaceholder')}
							bind:value={rejectReason}
							aria-label={m('expenses.reports.rejectAria')}
						/>
						<button class="btn-secondary danger" disabled={reportBusy} onclick={rejectActiveReport}>{m('expenses.reports.confirmReject')}</button>
						<button class="btn-secondary" disabled={reportBusy} onclick={() => { showReject = false; rejectReason = ''; }}>{m('expenses.reports.cancel')}</button>
					</div>
				{/if}

				{#if activeSummary}
					<div class="kpi-row">
						<KpiCard value={formatMoney(activeSummary.total, { currency: activeSummary.currency })} label={m('expenses.reports.total')} />
						<KpiCard value={activeSummary.count} label={m('expenses.reports.expenses')} />
						<KpiCard value={activeSummary.by_category.length} label={m('expenses.reports.categories')} />
					</div>
					<!--
						The total sums each line's rate-locked conversion into the report
						currency. Lines with no usable rate are EXCLUDED, so the figure
						above would silently understate without this notice (issue #157).
					-->
					{#if activeSummary.unconverted_count > 0}
						<div class="unconverted-panel" role="alert">
							{m('expenses.reports.unconverted', {
								count: activeSummary.unconverted_count,
								currency: activeSummary.currency
							})}
						</div>
					{/if}
				{/if}

				{#if canCreate && activeReport.status === 'draft'}
					<div class="attach-row">
						<input
							type="text"
							placeholder={m('expenses.reports.attachPlaceholder')}
							bind:value={attachId}
							aria-label={m('expenses.reports.attachAria')}
						/>
						<button class="btn-secondary" disabled={reportBusy || !attachId.trim()} onclick={attachToReport}>{m('expenses.reports.attach')}</button>
					</div>
				{/if}

				<DataTable
					columns={[
						{ label: m('expenses.reports.col.date') },
						{ label: m('expenses.reports.col.merchant') },
						{ label: m('expenses.reports.col.category') },
						{ label: m('expenses.reports.col.amount'), class: 'right' },
						{ label: m('expenses.reports.col.status') },
						{ label: '', class: 'actions-col' }
					]}
					isEmpty={activeReport.expenses.length === 0}
					empty={m('expenses.reports.emptyExpenses')}
				>
					{#snippet body()}
						{#each activeReport?.expenses ?? [] as exp (exp.id)}
							<tr>
								<td class="muted">{formatDate(exp.expense_date)}</td>
								<td>{exp.merchant ?? '—'}</td>
								<td>{exp.category ?? '—'}</td>
								<td class="right mono"><Money amount={exp.amount} currency={exp.currency} /></td>
								<td>
									<span class="badge {exp.status}">{EXPENSE_STATUS_LABELS[exp.status as keyof typeof EXPENSE_STATUS_LABELS] ?? exp.status}</span>
									{#if exp.policy_violations && exp.policy_violations.length}
										<span class="badge violation" title={violationTitle(exp.policy_violations)}>⚠ {exp.policy_violations.length}</span>
									{/if}
								</td>
								<td class="actions">
									{#if canCreate && activeReport?.status === 'draft'}
										<RowAction variant="default" onclick={() => detachFromReport(exp.id)}>{m('expenses.reports.detach')}</RowAction>
									{/if}
								</td>
							</tr>
						{/each}
					{/snippet}
				</DataTable>
			</div>
		{:else}
			<DataTable
				columns={[
					{ label: m('expenses.reports.col.reportNumber') },
					{ label: m('expenses.reports.col.title') },
					{ label: m('expenses.reports.col.status') },
					{ label: m('expenses.reports.col.total'), class: 'right' }
				]}
				isEmpty={reports.length === 0}
				empty={reportsLoading ? m('expenses.loading') : m('expenses.reports.empty')}
			>
				{#snippet body()}
					{#each reports as r (r.id)}
						<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) openReport(r); }}>
							<td class="mono">
								<RowLink onclick={() => openReport(r)} ariaLabel={m('expenses.reports.openAria', { number: r.report_number })}>
									{r.report_number}
								</RowLink>
							</td>
							<td>{r.title ?? '—'}</td>
							<td><span class="badge {r.status}">{EXPENSE_REPORT_STATUS_LABELS[r.status as keyof typeof EXPENSE_REPORT_STATUS_LABELS] ?? r.status}</span></td>
							<td class="right mono"><Money amount={r.total_amount} currency={r.currency} /></td>
						</tr>
					{/each}
				{/snippet}
			</DataTable>
			{#if reportsTotal > 0}
				<div class="load-more-row">
					<span class="load-more-end">{m('expenses.reports.showingAll', { total: reportsTotal })}</span>
				</div>
			{/if}
		{/if}
	{:else if tab === 'policies'}
		<!-- ===================== Policies tab ===================== -->
		<DataTable
			columns={[
				{ label: m('expenses.policies.col.name') },
				{ label: m('expenses.policies.col.category') },
				{ label: m('expenses.policies.col.currency') },
				{ label: m('expenses.policies.col.limit'), class: 'right' },
				{ label: m('expenses.policies.col.receiptAbove'), class: 'right' },
				{ label: m('expenses.policies.col.preapprAbove'), class: 'right' },
				{ label: m('expenses.policies.col.active') },
				{ label: '', class: 'actions-col' }
			]}
			isEmpty={policies.length === 0}
			empty={policiesLoading ? m('expenses.loading') : m('expenses.policies.empty')}
		>
			{#snippet body()}
				{#each policies as p (p.id)}
					<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) editingPolicy = p; }}>
						<td>
							<RowLink onclick={() => (editingPolicy = p)} ariaLabel={m('expenses.policies.editAria', { name: p.name })}>
								{p.name}
							</RowLink>
						</td>
						<td>{p.category ?? m('expenses.policies.categoryAll')}</td>
						<td>{policyCurrency(p)}</td>
						<td class="right mono">{p.category_limit != null ? formatMoney(p.category_limit, { currency: policyCurrency(p) }) : '—'}</td>
						<td class="right mono">{p.requires_receipt_above != null ? formatMoney(p.requires_receipt_above, { currency: policyCurrency(p) }) : '—'}</td>
						<td class="right mono">{p.requires_preapproval_above != null ? formatMoney(p.requires_preapproval_above, { currency: policyCurrency(p) }) : '—'}</td>
						<td><span class="badge {p.active ? 'approved' : 'cancelled'}">{p.active ? m('expenses.policies.active') : m('expenses.policies.inactive')}</span></td>
						<td class="actions">
							{#if canManagePolicies}
								<RowAction
									variant="danger"
									armed={confirmDeletePolicyId === p.id}
									onclick={(e) => {
										e.stopPropagation();
										if (confirmDeletePolicyId === p.id) deletePolicy(p.id);
										else confirmDeletePolicyId = p.id;
									}}
								>
									{confirmDeletePolicyId === p.id ? m('expenses.row.confirm') : m('expenses.row.delete')}
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	{:else if tab === 'preapprovals'}
		<!-- ==================== Pre-approvals tab ==================== -->
		<div class="filter-row">
			<FilterChips chips={PREAPPROVAL_CHIPS} bind:active={preapprovalStatus} />
		</div>

		<DataTable
			columns={[
				{ label: m('expenses.preapprovals.col.title') },
				{ label: m('expenses.preapprovals.col.category') },
				{ label: m('expenses.preapprovals.col.estimated'), class: 'right' },
				{ label: m('expenses.preapprovals.col.status') },
				{ label: '', class: 'actions-col' }
			]}
			isEmpty={preapprovals.length === 0}
			empty={preapprovalsLoading ? m('expenses.loading') : m('expenses.preapprovals.empty')}
		>
			{#snippet body()}
				{#each preapprovals as pa (pa.id)}
					<tr>
						<td>{pa.title}</td>
						<td>{pa.category ?? '—'}</td>
						<td class="right mono"><Money amount={pa.estimated_amount} currency={pa.currency} /></td>
						<td><span class="badge {pa.status}">{EXPENSE_PREAPPROVAL_STATUS_LABELS[pa.status as keyof typeof EXPENSE_PREAPPROVAL_STATUS_LABELS] ?? pa.status}</span></td>
						<td class="actions">
							{#if canDecidePreapproval(pa)}
								<RowAction variant="success" onclick={() => approvePa(pa)}>{m('expenses.preapprovals.approve')}</RowAction>
								<RowAction
									variant="danger"
									armed={paRejectArmedId === pa.id}
									onclick={() => {
										if (paRejectArmedId === pa.id) rejectPa(pa);
										else paRejectArmedId = pa.id;
									}}
								>
									{paRejectArmedId === pa.id ? m('expenses.preapprovals.confirmReject') : m('expenses.preapprovals.reject')}
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	{:else if tab === 'cards'}
		<!-- ===================== Cards tab (WF4) ===================== -->
		<div class="kpi-row">
			<KpiCard value={unmatchedCount} label={m('expenses.cards.kpi.unmatched')} highlight={unmatchedCount ? 'red' : null} />
			<KpiCard value={matchedCount} label={m('expenses.cards.kpi.matched')} highlight={matchedCount ? 'green' : null} />
			<KpiCard value={cardsTotal} label={m('expenses.cards.kpi.transactions')} />
		</div>

		<div class="filter-row">
			<FilterChips chips={RECON_CHIPS} bind:active={reconFilter} />
		</div>

		<DataTable
			columns={[
				{ label: m('expenses.cards.col.date') },
				{ label: m('expenses.cards.col.merchant') },
				{ label: m('expenses.cards.col.card') },
				{ label: m('expenses.cards.col.amount'), class: 'right' },
				{ label: m('expenses.cards.col.status') },
				{ label: '', class: 'actions-col' }
			]}
			isEmpty={cardTxns.length === 0}
			empty={cardsLoading ? m('expenses.loading') : m('expenses.cards.empty')}
		>
			{#snippet body()}
				{#each cardTxns as txn (txn.id)}
					<tr>
						<td class="muted">{formatDate(txn.txn_date)}</td>
						<td>{txn.merchant ?? '—'}</td>
						<td class="muted">
							{#if txn.virtual_card_id}
								<span class="badge approved">{m('expenses.cards.virtual')}</span>
							{/if}
							{txn.card_last_four ? `•••• ${txn.card_last_four}` : '—'}
						</td>
						<td class="right mono"><Money amount={txn.amount} currency={txn.currency} /></td>
						<td>
							<span class="badge {txn.reconciliation_status}">
								{RECONCILIATION_STATUS_LABELS[
									txn.reconciliation_status as keyof typeof RECONCILIATION_STATUS_LABELS
								] ?? txn.reconciliation_status}
							</span>
						</td>
						<td class="actions">
							{#if canManagePolicies && txn.reconciliation_status === 'unmatched'}
								<RowAction variant="default" onclick={() => openMatchPicker(txn)}>{m('expenses.cards.match')}</RowAction>
							{/if}
							{#if canCreate && txn.reconciliation_status === 'unmatched'}
								<RowAction variant="default" onclick={() => createExpenseForCard(txn)}>{m('expenses.cards.createExpense')}</RowAction>
							{/if}
							{#if canManagePolicies && txn.reconciliation_status === 'matched'}
								<RowAction variant="default" onclick={() => unmatchCard(txn)}>{m('expenses.cards.unmatch')}</RowAction>
							{/if}
							{#if canManagePolicies && txn.reconciliation_status === 'unmatched'}
								<RowAction variant="default" onclick={() => ignoreCard(txn)}>{m('expenses.cards.ignore')}</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
		{#if cardsTotal > 0}
			<div class="load-more-row">
				<span class="load-more-end">{m('expenses.cards.showingAll', { total: cardsTotal })}</span>
			</div>
		{/if}
	{/if}
</PageHeader>

<!-- Bulk GL code bar (expenses tab only) -->
{#if tab === 'expenses'}
	<BulkBar count={selected.size} onclear={() => (selected = new Set())}>
		{#snippet actions()}
			<select class="bulk-gl-select" bind:value={bulkGl} aria-label={m('expenses.bulk.glAria')} disabled={bulkBusy}>
				<option value="">{m('expenses.bulk.clearGl')}</option>
				{#each glAccounts as g (g.id)}
					<option value={g.id}>{g.code} — {g.name}</option>
				{/each}
			</select>
			<button class="bulk-action-btn" disabled={bulkBusy} onclick={applyBulkGl}>
				{bulkBusy ? m('expenses.bulk.applying') : m('expenses.bulk.glCode', { n: selected.size })}
			</button>
		{/snippet}
	</BulkBar>
{/if}

{#if showCreate}
	<ExpenseModal expense={null} {glAccounts} onclose={() => (showCreate = false)} onsaved={onSaved} />
{/if}

{#if editing}
	<ExpenseModal expense={editing} {glAccounts} onclose={() => (editing = null)} onsaved={onSaved} />
{/if}

{#if showNewReport}
	<Modal open ariaLabel={m('expenses.newReport.aria')} title={m('expenses.newReport.title')} width="sm" onclose={() => (showNewReport = false)}>
		<form onsubmit={(e) => { e.preventDefault(); handleNewReport(); }}>
			<div class="report-form">
				<label>
					<span>{m('expenses.newReport.number')} <em class="required">*</em></span>
					<input type="text" bind:value={newReportNumber} required />
				</label>
				<label>
					<span>{m('expenses.newReport.titleField')}</span>
					<input type="text" bind:value={newReportTitle} />
				</label>
			</div>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (showNewReport = false)}>{m('expenses.newReport.cancel')}</button>
				<button type="submit" class="btn-primary" disabled={reportBusy || !newReportNumber.trim()}>
					{reportBusy ? m('expenses.newReport.creating') : m('expenses.newReport.create')}
				</button>
			</div>
		</form>
	</Modal>
{/if}

{#if showPolicyCreate}
	<PolicyModal policy={null} onclose={() => (showPolicyCreate = false)} onsaved={onPolicySaved} />
{/if}

{#if editingPolicy}
	<PolicyModal policy={editingPolicy} onclose={() => (editingPolicy = null)} onsaved={onPolicySaved} />
{/if}

{#if showNewPreapproval}
	<Modal open ariaLabel={m('expenses.newPreapproval.aria')} title={m('expenses.newPreapproval.title')} width="sm" onclose={() => (showNewPreapproval = false)}>
		<form onsubmit={(e) => { e.preventDefault(); handleNewPreapproval(); }}>
			<div class="report-form">
				<label>
					<span>{m('expenses.newPreapproval.titleField')} <em class="required">*</em></span>
					<input type="text" bind:value={paTitle} required />
				</label>
				<label>
					<span>{m('expenses.newPreapproval.estimated')} <em class="required">*</em></span>
					<input
						type="number"
						step="0.01"
						min="0"
						value={paAmount ?? ''}
						oninput={(e) => (paAmount = paNumOrNull(e.currentTarget.value))}
					/>
				</label>
				<label>
					<span>{m('expenses.newPreapproval.category')}</span>
					<input type="text" bind:value={paCategory} placeholder={m('expenses.newPreapproval.categoryPlaceholder')} />
				</label>
				<label>
					<span>{m('expenses.newPreapproval.justification')}</span>
					<textarea bind:value={paJustification} rows="2"></textarea>
				</label>
			</div>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (showNewPreapproval = false)}>{m('expenses.newReport.cancel')}</button>
				<button type="submit" class="btn-primary" disabled={paBusy || !paTitle.trim() || paAmount == null}>
					{paBusy ? m('expenses.newReport.creating') : m('expenses.newReport.create')}
				</button>
			</div>
		</form>
	</Modal>
{/if}

{#if matchTxn}
	<Modal open ariaLabel={m('expenses.match.aria')} title={m('expenses.match.title')} width="sm" onclose={closeMatchPicker}>
		<div class="match-picker">
			{#if matchLoading}
				<p class="muted">{m('expenses.match.loading')}</p>
			{:else if matchSuggestions.length === 0}
				<p class="muted">{m('expenses.match.empty')}</p>
			{:else}
				<ul class="match-list">
					{#each matchSuggestions as suggestion (suggestion.expense.id)}
						<li>
							<button
								type="button"
								class="match-row"
								disabled={cardBusy}
								onclick={() => confirmMatch(suggestion.expense.id)}
							>
								<span>{suggestion.expense.merchant ?? '—'} · {formatDate(suggestion.expense.expense_date)}</span>
								<span class="mono"><Money amount={suggestion.expense.amount} currency={suggestion.expense.currency} /></span>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={closeMatchPicker}>{m('expenses.match.cancel')}</button>
		</div>
	</Modal>
{/if}

<style>
	.tab-row {
		display: flex;
		gap: 4px;
		border-bottom: 1px solid var(--border);
	}
	.tab {
		padding: 8px 16px;
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.active {
		color: var(--accent);
		border-bottom-color: var(--accent);
	}

	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.btn-secondary {
		padding: 8px 14px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.88rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}
	.btn-secondary:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
	}
	.badge.draft { background: rgba(99, 140, 255, 0.12); color: #638cff; }
	.badge.submitted { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	.badge.pending_approval { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	.badge.approved { background: rgba(31, 168, 106, 0.12); color: #1fa86a; }
	.badge.rejected { background: rgba(224, 64, 64, 0.12); color: #e04040; }
	.badge.reimbursed { background: rgba(140, 100, 240, 0.12); color: #8c64f0; }
	.badge.cancelled { background: var(--bg); color: var(--text-muted); }
	.badge.pending { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	/* Card reconciliation statuses (WF4). */
	.badge.matched { background: rgba(31, 168, 106, 0.12); color: #1fa86a; }
	.badge.unmatched { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	.badge.ignored { background: var(--bg); color: var(--text-muted); }
	.badge.violation {
		margin-left: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
		cursor: help;
	}

	.bulk-gl-select {
		/* base look (border/colour/font/chevron) from the global select recipe */
		padding: 6px 30px 6px 10px;
		border-radius: 6px;
		background-color: var(--surface);
		font-size: 0.82rem;
	}

	/* --- Reports --- */
	.report-detail {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.report-detail-head {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.report-title-block {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.report-title-block h2 {
		margin: 0;
		font-size: 1.1rem;
	}
	.report-detail-actions {
		margin-left: auto;
		display: flex;
		gap: 8px;
	}
	.btn-back {
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
		padding: 4px 0;
	}
	.btn-back:hover {
		color: var(--accent);
	}
	.attach-row,
	.reject-row {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}
	.attach-row input,
	.reject-row input {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.85rem;
		min-width: 280px;
	}

	/* Danger variant of the secondary button (report reject). */
	.btn-secondary.danger:hover {
		border-color: #e04040;
		color: #e04040;
	}

	/* Inline blocking-violation panel above the report detail. */
	.violation-panel {
		border: 1px solid #e04040;
		background: rgba(224, 64, 64, 0.06);
		border-radius: 8px;
		padding: 12px 14px;
		color: var(--text);
		font-size: 0.85rem;
	}
	.violation-panel ul {
		margin: 8px 0 0;
		padding-left: 18px;
	}
	.violation-panel li {
		margin: 2px 0;
		color: #e04040;
	}

	/* Partial-total notice: some lines lack an FX lock and were excluded. */
	.unconverted-panel {
		margin-top: 10px;
		border: 1px solid #c98a00;
		background: rgba(201, 138, 0, 0.08);
		border-radius: 8px;
		padding: 10px 14px;
		color: var(--text);
		font-size: 0.85rem;
	}

	/* --- New-report mini modal --- */
	.report-form {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin: 12px 0;
	}
	.report-form label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.82rem;
		color: var(--text-muted);
	}
	.report-form input,
	.report-form textarea {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
	}

	/* --- Card match-suggestion picker (WF4) --- */
	.match-picker {
		margin: 12px 0;
	}
	.match-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.match-row {
		width: 100%;
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 12px;
		padding: 8px 10px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.85rem;
		cursor: pointer;
	}
	.match-row:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.match-row:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
