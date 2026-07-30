<script lang="ts">
	import Money from '$lib/components/ui/Money.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { formatDate, formatPeriod } from '$lib/utils/time';
	import { m } from '$lib/i18n/store.svelte';
	import type { PaymentPlanResult } from '$lib/types/cashFlow';

	let { result }: { result: PaymentPlanResult } = $props();

	function fmt(v: string | null | undefined): string {
		return formatMoney(v, { currency: result.currency, whole: true });
	}

	let selected = $derived(result.discount_recommendations.filter((r) => r.selected));
	let hasBreach = $derived(result.periods.some((p) => p.below_threshold));
	let breachCount = $derived(result.periods.filter((p) => p.below_threshold).length);
</script>

<figure class="plan-card" data-testid="payment-plan-card">
	<figcaption class="plan-cap">
		<span class="plan-title">{m('cashFlow.plan.title')}</span>
		<span class="plan-meta">
			{m('cashFlow.chart.opening', {
				amount: fmt(result.opening_balance),
				source: m(`cashFlow.chart.source.${result.opening_balance_source}` as never)
			})}
		</span>
	</figcaption>

	{#if hasBreach}
		<p class="plan-breach" role="alert">
			{m('cashFlow.chart.breach', { n: breachCount })}
			{#if result.first_shortfall_period}
				<span class="breach-when"
					>{m('cashFlow.chart.firstShortfall', {
						period: formatPeriod(result.first_shortfall_period)
					})}</span
				>
			{/if}
		</p>
	{:else}
		<p class="plan-healthy">{m('cashFlow.chart.ariaHealthy')}</p>
	{/if}

	<p class="plan-savings">
		{#if selected.length > 0}
			{m('cashFlow.plan.savingsSummary', {
				n: selected.length,
				amount: fmt(result.total_savings_selected)
			})}
		{:else}
			{m('cashFlow.plan.noSavings')}
		{/if}
	</p>

	<div class="plan-section">
		<h4 class="plan-subhead">{m('cashFlow.plan.scheduleHeading')}</h4>
		{#if result.periods.length === 0}
			<p class="plan-empty">{m('cashFlow.chart.empty')}</p>
		{:else}
			<table class="mini-table">
				<thead>
					<tr>
						<th>{m('cashFlow.plan.col.period')}</th>
						<th class="num">{m('cashFlow.plan.col.outflow')}</th>
						<th class="num">{m('cashFlow.plan.col.closing')}</th>
					</tr>
				</thead>
				<tbody>
					{#each result.periods as p (p.period)}
						<tr class:below={p.below_threshold}>
							<td>{formatPeriod(p.period)}</td>
							<td class="num"><Money amount={p.outflow} currency={result.currency} mono /></td>
							<td class="num"><Money amount={p.closing} currency={result.currency} mono /></td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>

	{#if selected.length > 0}
		<div class="plan-section">
			<h4 class="plan-subhead">{m('cashFlow.plan.discountsHeading')}</h4>
			<table class="mini-table">
				<thead>
					<tr>
						<th>{m('cashFlow.plan.col.vendor')}</th>
						<th class="num">{m('cashFlow.plan.col.savings')}</th>
						<th>{m('cashFlow.plan.col.payBy')}</th>
					</tr>
				</thead>
				<tbody>
					{#each selected as r (r.offer_id)}
						<tr>
							<td>{r.vendor_name ?? r.invoice_number ?? m('assistant.tool.unknownVendor')}</td>
							<td class="num"><Money amount={r.savings} currency={result.currency} mono /></td>
							<td>{formatDate(r.pay_by)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if result.unretimed_offer_ids.length > 0}
		<p class="plan-note">
			{m('cashFlow.plan.unretimedNote', { n: result.unretimed_offer_ids.length })}
		</p>
	{/if}

	<p class="plan-disclaimer">{m('cashFlow.plan.disclaimer')}</p>
</figure>

<style>
	.plan-card {
		margin: 10px 0 0;
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 14px 16px 12px;
		background: var(--surface);
	}
	.plan-cap {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 12px;
		flex-wrap: wrap;
		margin-bottom: 8px;
	}
	.plan-title {
		font-size: 0.82rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text);
	}
	.plan-meta {
		font-size: 0.76rem;
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
	}
	.plan-breach {
		margin: 0 0 10px;
		padding: 8px 12px;
		border-radius: 8px;
		background: rgba(240, 70, 70, 0.1);
		border: 1px solid rgba(240, 70, 70, 0.3);
		color: #e04040;
		font-size: 0.82rem;
	}
	.breach-when {
		color: var(--text-muted);
	}
	.plan-healthy {
		margin: 0 0 10px;
		font-size: 0.82rem;
		color: var(--text-muted);
	}
	.plan-savings {
		margin: 0 0 12px;
		font-size: 0.88rem;
		font-weight: 600;
		color: var(--text);
	}
	.plan-section {
		margin-bottom: 12px;
	}
	.plan-section:last-of-type {
		margin-bottom: 8px;
	}
	.plan-subhead {
		margin: 0 0 6px;
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
	}
	.plan-empty {
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
	.mini-table tr.below td {
		color: #e04040;
	}
	.plan-note {
		margin: 0 0 8px;
		font-size: 0.76rem;
		color: var(--text-muted);
	}
	.plan-disclaimer {
		margin: 0;
		font-size: 0.74rem;
		color: var(--text-muted);
		font-style: italic;
	}
</style>
