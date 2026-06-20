<script lang="ts">
	import { api } from '$lib/api';
	import { STATUS_LABELS } from '$lib/types/invoice';
	import type { InvoiceStatus } from '$lib/types/invoice';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';

	interface DashboardData {
		total_invoices: number;
		total_amount: number;
		total_paid: number;
		total_pending: number;
		total_rebates: number;
		touchless_rate: number;
		stale_approvals: number;
		open_exceptions: number;
		pipeline: Record<string, number>;
		vendor_spend: Array<{ vendor: string; amount: number }>;
		aging: {
			current: number;
			days_30: number;
			days_60: number;
			days_90: number;
			days_90_plus: number;
		};
		monthly_trend: Array<{ month: string; count: number; amount: number }>;
		upcoming_payments: Array<{
			id: string;
			invoice_number: string;
			vendor_name: string;
			amount: number;
			due_date: string | null;
			is_overdue: boolean;
		}>;
	}

	let data = $state<DashboardData | null>(null);
	let loading = $state(true);

	$effect(() => {
		orgCurrency.ensureLoaded();
		api.get<DashboardData>('/api/dashboard').then((res) => {
			data = res;
			loading = false;
		}).catch(() => {
			loading = false;
		});
	});

	// Dashboard figures are tenant-wide roll-ups with no per-row currency,
	// so they render in the org's configured default currency.
	function fmt(n: number): string {
		return formatMoney(n, { currency: orgCurrency.currency, whole: true });
	}

	function fmtFull(n: number): string {
		return formatMoney(n, { currency: orgCurrency.currency });
	}

	function formatDate(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
	}

	// Pipeline order
	const PIPELINE_ORDER: InvoiceStatus[] = [
		'new', 'pending', 'ready_for_review', 'approved',
		'sending_to_erp', 'sent_to_erp', 'posted_in_erp',
		'payment_scheduled', 'paid', 'done', 'failed', 'rejected',
	];

	const PIPELINE_COLORS: Record<string, string> = {
		new: '#638cff',
		pending: '#d4940a',
		ready_for_review: '#8b5cf6',
		approved: '#1fa86a',
		rejected: '#e04040',
		sending_to_erp: '#638cff',
		sent_to_erp: '#3b82f6',
		posted_in_erp: '#1fa86a',
		payment_scheduled: '#059669',
		paid: '#047857',
		done: '#6b7280',
		failed: '#e04040',
	};

	const AGING_COLORS = ['#1fa86a', '#d4940a', '#f59e0b', '#ea580c', '#e04040'];

	const agingBuckets = $derived(
		data
			? [
				{ label: 'Current', value: data.aging.current, color: AGING_COLORS[0] },
				{ label: '1-30 days', value: data.aging.days_30, color: AGING_COLORS[1] },
				{ label: '31-60 days', value: data.aging.days_60, color: AGING_COLORS[2] },
				{ label: '61-90 days', value: data.aging.days_90, color: AGING_COLORS[3] },
				{ label: '90+ days', value: data.aging.days_90_plus, color: AGING_COLORS[4] },
			]
			: []
	);

	let agingTotal = $derived(agingBuckets.reduce((sum, b) => sum + b.value, 0));

	let maxVendorSpend = $derived(
		data && data.vendor_spend.length > 0 ? Math.max(...data.vendor_spend.map(v => v.amount)) : 1
	);

	let maxTrendAmount = $derived(
		data && data.monthly_trend.length > 0 ? Math.max(...data.monthly_trend.map(t => t.amount)) : 1
	);
</script>

<PageHeader title="Dashboard">
	{#if loading}
		<p class="loading">Loading...</p>
	{:else if data}
		<!-- KPI Cards -->
		<div class="kpi-row">
			<KpiCard value={data.total_invoices} label="Invoices" />
			<KpiCard value={fmt(data.total_amount)} label="Total Amount" />
			<KpiCard value={fmt(data.total_paid)} label="Paid" />
			<KpiCard value={fmt(data.total_pending)} label="Pending" />
			<KpiCard
				value={`${data.touchless_rate}%`}
				label="Touchless Rate"
				highlight={data.touchless_rate >= 80 ? 'green' : null}
			/>
			{#if data.open_exceptions > 0}
				<a href="/exceptions" class="kpi highlight-red kpi-link">
					<span class="kpi-value">{data.open_exceptions}</span>
					<span class="kpi-label">Exceptions</span>
				</a>
			{/if}
			{#if data.stale_approvals > 0}
				<KpiCard value={data.stale_approvals} label="Stale Approvals" highlight="red" />
			{/if}
			{#if data.total_rebates > 0}
				<KpiCard value={fmt(data.total_rebates)} label="Rebates Earned" highlight="green" />
			{/if}
		</div>

		<div class="charts-grid">
			<!-- Invoice Pipeline -->
			<div class="chart-card">
				<h2>Invoice Pipeline</h2>
				<div class="pipeline">
					{#each PIPELINE_ORDER.filter(s => (data?.pipeline[s] ?? 0) > 0) as status}
						{@const count = data?.pipeline[status] ?? 0}
						{@const pct = data ? Math.max(count / data.total_invoices * 100, 4) : 0}
						<div class="pipeline-row">
							<span class="pipeline-label">{STATUS_LABELS[status]}</span>
							<div class="pipeline-bar-bg">
								<div class="pipeline-bar" style="width:{pct}%;background:{PIPELINE_COLORS[status] ?? '#888'}"></div>
							</div>
							<span class="pipeline-count">{count}</span>
						</div>
					{/each}
				</div>
			</div>

			<!-- Spend by Vendor -->
			<div class="chart-card">
				<h2>Top Vendors by Spend</h2>
				{#if data.vendor_spend.length > 0}
					<div class="vendor-bars">
						{#each data.vendor_spend as v}
							<div class="vendor-row">
								<span class="vendor-name" title={v.vendor}>{v.vendor}</span>
								<div class="vendor-bar-bg">
									<div class="vendor-bar" style="width:{(v.amount / maxVendorSpend * 100)}%"></div>
								</div>
								<span class="vendor-amount">{fmt(v.amount)}</span>
							</div>
						{/each}
					</div>
				{:else}
					<p class="empty">No invoice data yet.</p>
				{/if}
			</div>

			<!-- Aging -->
			<div class="chart-card">
				<h2>Invoice Aging</h2>
					{#if agingTotal > 0}
					<div class="aging-bar">
						{#each agingBuckets as bucket}
							{#if bucket.value > 0}
								<div
									class="aging-segment"
									style="width:{(bucket.value / agingTotal * 100)}%;background:{bucket.color}"
									title="{bucket.label}: {fmtFull(bucket.value)}"
								></div>
							{/if}
						{/each}
					</div>
					<div class="aging-legend">
						{#each agingBuckets as bucket}
							<div class="aging-item">
								<span class="aging-dot" style="background:{bucket.color}"></span>
								<span class="aging-label">{bucket.label}</span>
								<span class="aging-value">{fmt(bucket.value)}</span>
							</div>
						{/each}
					</div>
				{:else}
					<p class="empty">No open invoices with due dates.</p>
				{/if}
			</div>

			<!-- Upcoming Payments -->
			<div class="chart-card">
				<h2>Upcoming & Overdue</h2>
				{#if data.upcoming_payments.length > 0}
					<div class="upcoming-list">
						{#each data.upcoming_payments as inv}
							<div class="upcoming-row" class:overdue={inv.is_overdue}>
								<div class="upcoming-info">
									<span class="upcoming-vendor">{inv.vendor_name}</span>
									<span class="upcoming-inv">{inv.invoice_number}</span>
								</div>
								<span class="upcoming-amount">{fmtFull(inv.amount)}</span>
								<span class="upcoming-date" class:overdue-text={inv.is_overdue}>
									{formatDate(inv.due_date)}
									{#if inv.is_overdue}
										<span class="overdue-badge">Overdue</span>
									{/if}
								</span>
							</div>
						{/each}
					</div>
				{:else}
					<p class="empty">No upcoming payments this week.</p>
				{/if}
			</div>

			<!-- Monthly Trend -->
			{#if data.monthly_trend.length > 0}
				<div class="chart-card wide">
					<h2>Monthly Volume</h2>
					<div class="trend-chart">
						{#each data.monthly_trend as month}
							<div class="trend-bar-group">
								<div class="trend-bar-wrap">
									<div
										class="trend-bar"
										style="height:{maxTrendAmount > 0 ? (month.amount / maxTrendAmount * 100) : 0}%"
										title="{fmt(month.amount)} ({month.count} invoices)"
									></div>
								</div>
								<span class="trend-label">{month.month.slice(5)}</span>
								<span class="trend-value">{fmt(month.amount)}</span>
							</div>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</PageHeader>

<style>
	/* Page-specific styling; shared design-system CSS lives in app.css. */
	h2 {
		margin: 0 0 14px;
		font-size: 0.88rem;
		font-weight: 600;
		color: var(--text);
	}

	.loading {
		color: var(--text-muted);
	}

	.empty {
		color: var(--text-muted);
		font-size: 0.85rem;
		text-align: center;
		padding: 20px;
	}

	/* Exceptions KPI renders as a link, so it stays inline markup using the
	   global .kpi classes plus this bespoke hover affordance. */
	.kpi-link {
		text-decoration: none;
		cursor: pointer;
		transition: transform 0.1s;
	}

	.kpi-link:hover {
		transform: translateY(-2px);
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
	}

	/* Charts Grid */
	.charts-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 16px;
	}

	.chart-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 18px 20px;
		/* WCAG 1.4.10: grid items default to min-width:auto, which keeps the
		   chart at its intrinsic width and scrolls the whole page on a narrow
		   viewport. min-width:0 lets the track (and chart) shrink to reflow. */
		min-width: 0;
	}

	/* Single column on narrow viewports so the two-up charts reflow. */
	@media (max-width: 700px) {
		.charts-grid {
			grid-template-columns: 1fr;
		}
	}

	.chart-card.wide {
		grid-column: 1 / -1;
	}

	/* Pipeline */
	.pipeline {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.pipeline-row {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	.pipeline-label {
		width: 110px;
		font-size: 0.78rem;
		color: var(--text-muted);
		text-align: right;
		flex-shrink: 0;
	}

	.pipeline-bar-bg {
		flex: 1;
		height: 20px;
		background: var(--bg);
		border-radius: 4px;
		overflow: hidden;
	}

	.pipeline-bar {
		height: 100%;
		border-radius: 4px;
		transition: width 0.3s ease;
	}

	.pipeline-count {
		width: 30px;
		font-size: 0.82rem;
		font-weight: 600;
		color: var(--text);
		text-align: right;
	}

	/* Vendor Spend */
	.vendor-bars {
		display: flex;
		flex-direction: column;
		gap: 7px;
	}

	.vendor-row {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.vendor-name {
		width: 120px;
		font-size: 0.78rem;
		color: var(--text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex-shrink: 0;
	}

	.vendor-bar-bg {
		flex: 1;
		height: 16px;
		background: var(--bg);
		border-radius: 3px;
		overflow: hidden;
	}

	.vendor-bar {
		height: 100%;
		background: var(--accent);
		border-radius: 3px;
		opacity: 0.7;
	}

	.vendor-amount {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		width: 70px;
		text-align: right;
		flex-shrink: 0;
	}

	/* Aging */
	.aging-bar {
		display: flex;
		height: 24px;
		border-radius: 6px;
		overflow: hidden;
		margin-bottom: 12px;
	}

	.aging-segment {
		transition: width 0.3s ease;
		cursor: help;
	}

	.aging-legend {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
	}

	.aging-item {
		display: flex;
		align-items: center;
		gap: 5px;
	}

	.aging-dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		flex-shrink: 0;
	}

	.aging-label {
		font-size: 0.75rem;
		color: var(--text-muted);
	}

	.aging-value {
		font-size: 0.75rem;
		font-weight: 600;
		color: var(--text);
	}

	/* Upcoming */
	.upcoming-list {
		display: flex;
		flex-direction: column;
		gap: 0;
	}

	.upcoming-row {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--border);
	}

	.upcoming-row:last-child {
		border-bottom: none;
	}

	.upcoming-row.overdue {
		background: rgba(224, 64, 64, 0.04);
		margin: 0 -20px;
		padding: 8px 20px;
	}

	.upcoming-info {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 1px;
		min-width: 0;
	}

	.upcoming-vendor {
		font-size: 0.82rem;
		font-weight: 500;
		color: var(--text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.upcoming-inv {
		font-size: 0.72rem;
		color: var(--text-muted);
		font-family: 'SF Mono', 'Cascadia Code', monospace;
	}

	.upcoming-amount {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--text);
		white-space: nowrap;
	}

	.upcoming-date {
		font-size: 0.78rem;
		color: var(--text-muted);
		white-space: nowrap;
		min-width: 80px;
		text-align: right;
	}

	.overdue-text {
		color: #f06464;
	}

	.overdue-badge {
		display: inline-block;
		font-size: 0.62rem;
		font-weight: 600;
		padding: 1px 5px;
		border-radius: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #f06464;
		margin-left: 4px;
	}

	/* Trend */
	.trend-chart {
		display: flex;
		align-items: flex-end;
		gap: 8px;
		height: 160px;
		padding-top: 10px;
	}

	.trend-bar-group {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 4px;
		height: 100%;
	}

	.trend-bar-wrap {
		flex: 1;
		width: 100%;
		display: flex;
		align-items: flex-end;
	}

	.trend-bar {
		width: 100%;
		background: var(--accent);
		border-radius: 4px 4px 0 0;
		opacity: 0.7;
		min-height: 4px;
		transition: height 0.3s ease;
		cursor: help;
	}

	.trend-bar:hover {
		opacity: 1;
	}

	.trend-label {
		font-size: 0.7rem;
		color: var(--text-muted);
	}

	.trend-value {
		font-size: 0.68rem;
		color: var(--text-muted);
		font-weight: 500;
	}

	@media (max-width: 768px) {
		.charts-grid {
			grid-template-columns: 1fr;
		}

		.kpi-row {
			flex-direction: column;
		}
	}
</style>
