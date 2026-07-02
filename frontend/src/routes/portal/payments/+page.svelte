<script lang="ts">
	import { portalApi } from '$lib/portalApi';
	import { onMount } from 'svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { formatDate } from '$lib/utils/time';
	import { m } from '$lib/i18n/store.svelte';

	interface PortalPayment {
		id: string;
		invoice_id: string;
		invoice_number: string;
		amount: number | string;
		currency: string;
		method: string | null;
		status: string;
		reference: string | null;
		submitted_at: string | null;
		completed_at: string | null;
	}

	interface PaymentListResponse {
		items: PortalPayment[];
		total: number;
	}

	let items = $state<PortalPayment[]>([]);
	let loading = $state(false);
	let error = $state('');
	let downloading = $state<string | null>(null);

	async function refresh() {
		loading = true;
		error = '';
		try {
			const res = await portalApi.get<PaymentListResponse>('/api/portal/payments');
			items = res.items;
		} catch (err) {
			error = err instanceof Error ? err.message : m('portal.payments.loadFailed');
		} finally {
			loading = false;
		}
	}

	async function downloadRemittance(p: PortalPayment) {
		downloading = p.id;
		error = '';
		try {
			const blob = await portalApi.download(`/api/portal/payments/${p.id}/remittance`);
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `remittance-${p.reference || p.id.slice(0, 8)}.pdf`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		} catch (err) {
			error = err instanceof Error ? err.message : m('portal.payments.downloadFailed');
		} finally {
			downloading = null;
		}
	}

	onMount(refresh);
</script>

<div class="page">
	<header>
		<h1>{m('portal.payments.title')}</h1>
	</header>

	{#if error}<div class="error" role="alert">{error}</div>{/if}

	{#if loading && !items.length}
		<div class="loading">{m('portal.common.loading')}</div>
	{:else if !items.length}
		<div class="empty">
			<p>{m('portal.payments.empty')}</p>
			<p class="hint">{m('portal.payments.emptyHint')}</p>
		</div>
	{:else}
		<table>
			<thead>
				<tr>
					<th>{m('portal.payments.col.invoiceNumber')}</th>
					<th>{m('portal.payments.col.submitted')}</th>
					<th>{m('portal.payments.col.completed')}</th>
					<th>{m('portal.payments.col.method')}</th>
					<th class="num">{m('portal.payments.col.amount')}</th>
					<th>{m('portal.payments.col.status')}</th>
					<th>{m('portal.payments.col.reference')}</th>
					<th class="actions-col"></th>
				</tr>
			</thead>
			<tbody>
				{#each items as p}
					<tr>
						<td>{p.invoice_number}</td>
						<td>{formatDate(p.submitted_at, m('portal.common.dash'))}</td>
						<td>{formatDate(p.completed_at, m('portal.common.dash'))}</td>
						<td>{p.method || m('portal.common.dash')}</td>
						<td class="num"><Money amount={p.amount} currency={p.currency} /></td>
						<td><span class="status s-{p.status}">{p.status}</span></td>
						<td>{p.reference || m('portal.common.dash')}</td>
						<td class="actions">
							{#if p.status === 'completed'}
								<button
									type="button"
									class="remit-btn"
									disabled={downloading === p.id}
									onclick={() => downloadRemittance(p)}
								>
									{downloading === p.id ? m('portal.payments.preparing') : m('portal.payments.downloadRemittance')}
								</button>
							{/if}
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
	.remit-btn {
		padding: 4px 12px;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: transparent;
		color: var(--text);
		font-size: 0.8rem;
		cursor: pointer;
	}
	.remit-btn:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.remit-btn:disabled {
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
	.s-completed {
		background: rgba(40, 160, 80, 0.15);
		border-color: rgba(40, 160, 80, 0.4);
	}
	.s-failed {
		background: rgba(224, 64, 64, 0.12);
		border-color: rgba(224, 64, 64, 0.35);
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
</style>
