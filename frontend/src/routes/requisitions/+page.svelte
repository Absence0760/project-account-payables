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
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { onMount } from 'svelte';

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

	const STATUS_CHIPS = [
		{ key: 'all', label: 'All' },
		...REQUISITION_STATUSES.map((s) => ({ key: s, label: REQUISITION_STATUS_LABELS[s] }))
	];

	const COLUMNS = [
		{ label: 'Requisition #' },
		{ label: 'Title' },
		{ label: 'Department' },
		{ label: 'Needed By' },
		{ label: 'Total', class: 'right' },
		{ label: 'Status' },
		{ label: '', class: 'actions-col' }
	];

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

	function syncUrl() {
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
	}

	async function load() {
		loading = true;
		try {
			const params: { status?: string; page_size: number } = { page_size: 100 };
			if (statusFilter !== 'all') params.status = statusFilter;
			const res = await listRequisitions(params);
			requisitions = res.items;
			total = res.total;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to load requisitions', 'error');
		} finally {
			loading = false;
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
			.catch(() => toast('Requisition not found', 'error'));
	});

	async function loadGlAccounts() {
		try {
			glAccounts = await listGlAccounts();
		} catch {
			/* non-critical for the list view */
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

	function onSaved(r: Requisition) {
		const idx = requisitions.findIndex((x) => x.id === r.id);
		if (idx >= 0) requisitions = requisitions.map((x) => (x.id === r.id ? r : x));
		else {
			requisitions = [r, ...requisitions];
			total += 1;
		}
		if (editing && editing.id === r.id) editing = r;
	}

	function replaceRow(r: Requisition) {
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
		runAction(r.id, () => submitRequisition(r.id), 'Requisition submitted', 'Submit failed');
	}
	function doApprove(r: Requisition) {
		runAction(r.id, () => approveRequisition(r.id), 'Requisition approved', 'Approve failed');
	}
	function doReject(r: Requisition) {
		runAction(r.id, () => rejectRequisition(r.id), 'Requisition rejected', 'Reject failed');
	}
	function doCancel(r: Requisition) {
		runAction(r.id, () => cancelRequisition(r.id), 'Requisition cancelled', 'Cancel failed');
	}

	async function doConvert(r: Requisition) {
		busyId = r.id;
		try {
			const res = await convertRequisitionToPo(r.id);
			toast(
				res.created
					? `Converted to ${res.po_number}`
					: `Already converted (${res.po_number})`,
				'success'
			);
			await load();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Convert failed', 'error');
		} finally {
			busyId = null;
		}
	}

	async function doDelete(id: string) {
		try {
			await apiDelete(id);
			requisitions = requisitions.filter((r) => r.id !== id);
			total = Math.max(0, total - 1);
			toast('Requisition deleted', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Delete failed', 'error');
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

<PageHeader title="Requisitions">
	{#snippet actions()}
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>+ New Requisition</button>
		{/if}
	{/snippet}

	<div class="kpi-row">
		<KpiCard value={formatMoney(periodTotal, { currency: orgCurrency.currency })} label="Open total" />
		<KpiCard value={total} label="Requisitions" />
		<KpiCard value={pendingCount} label="Pending approval" highlight={pendingCount ? 'red' : null} />
	</div>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder="Search requisitions..." ariaLabel="Search requisitions" />
		<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={visible.length === 0}
		empty={loading ? 'Loading…' : 'No requisitions match your filters.'}
	>
		{#snippet body()}
			{#each visible as r (r.id)}
				<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) editing = r; }}>
					<td class="mono">
						<RowLink onclick={() => (editing = r)} ariaLabel={`Open requisition ${r.requisition_number}`}>
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
							<RowAction variant="default" onclick={() => doSubmit(r)} disabled={busyId === r.id}>Submit</RowAction>
						{/if}
						{#if canDecide(r)}
							<RowAction variant="success" onclick={() => doApprove(r)} disabled={busyId === r.id}>Approve</RowAction>
							<RowAction variant="danger" onclick={() => doReject(r)} disabled={busyId === r.id}>Reject</RowAction>
						{/if}
						{#if canApprove && r.status === 'approved'}
							<RowAction variant="default" onclick={() => doConvert(r)} disabled={busyId === r.id}>Convert to PO</RowAction>
						{/if}
						{#if canCreate && (r.status === 'draft' || r.status === 'submitted' || r.status === 'pending_approval' || r.status === 'approved')}
							<RowAction variant="default" onclick={() => doCancel(r)} disabled={busyId === r.id}>Cancel</RowAction>
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
								{confirmDeleteId === r.id ? 'Confirm' : 'Delete'}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">Showing all {total} requisition{total === 1 ? '' : 's'}</span>
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
