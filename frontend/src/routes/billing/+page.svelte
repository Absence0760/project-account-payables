<script lang="ts">
	import { goto } from '$app/navigation';
	import { auth } from '$lib/stores/auth.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import KpiCard from '$lib/components/ui/KpiCard.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import SubscriptionBadge from '$lib/components/ui/SubscriptionBadge.svelte';
	import { getBillingInvoices, getBillingSubscription } from '$lib/api/billing';
	import { formatMoney } from '$lib/utils/money';
	import type {
		BillingInvoice,
		BillingInvoiceStatus,
		BillingSubscriptionResponse
	} from '$lib/types/billing';

	// RBAC: the backend gates `GET /api/billing/subscription` to admin / cfo and
	// 403s everyone else. `isAdmin` covers admin; `isCfo` = admin|cfo, so the
	// union is exactly {admin, cfo}. Wait for `auth.user` to resolve before
	// redirecting — bouncing before /me lands would race first paint (the
	// discounts + audit pages document the same gotcha).
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isAdmin || auth.isCfo);

	$effect(() => {
		if (userLoaded && !allowed) goto('/');
	});

	let data = $state<BillingSubscriptionResponse | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Invoices / receipts list — loaded independently so a slow / failed invoices
	// fetch never blocks the plan + usage surface (and vice-versa).
	let invoices = $state<BillingInvoice[]>([]);
	let invoicesLoading = $state(true);
	let invoicesError = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			data = await getBillingSubscription();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load billing details.';
		} finally {
			loading = false;
		}
	}

	async function loadInvoices() {
		invoicesLoading = true;
		invoicesError = null;
		try {
			invoices = (await getBillingInvoices()).invoices;
		} catch (e) {
			invoicesError = e instanceof Error ? e.message : 'Failed to load invoices.';
		} finally {
			invoicesLoading = false;
		}
	}

	$effect(() => {
		// Only fetch once we know the role is allowed (avoids a guaranteed 403
		// for the clerk/manager before the redirect fires).
		if (userLoaded && allowed) {
			load();
			loadInvoices();
		}
	});

	const plan = $derived(data?.plan ?? null);
	const subscription = $derived(data?.subscription ?? null);
	const hasSubscription = $derived(plan !== null && subscription !== null);

	function formatDate(dateStr: string | null): string {
		if (!dateStr) return '—';
		const d = new Date(dateStr);
		if (Number.isNaN(d.getTime())) return dateStr;
		return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
	}

	/** Whole-number usage counter rendered defensively (the API sends strings). */
	function asCount(n: string | undefined): string {
		if (n === undefined || n === '') return '0';
		const v = Number(n);
		return Number.isFinite(v) ? v.toLocaleString() : n;
	}

	/** Period window label, e.g. "Jun 1 – Jul 1, 2026". */
	const periodWindow = $derived(
		subscription
			? `${formatDate(subscription.current_period_start)} → ${formatDate(subscription.current_period_end)}`
			: '—'
	);

	/** Human label for an invoice settlement state (data-driven English). */
	const INVOICE_STATUS_LABELS: Record<BillingInvoiceStatus, string> = {
		paid: 'Paid',
		open: 'Open',
		void: 'Void'
	};

	const INVOICE_COLUMNS = [
		{ label: 'Invoice' },
		{ label: 'Period' },
		{ label: 'Amount', class: 'num' },
		{ label: 'Status' },
		{ label: 'Date' },
		{ label: '', class: 'actions-col' }
	];

	/** Pretty list of granted entitlement flags (truthy boolean keys). */
	const entitlementFlags = $derived(
		plan
			? Object.entries(plan.entitlements)
					.filter(([, v]) => v === true)
					.map(([k]) => k)
			: []
	);
</script>

<PageHeader title="Billing">
	{#if loading}
		<p class="state" data-testid="billing-loading">Loading billing details…</p>
	{:else if error}
		<div class="state error" data-testid="billing-error" role="alert">
			<p>{error}</p>
			<button type="button" class="btn" onclick={load}>Retry</button>
		</div>
	{:else if !hasSubscription}
		<!-- Friendly empty state — the org has no live subscription yet. -->
		<div class="empty" data-testid="billing-empty">
			<h2>No active subscription</h2>
			<p>
				This workspace isn't on a billing plan yet. Usage is still metered below.
				To start a subscription or discuss plans, contact our team.
			</p>
			<a class="btn primary" href="mailto:billing@example.com">Contact sales</a>

			<section class="usage-section" aria-label="Usage to date">
				<h3>Usage this period <span class="period">({data?.period ?? '—'})</span></h3>
				<div class="kpi-row">
					<KpiCard value={asCount(data?.usage.extractions)} label="Extractions" />
					<KpiCard
						value={asCount(data?.usage.extractions_platform)}
						label="Billable extractions"
					/>
					<KpiCard
						value={formatMoney(data?.usage.card_rebate_total)}
						label="Card rebates"
						highlight="green"
					/>
				</div>
			</section>
		</div>
	{:else if plan && subscription}
		<!-- Active subscription detail. -->
		<section class="plan-card" aria-label="Current plan" data-testid="billing-plan">
			<div class="plan-head">
				<div>
					<span class="eyebrow">Current plan</span>
					<h2 class="plan-name">{plan.name}</h2>
				</div>
				<SubscriptionBadge status={subscription.status} />
			</div>

			<div class="plan-meta">
				<div class="meta-item">
					<span class="meta-label">Price</span>
					<span class="meta-value">
						<Money amount={plan.monthly_price} currency={plan.currency} />
						<span class="per">/ month</span>
					</span>
				</div>
				<div class="meta-item">
					<span class="meta-label">Billing period</span>
					<span class="meta-value">{periodWindow}</span>
				</div>
				{#if subscription.status === 'trialing'}
					<div class="meta-item">
						<span class="meta-label">Trial ends</span>
						<span class="meta-value">{formatDate(subscription.trial_end)}</span>
					</div>
				{/if}
				<div class="meta-item">
					<span class="meta-label">Managed by</span>
					<span class="meta-value">
						{subscription.externally_managed ? data?.provider ?? 'provider' : 'Self-serve'}
					</span>
				</div>
			</div>

			{#if entitlementFlags.length > 0}
				<div class="entitlements">
					<span class="meta-label">Included</span>
					<ul>
						{#each entitlementFlags as flag (flag)}
							<li>{flag.replace(/_/g, ' ')}</li>
						{/each}
					</ul>
				</div>
			{/if}

			<!-- Plan-change / payment-method actions are a later backend slice
			     (live Stripe). Surfaced disabled so the surface reads complete
			     without implying an action that isn't wired. -->
			<div class="actions">
				<button type="button" class="btn" disabled title="Coming soon">Change plan</button>
				<button type="button" class="btn" disabled title="Coming soon">
					Manage payment method
				</button>
				<a class="link" href="mailto:billing@example.com">Need a change? Contact us</a>
			</div>
		</section>

		<section class="usage-section" aria-label="Usage to date">
			<h3>Usage this period <span class="period">({data?.period ?? '—'})</span></h3>
			<div class="kpi-row">
				<KpiCard value={asCount(data?.usage.extractions)} label="Extractions" />
				<KpiCard
					value={asCount(data?.usage.extractions_platform)}
					label="Billable extractions"
				/>
			</div>
			<p class="note">
				Card rebates:
				<Money amount={data?.usage.card_rebate_total} />
				(informational)
			</p>
		</section>
	{/if}

	<!-- Invoices / receipts — the org's past platform-billing invoices. Loaded
	     independently of the plan/usage block above, so it renders for an org
	     with receipts but no live subscription too. -->
	{#if !loading && !error}
		<section class="invoices-section" aria-label="Invoices and receipts" data-testid="billing-invoices">
			<h3>Invoices &amp; receipts</h3>
			{#if invoicesLoading}
				<p class="state" data-testid="billing-invoices-loading">Loading invoices…</p>
			{:else if invoicesError}
				<div class="state error" data-testid="billing-invoices-error" role="alert">
					<p>{invoicesError}</p>
					<button type="button" class="btn" onclick={loadInvoices}>Retry</button>
				</div>
			{:else}
				<DataTable
					columns={INVOICE_COLUMNS}
					isEmpty={invoices.length === 0}
					empty="No invoices yet."
				>
					{#snippet body()}
						{#each invoices as inv (inv.id)}
							<tr>
								<td class="mono">{inv.number ?? '—'}</td>
								<td>{inv.period ?? '—'}</td>
								<td class="num"><Money amount={inv.amount} currency={inv.currency} /></td>
								<td>
									<span class="inv-badge {inv.status}">
										{INVOICE_STATUS_LABELS[inv.status] ?? inv.status}
									</span>
								</td>
								<td>{formatDate(inv.created_at)}</td>
								<td class="actions">
									{#if inv.hosted_url}
										<a
											class="link"
											href={inv.hosted_url}
											target="_blank"
											rel="noopener noreferrer"
											aria-label={`View invoice ${inv.number ?? inv.id} (opens in a new tab)`}
										>
											View
										</a>
									{:else}
										<span class="muted">—</span>
									{/if}
								</td>
							</tr>
						{/each}
					{/snippet}
				</DataTable>
			{/if}
		</section>
	{/if}
</PageHeader>

<style>
	.state {
		color: var(--text-muted, #94a3b8);
		padding: 1rem 0;
	}

	.state.error {
		color: #f06464;
	}

	.empty {
		max-width: 640px;
		padding: 1rem 0;
	}

	.empty h2 {
		margin: 0 0 0.5rem;
	}

	.empty p {
		color: var(--text-muted, #94a3b8);
		margin: 0 0 1rem;
	}

	.plan-card {
		background: var(--surface, #1a2035);
		border: 1px solid var(--border, #2a3350);
		border-radius: 12px;
		padding: 1.5rem;
		max-width: 720px;
		margin-bottom: 1.5rem;
	}

	.plan-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}

	.eyebrow {
		display: block;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-muted, #94a3b8);
	}

	.plan-name {
		margin: 0.25rem 0 0;
		font-size: 1.5rem;
	}

	.plan-meta {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 1rem;
		margin-top: 1.25rem;
	}

	.meta-item {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.meta-label {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted, #94a3b8);
	}

	.meta-value {
		font-weight: 600;
	}

	.per {
		font-weight: 400;
		color: var(--text-muted, #94a3b8);
		font-size: 0.85rem;
	}

	.entitlements {
		margin-top: 1.25rem;
	}

	.entitlements ul {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		list-style: none;
		padding: 0;
		margin: 0.5rem 0 0;
	}

	.entitlements li {
		background: rgba(99, 140, 255, 0.15);
		color: #7d9bff;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.8rem;
		text-transform: capitalize;
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
		margin-top: 1.5rem;
		padding-top: 1.25rem;
		border-top: 1px solid var(--border, #2a3350);
	}

	.usage-section h3 {
		margin: 0 0 0.75rem;
	}

	.period {
		font-weight: 400;
		color: var(--text-muted, #94a3b8);
		font-size: 0.9rem;
	}

	.kpi-row {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
		gap: 1rem;
	}

	.note {
		margin-top: 0.75rem;
		color: var(--text-muted, #94a3b8);
		font-size: 0.9rem;
	}

	.btn {
		background: var(--surface-2, #232b44);
		color: var(--text, #e2e8f0);
		border: 1px solid var(--border, #2a3350);
		border-radius: 8px;
		padding: 0.5rem 1rem;
		font-size: 0.9rem;
		cursor: pointer;
	}

	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn.primary {
		background: var(--accent, #638cff);
		color: #fff;
		border-color: transparent;
		text-decoration: none;
		display: inline-block;
	}

	.link {
		color: var(--accent, #7d9bff);
		font-size: 0.9rem;
	}

	.invoices-section {
		margin-top: 1.5rem;
		max-width: 720px;
	}

	.invoices-section h3 {
		margin: 0 0 0.75rem;
	}

	.num {
		text-align: right;
	}

	.muted {
		color: var(--text-muted, #94a3b8);
	}

	/* Settlement-state pill — mirrors the StatusBadge .badge recipe, tones
	   WCAG-1.4.3-calibrated against the dark surface (green/grey/red). */
	.inv-badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		white-space: nowrap;
	}

	.inv-badge.paid {
		background: rgba(31, 168, 106, 0.15);
		color: #26b977;
	}

	.inv-badge.open {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}

	.inv-badge.void {
		background: rgba(148, 163, 184, 0.18);
		color: #94a3b8;
	}
</style>
