<script lang="ts">
	import Money from '$lib/components/ui/Money.svelte';
	import StatusBadge from '$lib/components/ui/StatusBadge.svelte';
	import SpendBarChart from '$lib/components/assistant/SpendBarChart.svelte';
	import { formatMoney } from '$lib/utils/money';
	import type { InvoiceStatus } from '$lib/types/invoice';
	import { STATUS_LABELS } from '$lib/types/invoice';
	import type {
		ToolInvocation,
		VendorSpendResult,
		ForecastResult,
		InvoiceListResult,
		PendingApprovalsResult,
		TextSearchResult
	} from '$lib/types/assistant';

	let { invocation }: { invocation: ToolInvocation } = $props();

	const TOOL_TITLES: Record<string, string> = {
		get_vendor_spend: 'Vendor spend',
		get_payment_forecast: 'Payment forecast',
		list_invoices: 'Invoices',
		list_pending_approvals: 'Pending approvals',
		find_invoices_by_text: 'Matching invoices'
	};

	let title = $derived(TOOL_TITLES[invocation.tool] ?? invocation.tool);

	// Narrowed views of `result` per tool. The cast is safe because the page
	// only routes a tool's own result to its branch via `invocation.tool`.
	let result = $derived(invocation.result as Record<string, unknown> | null);

	function numOf(v: unknown): number {
		const n = typeof v === 'string' ? parseFloat(v) : Number(v);
		return Number.isFinite(n) ? n : 0;
	}

	function fmtDate(d?: string | null): string {
		if (!d) return '—';
		const dt = new Date(d.length === 7 ? `${d}-01` : d);
		if (Number.isNaN(dt.getTime())) return d;
		return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
	}

	// A tool status string maps onto the known InvoiceStatus union for the
	// badge; an unrecognised value still renders the badge label fallback.
	function asStatus(s: string): InvoiceStatus {
		return (s in STATUS_LABELS ? s : 'new') as InvoiceStatus;
	}

	let spend = $derived(
		invocation.tool === 'get_vendor_spend' ? (result as unknown as VendorSpendResult | null) : null
	);
	let forecast = $derived(
		invocation.tool === 'get_payment_forecast'
			? (result as unknown as ForecastResult | null)
			: null
	);
	let invoiceList = $derived(
		invocation.tool === 'list_invoices' ? (result as unknown as InvoiceListResult | null) : null
	);
	let approvals = $derived(
		invocation.tool === 'list_pending_approvals'
			? (result as unknown as PendingApprovalsResult | null)
			: null
	);
	let textSearch = $derived(
		invocation.tool === 'find_invoices_by_text'
			? (result as unknown as TextSearchResult | null)
			: null
	);

	let spendBars = $derived(
		spend
			? spend.vendors.map((v) => ({
					label: v.vendor_name,
					value: numOf(v.amount),
					amountLabel: formatMoney(v.amount, { currency: spend!.currency, whole: true }),
					sub: `${numOf(v.share_pct).toFixed(1)}%`
				}))
			: []
	);
	let forecastBars = $derived(
		forecast
			? forecast.buckets.map((b) => ({
					label: fmtDate(b.period),
					value: numOf(b.amount),
					amountLabel: formatMoney(b.amount, { currency: forecast!.currency, whole: true }),
					sub: `${b.count} inv`
				}))
			: []
	);
</script>

<div class="tool-result" data-tool={invocation.tool}>
	{#if invocation.error}
		<div class="tool-head">
			<span class="tool-title">{title}</span>
		</div>
		<p class="tool-err">Tool error: {invocation.error}</p>
	{:else if spend}
		<div class="tool-head">
			<span class="tool-title">{title}</span>
			<span class="tool-meta">
				{spend.period_label} · total {formatMoney(spend.total_spend, {
					currency: spend.currency,
					whole: true
				})}
			</span>
		</div>
		<SpendBarChart bars={spendBars} />
	{:else if forecast}
		<div class="tool-head">
			<span class="tool-title">{title}</span>
			<span class="tool-meta">
				{forecast.horizon_label} · total {formatMoney(forecast.total, {
					currency: forecast.currency,
					whole: true
				})}
			</span>
		</div>
		<SpendBarChart bars={forecastBars} />
	{:else if invoiceList}
		<div class="tool-head">
			<span class="tool-title">{title}</span>
			<span class="tool-meta">{invoiceList.total} total</span>
		</div>
		{#if invoiceList.items.length === 0}
			<p class="tool-empty">No invoices matched.</p>
		{:else}
			<table class="mini-table">
				<thead>
					<tr><th>Invoice</th><th>Vendor</th><th class="num">Amount</th><th>Status</th><th>Due</th></tr>
				</thead>
				<tbody>
					{#each invoiceList.items as row (row.id)}
						<tr>
							<td class="mono">{row.invoice_number}</td>
							<td>{row.vendor_name}</td>
							<td class="num"><Money amount={row.amount} currency={row.currency} mono /></td>
							<td><StatusBadge status={asStatus(row.status)} /></td>
							<td>{fmtDate(row.due_date)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	{:else if approvals}
		<div class="tool-head">
			<span class="tool-title">{title}</span>
			<span class="tool-meta">{approvals.total} total</span>
		</div>
		{#if approvals.items.length === 0}
			<p class="tool-empty">Nothing pending.</p>
		{:else}
			<table class="mini-table">
				<thead>
					<tr><th>Invoice</th><th>Vendor</th><th class="num">Amount</th><th>Waiting since</th></tr>
				</thead>
				<tbody>
					{#each approvals.items as row (row.invoice_id)}
						<tr>
							<td class="mono">{row.invoice_number}</td>
							<td>{row.vendor_name}</td>
							<td class="num"><Money amount={row.amount} currency={row.currency} mono /></td>
							<td>{fmtDate(row.waiting_since)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	{:else if textSearch}
		<div class="tool-head">
			<span class="tool-title">{title}</span>
			<span class="tool-meta">{textSearch.matches.length} match{textSearch.matches.length === 1 ? '' : 'es'}</span>
		</div>
		{#if textSearch.matches.length === 0}
			<p class="tool-empty">No matching invoices found.</p>
		{:else}
			<div class="match-list">
				{#each textSearch.matches as m (m.invoice_id)}
					<div class="match-card">
						<div class="match-head">
							<span class="match-vendor">{m.vendor_name ?? 'Unknown vendor'}</span>
							<span class="match-sim">{(m.similarity * 100).toFixed(0)}% match</span>
						</div>
						<p class="match-snippet">{m.snippet}</p>
					</div>
				{/each}
			</div>
		{/if}
	{:else}
		<!-- Unrecognised tool: render the structured payload as fallback JSON. -->
		<div class="tool-head">
			<span class="tool-title">{title}</span>
		</div>
		<pre class="tool-json">{JSON.stringify(invocation.result, null, 2)}</pre>
	{/if}
</div>

<style>
	.tool-result {
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 14px 16px;
		background: var(--surface);
		margin-top: 10px;
	}
	.tool-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 10px;
	}
	.tool-title {
		font-size: 0.82rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text);
	}
	.tool-meta {
		font-size: 0.76rem;
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
	}
	.tool-err {
		color: #e04040;
		font-size: 0.85rem;
		margin: 0;
	}
	.tool-empty {
		color: var(--text-muted);
		font-size: 0.85rem;
		margin: 0;
	}
	.mini-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.82rem;
	}
	.mini-table th {
		text-align: left;
		font-weight: 600;
		color: var(--text-muted);
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		padding: 4px 8px;
		border-bottom: 1px solid var(--border);
	}
	.mini-table td {
		padding: 6px 8px;
		border-bottom: 1px solid rgba(128, 128, 128, 0.12);
	}
	.mini-table tr:last-child td {
		border-bottom: none;
	}
	.mini-table .num {
		text-align: right;
	}
	.mono {
		font-variant-numeric: tabular-nums;
		font-family: var(--mono, ui-monospace, monospace);
	}
	.match-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.match-card {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 8px 12px;
	}
	.match-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 8px;
		margin-bottom: 4px;
	}
	.match-vendor {
		font-weight: 600;
		font-size: 0.84rem;
	}
	.match-sim {
		font-size: 0.74rem;
		color: var(--accent);
		font-variant-numeric: tabular-nums;
	}
	.match-snippet {
		margin: 0;
		font-size: 0.82rem;
		color: var(--text-muted);
		line-height: 1.4;
	}
	.tool-json {
		margin: 0;
		font-size: 0.76rem;
		background: rgba(128, 128, 128, 0.08);
		border-radius: 6px;
		padding: 10px;
		overflow-x: auto;
		max-height: 280px;
	}
</style>
