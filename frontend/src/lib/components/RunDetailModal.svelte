<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/Toast.svelte';
	import { PAYMENT_METHOD_LABELS } from '$lib/types/payment';
	import type { PaymentMethod } from '$lib/types/payment';

	let {
		runId,
		onclose,
		onchange,
	}: {
		runId: string;
		onclose: () => void;
		// Fired after Execute completes so the parent can refresh queue + summary + runs list.
		onchange?: () => void;
	} = $props();

	interface RunPayment {
		id: string;
		invoice_id: string;
		invoice_number: string | null;
		vendor_name: string | null;
		amount: number;
		method: string | null;
		status: string;
		reference: string | null;
	}

	interface RunDetail {
		id: string;
		status: string;
		total_amount: number;
		initiated_by: string | null;
		executed_at: string | null;
		created_at: string;
		payments: RunPayment[];
	}

	let run = $state<RunDetail | null>(null);
	let loading = $state(true);
	let executing = $state(false);
	let error = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			run = await api.get<RunDetail>(`/api/payments/runs/${runId}`);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load payment run';
		} finally {
			loading = false;
		}
	}

	onMount(load);

	async function execute() {
		if (!run || run.status !== 'draft') return;
		executing = true;
		try {
			const result = await api.post<{ message: string }>(
				`/api/payments/runs/${runId}/execute`,
				{}
			);
			toast(result.message, 'success');
			await load();
			onchange?.();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Execution failed', 'error');
		} finally {
			executing = false;
		}
	}

	function handleBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}

	function fmt(amount: number, currency = 'USD'): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);
	}

	function fmtDate(s: string | null): string {
		if (!s) return '—';
		return new Date(s).toLocaleString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric',
			hour: 'numeric',
			minute: '2-digit',
		});
	}

	function methodLabel(m: string | null): string {
		if (!m) return '—';
		return PAYMENT_METHOD_LABELS[m as PaymentMethod] ?? m;
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions a11y_no_noninteractive_element_interactions -->
<div class="backdrop" onclick={handleBackdrop} role="presentation">
	<div class="modal" role="dialog" aria-label="Payment run">
		<header>
			<div class="title-block">
				<h2>Payment Run</h2>
				{#if run}
					<span class="run-id">{run.id.slice(0, 8)}</span>
					<span class="status-badge {run.status}">{run.status}</span>
				{/if}
			</div>
			<button class="close-btn" onclick={onclose} aria-label="Close">&times;</button>
		</header>

		<div class="body">
			{#if loading}
				<div class="loading">Loading…</div>
			{:else if error}
				<div class="error">{error}</div>
			{:else if run}
				<dl class="meta">
					<dt>Total</dt>
					<dd class="total">{fmt(run.total_amount)}</dd>
					<dt>Payments</dt>
					<dd>{run.payments.length}</dd>
					<dt>Created</dt>
					<dd>{fmtDate(run.created_at)}</dd>
					{#if run.executed_at}
						<dt>Executed</dt>
						<dd>{fmtDate(run.executed_at)}</dd>
					{/if}
				</dl>

				<table>
					<thead>
						<tr>
							<th>Invoice</th>
							<th>Vendor</th>
							<th class="right">Amount</th>
							<th>Method</th>
							<th>Status</th>
							<th>Reference</th>
						</tr>
					</thead>
					<tbody>
						{#each run.payments as p (p.id)}
							<tr>
								<td class="mono">{p.invoice_number ?? '—'}</td>
								<td>{p.vendor_name ?? '—'}</td>
								<td class="right mono">{fmt(p.amount)}</td>
								<td>{methodLabel(p.method)}</td>
								<td><span class="badge {p.status}">{p.status}</span></td>
								<td class="mono muted">{p.reference ?? '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>

		<footer>
			{#if run?.status === 'draft'}
				<p class="footer-note">
					This run is still a <strong>draft</strong>. No money will move until you
					execute it.
				</p>
				<div class="actions">
					<button class="btn-cancel" onclick={onclose}>Close</button>
					<button class="btn-execute" disabled={executing} onclick={execute}>
						{executing ? 'Executing…' : `Execute · ${fmt(run.total_amount)}`}
					</button>
				</div>
			{:else}
				<div class="actions">
					<button class="btn-cancel" onclick={onclose}>Close</button>
				</div>
			{/if}
		</footer>
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: grid;
		place-items: center;
		z-index: 100;
		backdrop-filter: blur(2px);
	}

	.modal {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		width: min(820px, 95vw);
		max-height: 90vh;
		display: flex;
		flex-direction: column;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
	}

	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px 20px;
		border-bottom: 1px solid var(--border);
	}

	.title-block {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	h2 {
		margin: 0;
		font-size: 1.1rem;
		font-weight: 600;
	}

	.run-id {
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
		font-size: 0.82rem;
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

	.body {
		padding: 20px;
		overflow-y: auto;
		flex: 1;
	}

	.loading,
	.error {
		padding: 40px;
		text-align: center;
		color: var(--text-muted);
	}

	.error {
		color: #e04040;
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

	dd.total {
		font-weight: 700;
		font-size: 1rem;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}

	th {
		background: var(--bg);
		text-align: left;
		padding: 8px 10px;
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
		white-space: nowrap;
	}

	td {
		padding: 8px 10px;
		border-bottom: 1px solid var(--border);
		white-space: nowrap;
	}

	tr:last-child td {
		border-bottom: none;
	}

	.mono {
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
		font-size: 0.8rem;
	}

	.right {
		text-align: right;
	}

	.muted {
		color: var(--text-muted);
	}

	.badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: capitalize;
	}

	.badge.pending {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}

	.badge.processing {
		background: rgba(99, 140, 255, 0.15);
		color: #638cff;
	}

	.badge.completed {
		background: rgba(50, 200, 130, 0.15);
		color: #1fa86a;
	}

	.badge.failed {
		background: rgba(240, 70, 70, 0.15);
		color: #e04040;
	}

	.status-badge {
		display: inline-block;
		padding: 2px 10px;
		border-radius: 10px;
		font-size: 0.74rem;
		font-weight: 600;
		text-transform: capitalize;
	}

	.status-badge.draft {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}

	.status-badge.completed {
		background: rgba(50, 200, 130, 0.15);
		color: #1fa86a;
	}

	footer {
		padding: 14px 20px;
		border-top: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.footer-note {
		margin: 0;
		font-size: 0.8rem;
		color: var(--text-muted);
	}

	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
	}

	.btn-cancel {
		padding: 8px 16px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-cancel:hover {
		background: var(--bg);
	}

	.btn-execute {
		padding: 8px 20px;
		border-radius: 4px;
		border: none;
		background: #1fa86a;
		color: #fff;
		font-size: 0.88rem;
		font-weight: 600;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-execute:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-execute:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
