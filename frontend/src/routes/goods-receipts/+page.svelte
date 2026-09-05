<script lang="ts">
	import { api } from '$lib/api';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import { formatDate } from '$lib/utils/time';

	const COLUMNS = $derived([
		{ label: m('goodsReceipts.col.grNumber') },
		{ label: m('goodsReceipts.col.po') },
		{ label: m('goodsReceipts.col.received') },
		{ label: m('goodsReceipts.col.status') },
		{ label: m('goodsReceipts.col.lines') },
		{ label: m('goodsReceipts.col.created') }
	]);

	interface GRLine {
		id: string;
		description: string | null;
		quantity_received: number | null;
	}

	interface GRListItem {
		id: string;
		gr_number: string;
		po_id: string | null;
		po_number: string | null;
		received_date: string | null;
		status: string;
		line_count: number;
		created_at: string;
	}

	interface GRDetail extends GRListItem {
		line_items: GRLine[];
	}

	const PAGE_SIZE = 20;

	let grs = $state<GRListItem[]>([]);
	let total = $state(0);
	let page = $state(1);
	let loading = $state(true);
	let loadingMore = $state(false);

	let detailId = $state<string | null>(null);
	let detail = $state<GRDetail | null>(null);
	let detailLoading = $state(false);

	// Two INDEPENDENT request streams, so two sequencers — a shared counter
	// would let a detail open mark the list's in-flight response un-committable
	// and blank the table. Neither loader edits a row in place (there is no
	// mutation on this page at all), so neither needs `supersedeInFlight()`.
	// See `frontend/CLAUDE.md` § Sequencing list fetches.
	const listSequence = createRequestSequencer();
	const detailSequence = createRequestSequencer();

	$effect(() => {
		void loadGRs();
	});

	$effect(() => {
		if (!detailId) {
			detail = null;
			return;
		}
		void loadDetail(detailId);
	});

	async function loadGRs(opts: { append?: boolean; nextPage?: number } = {}) {
		const token = listSequence.start();
		if (opts.append) loadingMore = true;
		else loading = true;
		try {
			const nextPage = opts.nextPage ?? 1;
			const params = new URLSearchParams({
				page: String(nextPage),
				page_size: String(PAGE_SIZE)
			});
			const data = await api.get<{ items: GRListItem[]; total: number }>(
				`/api/goods-receipts?${params}`
			);
			// Superseded by a newer load — discard rather than clobber. A page-2
			// append landing after a page-1 reload used to push the second page
			// onto the fresh list and overwrite `total`/`page` with it.
			if (!listSequence.canCommit(token)) return;
			grs = opts.append ? appendUnique(grs, data.items) : data.items;
			total = data.total;
			page = nextPage;
		} catch (err) {
			// `isCurrentRequest`, not `canCommit`: only the newest request reports.
			if (!listSequence.isCurrentRequest(token)) return;
			toast(err instanceof Error ? err.message : m('goodsReceipts.toast.loadListFailed'), 'error');
		} finally {
			// Both flags belong to the newest request: an append used to clear
			// `loading` for a page-1 reload that was still out, dropping the
			// spinner while the table still held the previous page.
			if (listSequence.isCurrentRequest(token)) {
				loading = false;
				loadingMore = false;
			}
		}
	}

	async function loadMore() {
		await loadGRs({ append: true, nextPage: page + 1 });
	}

	async function loadDetail(id: string) {
		const token = detailSequence.start();
		detailLoading = true;
		try {
			const data = await api.get<GRDetail>(`/api/goods-receipts/${id}`);
			// Open one receipt, close it, open another: the first response must
			// not land in the modal now showing the second.
			if (!detailSequence.canCommit(token)) return;
			detail = data;
		} catch (err) {
			if (!detailSequence.isCurrentRequest(token)) return;
			toast(err instanceof Error ? err.message : m('goodsReceipts.toast.loadFailed'), 'error');
			detailId = null;
		} finally {
			if (detailSequence.isCurrentRequest(token)) detailLoading = false;
		}
	}

	let hasMore = $derived(grs.length < total);
</script>

<PageHeader title={m('goodsReceipts.title')}>
	<DataTable
		columns={COLUMNS}
		isEmpty={grs.length === 0}
		empty={loading ? m('common.loading') : m('goodsReceipts.empty')}
		colspan={6}
	>
		{#snippet body()}
			{#each grs as gr (gr.id)}
				<tr
					class="clickable"
					onclick={(e) => {
						if (isRowOpenClick(e)) detailId = gr.id;
					}}
				>
					<td class="mono">
						<RowLink
							onclick={() => (detailId = gr.id)}
							ariaLabel={m('goodsReceipts.row.view', { number: gr.gr_number })}
						>
							{gr.gr_number}
						</RowLink>
					</td>
					<td class="mono">{gr.po_number ?? '—'}</td>
					<td>{formatDate(gr.received_date)}</td>
					<td><Badge tone="success" variant={gr.status}>{gr.status}</Badge></td>
					<td class="muted">{gr.line_count}</td>
					<td class="muted">{formatDate(gr.created_at)}</td>
				</tr>
			{/each}
		{/snippet}
	</DataTable>

	{#if hasMore}
		<div class="load-more-row">
			<button class="btn-load-more" onclick={loadMore} disabled={loadingMore}>
				{loadingMore ? m('common.loading') : m('goodsReceipts.loadMore', { shown: grs.length, total })}
			</button>
		</div>
	{:else if total > 0}
		<div class="load-more-row">
			<span class="load-more-end">{m('goodsReceipts.showingAll', { total })}</span>
		</div>
	{/if}
</PageHeader>

<Modal open={detailId !== null} ariaLabel={m('goodsReceipts.modal.aria')} onclose={() => (detailId = null)}>
	{#snippet header()}
		<header class="modal-header">
			<div class="title-block">
				<h2>{m('goodsReceipts.modal.title')}</h2>
				{#if detail}
					<span class="num-badge">{detail.gr_number}</span>
					<Badge tone="success" variant={detail.status}>{detail.status}</Badge>
				{/if}
			</div>
			<button class="close-btn" onclick={() => (detailId = null)} aria-label={m('goodsReceipts.modal.close')}>&times;</button>
		</header>
	{/snippet}

	<div class="modal-body">
		{#if detailLoading}
			<div class="loading">{m('common.loading')}</div>
		{:else if detail}
			<dl class="meta">
				<dt>{m('goodsReceipts.modal.po')}</dt><dd class="mono">{detail.po_number ?? '—'}</dd>
				<dt>{m('goodsReceipts.modal.received')}</dt><dd>{formatDate(detail.received_date)}</dd>
			</dl>

			<h3>{m('goodsReceipts.modal.lineItemsReceived')}</h3>
			<table class="line-table">
				<thead>
					<tr>
						<th>{m('goodsReceipts.modal.description')}</th>
						<th class="right">{m('goodsReceipts.modal.quantityReceived')}</th>
					</tr>
				</thead>
				<tbody>
					{#each detail.line_items as li (li.id)}
						<tr>
							<td>{li.description ?? '—'}</td>
							<td class="right mono">{li.quantity_received ?? '—'}</td>
						</tr>
					{:else}
						<tr><td colspan="2" class="empty">{m('goodsReceipts.modal.noLineItems')}</td></tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
</Modal>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	/* The status pill is `<Badge>`; the tone is `success` for EVERY status,
	   which is what the single hand-rolled `.badge` rule this replaced already
	   did — the conversion is faithful, not a re-colour. Worth knowing before
	   the next edit: `GoodsReceipt.status` is a free-form `String(30)`, and the
	   backend's own `po_matching.CANCELLED_GR_STATUSES` treats cancelled /
	   voided / reversed receipts as deliveries that did NOT happen, so those
	   currently badge green here. Giving them a `muted` tone means mirroring
	   that frozenset with a drift guard, which is its own change. */
	/* Detail modal — bespoke header / body layout not covered by the shared modal CSS. */
	.modal-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px 20px;
		border-bottom: 1px solid var(--border);
		margin: -24px -24px 0;
	}
	.title-block {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.modal-header h2 {
		margin: 0;
		font-size: 1.1rem;
		font-weight: 600;
	}
	.num-badge {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.85rem;
		color: var(--text-muted);
	}
	.close-btn {
		background: none;
		border: none;
		font-size: 1.5rem;
		cursor: pointer;
		color: var(--text-muted);
		line-height: 1;
		padding: 0 4px;
	}
	.close-btn:hover {
		color: var(--text);
	}
	.modal-body {
		padding: 20px 0 0;
		overflow-y: auto;
	}
	.modal-body h3 {
		margin: 18px 0 8px;
		font-size: 0.85rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}
	dl.meta {
		display: grid;
		grid-template-columns: 90px 1fr 90px 1fr;
		gap: 8px 14px;
		margin: 0 0 18px;
		padding-bottom: 14px;
		border-bottom: 1px solid var(--border);
	}
	dt {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
		align-self: center;
	}
	dd {
		margin: 0;
		font-size: 0.9rem;
	}
	.line-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	.line-table th {
		text-align: left;
		padding: 6px 10px;
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
	}
	.line-table td {
		padding: 6px 10px;
		border-bottom: 1px solid var(--border);
	}
	.line-table .mono {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
		font-size: 0.82rem;
	}
	.line-table .right {
		text-align: right;
	}
	.line-table .empty {
		text-align: center;
		padding: 40px;
		color: var(--text-muted);
	}
	.loading {
		padding: 40px;
		text-align: center;
		color: var(--text-muted);
	}
</style>
