<script lang="ts">
	import type { IntakeRequest } from '$lib/types/intake';
	import {
		INTAKE_STATUSES,
		INTAKE_STATUS_LABELS,
		INTAKE_TYPES,
		INTAKE_TYPE_LABELS
	} from '$lib/types/intake';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import {
		listIntake,
		deleteIntake as apiDelete,
		submitIntake,
		approveIntake,
		rejectIntake,
		cancelIntake,
		convertIntakeToRequisition
	} from '$lib/api/intake';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import IntakeModal from '$lib/components/modals/IntakeModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { m } from '$lib/i18n/store.svelte';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';

	// Intake is broad-access — anyone in the org can raise / read / cancel.
	const canCreate = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk', 'cfo'));
	// approve / reject / convert = admin | ap_manager (the reviewers).
	const canReview = $derived(auth.isManager);

	let items = $state<IntakeRequest[]>([]);
	let total = $state(0);
	let loading = $state(false);

	let search = $state($page.url.searchParams.get('search') ?? '');
	let statusFilter = $state<string>($page.url.searchParams.get('status') ?? 'all');
	let typeFilter = $state<string>($page.url.searchParams.get('type') ?? 'all');

	let showCreate = $state(false);
	let editing = $state<IntakeRequest | null>(null);
	let confirmDeleteId = $state<string | null>(null);
	let rejectArmedId = $state<string | null>(null);
	let busyId = $state<string | null>(null);

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...INTAKE_STATUSES.map((s) => ({ key: s, label: INTAKE_STATUS_LABELS[s] }))
	]);
	const TYPE_CHIPS = $derived([
		{ key: 'all', label: m('intake.filter.allTypes') },
		...INTAKE_TYPES.map((t) => ({ key: t, label: INTAKE_TYPE_LABELS[t] }))
	]);

	const COLUMNS = $derived([
		{ label: m('intake.col.requestNumber') },
		{ label: m('intake.col.title') },
		{ label: m('intake.col.type') },
		{ label: m('intake.col.vendor') },
		{ label: m('intake.col.estimated'), class: 'right' },
		{ label: m('intake.col.status') },
		{ label: '', class: 'actions-col' }
	]);

	// KPIs over the loaded page.
	const openCount = $derived(items.filter((i) => i.status === 'open').length);
	const reviewCount = $derived(items.filter((i) => i.status === 'in_review').length);

	function buildParams() {
		const params: { status?: string; type?: string; search?: string } = {};
		if (statusFilter !== 'all') params.status = statusFilter;
		if (typeFilter !== 'all') params.type = typeFilter;
		if (search.trim()) params.search = search.trim();
		return params;
	}

	// Read the URL untracked — syncUrl() writes it via replaceState inside a
	// filter $effect; a tracked $page.url read would self-trigger the effect
	// (Svelte effect_update_depth_exceeded loop).
	function syncUrl() {
		const url = new URL(untrack(() => $page.url));
		if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
		else url.searchParams.delete('status');
		if (typeFilter !== 'all') url.searchParams.set('type', typeFilter);
		else url.searchParams.delete('type');
		if (search.trim()) url.searchParams.set('search', search.trim());
		else url.searchParams.delete('search');
		replaceState(`${url.pathname}${url.search}`, {});
	}

	async function load() {
		loading = true;
		try {
			const res = await listIntake({ ...buildParams(), page_size: 50 });
			items = res.items;
			total = res.total;
		} catch (err) {
			toast(err instanceof Error ? err.message : m('intake.toast.loadFailed'), 'error');
		} finally {
			loading = false;
		}
	}

	// Status/type filter → server refetch + URL sync.
	$effect(() => {
		statusFilter;
		typeFilter;
		syncUrl();
		load();
	});

	// Debounced search (server-side via ?search=).
	//
	// `searchEffectRan` skips this effect's own mount-time run: a Svelte
	// `$effect` always fires once immediately regardless of whether its
	// tracked value actually changed, so without the guard this queued a
	// SECOND, redundant `load()` ~300ms after the status/type effect above
	// already loaded the page once. `load()` replaces `items` wholesale,
	// so if a create/edit lands in that window, the delayed duplicate can
	// resolve afterward and silently clobber it with a stale snapshot —
	// same class of bug fixed in UsersPanel.svelte.
	let searchTimer: ReturnType<typeof setTimeout>;
	let searchEffectRan = false;
	$effect(() => {
		search;
		if (!searchEffectRan) {
			searchEffectRan = true;
			return;
		}
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			syncUrl();
			load();
		}, 300);
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
	});

	function upsert(i: IntakeRequest) {
		const idx = items.findIndex((x) => x.id === i.id);
		if (idx >= 0) items = items.map((x) => (x.id === i.id ? i : x));
		else items = [i, ...items];
		if (editing && editing.id === i.id) editing = i;
	}

	async function onDelete(id: string) {
		try {
			await apiDelete(id);
			items = items.filter((x) => x.id !== id);
			total = Math.max(0, total - 1);
			toast(m('intake.toast.deleted'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('intake.toast.deleteFailed'), 'error');
		} finally {
			confirmDeleteId = null;
		}
	}

	async function onSubmit(i: IntakeRequest) {
		busyId = i.id;
		try {
			upsert(await submitIntake(i.id));
			toast(m('intake.toast.submitted'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('intake.toast.submitFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	async function onApprove(i: IntakeRequest) {
		busyId = i.id;
		try {
			upsert(await approveIntake(i.id));
			toast(m('intake.toast.approved'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('intake.toast.approveFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	async function onReject(i: IntakeRequest) {
		busyId = i.id;
		try {
			upsert(await rejectIntake(i.id));
			toast(m('intake.toast.rejected'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('intake.toast.rejectFailed'), 'error');
		} finally {
			busyId = null;
			rejectArmedId = null;
		}
	}

	async function onCancel(i: IntakeRequest) {
		busyId = i.id;
		try {
			upsert(await cancelIntake(i.id));
			toast(m('intake.toast.cancelled'), 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : m('intake.toast.cancelFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	async function onConvert(i: IntakeRequest) {
		busyId = i.id;
		try {
			const res = await convertIntakeToRequisition(i.id);
			upsert(res.intake);
			toast(
				res.created
					? m('intake.toast.created', { number: res.requisition_number })
					: m('intake.toast.alreadyConverted', { number: res.requisition_number }),
				'success'
			);
		} catch (err) {
			toast(err instanceof Error ? err.message : m('intake.toast.convertFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	// Outside-click un-arms any pending armed-confirm (delete, reject).
	function onWindowClick(e: MouseEvent) {
		const target = e.target as Element | null;
		if (!target?.closest('.row-action')) {
			if (confirmDeleteId) confirmDeleteId = null;
			if (rejectArmedId) rejectArmedId = null;
		}
	}
</script>

<svelte:window onclick={onWindowClick} />

<PageHeader title={m('intake.title')}>
	{#snippet actions()}
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>{m('intake.action.new')}</button>
		{/if}
	{/snippet}

	<div class="kpi-row">
		<KpiCard value={total} label={m('intake.kpi.requests')} />
		<KpiCard value={openCount} label={m('intake.kpi.open')} />
		<KpiCard value={reviewCount} label={m('intake.kpi.inReview')} highlight={reviewCount ? 'red' : null} />
	</div>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('intake.search.placeholder')} ariaLabel={m('intake.search.aria')} />
		<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
	</div>
	<div class="filter-row">
		<FilterChips chips={TYPE_CHIPS} bind:active={typeFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={items.length === 0}
		empty={loading ? m('common.loading') : m('intake.empty')}
	>
		{#snippet body()}
			{#each items as i (i.id)}
				<tr class="clickable" onclick={(e) => { if (isRowOpenClick(e)) editing = i; }}>
					<td class="mono">
						<RowLink onclick={() => (editing = i)} ariaLabel={m('intake.row.open', { number: i.request_number })}>
							{i.request_number}
						</RowLink>
					</td>
					<td>{i.title}</td>
					<td>{INTAKE_TYPE_LABELS[i.request_type as keyof typeof INTAKE_TYPE_LABELS] ?? i.request_type}</td>
					<td>{i.vendor_name ?? '—'}</td>
					<td class="right mono">
						{#if i.estimated_amount != null}
							<Money amount={i.estimated_amount} currency={i.currency} />
						{:else}—{/if}
					</td>
					<td>
						<span class="badge {i.status}">{INTAKE_STATUS_LABELS[i.status as keyof typeof INTAKE_STATUS_LABELS] ?? i.status}</span>
					</td>
					<td class="actions">
						{#if canCreate && i.status === 'open'}
							<RowAction variant="default" disabled={busyId === i.id} onclick={() => onSubmit(i)}>{m('intake.row.submit')}</RowAction>
						{/if}
						{#if canReview && i.status === 'in_review'}
							<RowAction variant="success" disabled={busyId === i.id} onclick={() => onApprove(i)}>{m('intake.row.approve')}</RowAction>
							<RowAction
								variant="danger"
								armed={rejectArmedId === i.id}
								disabled={busyId === i.id}
								onclick={(e) => {
									e.stopPropagation();
									if (rejectArmedId === i.id) onReject(i);
									else rejectArmedId = i.id;
								}}
							>
								{rejectArmedId === i.id ? m('intake.row.confirm') : m('intake.row.reject')}
							</RowAction>
						{/if}
						{#if canReview && i.status === 'approved'}
							<RowAction variant="default" disabled={busyId === i.id} onclick={() => onConvert(i)}>{m('intake.row.convertToRequisition')}</RowAction>
						{/if}
						{#if i.status === 'converted' && i.converted_requisition_id}
							<a class="req-link" href={`/requisitions?id=${i.converted_requisition_id}`}>{m('intake.row.viewRequisition')}</a>
						{/if}
						{#if canCreate && (i.status === 'open' || i.status === 'in_review' || i.status === 'approved')}
							<RowAction variant="default" disabled={busyId === i.id} onclick={() => onCancel(i)}>{m('intake.row.cancel')}</RowAction>
						{/if}
						{#if canCreate}
							<RowAction
								variant="danger"
								armed={confirmDeleteId === i.id}
								onclick={(e) => {
									e.stopPropagation();
									if (confirmDeleteId === i.id) onDelete(i.id);
									else confirmDeleteId = i.id;
								}}
							>
								{confirmDeleteId === i.id ? m('intake.row.confirm') : m('intake.row.delete')}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('intake.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<IntakeModal intake={null} onclose={() => (showCreate = false)} onsaved={upsert} />
{/if}

{#if editing}
	<IntakeModal intake={editing} onclose={() => (editing = null)} onsaved={upsert} />
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
	.badge.open { background: rgba(99, 140, 255, 0.12); color: #638cff; }
	.badge.in_review { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	.badge.approved { background: rgba(31, 168, 106, 0.12); color: #1fa86a; }
	.badge.rejected { background: rgba(224, 64, 64, 0.12); color: #e04040; }
	.badge.converted { background: rgba(140, 100, 240, 0.12); color: #8c64f0; }
	.badge.cancelled { background: var(--bg); color: var(--text-muted); }

	.req-link {
		font-size: 0.8rem;
		color: var(--accent);
		text-decoration: none;
	}
	.req-link:hover {
		text-decoration: underline;
	}
</style>
