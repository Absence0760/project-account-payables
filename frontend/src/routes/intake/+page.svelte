<script lang="ts">
	import type { IntakeRequest } from '$lib/types/intake';
	import {
		INTAKE_STATUSES,
		INTAKE_STATUS_LABELS,
		intakeStatusTone,
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
	import Badge from '$lib/components/ui/Badge.svelte';
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
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { appendUnique } from '$lib/utils/pagination';

	const PAGE_SIZE = 50;

	// Intake is broad-access — anyone in the org can raise / read / cancel.
	const canCreate = $derived(auth.hasAnyRole('admin', 'ap_manager', 'ap_clerk', 'cfo'));
	// approve / reject / convert = admin | ap_manager (the reviewers).
	const canReview = $derived(auth.isManager);

	let items = $state<IntakeRequest[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let pageNum = $state(1);
	let loadingMore = $state(false);

	// The list is paged: `total` is the whole filtered set, so a footer that
	// says "Showing all {total}" is only true once every row is loaded.
	let hasMore = $derived(items.length < total);

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

	// `untrack` on the `search` read: buildParams() is called from `load()`,
	// which the filter `$effect` below calls directly — and Svelte tracks reads
	// transitively through called functions, so a plain read here would make
	// that effect depend on `search` and re-fire it on every keystroke,
	// un-debounced, racing the dedicated 300ms timer (issue #168). `untrack`
	// still reads the CURRENT value, so the request carries the live search
	// term; it just stops the read registering as the caller's dependency.
	function buildParams() {
		const params: { status?: string; type?: string; search?: string } = {};
		if (statusFilter !== 'all') params.status = statusFilter;
		if (typeFilter !== 'all') params.type = typeFilter;
		const currentSearch = untrack(() => search);
		if (currentSearch.trim()) params.search = currentSearch.trim();
		return params;
	}

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
			if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
			else url.searchParams.delete('status');
			if (typeFilter !== 'all') url.searchParams.set('type', typeFilter);
			else url.searchParams.delete('type');
			if (search.trim()) url.searchParams.set('search', search.trim());
			else url.searchParams.delete('search');
			replaceState(`${url.pathname}${url.search}`, {});
		});
	}

	// Sequences `load` (latest-issued wins) so a slow response for an earlier
	// filter can't land after a faster later one. `upsert` / `onDelete` edit the
	// list in place with no fetch of their own, so they retire whatever is in
	// flight first — a raised request needs no pre-existing row, so it races
	// even the first load. See `frontend/CLAUDE.md` § Sequencing list fetches.
	const fetchSequence = createRequestSequencer();

	async function load(opts: { append?: boolean } = {}) {
		const nextPage = opts.append ? pageNum + 1 : 1;
		const token = fetchSequence.start();
		if (opts.append) loadingMore = true;
		else loading = true;
		try {
			const res = await listIntake({
				...buildParams(),
				page: nextPage,
				page_size: PAGE_SIZE
			});
			// Superseded by a newer load, or by a local create/lifecycle edit.
			if (!fetchSequence.canCommit(token)) return;
			items = opts.append ? appendUnique(items, res.items) : res.items;
			total = res.total;
			pageNum = nextPage;
		} catch (err) {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!fetchSequence.isCurrentRequest(token)) return;
			toast(err instanceof Error ? err.message : m('intake.toast.loadFailed'), 'error');
		} finally {
			if (fetchSequence.isCurrentRequest(token)) {
				loading = false;
				loadingMore = false;
			}
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
		// Cancel a pending debounce on teardown: without it the timer fires
		// after the page is gone, running syncUrl()/a list fetch against a route
		// the user already left.
		return () => clearTimeout(searchTimer);
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
	});

	function upsert(i: IntakeRequest) {
		fetchSequence.supersedeInFlight();
		const idx = items.findIndex((x) => x.id === i.id);
		if (idx >= 0) items = items.map((x) => (x.id === i.id ? i : x));
		else items = [i, ...items];
		if (editing && editing.id === i.id) editing = i;
	}

	async function onDelete(id: string) {
		try {
			await apiDelete(id);
			fetchSequence.supersedeInFlight();
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
						<Badge tone={intakeStatusTone(i.status)} variant={i.status}>
							{INTAKE_STATUS_LABELS[i.status as keyof typeof INTAKE_STATUS_LABELS] ?? i.status}
						</Badge>
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

	{#if hasMore}
		<div class="load-more-row">
			<button
				class="btn-load-more"
				onclick={() => load({ append: true })}
				disabled={loadingMore}
			>
				{loadingMore
					? m('common.loading')
					: m('intake.loadMore', { shown: items.length, total })}
			</button>
		</div>
	{:else if total > 0}
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

	/* The status pill is `<Badge>` now — this file and `IntakeModal` used to
	   tint the same six statuses at two different alphas. One owner, and the
	   tone per status lives beside the labels in `types/intake`. */

	.req-link {
		font-size: 0.8rem;
		color: var(--accent);
		text-decoration: none;
	}
	.req-link:hover {
		text-decoration: underline;
	}
</style>
