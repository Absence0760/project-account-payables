<script lang="ts">
	import type { Requisition } from '$lib/types/requisition';
	import { REQUISITION_STATUSES, REQUISITION_STATUS_LABELS } from '$lib/types/requisition';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import {
		listRequisitions,
		getRequisition,
		deleteRequisition as apiDelete,
		submitRequisition,
		approveRequisition,
		rejectRequisition,
		cancelRequisition,
		convertRequisitionToPo
	} from '$lib/api/requisitions';
	import { listGlAccounts, type GlAccountOption } from '$lib/api/expenses';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { formatMoney } from '$lib/utils/money';
	import RequisitionModal from '$lib/components/modals/RequisitionModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { m } from '$lib/i18n/store.svelte';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { onMount, untrack } from 'svelte';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { formatDate } from '$lib/utils/time';

	const canCreate = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk'));
	// approve / reject / convert = admin | ap_manager (convert is the money step).
	const canApprove = $derived(auth.isManager);

	let requisitions = $state<Requisition[]>([]);
	let total = $state(0);
	let loading = $state(false);

	let search = $state($page.url.searchParams.get('search') ?? '');
	let statusFilter = $state<string>($page.url.searchParams.get('status') ?? 'all');

	let showCreate = $state(false);
	let editing = $state<Requisition | null>(null);
	let confirmDeleteId = $state<string | null>(null);
	let busyId = $state<string | null>(null);
	let glAccounts = $state<GlAccountOption[]>([]);

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...REQUISITION_STATUSES.map((s) => ({ key: s, label: REQUISITION_STATUS_LABELS[s] }))
	]);

	const COLUMNS = $derived([
		{ label: m('requisitions.col.requisitionNumber') },
		{ label: m('requisitions.col.title') },
		{ label: m('requisitions.col.department') },
		{ label: m('requisitions.col.neededBy') },
		{ label: m('requisitions.col.total'), class: 'right' },
		{ label: m('requisitions.col.status') },
		{ label: '', class: 'actions-col' }
	]);

	// Client-side text search over the loaded page (number / title / department).
	const visible = $derived.by(() => {
		const q = search.trim().toLowerCase();
		if (!q) return requisitions;
		return requisitions.filter(
			(r) =>
				r.requisition_number.toLowerCase().includes(q) ||
				(r.title ?? '').toLowerCase().includes(q) ||
				(r.department ?? '').toLowerCase().includes(q)
		);
	});

	const pendingCount = $derived(
		requisitions.filter((r) => r.status === 'pending_approval').length
	);
	const periodTotal = $derived(requisitions.reduce((sum, r) => sum + (r.total || 0), 0));

	// Reflect the live filter state into the URL. EVERY read in here is
	// untracked, `$page.url` included, because syncUrl() is a WRITER called
	// from the filter `$effect`s below — not a source of dependencies:
	//   - the URL read would self-trigger the effect that writes it via
	//     replaceState (Svelte effect_update_depth_exceeded);
	//   - a tracked `search` read would make every filter effect depend on
	//     `search`, so each keystroke re-fired it: an immediate, un-debounced
	//     load racing the dedicated 300ms debounce timer. That is issue #168,
	//     fixed on /invoices, /payments and /vendors but never carried to this
	//     page. Each effect declares the filters it actually depends on by
	//     reading them directly, so nothing here needs to be tracked.
	function syncUrl() {
		untrack(() => {
			const url = new URL($page.url);
			// `id` is a transient deep-link param (see deepLinkId below) — it is
			// consumed once at load and never persisted, so the filter-state sync
			// always drops it rather than resurrecting it from a stale URL read.
			url.searchParams.delete('id');
			if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
			else url.searchParams.delete('status');
			if (search.trim()) url.searchParams.set('search', search.trim());
			else url.searchParams.delete('search');
			replaceState(`${url.pathname}${url.search}`, {});
		});
	}

	// Sequences `load` (latest-issued wins) so a slow response for an earlier
	// status filter can't land after a faster later one. `onSaved` / `replaceRow`
	// / `doDelete` edit the list in place with no fetch of their own, so they
	// retire whatever is in flight first — a new requisition needs no
	// pre-existing row, so it races even the first load. See
	// `frontend/CLAUDE.md` § Sequencing list fetches.
	const fetchSequence = createRequestSequencer();

	async function load() {
		const token = fetchSequence.start();
		loading = true;
		try {
			const params: { status?: string; page_size: number } = { page_size: 100 };
			if (statusFilter !== 'all') params.status = statusFilter;
			const res = await listRequisitions(params);
			// Superseded by a newer load, or by a local create/lifecycle edit.
			if (!fetchSequence.canCommit(token)) return;
			requisitions = res.items;
			total = res.total;
		} catch (err) {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!fetchSequence.isCurrentRequest(token)) return;
			toast(err instanceof Error ? err.message : m('requisitions.toast.loadFailed'), 'error');
		} finally {
			if (fetchSequence.isCurrentRequest(token)) loading = false;
		}
	}

	$effect(() => {
		statusFilter;
		syncUrl();
		load();
	});

	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => syncUrl(), 300);
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
		loadGlAccounts();
	});

	// Deep-link: `?id=<uuid>` opens that requisition's detail modal (e.g. after a
	// punch-out cart is converted into a draft). Captured once at init — before
	// syncUrl() normalizes the URL and strips the transient param — then resolved
	// straight from the API (the target may live past the 100 rows we fetch).
	const deepLinkId = $page.url.searchParams.get('id');
	onMount(() => {
		if (!deepLinkId) return;
		getRequisition(deepLinkId)
			.then((r) => (editing = r))
			.catch(() => toast(m('requisitions.notFound'), 'error'));
	});

	async function loadGlAccounts() {
		try {
			glAccounts = await listGlAccounts();
		} catch {
			/* non-critical for the list view */
		}
	}

	function onSaved(r: Requisition) {
		fetchSequence.supersedeInFlight();
		const idx = requisitions.findIndex((x) => x.id === r.id);
		if (idx >= 0) requisitions = requisitions.map((x) => (x.id === r.id ? r : x));
		else {
			requisitions = [r, ...requisitions];
			total += 1;
		}
		if (editing && editing.id === r.id) editing = r;
	}

	function replaceRow(r: Requisition) {
		fetchSequence.supersedeInFlight();
		requisitions = requisitions.map((x) => (x.id === r.id ? r : x));
		if (editing && editing.id === r.id) editing = r;
	}

	async function runAction(
		id: string,
		fn: () => Promise<Requisition>,
		successMsg: string,
		failMsg: string
	) {
		busyId = id;
		try {
			replaceRow(await fn());
			toast(successMsg, 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : failMsg, 'error');
		} finally {
			busyId = null;
		}
	}

	function doSubmit(r: Requisition) {
		runAction(r.id, () => submitRequisition(r.id), m('requisitions.toast.submitted'), m('requisitions.toast.submitFailed'));
	}
	function doApprove(r: Requisition) {
		runAction(r.id, () => approveRequisition(r.id), m('requisitions.toast.approved'), m('requisitions.toast.approveFailed'));
	}
	function doReject(r: Requisition) {
		runAction(r.id, () => rejectRequisition(r.id), m('requisitions.toast.rejected'), m('requisitions.toast.rejectFailed'));
	}
	function doCancel(r: Requisition) {
		runAction(r.id, () => cancelRequisition(r.id), m('requisitions.toast.cancelled'), m('requisitions.toast.cancelFailed'));
	}

	async function doConvert(r: Requisition) {
		busyId = r.id;
		try {
			const res = await convertRequisitionToPo(r.id);
			toast(
				res.created
					? m('requisitions.toast.converted', { poNumber: res.po_number })
					: m('requisitions.toast.alreadyConverted', { poNumber: res.po_number }),
				'success'
			);
			await load();
		} catch (err) {
			toast(err instanceof Error ? err.message : m('requisitions.toast.convertFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	async function doDelete(id: string) {
		try {
			await apiDelete(id);
			fetchSequence.supersedeInFlight();
			requisitions = requisitions.filter((r) => r.id !== id);
			total = Math.max(0, total - 1);
			toast(m('requisitions.toast.deleted'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('requisitions.toast.deleteFailed'), 'error');
		} finally {
			confirmDeleteId = null;
		}
	}

	// SoD: a manager can approve/reject a pending requisition only if they aren't
	// the requester (the backend also enforces this — 403).
	function canDecide(r: Requisition): boolean {
		return (
			canApprove &&
			r.status === 'pending_approval' &&
			auth.user?.id !== r.requester_user_id
		);
	}

	function onWindowClick(e: MouseEvent) {
		const target = e.target as Element | null;
		if (!target?.closest('.row-action') && confirmDeleteId) confirmDeleteId = null;
	}
</script>

<svelte:window onclick={onWindowClick} />

<PageHeader title={m('requisitions.title')}>
	{#snippet actions()}
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>{m('requisitions.action.new')}</button>
		{/if}
	{/snippet}

	<div class="kpi-row">
		<KpiCard value={formatMoney(periodTotal, { currency: orgCurrency.currency })} label={m('requisitions.kpi.openTotal')} />
		<KpiCard value={total} label={m('requisitions.kpi.requisitions')} />
		<KpiCard value={pendingCount} label={m('requisitions.kpi.pendingApproval')} highlight={pendingCount ? 'red' : null} />
	</div>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('requisitions.search.placeholder')} ariaLabel={m('requisitions.search.aria')} />
		<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={visible.length === 0}
		empty={loading ? m('common.loading') : m('requisitions.empty')}
	>
		{#snippet body()}
			{#each visible as r (r.id)}
				<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) editing = r; }}>
					<td class="mono">
						<RowLink onclick={() => (editing = r)} ariaLabel={m('requisitions.row.open', { number: r.requisition_number })}>
							{r.requisition_number}
						</RowLink>
					</td>
					<td>{r.title ?? '—'}</td>
					<td>{r.department ?? '—'}</td>
					<td class="muted">{formatDate(r.needed_by)}</td>
					<td class="right mono"><Money amount={r.total} currency={r.currency} /></td>
					<td>
						<span class="badge {r.status}">{REQUISITION_STATUS_LABELS[r.status as keyof typeof REQUISITION_STATUS_LABELS] ?? r.status}</span>
					</td>
					<td class="actions">
						{#if canCreate && r.status === 'draft'}
							<RowAction variant="default" onclick={() => doSubmit(r)} disabled={busyId === r.id}>{m('requisitions.row.submit')}</RowAction>
						{/if}
						{#if canDecide(r)}
							<RowAction variant="success" onclick={() => doApprove(r)} disabled={busyId === r.id}>{m('requisitions.row.approve')}</RowAction>
							<RowAction variant="danger" onclick={() => doReject(r)} disabled={busyId === r.id}>{m('requisitions.row.reject')}</RowAction>
						{/if}
						{#if canApprove && r.status === 'approved'}
							<RowAction variant="default" onclick={() => doConvert(r)} disabled={busyId === r.id}>{m('requisitions.row.convertToPo')}</RowAction>
						{/if}
						{#if canCreate && (r.status === 'draft' || r.status === 'submitted' || r.status === 'pending_approval' || r.status === 'approved')}
							<RowAction variant="default" onclick={() => doCancel(r)} disabled={busyId === r.id}>{m('requisitions.row.cancel')}</RowAction>
						{/if}
						{#if canCreate && r.status === 'draft'}
							<RowAction
								variant="danger"
								armed={confirmDeleteId === r.id}
								onclick={(e) => {
									e.stopPropagation();
									if (confirmDeleteId === r.id) doDelete(r.id);
									else confirmDeleteId = r.id;
								}}
							>
								{confirmDeleteId === r.id ? m('requisitions.row.confirm') : m('requisitions.row.delete')}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('requisitions.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<RequisitionModal
		requisition={null}
		{glAccounts}
		onclose={() => (showCreate = false)}
		onsaved={onSaved}
	/>
{/if}

{#if editing}
	<RequisitionModal
		requisition={editing}
		{glAccounts}
		onclose={() => (editing = null)}
		onsaved={onSaved}
	/>
{/if}

<style>
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
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
	.badge.converted { background: rgba(140, 100, 240, 0.12); color: #8c64f0; }
	.badge.cancelled { background: var(--bg); color: var(--text-muted); }
</style>
