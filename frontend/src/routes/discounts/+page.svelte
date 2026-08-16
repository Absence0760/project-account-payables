<script lang="ts">
	import { goto } from '$app/navigation';
	import { appendUnique } from '$lib/utils/pagination';
	import { createRequestSequencer } from '$lib/utils/requestSequence';
	import { auth } from '$lib/stores/auth.svelte';
	import { orgCurrency } from '$lib/stores/orgSettings.svelte';
	import { formatMoney } from '$lib/utils/money';
	import { formatDate } from '$lib/utils/time';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import FilterChips from '$lib/components/ui/FilterChips.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import DiscountTierBar from '$lib/components/ui/DiscountTierBar.svelte';
	import {
		getDiscountDashboard,
		listDiscountOffers,
		acceptDiscountOffer,
		declineDiscountOffer,
		optimizeDiscounts
	} from '$lib/api/discounts';
	import type {
		DiscountDashboard,
		DiscountOffer,
		DiscountTier,
		DiscountStatus,
		DiscountStatusFilter,
		DiscountOptimization
	} from '$lib/types/discounts';

	// RBAC: the dashboard is admin / ap_manager / cfo (the backend 403s
	// everyone else). `isManager` = admin|ap_manager, `isCfo` = admin|cfo, so
	// the union covers exactly those three roles. Wait for `auth.user` to load
	// before deciding — a redirect before /me resolves would race first paint
	// (the audit page documents the same gotcha).
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isManager || auth.isCfo);

	$effect(() => {
		if (userLoaded && !allowed) goto('/');
	});

	const PAGE_SIZE = 20;

	const OFFER_COLUMNS = $derived([
		{ label: m('discounts.col.vendorInvoice') },
		{ label: m('discounts.col.scope') },
		{ label: m('discounts.col.tiers') },
		{ label: m('discounts.col.base'), class: 'right' },
		{ label: m('discounts.col.bestDiscount'), class: 'right' },
		{ label: m('discounts.col.status') },
		{ label: m('discounts.col.validUntil') },
		{ class: 'actions-col' }
	]);

	const STATUS_LABELS = $derived<Record<DiscountStatus, string>>({
		offered: m('discounts.status.offered'),
		accepted: m('discounts.status.accepted'),
		captured: m('discounts.status.captured'),
		declined: m('discounts.status.declined'),
		expired: m('discounts.status.expired')
	});

	let dashboard = $state<DiscountDashboard | null>(null);
	let offers = $state<DiscountOffer[]>([]);
	let total = $state(0);
	let page = $state(1);
	let statusFilter = $state<DiscountStatusFilter>('all');

	let loading = $state(true);
	let loadingMore = $state(false);
	let error = $state<string | null>(null);

	let hasMore = $derived(offers.length < total);

	// Aggregate (tenant-wide) money uses the org default currency. Per-offer
	// rows render their own `currency` via <Money>.
	function aggMoney(n: number | null | undefined): string {
		return formatMoney(n ?? 0, { currency: orgCurrency.currency, whole: true });
	}

	/** Friendly relative "in 3 days" / "5 days ago" for a deadline. */
	function relativeDeadline(dateStr: string | null): string {
		if (!dateStr) return '';
		const d = new Date(dateStr);
		if (Number.isNaN(d.getTime())) return '';
		const days = Math.round((d.getTime() - Date.now()) / 86_400_000);
		if (days === 0) return m('discounts.deadline.today');
		if (days > 0) return m('discounts.deadline.inDays', { n: days });
		return m('discounts.deadline.daysAgo', { n: -days });
	}

	function bestTier(o: DiscountOffer): DiscountTier | null {
		if (o.tiers.length === 0) return null;
		return o.tiers.reduce((best, t) => (t.percent > best.percent ? t : best));
	}

	function bestDiscountAmount(o: DiscountOffer): number | null {
		const t = bestTier(o);
		if (!t) return null;
		return (o.base_amount * t.percent) / 100;
	}

	const STATUS_CHIPS = $derived<{ key: DiscountStatusFilter; label: string }[]>([
		{ key: 'all', label: m('common.all') },
		{ key: 'offered', label: m('discounts.status.offered') },
		{ key: 'accepted', label: m('discounts.status.accepted') },
		{ key: 'captured', label: m('discounts.status.captured') },
		{ key: 'missed', label: m('discounts.chip.missed') }
	]);

	// Two sequencers: the offers list and the KPI dashboard are separate
	// requests, and each accept/decline re-fires the dashboard, so two of those
	// can resolve out of order. Sharing one counter would let a dashboard
	// refresh mark an in-flight offers load un-committable and blank the table.
	// `confirmAccept`/`decline` edit a row in place with no fetch, so they
	// supersede the offers sequencer first. See `frontend/CLAUDE.md`
	// § Sequencing list fetches.
	const offersSequence = createRequestSequencer();
	const dashboardSequence = createRequestSequencer();

	async function loadDashboard() {
		const token = dashboardSequence.start();
		try {
			const data = await getDiscountDashboard();
			if (!dashboardSequence.canCommit(token)) return;
			dashboard = data;
		} catch (e) {
			// The KPI row is best-effort — a failure here shouldn't blank the table.
			if (!dashboardSequence.isCurrentRequest(token)) return;
			dashboard = null;
			if (!error) error = e instanceof Error ? e.message : m('discounts.error.dashboard');
		}
	}

	async function loadOffers(opts: { append?: boolean } = {}) {
		const nextPage = opts.append ? page + 1 : 1;
		const token = offersSequence.start();
		if (opts.append) loadingMore = true;
		else loading = true;
		try {
			const data = await listDiscountOffers({
				status: statusFilter,
				page: nextPage,
				pageSize: PAGE_SIZE
			});
			// Superseded by a newer load, or by a local accept/decline.
			if (!offersSequence.canCommit(token)) return;
			offers = opts.append ? appendUnique(offers, data.items) : data.items;
			total = data.total;
			page = nextPage;
			error = null;
		} catch (e) {
			// `isCurrentRequest`, not `canCommit`: a load superseded by a local
			// edit still failed, and no newer load is coming to report it.
			if (!offersSequence.isCurrentRequest(token)) return;
			error = e instanceof Error ? e.message : m('discounts.error.offers');
			if (!opts.append) offers = [];
		} finally {
			if (offersSequence.isCurrentRequest(token)) {
				loading = false;
				loadingMore = false;
			}
		}
	}

	$effect(() => {
		if (!allowed) return;
		orgCurrency.ensureLoaded();
		loadDashboard();
	});

	$effect(() => {
		if (!allowed) return;
		// Re-fetch the offers whenever the status filter changes.
		statusFilter;
		loadOffers();
	});

	// --- Accept modal ---
	let acceptTarget = $state<DiscountOffer | null>(null);
	let selectedTierDays = $state<number | null>(null);
	let accepting = $state(false);

	function openAccept(o: DiscountOffer) {
		acceptTarget = o;
		// Default to the richest tier.
		selectedTierDays = bestTier(o)?.days ?? null;
	}

	async function confirmAccept() {
		if (!acceptTarget) return;
		accepting = true;
		try {
			const updated = await acceptDiscountOffer(
				acceptTarget.id,
				selectedTierDays ?? undefined
			);
			// A load already in flight read this offer BEFORE the accept landed,
			// so its response would revert the row to `offered`. Retire it.
			offersSequence.supersedeInFlight();
			offers = offers.map((o) => (o.id === updated.id ? updated : o));
			toast(m('discounts.toast.accepted'), 'success');
			acceptTarget = null;
			await loadDashboard();
		} catch (e) {
			toast(e instanceof Error ? e.message : m('discounts.toast.acceptFailed'), 'error');
		} finally {
			accepting = false;
		}
	}

	// --- Decline (armed two-click) ---
	let confirmDeclineId = $state<string | null>(null);
	let declining = $state(false);

	async function decline(o: DiscountOffer) {
		if (confirmDeclineId !== o.id) {
			confirmDeclineId = o.id;
			return;
		}
		declining = true;
		try {
			const updated = await declineDiscountOffer(o.id);
			offersSequence.supersedeInFlight();
			offers = offers.map((x) => (x.id === updated.id ? updated : x));
			toast(m('discounts.toast.declined'), 'success');
			confirmDeclineId = null;
			await loadDashboard();
		} catch (e) {
			toast(e instanceof Error ? e.message : m('discounts.toast.declineFailed'), 'error');
		} finally {
			declining = false;
		}
	}

	// --- Optimize panel ---
	let cashBudget = $state('');
	let optimizing = $state(false);
	let optimization = $state<DiscountOptimization | null>(null);

	async function runOptimize() {
		optimizing = true;
		try {
			const budget = cashBudget.trim() ? Number(cashBudget.trim()) : undefined;
			optimization = await optimizeDiscounts(
				budget !== undefined && Number.isFinite(budget) ? budget : undefined
			);
		} catch (e) {
			toast(e instanceof Error ? e.message : m('discounts.toast.optimizeFailed'), 'error');
		} finally {
			optimizing = false;
		}
	}
</script>

<svelte:window
	onclick={(e) => {
		// Un-arm the decline confirm when the click lands outside a row action.
		if (confirmDeclineId && !(e.target as HTMLElement)?.closest?.('.row-action')) {
			confirmDeclineId = null;
		}
	}}
/>

<PageHeader title={m('discounts.title')}>
	{#if !userLoaded}
		<p class="loading">{m('common.loading')}</p>
	{:else if !allowed}
		<p class="loading">{m('discounts.redirecting')}</p>
	{:else}
		<!-- KPI row -->
		<div class="kpi-row">
			<KpiCard
				value={aggMoney(dashboard?.captured_amount)}
				label={m('discounts.kpi.captured', { n: dashboard?.captured_count ?? 0 })}
				highlight="green"
			/>
			<KpiCard
				value={aggMoney(dashboard?.missed_amount)}
				label={m('discounts.kpi.missed', { n: dashboard?.missed_count ?? 0 })}
				highlight="red"
			/>
			<KpiCard
				value={`${(dashboard?.capture_rate_pct ?? 0).toFixed(0)}%`}
				label={m('discounts.kpi.captureRate')}
			/>
			<KpiCard
				value={aggMoney(dashboard?.projected_savings)}
				label={m('discounts.kpi.projectedSavings')}
				highlight="green"
			/>
			<KpiCard value={dashboard?.open_offer_count ?? 0} label={m('discounts.kpi.openOffers')} />
		</div>

		<!-- Optimize panel -->
		<div class="opt-panel">
			<div class="opt-head">
				<div>
					<h2>{m('discounts.opt.heading')}</h2>
					<p class="opt-sub">{m('discounts.opt.sub')}</p>
				</div>
				<form class="opt-form" onsubmit={(e) => { e.preventDefault(); runOptimize(); }}>
					<label class="opt-field">
						<span class="opt-label">{m('discounts.opt.budgetLabel')}</span>
						<input
							class="opt-input"
							type="text"
							inputmode="decimal"
							placeholder={m('discounts.opt.budgetPlaceholder')}
							bind:value={cashBudget}
							aria-label={m('discounts.opt.budgetAria')}
						/>
					</label>
					<button class="btn-primary" type="submit" disabled={optimizing}>
						{optimizing ? m('discounts.opt.optimizing') : m('discounts.opt.optimize')}
					</button>
				</form>
			</div>

			{#if optimization}
				<div class="opt-summary">
					<span>{m('discounts.opt.availableSavings')} <strong>{aggMoney(optimization.total_savings_available)}</strong></span>
					<span>{m('discounts.opt.selectedSavings')} <strong class="pos">{aggMoney(optimization.total_savings_selected)}</strong></span>
					<span>{m('discounts.opt.selectedOutlay')} <strong>{aggMoney(optimization.total_outlay_selected)}</strong></span>
					<span>{m('discounts.opt.costOfCapital')} <strong>{optimization.cost_of_capital_pct.toFixed(1)}%</strong></span>
				</div>
				{#if optimization.recommendations.length > 0}
					<div class="scenario-grid">
						{#each optimization.recommendations as rec (rec.offer_id)}
							<div class="scenario-card" class:best={rec.selected}>
								<span class="scenario-title">
									{rec.vendor_name ?? rec.invoice_number ?? rec.offer_id.slice(0, 8)}
								</span>
								<span class="scenario-roi" class:pos={rec.roi.worthwhile}>
									{rec.roi.annualized_return_pct.toFixed(1)}% APR
								</span>
								<span class="scenario-sub">
									{m('discounts.opt.save')} <Money amount={rec.roi.savings} currency={orgCurrency.currency} />
									· {rec.discount_percent}% / {rec.tier_days}d
								</span>
								<span class="scenario-sub">{m('discounts.opt.payBy', { date: formatDate(rec.pay_by) })}</span>
								<span class="scenario-flag" class:selected={rec.selected}>
									{rec.selected ? m('discounts.opt.selected') : m('discounts.opt.notSelected')}
								</span>
							</div>
						{/each}
					</div>
				{:else}
					<p class="empty">{m('discounts.opt.noOffers')}</p>
				{/if}
			{/if}
		</div>

		<!-- Status filter -->
		<FilterChips chips={STATUS_CHIPS} bind:active={statusFilter} />

		{#if error}
			<p class="dash-error" role="alert">{error}</p>
		{/if}

		<DataTable
			columns={OFFER_COLUMNS}
			isEmpty={!loading && offers.length === 0}
			empty={loading ? m('discounts.table.loading') : m('discounts.table.empty')}
			colspan={8}
		>
			{#snippet body()}
				{#each offers as offer (offer.id)}
					{@const best = bestTier(offer)}
					{@const bestAmt = bestDiscountAmount(offer)}
					<tr>
						<td>
							<span class="primary">{offer.vendor_name ?? '—'}</span>
							{#if offer.invoice_number}
								<span class="secondary mono">{offer.invoice_number}</span>
							{/if}
						</td>
						<td><span class="scope-pill">{offer.scope}</span></td>
						<td>
							<DiscountTierBar
								tiers={offer.tiers}
								acceptedDays={offer.accepted_tier?.days ?? null}
							/>
						</td>
						<td class="right"><Money amount={offer.base_amount} currency={offer.currency} mono /></td>
						<td class="right">
							{#if bestAmt !== null && best}
								<span class="best-discount">
									<Money amount={bestAmt} currency={offer.currency} mono />
									<span class="best-pct">{best.percent}%</span>
								</span>
							{:else}
								<span class="muted">—</span>
							{/if}
						</td>
						<td><span class="status-badge {offer.status}">{STATUS_LABELS[offer.status]}</span></td>
						<td class="muted">
							{formatDate(offer.valid_until)}
							{#if offer.valid_until && offer.status === 'offered'}
								<span class="rel">{relativeDeadline(offer.valid_until)}</span>
							{/if}
						</td>
						<td class="actions">
							{#if offer.status === 'offered'}
								<RowAction variant="success" onclick={() => openAccept(offer)} ariaLabel={m('discounts.row.acceptAria', { vendor: offer.vendor_name ?? offer.id.slice(0, 8) })}>
									{m('discounts.row.accept')}
								</RowAction>
								<RowAction
									variant="danger"
									armed={confirmDeclineId === offer.id}
									disabled={declining}
									onclick={() => decline(offer)}
									ariaLabel={m('discounts.row.declineAria', { vendor: offer.vendor_name ?? offer.id.slice(0, 8) })}
								>
									{confirmDeclineId === offer.id ? m('discounts.row.confirm') : m('discounts.row.decline')}
								</RowAction>
							{/if}
						</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>

		{#if hasMore}
			<div class="load-more-row">
				<button class="btn-load-more" onclick={() => loadOffers({ append: true })} disabled={loadingMore}>
					{loadingMore ? m('common.loading') : m('discounts.loadMore', { shown: offers.length, total })}
				</button>
			</div>
		{:else if total > 0}
			<div class="load-more-row">
				<span class="load-more-end">{m('discounts.showingAll', { total })}</span>
			</div>
		{/if}
	{/if}
</PageHeader>

<!-- Accept-tier modal -->
<Modal
	open={acceptTarget !== null}
	ariaLabel={m('discounts.modal.aria')}
	title={m('discounts.modal.title')}
	width="sm"
	onclose={() => (acceptTarget = null)}
>
	{#if acceptTarget}
		<form onsubmit={(e) => { e.preventDefault(); confirmAccept(); }}>
			<p class="modal-hint">
				<strong>{acceptTarget.vendor_name ?? acceptTarget.id.slice(0, 8)}</strong>
				{#if acceptTarget.invoice_number}· {acceptTarget.invoice_number}{/if}
				· {m('discounts.modal.base')} <Money amount={acceptTarget.base_amount} currency={acceptTarget.currency} />
			</p>

			{#if acceptTarget.tiers.length > 0}
				<fieldset class="tier-picker">
					<legend>{m('discounts.modal.chooseTier')}</legend>
					{#each [...acceptTarget.tiers].sort((a, b) => a.days - b.days) as tier (tier.days)}
						<label class="tier-option">
							<input
								type="radio"
								name="tier"
								value={tier.days}
								checked={selectedTierDays === tier.days}
								onchange={() => (selectedTierDays = tier.days)}
							/>
							<span class="tier-option-label">
								{m('discounts.modal.tierOption', { percent: tier.percent, days: tier.days })}
								<span class="tier-option-amt">
									{m('discounts.modal.save')} <Money
										amount={(acceptTarget.base_amount * tier.percent) / 100}
										currency={acceptTarget.currency}
									/>
								</span>
							</span>
						</label>
					{/each}
				</fieldset>
			{:else}
				<p class="modal-hint">{m('discounts.modal.noTiers')}</p>
			{/if}

			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={() => (acceptTarget = null)}>{m('common.cancel')}</button>
				<button type="submit" class="btn-primary" disabled={accepting}>
					{accepting ? m('discounts.modal.accepting') : m('discounts.modal.acceptOffer')}
				</button>
			</div>
		</form>
	{/if}
</Modal>

<style>
	.loading,
	.empty {
		color: var(--text-muted);
		text-align: center;
		padding: 20px;
	}
	.dash-error {
		color: var(--danger);
		font-weight: 500;
	}

	/* --- Optimize panel --- */
	.opt-panel {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 20px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.opt-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-end;
		gap: 18px;
		flex-wrap: wrap;
	}
	.opt-head h2 {
		font-size: 1rem;
		margin: 0 0 4px;
	}
	.opt-sub {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0;
		max-width: 52ch;
	}
	.opt-form {
		display: flex;
		align-items: flex-end;
		gap: 10px;
	}
	.opt-field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.opt-label {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-muted);
	}
	.opt-input {
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface);
		color: var(--text);
		font-family: inherit;
		font-size: 0.9rem;
		width: 180px;
	}
	.opt-summary {
		display: flex;
		flex-wrap: wrap;
		gap: 18px;
		font-size: 0.85rem;
		color: var(--text-muted);
		padding-top: 4px;
		border-top: 1px solid var(--border);
	}
	.opt-summary strong {
		color: var(--text);
	}
	.opt-summary strong.pos {
		color: #1fa86a;
	}

	/* --- Optimizer recommendation cards (cfo scenario-grid style) --- */
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
	}
	.scenario-roi {
		font-size: 1.3rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
	}
	.scenario-roi.pos {
		color: #1fa86a;
	}
	.scenario-sub {
		font-size: 0.78rem;
		color: var(--text-muted);
	}
	.scenario-flag {
		margin-top: 4px;
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--text-muted);
	}
	.scenario-flag.selected {
		color: #1fa86a;
	}

	/* --- Offers table cells --- */
	.primary {
		display: block;
		font-weight: 500;
	}
	.secondary {
		display: block;
		font-size: 0.78rem;
		color: var(--text-muted);
	}
	.mono {
		font-variant-numeric: tabular-nums;
		font-family: ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace;
	}
	.muted {
		color: var(--text-muted);
	}
	.scope-pill {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		background: var(--bg);
		font-size: 0.74rem;
		font-weight: 600;
		text-transform: capitalize;
		color: var(--text-muted);
	}
	.best-discount {
		display: inline-flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 1px;
	}
	.best-pct {
		font-size: 0.7rem;
		color: #1fa86a;
		font-weight: 600;
	}
	.rel {
		display: block;
		font-size: 0.72rem;
		color: var(--text-muted);
	}

	/* --- Status badge --- */
	.status-badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.74rem;
		font-weight: 600;
		text-transform: capitalize;
		white-space: nowrap;
	}
	.status-badge.offered {
		background: var(--accent-tint);
		color: var(--accent-on-tint);
	}
	.status-badge.accepted {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}
	.status-badge.captured {
		background: rgba(31, 168, 106, 0.15);
		color: #1fa86a;
	}
	.status-badge.declined,
	.status-badge.expired {
		background: var(--muted-tint);
		color: var(--muted-on-tint);
	}

	/* --- Accept modal tier picker --- */
	.tier-picker {
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 12px;
		margin: 0 0 14px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.tier-picker legend {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		padding: 0 4px;
	}
	.tier-option {
		display: flex;
		align-items: center;
		gap: 8px;
		cursor: pointer;
		font-size: 0.88rem;
	}
	.tier-option input {
		accent-color: var(--accent);
	}
	.tier-option-label {
		display: flex;
		flex-direction: column;
	}
	.tier-option-amt {
		font-size: 0.78rem;
		color: #1fa86a;
		font-weight: 600;
	}
	.modal-hint {
		font-size: 0.85rem;
		color: var(--text-muted);
		margin: 0 0 14px;
	}

	@media (max-width: 768px) {
		.opt-head {
			flex-direction: column;
			align-items: stretch;
		}
		.opt-form {
			flex-direction: column;
			align-items: stretch;
		}
		.opt-input {
			width: 100%;
		}
	}
</style>
