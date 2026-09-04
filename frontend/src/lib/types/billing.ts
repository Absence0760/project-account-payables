/**
 * Response contracts for the platform billing surface — how the AP platform
 * bills its OWN customers (the orgs/tenants): plans, subscription state, and
 * usage-to-date. This is the control-plane billing read surface, distinct from
 * the accounts-payable money path the app manages for customers.
 *
 * Mirrors `GET /api/billing/subscription` (`backend/app/api/billing.py`).
 * Money values arrive as exact decimal **strings** (this is a billing surface —
 * exactness is the point) and are rendered through `<Money>` / `formatMoney`,
 * never re-computed client-side.
 */

/** Lifecycle of an org's subscription. Mirrors the backend's four states. */
export type SubscriptionStatus = 'trialing' | 'active' | 'past_due' | 'canceled';

export interface BillingPlan {
	/** Stable machine id (e.g. `growth`). */
	code: string;
	/** Display name (e.g. `Growth`). */
	name: string;
	/** Monthly price as an exact decimal string (e.g. `"49.00"`). */
	monthly_price: string;
	/** ISO 4217 currency code. */
	currency: string;
	/** Feature flags, e.g. `{ "public_api": true, "max_seats": 25 }`. */
	entitlements: Record<string, unknown>;
	/** Free-trial length in days. */
	trial_days: number;
}

export interface BillingSubscription {
	status: SubscriptionStatus;
	/** ISO timestamps bounding the current billing period. */
	current_period_start: string | null;
	current_period_end: string | null;
	/** ISO timestamp the trial ends (null once the trial is over / never trialed). */
	trial_end: string | null;
	/** True when a live provider (Stripe) owns the subscription. */
	externally_managed: boolean;
}

/** Usage-to-date for the current period. All values are exact strings.
 *
 * The rebate meter arrives as one key PER CURRENCY
 * (`card_rebate_total.USD`), never a bare `card_rebate_total` — it used to be
 * a single cross-currency sum, which is a quantity in no currency at all, and
 * this is a meter a later billing slice prices. Read it through
 * {@link rebateMeterGroups} rather than indexing a key by hand.
 */
export interface BillingUsage {
	/** Total extraction events in the period. */
	extractions: string;
	/** The billable (platform-program) subset of extractions. */
	extractions_platform: string;
	/** Per-currency rebate meters + any meter a later slice adds. */
	[meter: string]: string;
}

/** One currency's rebate total, as it arrives on the meter map. */
export interface RebateMeterGroup {
	/** ISO 4217 code, from the meter key. */
	currency: string;
	/** Exact decimal string — never parsed to a number for display. */
	amount: string;
}

/** The prefix every per-currency rebate meter carries. */
const REBATE_METER_PREFIX = 'card_rebate_total.';

/** Per-currency rebate totals from a usage meter map, sorted by currency code.
 *
 * Returns `[]` when the org accrued no rebates — the backend emits no rebate
 * key at all in that case, deliberately, because zero rebates in an unstated
 * currency is not a fact. A caller renders nothing rather than a `$0.00` whose
 * currency it invented.
 *
 * Pure, so it is unit-tested; a malformed key (no 3-letter code) is skipped
 * rather than rendered under a fabricated code.
 */
export function rebateMeterGroups(usage: BillingUsage | null | undefined): RebateMeterGroup[] {
	if (!usage) return [];
	const groups: RebateMeterGroup[] = [];
	for (const [key, amount] of Object.entries(usage)) {
		if (!key.startsWith(REBATE_METER_PREFIX)) continue;
		const currency = key.slice(REBATE_METER_PREFIX.length).trim().toUpperCase();
		if (currency.length !== 3) continue;
		groups.push({ currency, amount: String(amount ?? '0') });
	}
	groups.sort((a, b) => a.currency.localeCompare(b.currency));
	return groups;
}

export interface BillingSubscriptionResponse {
	/** Active billing adapter (e.g. `mock`, `stripe_billing`). */
	provider: string;
	/** Null when the org has no live subscription. */
	plan: BillingPlan | null;
	/** Null when the org has no live subscription. */
	subscription: BillingSubscription | null;
	/** The period the usage is rolled up for, `YYYY-MM`. */
	period: string;
	usage: BillingUsage;
}

/** Settlement state of a past billing invoice / receipt. */
export type BillingInvoiceStatus = 'paid' | 'open' | 'void';

/** One past platform-billing invoice / receipt. Mirrors `GET /api/billing/invoices`
 *  (`backend/app/api/billing.py`). Money is an exact decimal string. */
export interface BillingInvoice {
	/** Provider-side invoice id (stable row key). */
	id: string;
	/** Human invoice number (e.g. `MOCK-2026-06`), null when the provider omits it. */
	number: string | null;
	/** Billing period the invoice covers, `YYYY-MM` (null when unknown). */
	period: string | null;
	/** Total as an exact decimal string — rendered via `<Money>`, never re-computed. */
	amount: string;
	/** ISO 4217 currency code. */
	currency: string;
	status: BillingInvoiceStatus;
	/** Hosted invoice / receipt URL (e.g. Stripe), null when the provider has none. */
	hosted_url: string | null;
	/** ISO timestamp the invoice was created (null when unknown). */
	created_at: string | null;
}

export interface BillingInvoicesResponse {
	/** Active billing adapter (e.g. `mock`, `stripe_billing`). */
	provider: string;
	/** Past invoices / receipts, newest first. Empty when the org has none. */
	invoices: BillingInvoice[];
}

/**
 * One saved payment method — PII-safe metadata ONLY (brand / last4 / expiry).
 * NEVER a full PAN. Mirrors `GET /api/billing/payment-methods`
 * (`backend/app/api/billing.py`).
 */
export interface BillingPaymentMethod {
	/** Provider-side payment-method id (stable row key). */
	id: string;
	/** Card brand (e.g. `visa`), null when the provider omits it. */
	brand: string | null;
	/** Last four digits of the card — never the full PAN. */
	last4: string | null;
	/** Expiry month (1–12), null when unknown. */
	exp_month: number | null;
	/** Expiry year (four-digit), null when unknown. */
	exp_year: number | null;
	/** Whether this is the org's default card. */
	is_default: boolean;
}

export interface BillingPaymentMethodsResponse {
	/** Active billing adapter (e.g. `mock`, `stripe_billing`). */
	provider: string;
	/** Saved cards as PII-safe metadata. Empty when the org has none on file. */
	payment_methods: BillingPaymentMethod[];
}

/**
 * Result of starting a SetupIntent to add / replace a card. Mirrors
 * `POST /api/billing/payment-method/setup-intent` (`backend/app/api/billing.py`).
 * `client_secret` is the single-use secret the provider's JS SDK (Stripe
 * Elements, deployed-only) confirms the card against — never a long-lived secret
 * or a PAN. `configured=false` (no customer / unconfigured provider) → null
 * secret + the UI shows a "billing not configured" state, not an error.
 */
export interface BillingSetupIntentResponse {
	/** Active billing adapter (e.g. `mock`, `stripe_billing`). */
	provider: string;
	/** True once a SetupIntent could be started (org provisioned + provider configured). */
	configured: boolean;
	/** Single-use secret the frontend confirms the card with. Null when not configured. */
	client_secret: string | null;
	/** Provider-side SetupIntent id. Null when not configured. */
	setup_intent_id: string | null;
}

/** The sellable plan catalog (active plans only). Mirrors `GET /api/billing/plans`
 *  (`backend/app/api/billing.py`) — the data source for the plan-change picker. */
export interface BillingPlansResponse {
	/** Active plans, cheapest first. */
	plans: BillingPlan[];
}

/**
 * Mid-period proration for a plan change — an exact decimal STRING. Positive =
 * extra charge (upgrade), negative = credit (downgrade), `"0.00"` = no change /
 * same plan. Mirrors `POST /api/billing/change-plan`'s `ProrationView`.
 */
export interface BillingPlanChangeProration {
	amount: string;
	unused_days: number;
	period_days: number;
}

/**
 * Result of `POST /api/billing/change-plan`. This call APPLIES the change —
 * there is no preview-only mode on the backend — so `changed` distinguishes a
 * real mutation from the idempotent no-op of "changing" to the plan the org is
 * already on (no mutation, zero proration).
 */
export interface BillingPlanChangeResponse {
	changed: boolean;
	old_plan_code: string;
	new_plan_code: string;
	proration: BillingPlanChangeProration;
}
