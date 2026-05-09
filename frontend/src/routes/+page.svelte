<script lang="ts">
	import { api } from '$lib/api';
	import { STATUS_LABELS } from '$lib/types/invoice';
	import type { InvoiceStatus } from '$lib/types/invoice';

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
		aging: { current: number; days_30: number; days_60: number; days_90_plus: number };
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
		api.get<DashboardData>('/api/dashboard').then((res) => {
			data = res;
			loading = false;
		}).catch(() => {
			loading = false;
		});
	});

	function fmt(n: number): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(n);
	}

	function fmtFull(n: number): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
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

	const AGING_COLORS = ['#1fa86a', '#d4940a', '#f59e0b', '#e04040'];

	let agingTotal = $derived(
		data ? data.aging.current + data.aging.days_30 + data.aging.days_60 + data.aging.days_90_plus : 0
	);

	let maxVendorSpend = $derived(
		data && data.vendor_spend.length > 0 ? Math.max(...data.vendor_spend.map(v => v.amount)) : 1
	);

	let maxTrendAmount = $derived(
		data && data.monthly_trend.length > 0 ? Math.max(...data.monthly_trend.map(t => t.amount)) : 1
	);
</script>

<div class="dashboard">
	<h1>Dashboard</h1>

	{#if loading}
		<p class="loading">Loading...</p>
	{:else if data}
		<!-- KPI Cards -->
		<div class="kpi-row">
			<div class="kpi">
				<span class="kpi-value">{data.total_invoices}</span>
				<span class="kpi-label">Invoices</span>
			</div>
			<div class="kpi">
				<span class="kpi-value">{fmt(data.total_amount)}</span>
				<span class="kpi-label">Total Amount</span>
			</div>
			<div class="kpi">
				<span class="kpi-value">{fmt(data.total_paid)}</span>
				<span class="kpi-label">Paid</span>
			</div>
			<div class="kpi">
				<span class="kpi-value">{fmt(data.total_pending)}</span>
				<span class="kpi-label">Pending</span>
			</div>
			<div class="kpi" class:highlight-green={data.touchless_rate >= 80}>
				<span class="kpi-value">{data.touchless_rate}%</span>
				<span class="kpi-label">Touchless Rate</span>
			</div>
			{#if data.open_exceptions > 0}
				<a href="/exceptions" class="kpi highlight-red kpi-link">
					<span class="kpi-value">{data.open_exceptions}</span>
					<span class="kpi-label">Exceptions</span>
				</a>
			{/if}
			{#if data.stale_approvals > 0}
				<div class="kpi highlight-red">
					<span class="kpi-value">{data.stale_approvals}</span>
					<span class="kpi-label">Stale Approvals</span>
				</div>
			{/if}
			{#if data.total_rebates > 0}
				<div class="kpi highlight-green">
					<span class="kpi-value">{fmt(data.total_rebates)}</span>
					<span class="kpi-label">Rebates Earned</span>
				</div>
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
						{#each [
							{ label: 'Current', value: data.aging.current, color: AGING_COLORS[0] },
							{ label: '1-30 days', value: data.aging.days_30, color: AGING_COLORS[1] },
							{ label: '31-60 days', value: data.aging.days_60, color: AGING_COLORS[2] },
							{ label: '90+ days', value: data.aging.days_90_plus, color: AGING_COLORS[3] },
						] as bucket}
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
						{#each [
							{ label: 'Current', value: data.aging.current, color: AGING_COLORS[0] },
							{ label: '1-30 days', value: data.aging.days_30, color: AGING_COLORS[1] },
							{ label: '31-60 days', value: data.aging.days_60, color: AGING_COLORS[2] },
							{ label: '90+ days', value: data.aging.days_90_plus, color: AGING_COLORS[3] },
						] as bucket}
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
</div>

<style>
	.dashboard {
		padding: 24px 20px;
		max-width: 1280px;
		margin: 0 auto;
	}

	h1 {
		margin: 0 0 20px;
		font-size: 1.3rem;
		font-weight: 700;
	}

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

	/* KPI Row */
	.kpi-row {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
		margin-bottom: 20px;
	}

	.kpi {
		flex: 1;
		min-width: 130px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 16px 18px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.kpi.highlight-green {
		border-color: rgba(31, 168, 106, 0.3);
		background: rgba(31, 168, 106, 0.04);
	}

	.kpi.highlight-green .kpi-value {
		color: #1fa86a;
	}

	.kpi.highlight-red {
		border-color: rgba(224, 64, 64, 0.3);
		background: rgba(224, 64, 64, 0.04);
	}

	.kpi.highlight-red .kpi-value {
		color: #e04040;
	}

	.kpi-link {
		text-decoration: none;
		cursor: pointer;
		transition: transform 0.1s;
	}

	.kpi-link:hover {
		transform: translateY(-2px);
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
	}

	.kpi-value {
		font-size: 1.4rem;
		font-weight: 700;
		color: var(--text);
	}

	.kpi-label {
		font-size: 0.72rem;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
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
		color: #e04040;
	}

	.overdue-badge {
		display: inline-block;
		font-size: 0.62rem;
		font-weight: 600;
		padding: 1px 5px;
		border-radius: 6px;
		background: rgba(224, 64, 64, 0.12);
		color: #e04040;
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

		.kpi {
			min-width: unset;
		}
	}
</style>
