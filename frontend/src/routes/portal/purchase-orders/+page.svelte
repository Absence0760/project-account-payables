<script lang="ts">
	import { portalApi } from '$lib/portalApi';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { m } from '$lib/i18n/store.svelte';

	interface PortalPO {
		id: string;
		po_number: string;
		status: string;
		total: number | string;
		currency: string;
		line_item_count: number;
		created_at: string;
	}

	interface POListResponse {
		items: PortalPO[];
		total: number;
	}

	let items = $state<PortalPO[]>([]);
	let loading = $state(false);
	let error = $state('');
	let message = $state('');
	let flipping = $state<string | null>(null);

	async function refresh() {
		loading = true;
		error = '';
		try {
			const res = await portalApi.get<POListResponse>('/api/portal/purchase-orders');
			items = res.items;
		} catch (err) {
			error = err instanceof Error ? err.message : m('portal.po.loadFailed');
		} finally {
			loading = false;
		}
	}

	async function flip(po: PortalPO) {
		flipping = po.id;
		error = '';
		message = '';
		try {
			await portalApi.post<{ message: string }>(
				`/api/portal/purchase-orders/${po.id}/flip`,
				{}
			);
			message = m('portal.po.created', { po: po.po_number });
			// Land the supplier on their invoices so they can see it in the queue.
			await goto('/portal/invoices');
		} catch (err) {
			error = err instanceof Error ? err.message : m('portal.po.createFailed');
		} finally {
			flipping = null;
		}
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return m('portal.common.dash');
		return new Date(iso).toLocaleDateString();
	}

	onMount(refresh);
</script>

<div class="page">
	<header>
		<h1>{m('portal.po.title')}</h1>
	</header>

	{#if error}<div class="error" role="alert">{error}</div>{/if}
	{#if message}<div class="message">{message}</div>{/if}

	{#if loading && !items.length}
		<div class="loading">{m('portal.common.loading')}</div>
	{:else if !items.length}
		<div class="empty">
			<p>{m('portal.po.empty')}</p>
			<p class="hint">{m('portal.po.emptyHint')}</p>
		</div>
	{:else}
		<table>
			<thead>
				<tr>
					<th>{m('portal.po.col.poNumber')}</th>
					<th>{m('portal.po.col.created')}</th>
					<th>{m('portal.po.col.lines')}</th>
					<th class="num">{m('portal.po.col.total')}</th>
					<th>{m('portal.po.col.status')}</th>
					<th class="actions-col"></th>
				</tr>
			</thead>
			<tbody>
				{#each items as po (po.id)}
					<tr>
						<td>{po.po_number}</td>
						<td>{fmtDate(po.created_at)}</td>
						<td>{po.line_item_count}</td>
						<td class="num"><Money amount={po.total} currency={po.currency} /></td>
						<td><span class="status s-{po.status}">{po.status}</span></td>
						<td class="actions">
							<button
								type="button"
								class="flip-btn"
								disabled={flipping === po.id}
								onclick={() => flip(po)}
							>
								{flipping === po.id ? m('portal.po.creating') : m('portal.po.createInvoice')}
							</button>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>

<style>
	.page {
		max-width: 1100px;
		margin: 0 auto;
	}
	header {
		margin-bottom: 20px;
	}
	h1 {
		margin: 0;
		font-size: 1.25rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 4px;
		overflow: hidden;
	}
	th,
	td {
		text-align: left;
		padding: 10px 12px;
		font-size: 0.88rem;
		border-bottom: 1px solid var(--border);
	}
	th {
		background: var(--bg);
		color: var(--text-muted);
		font-weight: 500;
		text-transform: uppercase;
		font-size: 0.72rem;
		letter-spacing: 0.04em;
	}
	tbody tr:last-child td {
		border-bottom: none;
	}
	.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 6px;
		white-space: nowrap;
	}
	.flip-btn {
		padding: 4px 12px;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: transparent;
		color: var(--text);
		font-size: 0.82rem;
		cursor: pointer;
	}
	.flip-btn:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.flip-btn:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.status {
		padding: 2px 8px;
		border-radius: 3px;
		font-size: 0.75rem;
		background: var(--bg);
		border: 1px solid var(--border);
	}
	.empty,
	.loading {
		padding: 40px;
		text-align: center;
		background: var(--surface);
		border: 1px dashed var(--border);
		border-radius: 4px;
		color: var(--text-muted);
	}
	.empty .hint {
		font-size: 0.82rem;
	}
	.error {
		background: rgba(224, 64, 64, 0.1);
		border: 1px solid rgba(224, 64, 64, 0.3);
		color: #e04040;
		padding: 10px 14px;
		border-radius: 4px;
		margin-bottom: 12px;
	}
	.message {
		background: rgba(40, 160, 80, 0.12);
		border: 1px solid rgba(40, 160, 80, 0.35);
		color: #1f7a44;
		padding: 10px 14px;
		border-radius: 4px;
		margin-bottom: 12px;
	}
</style>
