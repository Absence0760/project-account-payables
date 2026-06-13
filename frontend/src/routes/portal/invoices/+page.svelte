<script lang="ts">
	import { portalApi } from '$lib/portalApi';
	import { onMount } from 'svelte';
	import { formatMoney } from '$lib/utils/money';

	interface PortalInvoice {
		id: string;
		invoice_number: string;
		amount: number | string;
		currency: string;
		status: string;
		invoice_date: string | null;
		due_date: string | null;
		submitted_at: string;
		file_url: string | null;
	}

	interface InvoiceListResponse {
		items: PortalInvoice[];
		total: number;
	}

	let items = $state<PortalInvoice[]>([]);
	let loading = $state(false);
	let uploading = $state(false);
	let error = $state('');
	let message = $state('');

	async function refresh() {
		loading = true;
		error = '';
		try {
			const res = await portalApi.get<InvoiceListResponse>('/api/portal/invoices');
			items = res.items;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Load failed';
		} finally {
			loading = false;
		}
	}

	async function handleUpload(e: Event) {
		const input = e.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		uploading = true;
		error = '';
		message = '';
		try {
			await portalApi.upload<{ message: string }>('/api/portal/invoices', file);
			message = 'Invoice submitted. Your customer has been notified.';
			input.value = '';
			await refresh();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Upload failed';
		} finally {
			uploading = false;
		}
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString();
	}

	function fmtAmount(amount: number | string, ccy: string): string {
		return formatMoney(amount, { currency: ccy }, formatMoney(0, { currency: ccy }));
	}

	onMount(refresh);
</script>

<div class="page">
	<header>
		<h1>My Invoices</h1>
		<label class="upload-btn" class:uploading>
			<input type="file" accept="application/pdf,image/*" onchange={handleUpload} disabled={uploading} />
			{uploading ? 'Submitting...' : 'Submit invoice'}
		</label>
	</header>

	{#if error}<div class="error">{error}</div>{/if}
	{#if message}<div class="msg">{message}</div>{/if}

	{#if loading && !items.length}
		<div class="loading">Loading...</div>
	{:else if !items.length}
		<div class="empty">
			<p>You haven't submitted any invoices yet.</p>
			<p class="hint">Upload a PDF or photo using the button above.</p>
		</div>
	{:else}
		<table>
			<thead>
				<tr>
					<th>Invoice #</th>
					<th>Submitted</th>
					<th>Invoice date</th>
					<th>Due</th>
					<th class="num">Amount</th>
					<th>Status</th>
				</tr>
			</thead>
			<tbody>
				{#each items as inv}
					<tr>
						<td>{inv.invoice_number || '(pending extraction)'}</td>
						<td>{fmtDate(inv.submitted_at)}</td>
						<td>{fmtDate(inv.invoice_date)}</td>
						<td>{fmtDate(inv.due_date)}</td>
						<td class="num">{fmtAmount(inv.amount, inv.currency)}</td>
						<td><span class="status s-{inv.status}">{inv.status}</span></td>
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
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 20px;
	}
	h1 {
		margin: 0;
		font-size: 1.25rem;
	}
	.upload-btn {
		display: inline-block;
		background: var(--accent);
		color: #fff;
		padding: 8px 14px;
		border-radius: 4px;
		cursor: pointer;
		font-size: 0.85rem;
	}
	.upload-btn.uploading {
		opacity: 0.6;
		cursor: default;
	}
	.upload-btn input {
		display: none;
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
	.s-paid {
		background: rgba(40, 160, 80, 0.15);
		border-color: rgba(40, 160, 80, 0.4);
	}
	.s-rejected {
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
	.msg {
		background: rgba(40, 160, 80, 0.12);
		border: 1px solid rgba(40, 160, 80, 0.35);
		color: #2a9255;
		padding: 10px 14px;
		border-radius: 4px;
		margin-bottom: 12px;
	}
</style>
