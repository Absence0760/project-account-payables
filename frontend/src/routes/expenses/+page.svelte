<script lang="ts">
	import type { Expense, ExpenseReport, ExpenseReportSummary } from '$lib/types/expense';
	import {
		EXPENSE_STATUSES,
		EXPENSE_STATUS_LABELS,
		EXPENSE_REPORT_STATUS_LABELS
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
		type GlAccountOption
	} from '$lib/api/expenses';
	import { api } from '$lib/api';
	import Modal from '$lib/components/ui/Modal.svelte';
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

	// --- Tabs ---
	type Tab = 'expenses' | 'reports';
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

	// Outside-click un-arms a pending delete confirm.
	function onWindowClick(e: MouseEvent) {
		const target = e.target as Element | null;
		if (confirmDeleteId && !target?.closest('.row-action')) confirmDeleteId = null;
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

	async function submitReport() {
		if (!activeReport) return;
		reportBusy = true;
		try {
			// draft → submitted. We send the status on the report PATCH and then
			// VERIFY the server actually applied it: the WF2 report-update schema
			// may ignore an out-of-scope field, so trusting the request alone could
			// surface a false "submitted" on a still-draft report. We confirm
			// against the persisted record before claiming success.
			await api.patch<ExpenseReport>(`/api/expense-reports/${activeReport.id}`, {
				status: 'submitted'
			});
			const fresh = await getExpenseReport(activeReport.id);
			activeReport = fresh;
			if (fresh.status === 'submitted') {
				toast('Report submitted', 'success');
			} else {
				toast('Submitting reports is not available yet.', 'warning');
			}
			await loadReports();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Submit failed', 'error');
		} finally {
			reportBusy = false;
		}
	}

	function exportReportCsv() {
		if (!activeReport) return;
		exportExpensesCsv({ report_id: activeReport.id });
	}

	function switchTab(next: Tab) {
		tab = next;
		closeReport();
		syncUrl();
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
		{:else if canCreate}
			<button class="btn-primary" onclick={() => (showNewReport = true)}>+ New Report</button>
		{/if}
	{/snippet}

	<div class="tab-row">
		<button class="tab" class:active={tab === 'expenses'} onclick={() => switchTab('expenses')}>Expenses</button>
		<button class="tab" class:active={tab === 'reports'} onclick={() => switchTab('reports')}>Reports</button>
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
						<td><span class="badge {exp.status}">{EXPENSE_STATUS_LABELS[exp.status as keyof typeof EXPENSE_STATUS_LABELS] ?? exp.status}</span></td>
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
	{:else}
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
					</div>
				</div>

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
								<td><span class="badge {exp.status}">{EXPENSE_STATUS_LABELS[exp.status as keyof typeof EXPENSE_STATUS_LABELS] ?? exp.status}</span></td>
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
	.attach-row {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.attach-row input {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.85rem;
		min-width: 280px;
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
	.report-form input {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
	}
</style>
