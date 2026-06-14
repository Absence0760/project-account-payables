<script lang="ts">
	import type {
		Expense,
		ExpenseReport,
		ExpenseReportSummary,
		ExpensePolicy,
		ExpensePreapproval,
		PolicyViolation
	} from '$lib/types/expense';
	import {
		EXPENSE_STATUSES,
		EXPENSE_STATUS_LABELS,
		EXPENSE_REPORT_STATUS_LABELS,
		EXPENSE_PREAPPROVAL_STATUSES,
		EXPENSE_PREAPPROVAL_STATUS_LABELS
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
	import ExpenseModal from '$lib/components/modals/ExpenseModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';

	const canCreate = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk'));
	// Policy CRUD + report/pre-approval approve/reject = admin | ap_manager.
	const canManagePolicies = $derived(auth.isManager);

	// --- Tabs ---
	type Tab = 'expenses' | 'reports' | 'policies' | 'preapprovals';
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

	const STATUS_CHIPS = [
		{ key: 'all', label: 'All' },
		...EXPENSE_STATUSES.map((s) => ({ key: s, label: EXPENSE_STATUS_LABELS[s] }))
	];

	const COLUMNS = [
		{ label: '', class: 'checkbox-col' },
		{ label: 'Date' },
		{ label: 'Merchant' },
		{ label: 'Category' },
		{ label: 'GL' },
		{ label: 'Amount', class: 'right' },
		{ label: 'Status' },
		{ label: '', class: 'actions-col' }
	];

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

	function syncUrl() {
		const url = new URL($page.url);
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
			toast(`GL coded ${res.updated} expense${res.updated === 1 ? '' : 's'}`, 'success');
			selected = new Set();
			bulkGl = '';
			await expenseStore.fetch(buildParams()); // noqa: raw-fetch-in-component — store method, routes through api client
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Bulk GL code failed', 'error');
		} finally {
			bulkBusy = false;
		}
	}

	// --- Per-row delete ---
	async function deleteExpense(id: string) {
		try {
			await apiDeleteExpense(id);
			expenseStore.remove(id);
			toast('Expense deleted', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Delete failed', 'error');
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

	function formatDate(s: string | null): string {
		if (!s) return '—';
		return new Date(s).toLocaleDateString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric'
		});
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

	async function loadReports() {
		reportsLoading = true;
		try {
			const res = await listExpenseReports({ page_size: 50 });
			reports = res.items;
			reportsTotal = res.total;
		} finally {
			reportsLoading = false;
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
			toast('Report created', 'success');
			showNewReport = false;
			newReportNumber = '';
			newReportTitle = '';
			await loadReports();
			await openReport(created);
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Create failed', 'error');
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
			toast('Expense attached', 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Attach failed', 'error');
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
			toast('Expense detached', 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Detach failed', 'error');
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
				toast('Report submitted', 'success');
				await refreshSummary();
				await loadReports();
			} else {
				submitViolations = result.violations;
				toast(result.message || 'Submit blocked by policy.', 'warning');
			}
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Submit failed', 'error');
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
			toast('Report approved', 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Approve failed', 'error');
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
			toast('Report rejected', 'success');
			await refreshSummary();
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Reject failed', 'error');
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
		showReject = false;
		submitViolations = [];
		syncUrl();
	}

	// ========================== Policies tab ==========================
	let policies = $state<ExpensePolicy[]>([]);
	let policiesLoading = $state(false);
	let showPolicyCreate = $state(false);
	let editingPolicy = $state<ExpensePolicy | null>(null);
	let confirmDeletePolicyId = $state<string | null>(null);

	async function loadPolicies() {
		policiesLoading = true;
		try {
			policies = await listPolicies();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load policies', 'error');
		} finally {
			policiesLoading = false;
		}
	}

	$effect(() => {
		if (tab === 'policies') loadPolicies();
	});

	function onPolicySaved(p: ExpensePolicy) {
		const idx = policies.findIndex((x) => x.id === p.id);
		if (idx >= 0) policies = policies.map((x) => (x.id === p.id ? p : x));
		else policies = [p, ...policies];
	}

	async function deletePolicy(id: string) {
		try {
			await apiDeletePolicy(id);
			policies = policies.filter((p) => p.id !== id);
			toast('Policy deleted', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Delete failed', 'error');
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

	const PREAPPROVAL_CHIPS = [
		{ key: 'all', label: 'All' },
		...EXPENSE_PREAPPROVAL_STATUSES.map((s) => ({
			key: s,
			label: EXPENSE_PREAPPROVAL_STATUS_LABELS[s]
		}))
	];

	async function loadPreapprovals() {
		preapprovalsLoading = true;
		try {
			const params = preapprovalStatus !== 'all' ? { status: preapprovalStatus } : {};
			preapprovals = await listPreapprovals(params);
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load pre-approvals', 'error');
		} finally {
			preapprovalsLoading = false;
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
			toast('Pre-approval request created', 'success');
			showNewPreapproval = false;
			paTitle = '';
			paAmount = null;
			paCategory = '';
			paJustification = '';
			await loadPreapprovals();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Create failed', 'error');
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
			preapprovals = preapprovals.map((p) => (p.id === pa.id ? updated : p));
			toast('Pre-approval approved', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Approve failed', 'error');
		}
	}

	async function rejectPa(pa: ExpensePreapproval) {
		try {
			const updated = await rejectPreapproval(pa.id);
			preapprovals = preapprovals.map((p) => (p.id === pa.id ? updated : p));
			toast('Pre-approval rejected', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Reject failed', 'error');
		} finally {
			paRejectArmedId = null;
		}
	}
</script>

<svelte:window onclick={onWindowClick} />

<PageHeader title="Expenses">
	{#snippet actions()}
		{#if tab === 'expenses'}
			<button class="btn-secondary" onclick={() => exportExpensesCsv(buildParams())}>Export CSV</button>
			{#if canCreate}
				<button class="btn-primary" onclick={() => (showCreate = true)}>+ New Expense</button>
			{/if}
		{:else if tab === 'reports'}
			{#if canCreate}
				<button class="btn-primary" onclick={() => (showNewReport = true)}>+ New Report</button>
			{/if}
		{:else if tab === 'policies'}
			{#if canManagePolicies}
				<button class="btn-primary" onclick={() => (showPolicyCreate = true)}>+ New Policy</button>
			{/if}
		{:else if tab === 'preapprovals'}
			{#if canCreate}
				<button class="btn-primary" onclick={() => (showNewPreapproval = true)}>+ New Request</button>
			{/if}
		{/if}
	{/snippet}

	<div class="tab-row">
		<button class="tab" class:active={tab === 'expenses'} onclick={() => switchTab('expenses')}>Expenses</button>
		<button class="tab" class:active={tab === 'reports'} onclick={() => switchTab('reports')}>Reports</button>
		{#if canManagePolicies}
			<button class="tab" class:active={tab === 'policies'} onclick={() => switchTab('policies')}>Policies</button>
		{/if}
		<button class="tab" class:active={tab === 'preapprovals'} onclick={() => switchTab('preapprovals')}>Pre-approvals</button>
	</div>

	{#if tab === 'expenses'}
		<div class="kpi-row">
			<KpiCard value={formatMoney(periodTotal, { currency: orgCurrency.currency })} label="Period total" />
			<KpiCard value={expenseStore.total} label="Expenses" />
			<KpiCard value={pendingCount} label="Pending" highlight={pendingCount ? 'red' : null} />
		</div>

		<div class="filter-row">
			<SearchBox bind:value={search} placeholder="Search expenses..." ariaLabel="Search expenses" />
			<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
		</div>

		<DataTable
			columns={COLUMNS}
			isEmpty={visibleExpenses.length === 0}
			empty={expenseStore.loading ? 'Loading…' : 'No expenses match your filters.'}
		>
			{#snippet header()}
				<tr>
					<th class="checkbox-col">
						<input
							type="checkbox"
							aria-label="Select all expenses"
							checked={visibleExpenses.length > 0 && selected.size === visibleExpenses.length}
							onchange={toggleSelectAll}
						/>
					</th>
					<th>Date</th>
					<th>Merchant</th>
					<th>Category</th>
					<th>Method</th>
					<th class="right">Amount</th>
					<th>Status</th>
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
								aria-label={`Select expense ${exp.merchant ?? exp.id}`}
								checked={selected.has(exp.id)}
								onchange={() => toggleSelect(exp.id)}
							/>
						</td>
						<td class="muted">{formatDate(exp.expense_date)}</td>
						<td>
							<RowLink onclick={() => (editing = exp)} ariaLabel={`Edit expense ${exp.merchant ?? exp.id}`}>
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
									{confirmDeleteId === exp.id ? 'Confirm' : 'Delete'}
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
					{expenseStore.loading ? 'Loading…' : `Load more (${expenseStore.all.length} of ${expenseStore.total})`}
				</button>
			</div>
		{:else if expenseStore.total > 0}
			<div class="load-more-row">
				<span class="load-more-end">Showing all {expenseStore.total} expense{expenseStore.total === 1 ? '' : 's'}</span>
			</div>
		{/if}
	{:else if tab === 'reports'}
		<!-- ===================== Reports tab ===================== -->
		{#if activeReport}
			<div class="report-detail">
				<div class="report-detail-head">
					<button class="btn-back" onclick={closeReport}>← All reports</button>
					<div class="report-title-block">
						<h2>{activeReport.report_number}</h2>
						<span class="badge {activeReport.status}">{EXPENSE_REPORT_STATUS_LABELS[activeReport.status as keyof typeof EXPENSE_REPORT_STATUS_LABELS] ?? activeReport.status}</span>
					</div>
					<div class="report-detail-actions">
						<button class="btn-secondary" onclick={exportReportCsv}>Export CSV</button>
						{#if canCreate && activeReport.status === 'draft'}
							<button class="btn-primary" disabled={reportBusy} onclick={submitReport}>Submit</button>
						{/if}
						{#if canDecideReport(activeReport)}
							<button class="btn-primary" disabled={reportBusy} onclick={approveActiveReport}>Approve</button>
							<button class="btn-secondary danger" disabled={reportBusy} onclick={() => (showReject = true)}>Reject</button>
						{/if}
					</div>
				</div>

				{#if submitViolations.length}
					<div class="violation-panel">
						<strong>Submit blocked — resolve these policy violations:</strong>
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
							placeholder="Reason for rejection (optional)"
							bind:value={rejectReason}
							aria-label="Rejection reason"
						/>
						<button class="btn-secondary danger" disabled={reportBusy} onclick={rejectActiveReport}>Confirm reject</button>
						<button class="btn-secondary" disabled={reportBusy} onclick={() => { showReject = false; rejectReason = ''; }}>Cancel</button>
					</div>
				{/if}

				{#if activeSummary}
					<div class="kpi-row">
						<KpiCard value={formatMoney(activeSummary.total, { currency: activeReport.currency })} label="Total" />
						<KpiCard value={activeSummary.count} label="Expenses" />
						<KpiCard value={activeSummary.by_category.length} label="Categories" />
					</div>
				{/if}

				{#if canCreate && activeReport.status === 'draft'}
					<div class="attach-row">
						<input
							type="text"
							placeholder="Expense ID to attach"
							bind:value={attachId}
							aria-label="Expense ID to attach"
						/>
						<button class="btn-secondary" disabled={reportBusy || !attachId.trim()} onclick={attachToReport}>Attach</button>
					</div>
				{/if}

				<DataTable
					columns={[
						{ label: 'Date' },
						{ label: 'Merchant' },
						{ label: 'Category' },
						{ label: 'Amount', class: 'right' },
						{ label: 'Status' },
						{ label: '', class: 'actions-col' }
					]}
					isEmpty={activeReport.expenses.length === 0}
					empty="No expenses on this report."
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
										<RowAction variant="default" onclick={() => detachFromReport(exp.id)}>Detach</RowAction>
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
					{ label: 'Report #' },
					{ label: 'Title' },
					{ label: 'Status' },
					{ label: 'Total', class: 'right' }
				]}
				isEmpty={reports.length === 0}
				empty={reportsLoading ? 'Loading…' : 'No expense reports yet.'}
			>
				{#snippet body()}
					{#each reports as r (r.id)}
						<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) openReport(r); }}>
							<td class="mono">
								<RowLink onclick={() => openReport(r)} ariaLabel={`Open report ${r.report_number}`}>
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
					<span class="load-more-end">Showing all {reportsTotal} report{reportsTotal === 1 ? '' : 's'}</span>
				</div>
			{/if}
		{/if}
	{:else if tab === 'policies'}
		<!-- ===================== Policies tab ===================== -->
		<DataTable
			columns={[
				{ label: 'Name' },
				{ label: 'Category' },
				{ label: 'Limit', class: 'right' },
				{ label: 'Receipt >', class: 'right' },
				{ label: 'Pre-appr >', class: 'right' },
				{ label: 'Active' },
				{ label: '', class: 'actions-col' }
			]}
			isEmpty={policies.length === 0}
			empty={policiesLoading ? 'Loading…' : 'No expense policies yet.'}
		>
			{#snippet body()}
				{#each policies as p (p.id)}
					<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) editingPolicy = p; }}>
						<td>
							<RowLink onclick={() => (editingPolicy = p)} ariaLabel={`Edit policy ${p.name}`}>
								{p.name}
							</RowLink>
						</td>
						<td>{p.category ?? 'All'}</td>
						<td class="right mono">{p.category_limit != null ? formatMoney(p.category_limit, { currency: orgCurrency.currency }) : '—'}</td>
						<td class="right mono">{p.requires_receipt_above != null ? formatMoney(p.requires_receipt_above, { currency: orgCurrency.currency }) : '—'}</td>
						<td class="right mono">{p.requires_preapproval_above != null ? formatMoney(p.requires_preapproval_above, { currency: orgCurrency.currency }) : '—'}</td>
						<td><span class="badge {p.active ? 'approved' : 'cancelled'}">{p.active ? 'Active' : 'Inactive'}</span></td>
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
									{confirmDeletePolicyId === p.id ? 'Confirm' : 'Delete'}
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	{:else}
		<!-- ==================== Pre-approvals tab ==================== -->
		<div class="filter-row">
			<FilterChips chips={PREAPPROVAL_CHIPS} bind:active={preapprovalStatus} />
		</div>

		<DataTable
			columns={[
				{ label: 'Title' },
				{ label: 'Category' },
				{ label: 'Estimated', class: 'right' },
				{ label: 'Status' },
				{ label: '', class: 'actions-col' }
			]}
			isEmpty={preapprovals.length === 0}
			empty={preapprovalsLoading ? 'Loading…' : 'No pre-approval requests.'}
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
								<RowAction variant="success" onclick={() => approvePa(pa)}>Approve</RowAction>
								<RowAction
									variant="danger"
									armed={paRejectArmedId === pa.id}
									onclick={() => {
										if (paRejectArmedId === pa.id) rejectPa(pa);
										else paRejectArmedId = pa.id;
									}}
								>
									{paRejectArmedId === pa.id ? 'Confirm reject' : 'Reject'}
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	{/if}
</PageHeader>

<!-- Bulk GL code bar (expenses tab only) -->
{#if tab === 'expenses'}
	<BulkBar count={selected.size} onclear={() => (selected = new Set())}>
		{#snippet actions()}
			<select class="bulk-gl-select" bind:value={bulkGl} aria-label="GL account for bulk recode" disabled={bulkBusy}>
				<option value="">Clear GL</option>
				{#each glAccounts as g (g.id)}
					<option value={g.id}>{g.code} — {g.name}</option>
				{/each}
			</select>
			<button class="bulk-action-btn" disabled={bulkBusy} onclick={applyBulkGl}>
				{bulkBusy ? 'Applying…' : `GL code ${selected.size}`}
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
	<Modal open ariaLabel="New report" title="New Expense Report" width="sm" onclose={() => (showNewReport = false)}>
		<form onsubmit={(e) => { e.preventDefault(); handleNewReport(); }}>
			<div class="report-form">
				<label>
					<span>Report Number <em class="required">*</em></span>
					<input type="text" bind:value={newReportNumber} required />
				</label>
				<label>
					<span>Title</span>
					<input type="text" bind:value={newReportTitle} />
				</label>
			</div>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (showNewReport = false)}>Cancel</button>
				<button type="submit" class="btn-primary" disabled={reportBusy || !newReportNumber.trim()}>
					{reportBusy ? 'Creating…' : 'Create'}
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
	<Modal open ariaLabel="New pre-approval" title="New Pre-approval Request" width="sm" onclose={() => (showNewPreapproval = false)}>
		<form onsubmit={(e) => { e.preventDefault(); handleNewPreapproval(); }}>
			<div class="report-form">
				<label>
					<span>Title <em class="required">*</em></span>
					<input type="text" bind:value={paTitle} required />
				</label>
				<label>
					<span>Estimated Amount <em class="required">*</em></span>
					<input
						type="number"
						step="0.01"
						min="0"
						value={paAmount ?? ''}
						oninput={(e) => (paAmount = paNumOrNull(e.currentTarget.value))}
					/>
				</label>
				<label>
					<span>Category</span>
					<input type="text" bind:value={paCategory} placeholder="e.g. travel" />
				</label>
				<label>
					<span>Justification</span>
					<textarea bind:value={paJustification} rows="2"></textarea>
				</label>
			</div>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (showNewPreapproval = false)}>Cancel</button>
				<button type="submit" class="btn-primary" disabled={paBusy || !paTitle.trim() || paAmount == null}>
					{paBusy ? 'Creating…' : 'Create'}
				</button>
			</div>
		</form>
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
	.badge.violation {
		margin-left: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
		cursor: help;
	}

	.bulk-gl-select {
		padding: 6px 10px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-family: inherit;
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
</style>
