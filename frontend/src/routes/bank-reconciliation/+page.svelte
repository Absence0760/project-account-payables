<script lang="ts">
	/**
	 * /bank-reconciliation — did every payment we think we made actually clear
	 * the bank, and is every debit the bank shows one of ours?
	 *
	 * Two tabs, because the surface answers two different questions:
	 *
	 *   - **Outstanding** (the default, and the reason the page exists) is the
	 *     month-end worksheet computed across every statement we have ever
	 *     imported: `uncleared_payments` (we sent it, nothing claims it),
	 *     `unmatched_debits` (money left the account with nothing of ours
	 *     behind it) and `discrepancies` (identified, but it does not
	 *     reconcile). A payment appears in exactly ONE bucket, so nothing a
	 *     reviewer needs can hide between them.
	 *   - **Statements** is the per-file view, and the way into the transaction
	 *     table where a match is confirmed or cleared.
	 *
	 * NOT entity-scoped: `BankStatement` / `BankTransaction` predate the
	 * multi-entity work and cover an org-wide bank account, so this page
	 * deliberately has no entity filter — adding one would imply a scope the
	 * data does not carry.
	 *
	 * RBAC mirrors `api/bank_reconciliation.py`: read is all four roles, every
	 * mutate control is `auth.isManager` (admin | ap_manager — treasury-
	 * adjacent, clerks excluded, the same split as Positive Pay). A clerk sees
	 * every figure and no button; the backend refuses regardless.
	 */
	import type {
		BankStatement,
		Discrepancy,
		UnclearedPayment,
		UnmatchedDebit
	} from '$lib/types/bankReconciliation';
	import { isTruncated } from '$lib/types/bankReconciliation';
	import {
		deleteBankStatement,
		getBankStatement,
		getOutstandingItems,
		listBankStatements
	} from '$lib/api/bankReconciliation';
	import type { OutstandingItems } from '$lib/types/bankReconciliation';
	import Badge from '$lib/components/ui/Badge.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import Tabs from '$lib/components/ui/Tabs.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import ImportStatementModal from './ImportStatementModal.svelte';
	import StatementDetailModal from './StatementDetailModal.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { formatDate } from '$lib/utils/time';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';

	const canMutate = $derived(auth.isManager);

	const PAGE_SIZE = 20;
	/**
	 * Row cap for `/outstanding`. Counts and totals on that response are
	 * whole-set regardless, so this only bounds what is rendered — and the page
	 * says so when it bites (`isTruncated`) rather than quietly showing less
	 * than the KPI above it claims.
	 */
	const OUTSTANDING_ROW_LIMIT = 500;

	// --- URL-backed view state -------------------------------------------
	const VALID_TABS = ['outstanding', 'statements'];
	const VALID_AGES = [0, 7, 30, 60];

	function initialTab(): string {
		const t = $page.url.searchParams.get('tab') ?? '';
		return VALID_TABS.includes(t) ? t : 'outstanding';
	}
	function initialAge(): number {
		const raw = Number($page.url.searchParams.get('older_than_days'));
		return VALID_AGES.includes(raw) ? raw : 0;
	}

	let tab = $state(initialTab());
	let olderThanDays = $state(initialAge());
	let search = $state($page.url.searchParams.get('search') ?? '');

	/**
	 * Reflect the live view state into the URL. EVERY read here is untracked:
	 * `syncUrl` is a WRITER called from the `$effect`s below, not a source of
	 * dependencies — reading `$page.url` tracked would self-trigger the effect
	 * that writes it via `replaceState`, and reading `search` tracked would
	 * make the age/tab effects fire on every keystroke.
	 */
	function syncUrl() {
		untrack(() => {
			const url = new URL($page.url);
			if (tab !== 'outstanding') url.searchParams.set('tab', tab);
			else url.searchParams.delete('tab');
			if (olderThanDays > 0) url.searchParams.set('older_than_days', String(olderThanDays));
			else url.searchParams.delete('older_than_days');
			if (search.trim()) url.searchParams.set('search', search.trim());
			else url.searchParams.delete('search');
			replaceState(`${url.pathname}${url.search}`, {});
		});
	}

	// --- Outstanding ------------------------------------------------------

	let outstanding = $state<OutstandingItems | null>(null);
	let outstandingLoading = $state(true);
	let outstandingError = $state(false);
	const outstandingSequence = createRequestSequencer();

	async function loadOutstanding() {
		const token = outstandingSequence.start();
		outstandingLoading = true;
		outstandingError = false;
		try {
			const res = await getOutstandingItems({
				older_than_days: untrack(() => olderThanDays),
				limit: OUTSTANDING_ROW_LIMIT
			});
			if (!outstandingSequence.canCommit(token)) return;
			outstanding = res;
		} catch {
			if (!outstandingSequence.isCurrentRequest(token)) return;
			outstanding = null;
			outstandingError = true;
		} finally {
			if (outstandingSequence.isCurrentRequest(token)) outstandingLoading = false;
		}
	}

	// The age filter is a SERVER filter (`?older_than_days=`) — re-fetch on
	// change. `search` is not: `/outstanding` offers no text filter, and its
	// three buckets arrive whole (up to the row cap) in ONE response, so
	// narrowing them in the browser is complete for what is loaded. The
	// truncation notice below is what keeps that honest.
	$effect(() => {
		olderThanDays;
		syncUrl();
		void loadOutstanding();
	});

	$effect(() => {
		tab;
		syncUrl();
	});

	// The search term only re-renders derived lists, so it needs no debounce —
	// but the URL write does, or every keystroke pushes a history entry.
	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(syncUrl, 300);
		// Cancel on teardown: without it the timer fires after the page is gone,
		// running replaceState against a route the user already left.
		return () => clearTimeout(searchTimer);
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
	});

	const term = $derived(search.trim().toLowerCase());

	function matches(fields: (string | null | undefined)[]): boolean {
		if (!term) return true;
		return fields.some((f) => !!f && f.toLowerCase().includes(term));
	}

	const unclearedRows = $derived(
		(outstanding?.uncleared_payments ?? []).filter((p: UnclearedPayment) =>
			matches([p.vendor_name, p.invoice_number, p.method, p.status])
		)
	);
	const unmatchedRows = $derived(
		(outstanding?.unmatched_debits ?? []).filter((d: UnmatchedDebit) =>
			matches([d.counterparty_name, d.reference, d.description, d.account_identifier])
		)
	);
	const discrepancyRows = $derived(
		(outstanding?.discrepancies ?? []).filter((d: Discrepancy) =>
			matches([d.counterparty_name, d.invoice_number, d.classification, d.account_identifier])
		)
	);

	const AGE_CHIPS = $derived([
		{ key: '0', label: m('bankRecon.age.any') },
		...[7, 30, 60].map((d) => ({ key: String(d), label: m('bankRecon.age.days', { days: d }) }))
	]);
	let ageChip = $state(String(initialAge()));
	$effect(() => {
		const next = Number(ageChip);
		if (VALID_AGES.includes(next)) olderThanDays = next;
	});

	const DISCREPANCY_LABELS: Record<string, string> = $derived({
		amount_mismatch: m('bankRecon.method.amount_mismatch'),
		currency_mismatch: m('bankRecon.method.currency_mismatch'),
		status_conflict: m('bankRecon.method.status_conflict')
	});

	const UNCLEARED_COLUMNS = $derived([
		{ label: m('bankRecon.col.vendor') },
		{ label: m('bankRecon.col.invoice') },
		{ label: m('bankRecon.col.amount'), class: 'right' },
		{ label: m('bankRecon.col.method') },
		{ label: m('bankRecon.col.status') },
		{ label: m('bankRecon.col.sent') },
		{ label: m('bankRecon.col.age'), class: 'right' }
	]);

	const UNMATCHED_COLUMNS = $derived([
		{ label: m('bankRecon.col.date') },
		{ label: m('bankRecon.col.account') },
		{ label: m('bankRecon.col.counterparty') },
		{ label: m('bankRecon.col.reference') },
		{ label: m('bankRecon.col.amount'), class: 'right' }
	]);

	const DISCREPANCY_COLUMNS = $derived([
		{ label: m('bankRecon.col.date') },
		{ label: m('bankRecon.col.counterparty') },
		{ label: m('bankRecon.col.invoice') },
		{ label: m('bankRecon.col.issue') },
		{ label: m('bankRecon.col.bankAmount'), class: 'right' },
		{ label: m('bankRecon.col.ourAmount'), class: 'right' },
		{ label: m('bankRecon.col.variance'), class: 'right' }
	]);

	// --- Statements -------------------------------------------------------

	let statements = $state<BankStatement[]>([]);
	let statementTotal = $state(0);
	let statementPage = $state(1);
	let statementsLoading = $state(true);
	let statementsLoadingMore = $state(false);
	let statementsError = $state(false);
	const statementSequence = createRequestSequencer();

	const hasMoreStatements = $derived(statements.length < statementTotal);

	async function loadStatements(opts: { append?: boolean } = {}) {
		const nextPage = opts.append ? statementPage + 1 : 1;
		const token = statementSequence.start();
		if (opts.append) statementsLoadingMore = true;
		else {
			statementsLoading = true;
			statementsError = false;
		}
		try {
			const data = await listBankStatements({ page: nextPage, page_size: PAGE_SIZE });
			if (!statementSequence.canCommit(token)) return;
			statements = opts.append ? appendUnique(statements, data.items) : data.items;
			statementTotal = data.total;
			statementPage = nextPage;
		} catch {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!statementSequence.isCurrentRequest(token)) return;
			if (!opts.append) {
				statements = [];
				statementsError = true;
			}
		} finally {
			if (statementSequence.isCurrentRequest(token)) {
				statementsLoading = false;
				statementsLoadingMore = false;
			}
		}
	}

	$effect(() => {
		void loadStatements();
	});

	const STATEMENT_COLUMNS = $derived([
		{ label: m('bankRecon.col.account') },
		{ label: m('bankRecon.col.period') },
		{ label: m('bankRecon.col.lines'), class: 'right' },
		{ label: m('bankRecon.col.reconciled'), class: 'right' },
		{ label: m('bankRecon.kpi.discrepancies'), class: 'right' },
		{ label: m('bankRecon.col.imported') },
		{ class: 'actions-col' }
	]);

	function statementLabel(s: BankStatement): string {
		return `${s.account_identifier} · ${formatDate(s.period_end)}`;
	}

	// --- Detail + import modals -------------------------------------------

	let detail = $state<BankStatement | null>(null);
	let showImport = $state(false);

	/** Loads the full statement (the list omits transactions) and opens it.
	 *  Takes an ID rather than a row so an outstanding-bucket row — which only
	 *  carries its `statement_id` — can open the same modal without a cast
	 *  inventing a half-built `BankStatement`. */
	async function openDetail(statementId: string) {
		try {
			detail = await getBankStatement(statementId);
		} catch {
			toast(m('bankRecon.toast.notFound'), 'error');
		}
	}

	// Deep-link: `/bank-reconciliation?id=<uuid>` opens that statement.
	let deepLinkLoaded = $state<string | null>(null);
	$effect(() => {
		const id = $page.url.searchParams.get('id');
		if (!id || deepLinkLoaded === id) return;
		deepLinkLoaded = id;
		getBankStatement(id)
			.then((s) => (detail = s))
			.catch(() => toast(m('bankRecon.toast.notFound'), 'error'));
	});

	function closeModals() {
		detail = null;
		showImport = false;
		const url = new URL($page.url);
		if (url.searchParams.has('id')) {
			url.searchParams.delete('id');
			replaceState(`${url.pathname}${url.search}`, {});
			deepLinkLoaded = null;
		}
	}

	function upsertStatement(s: BankStatement) {
		// Retire every load issued before this statement existed — their
		// responses predate it and would drop it back out of the list.
		statementSequence.supersedeInFlight();
		const idx = statements.findIndex((x) => x.id === s.id);
		if (idx === -1) {
			statements = [s, ...statements];
			statementTotal += 1;
		} else {
			statements = statements.map((x) => (x.id === s.id ? s : x));
		}
	}

	function onImported(s: BankStatement) {
		upsertStatement(s);
		showImport = false;
		detail = s;
		tab = 'statements';
		// A new statement claims payments and can raise discrepancies — the
		// outstanding worksheet is a server figure, so re-fetch it.
		void loadOutstanding();
	}

	function onStatementChanged(s: BankStatement) {
		upsertStatement(s);
		// A resolve moves a payment between outstanding buckets.
		void loadOutstanding();
	}

	// --- Delete (armed two-click confirm) ---------------------------------
	let busyId = $state<string | null>(null);
	let confirmDeleteId = $state<string | null>(null);

	async function deleteStatement(s: BankStatement) {
		if (confirmDeleteId !== s.id) {
			confirmDeleteId = s.id;
			return;
		}
		confirmDeleteId = null;
		busyId = s.id;
		try {
			await deleteBankStatement(s.id);
			statementSequence.supersedeInFlight();
			statements = statements.filter((x) => x.id !== s.id);
			statementTotal = Math.max(0, statementTotal - 1);
			void loadOutstanding();
			toast(m('bankRecon.toast.deleted'), 'success');
		} catch (e) {
			toast(e instanceof Error ? e.message : m('bankRecon.toast.deleteFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	const TABS = $derived([
		{ key: 'outstanding', label: m('bankRecon.tab.outstanding') },
		{ key: 'statements', label: m('bankRecon.tab.statements'), count: statementTotal }
	]);
</script>

<svelte:window
	onclick={(e) => {
		if (confirmDeleteId && !(e.target as HTMLElement)?.closest?.('.row-action')) {
			confirmDeleteId = null;
		}
	}}
/>

<PageHeader title={m('bankRecon.title')}>
	{#snippet actions()}
		{#if canMutate}
			<button class="btn-primary" onclick={() => (showImport = true)}>
				{m('bankRecon.action.import')}
			</button>
		{/if}
	{/snippet}

	<!-- KPI row — every figure is the WHOLE-SET count/total from
	     `/outstanding`, never a reduce over the rendered rows. -->
	<div class="kpi-row">
		<!-- Uncleared is deliberately NOT tinted: payments in transit are the
		     normal state of a period, not an alarm. The two beside it are —
		     money left the account we can't account for, and money that left
		     differently from how it was authorised. -->
		<KpiCard value={outstanding?.uncleared_count ?? 0} label={m('bankRecon.kpi.uncleared')} />
		<KpiCard
			value={outstanding?.unmatched_debit_count ?? 0}
			label={m('bankRecon.kpi.unmatched')}
			highlight={(outstanding?.unmatched_debit_count ?? 0) > 0 ? 'red' : null}
		/>
		<KpiCard
			value={outstanding?.discrepancy_count ?? 0}
			label={m('bankRecon.kpi.discrepancies')}
			highlight={(outstanding?.discrepancy_count ?? 0) > 0 ? 'red' : null}
		/>
	</div>

	<Tabs tabs={TABS} bind:active={tab} ariaLabel={m('bankRecon.title')} idPrefix="bank-recon" />

	{#if tab === 'outstanding'}
		<div
			id="bank-recon-panel-outstanding"
			role="tabpanel"
			aria-labelledby="bank-recon-tab-outstanding"
		>
			<div class="filter-row">
				<SearchBox
					bind:value={search}
					placeholder={m('bankRecon.search.placeholder')}
					ariaLabel={m('bankRecon.search.aria')}
				/>
				<FilterChips chips={AGE_CHIPS} bind:active={ageChip} />
				{#if outstanding}
					<span class="as-of muted">{m('bankRecon.asOf', { date: formatDate(outstanding.as_of) })}</span>
				{/if}
			</div>

			{#if outstandingLoading}
				<p class="state-note muted" data-testid="outstanding-loading">{m('common.loading')}</p>
			{:else if outstandingError}
				<p class="state-note" role="alert" data-testid="outstanding-error">
					{m('bankRecon.error.load')}
					<button class="link-btn" onclick={() => loadOutstanding()}>
						{m('bankRecon.error.retry')}
					</button>
				</p>
			{:else if outstanding}
				<!-- Bucket 1 — we sent it; no bank line claims it. -->
				<section class="bucket">
					<header class="bucket-head">
						<h2>{m('bankRecon.section.uncleared')}</h2>
						<span class="bucket-total">
							<Money
								amount={outstanding.uncleared_total}
								currency={orgCurrency.currency}
								mono
							/>
						</span>
					</header>
					<p class="bucket-help muted">{m('bankRecon.section.unclearedHelp')}</p>
					<DataTable
						columns={UNCLEARED_COLUMNS}
						isEmpty={unclearedRows.length === 0}
						empty={term ? m('bankRecon.empty.filtered') : m('bankRecon.empty.uncleared')}
						colspan={7}
					>
						{#snippet body()}
							{#each unclearedRows as p (p.payment_id)}
								<tr>
									<td>{p.vendor_name ?? '—'}</td>
									<td class="muted">{p.invoice_number ?? '—'}</td>
									<td class="right">
										<!-- `UnclearedPaymentResponse` carries no per-row currency
										     (`unmatched_debits` and `discrepancies` both do), so this
										     falls back to the org reporting currency — as does
										     `uncleared_total`, which the backend sums across
										     currencies. A multi-currency tenant can therefore see the
										     wrong symbol on THIS bucket only. The durable fix is a
										     `currency` field on that schema (the invoice's, the same
										     pair the matcher compares against); reported as a
										     follow-up rather than guessed at here. -->
										<Money amount={p.amount} currency={orgCurrency.currency} mono />
									</td>
									<td class="muted">{p.method ?? '—'}</td>
									<td class="muted">{p.status}</td>
									<td class="muted">{formatDate(p.sent_on)}</td>
									<td class="right mono muted">{p.days_outstanding ?? '—'}</td>
								</tr>
							{/each}
						{/snippet}
					</DataTable>
					{#if isTruncated(outstanding.uncleared_payments.length, outstanding.uncleared_count)}
						<p class="truncated muted">
							{m('bankRecon.truncated', {
								shown: outstanding.uncleared_payments.length,
								total: outstanding.uncleared_count
							})}
						</p>
					{/if}
				</section>

				<!-- Bucket 2 — money left the account with nothing of ours behind it. -->
				<section class="bucket">
					<header class="bucket-head">
						<h2>{m('bankRecon.section.unmatched')}</h2>
						<span class="bucket-total">
							<Money
								amount={outstanding.unmatched_debit_total}
								currency={orgCurrency.currency}
								mono
							/>
						</span>
					</header>
					<p class="bucket-help muted">{m('bankRecon.section.unmatchedHelp')}</p>
					<DataTable
						columns={UNMATCHED_COLUMNS}
						isEmpty={unmatchedRows.length === 0}
						empty={term ? m('bankRecon.empty.filtered') : m('bankRecon.empty.unmatched')}
						colspan={5}
					>
						{#snippet body()}
							{#each unmatchedRows as d (d.transaction_id)}
								<tr
									class="clickable"
									onclick={(e) => {
										if (isRowOpenClick(e)) openDetail(d.statement_id);
									}}
								>
									<td class="muted">{formatDate(d.transaction_date)}</td>
									<td>
										<RowLink
											onclick={() => openDetail(d.statement_id)}
											ariaLabel={m('bankRecon.row.open', { label: d.account_identifier })}
										>
											{d.account_identifier}
										</RowLink>
									</td>
									<td>{d.counterparty_name ?? d.description ?? '—'}</td>
									<td class="mono muted">{d.reference ?? '—'}</td>
									<td class="right"><Money amount={d.amount} currency={d.currency} mono /></td>
								</tr>
							{/each}
						{/snippet}
					</DataTable>
					{#if isTruncated(
						outstanding.unmatched_debits.length,
						outstanding.unmatched_debit_count
					)}
						<p class="truncated muted">
							{m('bankRecon.truncated', {
								shown: outstanding.unmatched_debits.length,
								total: outstanding.unmatched_debit_count
							})}
						</p>
					{/if}
				</section>

				<!-- Bucket 3 — identified, but it does not reconcile. -->
				<section class="bucket">
					<header class="bucket-head">
						<h2>{m('bankRecon.section.discrepancies')}</h2>
						<span class="bucket-total">
							{m('bankRecon.kpi.netVariance')}:
							<Money
								amount={outstanding.amount_mismatch_net_variance}
								currency={orgCurrency.currency}
								mono
							/>
						</span>
					</header>
					<p class="bucket-help muted">{m('bankRecon.section.discrepanciesHelp')}</p>
					<DataTable
						columns={DISCREPANCY_COLUMNS}
						isEmpty={discrepancyRows.length === 0}
						empty={term ? m('bankRecon.empty.filtered') : m('bankRecon.empty.discrepancies')}
						colspan={7}
					>
						{#snippet body()}
							{#each discrepancyRows as d (d.transaction_id)}
								<tr
									class="clickable"
									onclick={(e) => {
										if (isRowOpenClick(e)) openDetail(d.statement_id);
									}}
								>
									<td class="muted">{formatDate(d.transaction_date)}</td>
									<td>
										<RowLink
											onclick={() => openDetail(d.statement_id)}
											ariaLabel={m('bankRecon.row.open', { label: d.account_identifier })}
										>
											{d.counterparty_name ?? d.account_identifier}
										</RowLink>
									</td>
									<td class="muted">{d.invoice_number ?? '—'}</td>
									<td>
										<Badge tone="danger" variant={d.classification}>
											{DISCREPANCY_LABELS[d.classification] ?? d.classification}
										</Badge>
										{#if d.payment_status}
											<span class="pay-status muted">{d.payment_status}</span>
										{/if}
									</td>
									<td class="right"><Money amount={d.bank_amount} currency={d.bank_currency} mono /></td>
									<td class="right">
										<Money amount={d.payment_amount} currency={d.payment_currency} mono />
									</td>
									<td class="right">
										{#if d.variance_amount !== null && d.variance_amount !== undefined}
											<Money amount={d.variance_amount} currency={d.bank_currency} mono />
										{:else}
											<span class="muted">—</span>
										{/if}
									</td>
								</tr>
							{/each}
						{/snippet}
					</DataTable>
					{#if isTruncated(outstanding.discrepancies.length, outstanding.discrepancy_count)}
						<p class="truncated muted">
							{m('bankRecon.truncated', {
								shown: outstanding.discrepancies.length,
								total: outstanding.discrepancy_count
							})}
						</p>
					{/if}
				</section>
			{/if}
		</div>
	{:else}
		<div
			id="bank-recon-panel-statements"
			role="tabpanel"
			aria-labelledby="bank-recon-tab-statements"
		>
			{#if statementsError}
				<p class="state-note" role="alert" data-testid="statements-error">
					{m('bankRecon.error.statements')}
					<button class="link-btn" onclick={() => loadStatements()}>
						{m('bankRecon.error.retry')}
					</button>
				</p>
			{:else if !statementsLoading && statements.length === 0}
				<EmptyState
					icon="🏦"
					heading={m('bankRecon.empty.statements')}
					description={m('bankRecon.empty.statementsDesc')}
					actionLabel={canMutate ? m('bankRecon.action.import') : undefined}
					onaction={canMutate ? () => (showImport = true) : undefined}
					testId="statements-empty"
				/>
			{:else}
				<DataTable
					columns={STATEMENT_COLUMNS}
					isEmpty={statementsLoading && statements.length === 0}
					empty={m('common.loading')}
					colspan={7}
				>
					{#snippet body()}
						{#each statements as s (s.id)}
							<tr
								class="clickable"
								onclick={(e) => {
									if (isRowOpenClick(e)) openDetail(s.id);
								}}
							>
								<td>
									<RowLink
										onclick={() => openDetail(s.id)}
										ariaLabel={m('bankRecon.row.open', { label: statementLabel(s) })}
									>
										{s.account_identifier}
									</RowLink>
								</td>
								<td class="muted">
									{formatDate(s.period_start)} – {formatDate(s.period_end)}
								</td>
								<td class="right mono">{s.transaction_count}</td>
								<td class="right mono">{s.matched_count}</td>
								<td class="right">
									{#if s.discrepancy_count > 0}
										<Badge tone="danger" variant="has-discrepancies">
											{s.discrepancy_count}
										</Badge>
									{:else}
										<span class="muted">0</span>
									{/if}
								</td>
								<td class="muted">{formatDate(s.imported_at)}</td>
								<td class="actions">
									{#if canMutate}
										<RowAction
											variant="danger"
											armed={confirmDeleteId === s.id}
											disabled={busyId === s.id}
											onclick={() => deleteStatement(s)}
											ariaLabel={m('bankRecon.row.deleteAria', { label: statementLabel(s) })}
										>
											{confirmDeleteId === s.id
												? m('bankRecon.row.confirmDelete')
												: m('bankRecon.row.delete')}
										</RowAction>
									{/if}
								</td>
							</tr>
						{/each}
					{/snippet}
				</DataTable>

				{#if hasMoreStatements}
					<div class="load-more-row">
						<button
							class="btn-load-more"
							onclick={() => loadStatements({ append: true })}
							disabled={statementsLoadingMore}
						>
							{statementsLoadingMore
								? m('common.loading')
								: m('bankRecon.loadMore', { shown: statements.length, total: statementTotal })}
						</button>
					</div>
				{:else if statementTotal > 0}
					<div class="load-more-row">
						<span class="load-more-end">
							{m('bankRecon.showingAll', { total: statementTotal })}
						</span>
					</div>
				{/if}
			{/if}
		</div>
	{/if}
</PageHeader>

{#if showImport}
	<ImportStatementModal onclose={closeModals} onimported={onImported} />
{/if}

{#if detail}
	<StatementDetailModal statement={detail} onclose={closeModals} onchanged={onStatementChanged} />
{/if}

<style>
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.as-of {
		font-size: 0.8rem;
	}
	.bucket {
		margin-top: 24px;
	}
	.bucket-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 12px;
		flex-wrap: wrap;
	}
	.bucket-head h2 {
		margin: 0;
		font-size: 1rem;
		color: var(--text);
	}
	.bucket-total {
		font-size: 0.85rem;
		color: var(--text-muted);
	}
	.bucket-help {
		margin: 2px 0 10px;
		font-size: 0.8rem;
		line-height: 1.45;
	}
	.state-note {
		margin: 20px 0;
		font-size: 0.9rem;
	}
	.truncated {
		margin: 6px 0 0;
		font-size: 0.78rem;
	}
	.pay-status {
		margin-left: 6px;
		font-size: 0.75rem;
	}
	.muted {
		color: var(--text-muted);
	}
	.mono {
		font-variant-numeric: tabular-nums;
		font-family: var(--font-mono);
	}
	.link-btn {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent-on-tint);
		text-decoration: underline;
		cursor: pointer;
		font: inherit;
	}
</style>
