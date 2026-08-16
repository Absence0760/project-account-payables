<script lang="ts">
	import type { PositivePayFile } from '$lib/types/positivePay';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import {
		POSITIVE_PAY_FILE_TYPE_LABELS,
		POSITIVE_PAY_STATUS_LABELS
	} from '$lib/types/positivePay';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import {
		listPositivePayFiles,
		getPositivePayFile,
		deletePositivePayFile
	} from '$lib/api/positivePay';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import PositivePayModal from '$lib/components/modals/PositivePayModal.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { m } from '$lib/i18n/store.svelte';
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import { untrack } from 'svelte';
	import { formatDate } from '$lib/utils/time';

	const canCreate = $derived(auth.isManager);

	// file_type chips (single-select). "All" first.
	const TYPE_CHIPS = $derived([
		{ key: 'all', label: m('common.all') },
		{ key: 'check_issue', label: m('positivePay.filter.checkIssue') },
		{ key: 'ach_authorization', label: m('positivePay.filter.achAuth') }
	]);

	const COLUMNS = $derived([
		{ label: m('positivePay.col.file') },
		{ label: m('positivePay.col.type') },
		{ label: m('positivePay.col.format') },
		{ label: m('positivePay.col.items'), class: 'right' },
		{ label: m('positivePay.col.total'), class: 'right' },
		{ label: m('positivePay.col.account') },
		{ label: m('positivePay.col.created') },
		{ label: m('positivePay.col.status') },
		{ class: 'actions-col' }
	]);

	const PAGE_SIZE = 20;

	// URL-backed filter state (mirrors the vendor-statements page convention).
	let search = $state($page.url.searchParams.get('search') ?? '');
	let typeFilter = $state<string>($page.url.searchParams.get('file_type') ?? 'all');

	let files = $state<PositivePayFile[]>([]);
	let total = $state(0);
	let pageNum = $state(1);
	let loading = $state(true);
	let loadingMore = $state(false);

	let hasMore = $derived(files.length < total);

	let showCreate = $state(false);
	let detail = $state<PositivePayFile | null>(null);

	function fileLabel(f: PositivePayFile): string {
		return f.file_type === 'check_issue' && f.payment_run_id
			? m('positivePay.runLabel', { id: f.payment_run_id.slice(0, 8) })
			: m('positivePay.fileLabel', { type: POSITIVE_PAY_FILE_TYPE_LABELS[f.file_type], id: f.id.slice(0, 8) });
	}

	// Client-side search filter (list endpoint filters by file_type/status).
	const visibleFiles = $derived.by(() => {
		const q = search.trim().toLowerCase();
		if (!q) return files;
		return files.filter(
			(f) =>
				fileLabel(f).toLowerCase().includes(q) ||
				f.id.toLowerCase().includes(q) ||
				(f.payment_run_id ?? '').toLowerCase().includes(q)
		);
	});

	function buildParams() {
		const params: { file_type?: string } = {};
		if (typeFilter !== 'all') params.file_type = typeFilter;
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
			if (typeFilter !== 'all') url.searchParams.set('file_type', typeFilter);
			else url.searchParams.delete('file_type');
			if (search.trim()) url.searchParams.set('search', search.trim());
			else url.searchParams.delete('search');
			replaceState(`${url.pathname}${url.search}`, {});
		});
	}

	// Sequences `load` (filter change and load-more alike — one shared counter,
	// latest-issued wins). `upsert` / `deleteFile` edit the list in place with no
	// fetch of their own, so they retire whatever is in flight first: a generated
	// file is otherwise dropped by the load that was already out — and a generate
	// needs no pre-existing row, so it races even the first load. See
	// `frontend/CLAUDE.md` § Sequencing list fetches.
	const fetchSequence = createRequestSequencer();

	async function load(opts: { append?: boolean } = {}) {
		const nextPage = opts.append ? pageNum + 1 : 1;
		const token = fetchSequence.start();
		if (opts.append) loadingMore = true;
		else loading = true;
		try {
			const data = await listPositivePayFiles({
				...buildParams(),
				page: nextPage,
				page_size: PAGE_SIZE
			});
			// Superseded by a newer load, or by a local generate/delete.
			if (!fetchSequence.canCommit(token)) return;
			files = opts.append ? appendUnique(files, data.items) : data.items;
			total = data.total;
			pageNum = nextPage;
		} catch (e) {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!fetchSequence.isCurrentRequest(token)) return;
			if (!opts.append) files = [];
			toast(e instanceof Error ? e.message : m('positivePay.toast.loadFailed'), 'error');
		} finally {
			if (fetchSequence.isCurrentRequest(token)) {
				loading = false;
				loadingMore = false;
			}
		}
	}

	let searchTimer: ReturnType<typeof setTimeout>;
	$effect(() => {
		search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(syncUrl, 300);
	});

	$effect(() => {
		typeFilter;
		syncUrl();
		load();
	});

	$effect(() => {
		orgCurrency.ensureLoaded();
	});

	// Deep-link: `/positive-pay?id=<uuid>` opens that file's detail modal.
	let deepLinkLoaded = $state<string | null>(null);
	$effect(() => {
		const id = $page.url.searchParams.get('id');
		if (!id || deepLinkLoaded === id) return;
		deepLinkLoaded = id;
		getPositivePayFile(id)
			.then((f) => (detail = f))
			.catch(() => toast(m('positivePay.notFound'), 'error'));
	});

	async function openDetail(f: PositivePayFile) {
		try {
			detail = await getPositivePayFile(f.id);
		} catch {
			detail = f;
		}
	}

	function closeModal() {
		detail = null;
		showCreate = false;
		const url = new URL($page.url);
		if (url.searchParams.has('id')) {
			url.searchParams.delete('id');
			replaceState(`${url.pathname}${url.search}`, {});
			deepLinkLoaded = null;
		}
	}

	function upsert(f: PositivePayFile) {
		// Retire every load issued before this file existed — their responses
		// predate it and would drop it back out of the list.
		fetchSequence.supersedeInFlight();
		const idx = files.findIndex((x) => x.id === f.id);
		if (idx === -1) {
			files = [f, ...files];
			total += 1;
		} else {
			files = files.map((x) => (x.id === f.id ? f : x));
		}
	}

	function onSaved(f: PositivePayFile) {
		upsert(f);
		if (detail && detail.id === f.id) detail = f;
	}

	// --- Delete (armed two-click confirm) ---
	let busyId = $state<string | null>(null);
	let confirmDeleteId = $state<string | null>(null);

	async function deleteFile(f: PositivePayFile) {
		if (confirmDeleteId !== f.id) {
			confirmDeleteId = f.id;
			return;
		}
		confirmDeleteId = null;
		busyId = f.id;
		try {
			await deletePositivePayFile(f.id);
			fetchSequence.supersedeInFlight();
			files = files.filter((x) => x.id !== f.id);
			total = Math.max(0, total - 1);
			toast(m('positivePay.toast.deleted'), 'success');
		} catch (e) {
			toast(e instanceof Error ? e.message : m('positivePay.toast.deleteFailed'), 'error');
		} finally {
			busyId = null;
		}
	}

	// --- KPI math (derived from the loaded rows) ---
	const totalFiles = $derived(total);
	const itemsExported = $derived(files.reduce((sum, f) => sum + f.item_count, 0));
	const returnsFlagged = $derived(
		files.reduce(
			(sum, f) =>
				sum +
				((f.meta?.return_summary?.amount_mismatches ?? 0) +
					(f.meta?.return_summary?.not_on_file ?? 0)),
			0
		)
	);
</script>

<svelte:window
	onclick={(e) => {
		if (confirmDeleteId && !(e.target as HTMLElement)?.closest?.('.row-action')) {
			confirmDeleteId = null;
		}
	}}
/>

<PageHeader title={m('positivePay.title')}>
	{#snippet actions()}
		{#if canCreate}
			<button class="btn-primary" onclick={() => (showCreate = true)}>{m('positivePay.action.generate')}</button>
		{/if}
	{/snippet}

	<!-- KPI row -->
	<div class="kpi-row">
		<KpiCard value={totalFiles} label={m('positivePay.kpi.files')} />
		<KpiCard value={itemsExported} label={m('positivePay.kpi.itemsExported')} />
		<KpiCard
			value={returnsFlagged}
			label={m('positivePay.kpi.returnsFlagged')}
			highlight={returnsFlagged > 0 ? 'red' : null}
		/>
	</div>

	<div class="filter-row">
		<SearchBox bind:value={search} placeholder={m('positivePay.search.placeholder')} ariaLabel={m('positivePay.search.aria')} />
		<FilterChips chips={TYPE_CHIPS} bind:active={typeFilter} />
	</div>

	<DataTable
		columns={COLUMNS}
		isEmpty={!loading && visibleFiles.length === 0}
		empty={loading ? m('common.loading') : m('positivePay.empty')}
		colspan={9}
	>
		{#snippet body()}
			{#each visibleFiles as file (file.id)}
				<tr
					class="clickable"
					onclick={(e) => {
						if (isRowOpenClick(e)) openDetail(file);
					}}
				>
					<td>
						<RowLink
							onclick={() => openDetail(file)}
							ariaLabel={m('positivePay.row.open', { label: fileLabel(file) })}
						>
							{fileLabel(file)}
						</RowLink>
					</td>
					<td class="muted">{POSITIVE_PAY_FILE_TYPE_LABELS[file.file_type]}</td>
					<td class="muted">{file.bank_format}</td>
					<td class="right mono">{file.item_count}</td>
					<td class="right mono"><Money amount={file.total_amount} currency={file.currency ?? orgCurrency.currency} /></td>
					<td class="mono muted">{file.account_last4 ? `••••${file.account_last4}` : '—'}</td>
					<td class="muted">{formatDate(file.created_at)}</td>
					<td><span class="badge {file.status}">{POSITIVE_PAY_STATUS_LABELS[file.status]}</span></td>
					<td class="actions">
						{#if canCreate}
							<RowAction
								variant="danger"
								armed={confirmDeleteId === file.id}
								disabled={busyId === file.id}
								onclick={() => deleteFile(file)}
								ariaLabel={m('positivePay.row.deleteAria', { label: fileLabel(file) })}
							>
								{confirmDeleteId === file.id ? m('positivePay.row.confirm') : m('positivePay.row.delete')}
							</RowAction>
						{/if}
					</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={() => load({ append: true })} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('positivePay.loadMore', { shown: files.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('positivePay.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

{#if showCreate}
	<PositivePayModal file={null} onclose={closeModal} onsaved={onSaved} />
{/if}

{#if detail}
	<PositivePayModal file={detail} onclose={closeModal} onsaved={onSaved} />
{/if}

<style>
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

	.badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
	}
	.badge.generated {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.badge.returned_processed {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
</style>
