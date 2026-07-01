<script lang="ts">
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import SearchBox from '$lib/components/ui/SearchBox.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { get1099Report } from '$lib/api/tax';
	import { m } from '$lib/i18n/store.svelte';
	import type { Report1099, Vendor1099Row } from '$lib/types/tax';

	// Year selector — current year and the prior five (1099s are filed for
	// completed calendar years, so people mostly look back).
	const currentYear = new Date().getFullYear();
	const YEARS = Array.from({ length: 6 }, (_, i) => currentYear - i);

	let year = $state(currentYear);
	let report = $state<Report1099 | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let search = $state('');
	// Chip keys: 'all' | 'reportable' | 'missing_w9' | 'over_threshold'.
	// Typed as string to match FilterChips' bindable `active`.
	let rowFilter = $state('all');

	async function load() {
		loading = true;
		error = null;
		try {
			report = await get1099Report(year);
		} catch (e) {
			report = null;
			error = e instanceof Error ? e.message : m('tax.error.load');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		// Re-load whenever the year changes.
		void year;
		load();
	});

	// The report carries the org's reporting (home) currency the totals are
	// denominated in — authoritative for display. Fall back to USD only before
	// the first load resolves (no money renders until `report` exists anyway).
	let reportCurrency = $derived(report?.currency ?? 'USD');

	// A vendor is "reportable" when it's 1099-eligible and crossed the
	// threshold — that's the set that actually needs a form filed.
	function isReportable(r: Vendor1099Row): boolean {
		return r.is_1099_eligible && r.over_threshold;
	}

	let filteredRows = $derived.by(() => {
		const rows = report?.rows ?? [];
		const q = search.trim().toLowerCase();
		return rows.filter((r) => {
			if (q && !r.vendor_name.toLowerCase().includes(q)) return false;
			switch (rowFilter) {
				case 'reportable':
					return isReportable(r);
				case 'missing_w9':
					return isReportable(r) && !r.w9_on_file;
				case 'over_threshold':
					return r.over_threshold;
				default:
					return true;
			}
		});
	});

	// $derived so the column headers re-render when the locale changes.
	let COLUMNS = $derived([
		{ label: m('tax.col.vendor') },
		{ label: m('tax.col.classification') },
		{ label: m('tax.col.1099'), class: 'center' },
		{ label: m('tax.col.w9'), class: 'center' },
		{ label: m('tax.col.tin'), class: 'center' },
		{ label: m('tax.col.payments'), class: 'right' },
		{ label: m('tax.col.ytdPaid'), class: 'right' }
	]);

	function fmtDate(s: string | null): string {
		if (!s) return '—';
		const d = new Date(s);
		if (Number.isNaN(d.getTime())) return s;
		return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
	}

	// A TIN counts as "on file" when the vendor has a tax id captured.
	function hasTin(r: Vendor1099Row): boolean {
		return !!r.tax_id && r.tax_id.trim().length > 0;
	}

	// Display string for the IRS threshold, e.g. "$600". Derived so the
	// snippet body (where `report` isn't narrowed) can use it safely.
	let thresholdLabel = $derived(report ? `$${report.threshold_usd}` : '$600');
</script>

<PageHeader title={m('tax.title')}>
	{#snippet actions()}
		<label class="year-select">
			<span class="year-label">{m('tax.taxYear')}</span>
			<select bind:value={year} aria-label={m('tax.taxYear')}>
				{#each YEARS as y (y)}
					<option value={y}>{y}</option>
				{/each}
			</select>
		</label>
	{/snippet}

	{#if error}
		<div class="state-card error" role="alert">
			<p>{error}</p>
			<button class="btn-primary" onclick={load}>{m('tax.retry')}</button>
		</div>
	{:else if loading && !report}
		<div class="state-card" aria-busy="true">{m('tax.loadingReport', { year })}</div>
	{:else if report}
		<div class="kpi-row">
			<KpiCard value={String(report.vendor_count_total)} label={m('tax.kpi.vendorsWithPayments')} />
			<KpiCard
				value={String(report.vendor_count_eligible_over_threshold)}
				label={m('tax.kpi.reportableOver', { threshold: report.threshold_usd })}
				highlight="green"
			/>
			<KpiCard
				value={String(report.vendor_count_over_threshold_without_w9)}
				label={m('tax.kpi.reportableWithoutW9')}
				highlight={report.vendor_count_over_threshold_without_w9 > 0 ? 'red' : null}
			/>
			<KpiCard
				value={formatMoney(report.total_reportable, { currency: report.currency })}
				label={m('tax.kpi.totalReportable')}
			/>
		</div>

		<div class="toolbar-row">
			<FilterChips
				chips={[
					{ key: 'all', label: m('common.all'), count: report.rows.length },
					{
						key: 'reportable',
						label: m('tax.filter.reportable'),
						count: report.rows.filter(isReportable).length
					},
					{
						key: 'missing_w9',
						label: m('tax.filter.missingW9'),
						count: report.rows.filter((r) => isReportable(r) && !r.w9_on_file).length,
						alert: report.vendor_count_over_threshold_without_w9 > 0
					},
					{
						key: 'over_threshold',
						label: m('tax.filter.overThreshold', { threshold: report.threshold_usd }),
						count: report.rows.filter((r) => r.over_threshold).length
					}
				]}
				bind:active={rowFilter}
			/>
			<SearchBox bind:value={search} placeholder={m('tax.searchPlaceholder')} ariaLabel={m('tax.searchAria')} />
		</div>

		<DataTable
			columns={COLUMNS}
			isEmpty={filteredRows.length === 0}
			empty={report.rows.length === 0
				? m('tax.empty.noVendors')
				: m('tax.empty.noMatch')}
		>
			{#snippet body()}
				{#each filteredRows as r (r.vendor_id)}
					<tr class:row-flag={isReportable(r) && !r.w9_on_file}>
						<td class="vendor">{r.vendor_name}</td>
						<td class="muted">{r.tax_classification ?? '—'}</td>
						<td class="center">
							{#if r.is_1099_eligible}
								<span class="chip chip-on">{m('tax.chip.eligible')}</span>
							{:else}
								<span class="chip chip-off">{m('tax.chip.no')}</span>
							{/if}
						</td>
						<td class="center">
							{#if r.w9_on_file}
								<span class="chip chip-on" title={fmtDate(r.w9_received_date)}>{m('tax.chip.onFile')}</span>
							{:else}
								<span class="chip chip-warn">{m('tax.chip.missing')}</span>
							{/if}
						</td>
						<td class="center">
							{#if hasTin(r)}
								<span class="chip chip-on">{m('tax.chip.verified')}</span>
							{:else}
								<span class="chip chip-warn">{m('tax.chip.missing')}</span>
							{/if}
						</td>
						<td class="right mono">{r.payment_count}</td>
						<td class="right mono">
							<span class:over={r.over_threshold}>
								<Money amount={r.ytd_paid} currency={reportCurrency} />
							</span>
							{#if r.over_threshold}
								<span
									class="threshold-flag"
									title={m('tax.thresholdFlag', { threshold: thresholdLabel })}>▲</span
								>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

		<p class="report-meta">
			{m('tax.reportMeta', {
				generated: fmtDate(report.generated_at),
				threshold: report.threshold_usd,
				year
			})}
		</p>
	{/if}
</PageHeader>

<style>
	.year-select {
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}

	.year-label {
		font-size: 0.82rem;
		color: var(--text-muted);
	}

	.year-select select {
		/* base look (border/colour/font/chevron) from the global select recipe */
		padding: 6px 30px 6px 10px;
		border-radius: 6px;
		background-color: var(--surface);
	}

	.state-card {
		padding: 32px;
		text-align: center;
		color: var(--text-muted);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
	}

	.state-card.error {
		color: #e04040;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 12px;
	}

	.toolbar-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		flex-wrap: wrap;
	}

	.center {
		text-align: center;
	}

	.muted {
		color: var(--text-muted);
	}

	.vendor {
		font-weight: 500;
	}

	.chip {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 600;
		white-space: nowrap;
	}

	.chip-on {
		background: rgba(31, 168, 106, 0.15);
		color: #1fa86a;
	}

	.chip-off {
		background: rgba(150, 150, 150, 0.15);
		color: var(--text-muted);
	}

	.chip-warn {
		background: rgba(240, 70, 70, 0.15);
		color: #e04040;
	}

	.row-flag {
		background: rgba(240, 70, 70, 0.04);
	}

	.over {
		font-weight: 600;
	}

	.threshold-flag {
		color: #d4940a;
		margin-left: 4px;
		font-size: 0.7rem;
	}

	.report-meta {
		font-size: 0.8rem;
		color: var(--text-muted);
		margin: 4px 2px 0;
	}
</style>
