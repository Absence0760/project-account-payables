<script lang="ts">
	import { api } from '$lib/api';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import ByEntityBreakdown from '$lib/components/analytics/ByEntityBreakdown.svelte';
	import CfoMetrics from '$lib/components/analytics/CfoMetrics.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { formatPeriod } from '$lib/utils/time';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import type {
		CashflowForecast,
		CashflowGranularity,
		CashPosition,
		WhatIfScenarios
	} from '$lib/types/analytics';

	let granularity = $state<CashflowGranularity>('week');
	let horizonDays = $state(90);
	let openingBalance = $state('');
	let threshold = $state('');

	let forecast = $state<CashflowForecast | null>(null);
	let whatif = $state<WhatIfScenarios | null>(null);
	let position = $state<CashPosition | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	function fmt(n: number): string {
		return formatMoney(n, { currency: orgCurrency.currency, whole: true });
	}

	async function load() {
		loading = true;
		error = null;
		try {
			const base = `granularity=${granularity}&horizon_days=${horizonDays}`;
			const posQs =
				`${base}` +
				(openingBalance.trim() ? `&opening_balance=${encodeURIComponent(openingBalance.trim())}` : '') +
				(threshold.trim() ? `&min_balance_threshold=${encodeURIComponent(threshold.trim())}` : '');
			const [f, w, p] = await Promise.all([
				api.get<CashflowForecast>(`/api/analytics/cashflow_forecast?${base}`),
				api.get<WhatIfScenarios>(`/api/analytics/cashflow_whatif?${base}`),
				api.get<CashPosition>(`/api/analytics/cash_position?${posQs}`)
			]);
			forecast = f;
			whatif = w;
			position = p;
		} catch (e) {
			error = e instanceof Error ? e.message : m('cfo.error.loadFailed');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		orgCurrency.ensureLoaded();
		// Re-run whenever a control changes. Reading the deps registers them.
		void granularity;
		void horizonDays;
		void openingBalance;
		void threshold;
		load();
	});

	let maxScheduled = $derived(
		Math.max(1, ...(forecast?.periods ?? []).map((p) => p.scheduled_amount))
	);

	async function exportCsv() {
		try {
			const blob = await api.downloadBlob(
				`/api/analytics/export/cashflow_forecast?granularity=${granularity}&horizon_days=${horizonDays}`
			);
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `cashflow_forecast_${new Date().toISOString().slice(0, 10)}.csv`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		} catch (e) {
			error = e instanceof Error ? e.message : m('cfo.error.exportFailed');
		}
	}

	function granLabel(g: string): string {
		return g === 'day' ? m('cfo.gran.day') : g === 'month' ? m('cfo.gran.month') : m('cfo.gran.week');
	}
</script>

<PageHeader title={m('cfo.title')}>
	{#snippet actions()}
		<button class="btn-primary" onclick={exportCsv} data-testid="export-csv">{m('cfo.exportCsv')}</button>
	{/snippet}

	<div class="cf-controls">
		<div class="control">
			<span class="control-label">{m('cfo.control.granularity')}</span>
			<div class="seg">
				{#each ['day', 'week', 'month'] as g (g)}
					<button
						class="seg-btn"
						class:active={granularity === g}
						onclick={() => (granularity = g as CashflowGranularity)}
					>{granLabel(g)}</button>
				{/each}
			</div>
		</div>
		<div class="control">
			<span class="control-label">{m('cfo.control.horizon')}</span>
			<div class="seg">
				{#each [30, 90, 180, 365] as h (h)}
					<button class="seg-btn" class:active={horizonDays === h} onclick={() => (horizonDays = h)}>
						{m('cfo.control.horizonDays', { days: h })}
					</button>
				{/each}
			</div>
		</div>
		<label class="control">
			<span class="control-label">{m('cfo.control.openingBalance')}</span>
			<input
				class="cf-input"
				type="text"
				inputmode="decimal"
				placeholder={m('cfo.control.openingPlaceholder')}
				bind:value={openingBalance}
				aria-label={m('cfo.control.openingBalance')}
			/>
		</label>
		<label class="control">
			<span class="control-label">{m('cfo.control.minBalance')}</span>
			<input
				class="cf-input"
				type="text"
				inputmode="decimal"
				placeholder={m('cfo.control.minBalancePlaceholder')}
				bind:value={threshold}
				aria-label={m('cfo.control.minBalance')}
			/>
		</label>
	</div>

	{#if error}
		<p class="cf-error" role="alert">{error}</p>
	{:else if loading}
		<p class="loading">{m('cfo.loading')}</p>
	{:else if forecast}
		<div class="kpi-row" data-testid="forecast-kpi-row">
			<KpiCard value={fmt(forecast.totals.scheduled_amount)} label={m('cfo.kpi.projectedOutflow')} />
			<KpiCard value={fmt(forecast.totals.committed_amount)} label={m('cfo.kpi.committed')} />
			<KpiCard value={fmt(forecast.totals.pending_amount)} label={m('cfo.kpi.pipeline')} />
			<KpiCard
				value={fmt(whatif?.scenarios.early.total_discount_captured ?? 0)}
				label={m('cfo.kpi.discountIfEarly')}
				highlight="green"
			/>
		</div>

		<!-- Forecast bar chart -->
		<div class="chart-card">
			<h2>{m('cfo.chart.projectedOutflows', { granularity: granLabel(granularity) })}</h2>
			{#if forecast.periods.length > 0}
				<div class="cf-bars">
					{#each forecast.periods as p (p.period)}
						<div class="cf-bar-row">
							<span class="cf-bar-label">{formatPeriod(p.period)}</span>
							<div
								class="cf-bar-bg"
								role="img"
								aria-label={m('cfo.chart.barAria', { committed: fmt(p.committed_amount), pending: fmt(p.pending_amount) })}
							>
								<div
									class="cf-bar committed"
									style="width:{(p.committed_amount / maxScheduled) * 100}%"
									title={m('cfo.chart.committedTitle', { amount: fmt(p.committed_amount) })}
								></div>
								<div
									class="cf-bar pending"
									style="width:{(p.pending_amount / maxScheduled) * 100}%"
									title={m('cfo.chart.pendingTitle', { amount: fmt(p.pending_amount) })}
								></div>
							</div>
							<span class="cf-bar-amount">{fmt(p.scheduled_amount)}</span>
						</div>
					{/each}
				</div>
				<div class="cf-legend">
					<span class="cf-dot committed"></span> {m('cfo.legend.committed')}
					<span class="cf-dot pending"></span> {m('cfo.legend.pending')}
				</div>
			{:else}
				<p class="empty">{m('cfo.empty.outflows')}</p>
			{/if}
		</div>

		<!-- What-if scenarios -->
		{#if whatif}
			<div class="chart-card">
				<h2>{m('cfo.whatif.title')}</h2>
				<div class="scenario-grid">
					{#each [['early', m('cfo.whatif.early')], ['on_time', m('cfo.whatif.onTime')], ['late', m('cfo.whatif.late')]] as [key, label] (key)}
						{@const s = whatif.scenarios[key as 'early' | 'on_time' | 'late']}
						<div class="scenario-card" class:best={key === 'early'}>
							<span class="scenario-title">{label}</span>
							<span class="scenario-outflow">{fmt(s.total_outflow)}</span>
							<span class="scenario-sub">{m('cfo.whatif.netOutflow')}</span>
							{#if s.total_discount_captured > 0}
								<span class="scenario-discount">{m('cfo.whatif.discountCaptured', { amount: fmt(s.total_discount_captured) })}</span>
							{/if}
							<span class="scenario-days">{m('cfo.whatif.daysToPay', { days: s.weighted_avg_pay_date_days })}</span>
						</div>
					{/each}
				</div>
			</div>
		{/if}

		<!-- Cash position -->
		{#if position}
			<div class="chart-card">
				<h2>{m('cfo.position.title')}</h2>
				{#if position.opening_balance_source === 'none'}
					<p class="cf-hint">{m('cfo.position.enterOpening')}</p>
				{/if}
				{#if position.breaches.length > 0}
					<p class="cf-breach" role="alert">
						{m('cfo.position.breach', { n: position.breaches.length })}
					</p>
				{/if}
				<table class="cf-table">
					<thead>
						<tr>
							<th>{m('cfo.position.colPeriod')}</th>
							<th class="num">{m('cfo.position.colOpening')}</th>
							<th class="num">{m('cfo.position.colOutflow')}</th>
							<th class="num">{m('cfo.position.colClosing')}</th>
						</tr>
					</thead>
					<tbody>
						{#each position.periods as p (p.period)}
							<tr class:breach={p.below_threshold} data-breach={p.below_threshold}>
								<td>{formatPeriod(p.period)}</td>
								<td class="num">{fmt(p.opening)}</td>
								<td class="num">-{fmt(p.outflow)}</td>
								<td class="num closing">{fmt(p.closing)}</td>
							</tr>
						{/each}
						{#if position.periods.length === 0}
							<tr><td colspan="4" class="empty">{m('cfo.position.empty')}</td></tr>
						{/if}
					</tbody>
				</table>
			</div>
		{/if}

		<!-- CFO metrics: DPO, cash conversion cycle, accruals, supplier
		     concentration, fraud-rate trend, rebate yield -->
		<CfoMetrics periodDays={horizonDays} />

		<!-- Consolidated reporting across entities (self-hides for single-entity tenants) -->
		<ByEntityBreakdown />
	{/if}
</PageHeader>

<style>
	.cf-controls {
		display: flex;
		flex-wrap: wrap;
		gap: 18px;
		align-items: flex-end;
	}
	.control {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.control-label {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-muted);
	}
	.seg {
		display: inline-flex;
		border: 1px solid var(--border);
		border-radius: 6px;
		overflow: hidden;
	}
	.seg-btn {
		padding: 7px 14px;
		border: none;
		background: var(--surface);
		color: var(--text-muted);
		cursor: pointer;
		font-family: inherit;
		font-size: 0.85rem;
		text-transform: capitalize;
	}
	.seg-btn.active {
		background: var(--accent);
		color: #fff;
	}
	.cf-input {
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface);
		color: var(--text);
		font-family: inherit;
		font-size: 0.9rem;
		width: 180px;
	}
	.cf-error {
		color: #e04040;
		font-weight: 500;
	}
	.chart-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 20px;
	}
	.chart-card h2 {
		font-size: 1rem;
		margin: 0 0 16px;
	}
	.cf-bars {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.cf-bar-row {
		display: grid;
		grid-template-columns: 90px 1fr 110px;
		align-items: center;
		gap: 12px;
	}
	.cf-bar-label {
		font-size: 0.8rem;
		color: var(--text-muted);
	}
	.cf-bar-bg {
		display: flex;
		height: 18px;
		background: rgba(128, 128, 128, 0.12);
		border-radius: 4px;
		overflow: hidden;
	}
	.cf-bar {
		height: 100%;
	}
	.cf-bar.committed {
		background: #638cff;
	}
	.cf-bar.pending {
		background: #b7c5f5;
	}
	.cf-bar-amount {
		font-size: 0.82rem;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.cf-legend {
		margin-top: 14px;
		font-size: 0.78rem;
		color: var(--text-muted);
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.cf-dot {
		display: inline-block;
		width: 10px;
		height: 10px;
		border-radius: 2px;
		margin-left: 12px;
	}
	.cf-dot.committed {
		background: #638cff;
	}
	.cf-dot.pending {
		background: #b7c5f5;
	}
	.scenario-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
		gap: 14px;
	}
	.scenario-card {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 16px;
		border: 1px solid var(--border);
		border-radius: 8px;
	}
	.scenario-card.best {
		border-color: #2faa6a;
		background: rgba(47, 170, 106, 0.06);
	}
	.scenario-title {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--text-muted);
	}
	.scenario-outflow {
		font-size: 1.4rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.scenario-sub {
		font-size: 0.72rem;
		color: var(--text-muted);
	}
	.scenario-discount {
		font-size: 0.82rem;
		color: #2faa6a;
		font-weight: 600;
	}
	.scenario-days {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin-top: 4px;
	}
	.cf-hint,
	.cf-breach {
		font-size: 0.85rem;
		margin: 0 0 12px;
	}
	.cf-breach {
		color: #e04040;
		font-weight: 600;
	}
	.cf-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.88rem;
	}
	.cf-table th,
	.cf-table td {
		padding: 8px 12px;
		text-align: left;
		border-bottom: 1px solid var(--border);
	}
	.cf-table th.num,
	.cf-table td.num {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.cf-table td.closing {
		font-weight: 600;
	}
	.cf-table tr.breach td {
		color: #e04040;
	}
	.empty {
		color: var(--text-muted);
		text-align: center;
		padding: 20px;
	}
</style>
