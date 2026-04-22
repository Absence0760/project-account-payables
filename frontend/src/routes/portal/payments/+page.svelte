<script lang="ts">
	import { portalApi } from '$lib/portalApi';
	import { onMount } from 'svelte';

	interface PortalPayment {
		id: string;
		invoice_id: string;
		invoice_number: string;
		amount: number | string;
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

	async function refresh() {
		loading = true;
		error = '';
		try {
			const res = await portalApi.get<PaymentListResponse>('/api/portal/payments');
			items = res.items;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Load failed';
		} finally {
			loading = false;
		}
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString();
	}

	onMount(refresh);
</script>

<div class="page">
	<header>
		<h1>Payments</h1>
	</header>

	{#if error}<div class="error">{error}</div>{/if}

	{#if loading && !items.length}
		<div class="loading">Loading...</div>
	{:else if !items.length}
		<div class="empty">
			<p>No payments yet.</p>
			<p class="hint">Payments appear here once your customer has issued them.</p>
		</div>
	{:else}
		<table>
			<thead>
				<tr>
					<th>Invoice #</th>
					<th>Submitted</th>
					<th>Completed</th>
					<th>Method</th>
					<th class="num">Amount</th>
					<th>Status</th>
					<th>Reference</th>
				</tr>
			</thead>
			<tbody>
				{#each items as p}
					<tr>
						<td>{p.invoice_number}</td>
						<td>{fmtDate(p.submitted_at)}</td>
						<td>{fmtDate(p.completed_at)}</td>
						<td>{p.method || '—'}</td>
						<td class="num">{p.amount}</td>
						<td><span class="status s-{p.status}">{p.status}</span></td>
						<td>{p.reference || '—'}</td>
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
