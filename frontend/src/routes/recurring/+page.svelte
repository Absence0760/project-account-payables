<script lang="ts">
	import type { RecurringTemplate, RecurringStatus } from '$lib/types/recurring';
	import { RECURRING_STATUSES, STATUS_LABELS, CADENCE_LABELS } from '$lib/types/recurring';
	import { auth } from '$lib/stores/auth.svelte';
	import { appendUnique } from '$lib/utils/pagination';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { api } from '$lib/api';
	import {
		listRecurring,
		getRecurring,
		deleteRecurring,
		pauseRecurring,
		resumeRecurring,
		endRecurring,
		generateRecurringNow
	} from '$lib/api/recurring';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import RecurringModal from '$lib/components/modals/RecurringModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { m } from '$lib/i18n/store.svelte';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';
	import { formatDate } from '$lib/utils/time';

	const canCreate = $derived(auth.isManager);

	const STATUS_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		...RECURRING_STATUSES.map((s) => ({ key: s, label: STATUS_LABELS[s] }))
	]);

	const COLUMNS = $derived([
		{ label: m('recurring.col.name') },
		{ label: m('recurring.col.vendor') },
		{ label: m('recurring.col.amount'), class: 'right' },
		{ label: m('recurring.col.cadence') },
		{ label: m('recurring.col.nextRun') },
		{ label: m('recurring.col.generated'), class: 'right' },
		{ label: m('recurring.col.status') },
		{ class: 'actions-col' }
	]);

	const PAGE_SIZE = 20;

	interface VendorOption {
		id: string;
		name: string;
	}

	// URL-backed filter state (mirrors the contracts page convention).
	let search = $state($page.url.searchParams.get('search') ?? '');
	let statusFilter = $state<string>($page.url.searchParams.get('status') ?? 'all');
	let vendors = $state<VendorOption[]>([]);

	let templates = $state<RecurringTemplate[]>([]);
	let total = $state(0);
	let pageNum = $state(1);
	let loading = $state(true);
	let loadingMore = $state(false);

	let hasMore = $derived(templates.length < total);

	// Modal state: null = create; a RecurringTemplate = detail/edit.
	let showCreate = $state(false);
	let editing = $state<RecurringTemplate | null>(null);

	function buildParams() {
		const params: { status?: string; search?: string } = {};
		if (statusFilter !== 'all') params.status = statusFilter;
		if (search.trim()) params.search = search.trim();
		return params;
	}

	// Reflect filter state into the URL so it survives reload / back-forward.
	// Read the URL untracked — syncUrl() writes it via replaceState inside a
	// filter $effect; a tracked $page.url read would self-trigger the effect
	// (Svelte effect_update_depth_exceeded loop).
	function syncUrl() {
		const url = new URL(untrack(() => $page.url));
		if (statusFilter !== 'all') url.searchParams.set('status', statusFilter);
		else url.searchParams.delete('status');
		if (search.trim()) url.searchParams.set('search', search.trim());
		else url.searchParams.delete('search');
		replaceState(`${url.pathname}${url.search}`, {});
	}

	async function load(opts: { append?: boolean } = {}) {
		const nextPage = opts.append ? pageNum + 1 : 1;
		if (opts.append) loadingMore = true;
		else loading = true;
		try {
			const data = await listRecurring({ ...buildParams(), page: nextPage, page_size: PAGE_SIZE });
			templates = opts.append ? appendUnique(templates, data.items) : data.items;
			total = data.total;
			pageNum = nextPage;
		} catch (e) {
			if (!opts.append) templates = [];
			toast(e instanceof Error ? e.message : m('recurring.toast.loadFailed'), 'error');
		} finally {
			loading = false;
			loadingMore = false;
		}
	}

	// `searchEffectRan` skips this effect's own mount-time run: a Svelte
	// `$effect` always fires once immediately regardless of whether its
	// tracked value actually changed, so without the guard this queued a
	// SECOND, redundant `load()` ~300ms after the statusFilter effect
	// below already loaded the page once. `load()` replaces `templates`
	// wholesale, so if a create/edit lands in that window, the delayed
	// duplicate can resolve afterward and silently clobber it with a
	// stale snapshot — same class of bug fixed in UsersPanel.svelte (#286).
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
		statusFilter;
		syncUrl();
		load();
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
		loadVendors();
	});

	async function loadVendors() {
		try {
			const data = await api.get<{ items: VendorOption[] }>('/api/vendors');
			vendors = data.items;
		} catch {
			/* non-critical for the list view */
		}
	}

	// Deep-link: `/recurring?id=<uuid>` opens that template's detail modal.
	let deepLinkLoaded = $state<string | null>(null);
	$effect(() => {
		const id = $page.url.searchParams.get('id');
		if (!id || deepLinkLoaded === id) return;
		deepLinkLoaded = id;
		getRecurring(id)
			.then((t) => (editing = t))
			.catch(() => toast(m('recurring.notFound'), 'error'));
	});

	async function openDetail(t: RecurringTemplate) {
		try {
			editing = await getRecurring(t.id);
		} catch {
			editing = t;
		}
	}

	function closeModal() {
		editing = null;
		showCreate = false;
		const url = new URL($page.url);
		if (url.searchParams.has('id')) {
			url.searchParams.delete('id');
			replaceState(`${url.pathname}${url.search}`, {});
			deepLinkLoaded = null;
		}
	}

	function upsert(t: RecurringTemplate) {
		const idx = templates.findIndex((x) => x.id === t.id);
		if (idx === -1) {
			templates = [t, ...templates];
			total += 1;
		} else {
			templates = templates.map((x) => (x.id === t.id ? t : x));
		}
	}

	function onSaved(t: RecurringTemplate) {
		upsert(t);
		// Keep the open detail modal in sync after a lifecycle action.
		if (editing && editing.id === t.id) editing = t;
	}

	// --- Row lifecycle actions ---
	let busyId = $state<string | null>(null);

	async function runRowLifecycle(
		t: RecurringTemplate,
		fn: () => Promise<RecurringTemplate>,
		successMsg: string,
		fallback: string
	) {
		busyId = t.id;
		try {
			const updated = await fn();
			toast(successMsg, 'success');
			onSaved(updated);
		} catch (e) {
			toast(e instanceof Error ? e.message : fallback, 'error');
		} finally {
			busyId = null;
		}
	}

	async function generateNow(t: RecurringTemplate) {
		busyId = t.id;
		try {
			await generateRecurringNow(t.id);
			toast(m('recurring.toast.generated'), 'success');
			// Refresh the row so generated_count / next_run_on stay accurate.
			try {
				onSaved(await getRecurring(t.id));
			} catch {
				/* the toast already confirmed success */
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : m('recurring.toast.generateFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	// --- End (armed two-click confirm, like delete) ---
	let confirmEndId = $state<string | null>(null);

	async function endTemplate(t: RecurringTemplate) {
		if (confirmEndId !== t.id) {
			confirmEndId = t.id;
			return;
		}
		confirmEndId = null;
		await runRowLifecycle(
			t,
			() => endRecurring(t.id),
			m('recurring.toast.ended'),
			m('recurring.toast.endFailed')
		);
	}

	function statusBadgeClass(s: RecurringStatus): string {
		return s;
	}

	/** Friendly relative "in 3 days" / "5 days ago" for the next run. */
	function relativeRun(s: string | null): string {
		if (!s) return '';
		const d = new Date(s);
		if (Number.isNaN(d.getTime())) return '';
		const days = Math.round((d.getTime() - Date.now()) / 86_400_000);
		if (days === 0) return m('recurring.rel.today');
		if (days > 0) return m('recurring.rel.inDays', { n: days });
		return m('recurring.rel.daysAgo', { n: -days });
	}

	function aggMoney(n: number): string {
		return formatMoney(n, { currency: orgCurrency.currency, whole: true });
	}

	// --- KPI math (client-side, honest: derived from the loaded active rows) ---
	const activeTemplates = $derived(templates.filter((t) => t.status === 'active'));
	const activeCount = $derived(activeTemplates.length);

	const soonestNextRun = $derived.by(() => {
		const dates = activeTemplates
			.map((t) => t.next_run_on)
			.filter((d): d is string => !!d)
			.map((d) => new Date(d))
			.filter((d) => !Number.isNaN(d.getTime()))
			.sort((a, b) => a.getTime() - b.getTime());
		return dates[0] ?? null;
	});

	// Monthly-equivalent recurring total: monthly = amount, quarterly = amount/3,
	// annual = amount/12. Only counts active templates with a known amount.
	const monthlyRecurringTotal = $derived.by(() => {
		let sum = 0;
		for (const t of activeTemplates) {
			if (t.amount === null) continue;
			if (t.cadence === 'monthly') sum += t.amount;
			else if (t.cadence === 'quarterly') sum += t.amount / 3;
			else if (t.cadence === 'annual') sum += t.amount / 12;
		}
		return sum;
	});
</script>

<svelte:window
	onclick={(e) => {
		// Un-arm the End confirm when the click lands outside a row action.
		if (confirmEndId && !(e.target as HTMLElement)?.closest?.('.row-action')) {
			confirmEndId = null;
		}
	}}
/>

<PageHeader title={m('recurring.title')}>
	{#snippet actions()}
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>{m('recurring.action.new')}</button>
		{/if}
	{/snippet}

	<!-- KPI row -->
	<div class="kpi-row">
		<KpiCard value={activeCount} label={m('recurring.kpi.activeTemplates')} />
		<KpiCard
			value={soonestNextRun ? formatDate(soonestNextRun.toISOString()) : '—'}
			label={m('recurring.kpi.nextRun')}
		/>
		<KpiCard
			value={monthlyRecurringTotal > 0 ? aggMoney(monthlyRecurringTotal) : '—'}
			label={m('recurring.kpi.monthlyRecurring')}
			highlight="green"
		/>
	</div>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('recurring.search.placeholder')} ariaLabel={m('recurring.search.aria')} />
		<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={!loading && templates.length === 0}
		empty={loading ? m('common.loading') : m('recurring.empty')}
		colspan={8}
	>
		{#snippet body()}
			{#each templates as template (template.id)}
				<tr
					class="clickable"
					onclick={(e) => {
						if (isRowOpenClick(e)) openDetail(template);
					}}
				>
					<td>
						<RowLink
							onclick={() => openDetail(template)}
							ariaLabel={m('recurring.row.open', { name: template.name })}
						>
							{template.name}
						</RowLink>
					</td>
					<td>{template.vendor_name ?? '—'}</td>
					<td class="right mono"><Money amount={template.amount} currency={template.currency} /></td>
					<td>
						<span class="cadence">{CADENCE_LABELS[template.cadence]}</span>
						<span class="cadence-day">{m('recurring.dayOfPeriod', { day: template.day_of_period })}</span>
					</td>
					<td class="muted">
						{formatDate(template.next_run_on)}
						{#if template.next_run_on && template.status === 'active'}
							<span class="rel">{relativeRun(template.next_run_on)}</span>
						{/if}
					</td>
					<td class="right mono">{template.generated_count}</td>
					<td><span class="badge {statusBadgeClass(template.status)}">{STATUS_LABELS[template.status]}</span></td>
					<td class="actions">
						{#if canCreate}
							{#if template.status === 'active'}
								<RowAction
									disabled={busyId === template.id}
									onclick={() => generateNow(template)}
									ariaLabel={m('recurring.row.generateNowAria', { name: template.name })}
								>
									{m('recurring.row.generateNow')}
								</RowAction>
								<RowAction
									disabled={busyId === template.id}
									onclick={() => runRowLifecycle(template, () => pauseRecurring(template.id), m('recurring.toast.paused'), m('recurring.toast.pauseFailed'))}
									ariaLabel={m('recurring.row.pauseAria', { name: template.name })}
								>
									{m('recurring.row.pause')}
								</RowAction>
							{:else if template.status === 'paused'}
								<RowAction
									variant="success"
									disabled={busyId === template.id}
									onclick={() => runRowLifecycle(template, () => resumeRecurring(template.id), m('recurring.toast.resumed'), m('recurring.toast.resumeFailed'))}
									ariaLabel={m('recurring.row.resumeAria', { name: template.name })}
								>
									{m('recurring.row.resume')}
								</RowAction>
							{/if}
							{#if template.status !== 'ended'}
								<RowAction
									variant="danger"
									armed={confirmEndId === template.id}
									disabled={busyId === template.id}
									onclick={() => endTemplate(template)}
									ariaLabel={m('recurring.row.endAria', { name: template.name })}
								>
									{confirmEndId === template.id ? m('recurring.row.confirm') : m('recurring.row.end')}
								</RowAction>
							{/if}
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={() => load({ append: true })} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('recurring.loadMore', { shown: templates.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('recurring.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<RecurringModal template={null} {vendors} onclose={closeModal} onsaved={onSaved} />
{/if}

{#if editing}
	<RecurringModal template={editing} {vendors} onclose={closeModal} onsaved={onSaved} />
{/if}

<style>
	/* Page-specific bits; shared design-system CSS lives in app.css. */
	.filter-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.mono {
		font-variant-numeric: tabular-nums;
		font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
	}
	.muted {
		color: var(--text-muted);
	}
	.cadence {
		font-weight: 500;
	}
	.cadence-day {
		display: block;
		font-size: 0.72rem;
		color: var(--text-muted);
	}
	.rel {
		display: block;
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
	}
	.badge.active { background: rgba(31, 168, 106, 0.12); color: #1fa86a; }
	.badge.paused { background: rgba(212, 148, 10, 0.12); color: #d4940a; }
	.badge.ended { background: var(--bg); color: var(--text-muted); }
</style>
