// Response shapes for the predictive cash-flow forecasting endpoints
// (backend/app/api/analytics.py). Hand-maintained — the project has no
// codegen, mirroring src/lib/types/invoice.ts.
//
// Money is `MoneyString` — an EXACT decimal string, never a JSON number — so
// no currency figure round-trips through a binary float. Render it with
// `<Money>` / `formatMoney`; the only sanctioned way to get a number out of
// one is `parseMoneyForLayout` (chart geometry + ordering only). A day count,
// a percentage and a row count are genuinely numbers and stay `number`.

import type { MoneyString } from '$lib/utils/money';

export type CashflowGranularity = 'day' | 'week' | 'month';

export interface CashflowForecastPeriod {
	period: string;
	period_start: string;
	period_end: string;
	scheduled_amount: MoneyString;
	committed_amount: MoneyString;
	pending_amount: MoneyString;
	discount_eligible_amount: MoneyString;
	count: number;
}

export interface CashflowForecast {
	granularity: CashflowGranularity;
	horizon_days: number;
	include_pending: boolean;
	generated_at: string;
	periods: CashflowForecastPeriod[];
	totals: {
		scheduled_amount: MoneyString;
		committed_amount: MoneyString;
		pending_amount: MoneyString;
		discount_eligible_amount: MoneyString;
		count: number;
	};
}

export interface WhatIfScenario {
	scenario: 'early' | 'on_time' | 'late';
	total_outflow: MoneyString;
	total_discount_captured: MoneyString;
	/** A day count, not money. */
	weighted_avg_pay_date_days: number;
	periods: Array<{
		period: string;
		period_start: string;
		period_end: string;
		scheduled_amount: MoneyString;
	}>;
}

export interface WhatIfScenarios {
	granularity: CashflowGranularity;
	horizon_days: number;
	grace_days: number;
	scenarios: {
		early: WhatIfScenario;
		on_time: WhatIfScenario;
		late: WhatIfScenario;
	};
}

export interface CashPositionPeriod {
	period: string;
	period_start: string | null;
	period_end: string | null;
	opening: MoneyString;
	outflow: MoneyString;
	inflow: MoneyString;
	closing: MoneyString;
	below_threshold: boolean;
}

export interface CashPositionBreach {
	period: string;
	period_start: string | null;
	period_end: string | null;
	closing: MoneyString;
	shortfall: MoneyString;
}

export interface CashPosition {
	granularity: CashflowGranularity;
	horizon_days: number;
	opening_balance: MoneyString;
	// Provenance of the figure the curve starts from — the shared resolution
	// chain in `services/cashflow.py::resolve_opening_balance` (the same one the
	// cash-flow copilot uses, hence `explicit` rather than the endpoint's old
	// `query`). `provider` was already reachable before this union listed it.
	opening_balance_source: 'explicit' | 'provider' | 'settings' | 'none';
	// The reporting currency the whole curve is denominated in.
	opening_balance_currency: string;
	// Adapter name when a bank sync supplied the balance, else null.
	opening_balance_provider: string | null;
	// `'currency_mismatch'` when a live provider balance existed but was refused
	// because its account is in another currency than the org reports in — so a
	// fallback to `settings`/`none` isn't mistaken for "no bank is connected".
	opening_balance_provider_skipped: string | null;
	/** `null` means no threshold is set — deliberately not `"0"`. */
	threshold: MoneyString | null;
	periods: CashPositionPeriod[];
	breaches: CashPositionBreach[];
}

// CFO metrics dashboard — `GET /api/analytics/cfo`. A tenant-wide roll-up with
// no per-row currency, rendered via the org default currency (`orgCurrency`).
// Money is `MoneyString`, like every other endpoint in this file. What is NOT
// a string here is not money: a DAY COUNT (`dpo_*`, `cash_conversion_cycle`), a
// PERCENTAGE (`*_share_pct`, `rate_pct`, `yield_pct`) and a row count are
// genuinely numbers, and stringifying one would be a bug, not compliance.

export interface CfoDpoTrendPoint {
	month: string;
	/** A day count, not money. */
	dpo: number;
}

export interface CfoAccruals {
	open_po_amount: MoneyString;
	received_amount: MoneyString;
	unposted_invoice_amount: MoneyString;
	total_accrual: MoneyString;
}

export interface CfoSupplierConcentration {
	total_spend: MoneyString;
	top_10_share_pct: number;
	top_50_share_pct: number;
	largest_vendor: string | null;
	largest_vendor_share_pct: number;
	flagged: boolean;
}

export interface CfoFraudTrendPoint {
	month: string;
	invoice_count: number;
	exception_count: number;
	rate_pct: number;
}

export interface CfoRebateYield {
	rebates_total: MoneyString;
	total_spend: MoneyString;
	yield_pct: number;
	annualised_rebates: MoneyString;
}

export interface CfoUnrealizedFxByCurrency {
	currency: string;
	open_original_amount: MoneyString;
	booked_reporting_amount: MoneyString;
	current_reporting_amount: MoneyString;
	unrealized_gain_loss: MoneyString;
}

export interface CfoUnrealizedFx {
	reporting_currency: string;
	total_unrealized_gain_loss: MoneyString;
	by_currency: CfoUnrealizedFxByCurrency[];
	available: boolean;
}

export interface CfoReportingSpendByCurrency {
	currency: string;
	original_amount: MoneyString;
	reporting_amount: MoneyString;
	count: number;
	unconverted_count: number;
}

export interface CfoReportingSpend {
	reporting_currency: string;
	total_amount: MoneyString;
	total_count: number;
	unconverted_count: number;
	by_currency: CfoReportingSpendByCurrency[];
}

export interface CfoAnalytics {
	period_days: number;
	period_start: string;
	total_spend: MoneyString;
	reporting_spend: CfoReportingSpend;
	unrealized_fx: CfoUnrealizedFx;
	accounts_payable_balance: MoneyString;
	/** A day count, not money. */
	dpo_current: number;
	dpo_trend: CfoDpoTrendPoint[];
	/** A day count, not money; `null` when receivables data is unavailable. */
	cash_conversion_cycle: number | null;
	accruals: CfoAccruals;
	working_capital_impact_5_days: MoneyString;
	avg_daily_outflow: MoneyString;
	supplier_concentration: CfoSupplierConcentration;
	fraud_rate_trend: CfoFraudTrendPoint[];
	rebate_yield: CfoRebateYield;
}

// Consolidated reporting ACROSS entities — `GET /api/analytics/by-entity`.
// Money fields are string-Decimal (the backend never floats currency); render
// them through the `Money` component / `formatMoney`, never `parseFloat`. This
// endpoint reports every active entity at once and intentionally ignores the
// X-Entity-ID selection.

export interface EntityMetrics {
	total_spend: string;
	outstanding_amount: string;
	invoice_count: number;
	open_exceptions: number;
	open_po_amount: string;
}

export interface EntityRollupRow extends EntityMetrics {
	entity_id: string;
	entity_name: string;
	entity_slug: string;
	currency: string | null;
	is_default: boolean;
}

export interface AnalyticsByEntity {
	period_days: number;
	period_start: string;
	entities: EntityRollupRow[];
	consolidated: EntityMetrics;
}
