// Response shapes for the predictive cash-flow forecasting endpoints
// (backend/app/api/analytics.py). Hand-maintained — the project has no
// codegen, mirroring src/lib/types/invoice.ts.

export type CashflowGranularity = 'day' | 'week' | 'month';

export interface CashflowForecastPeriod {
	period: string;
	period_start: string;
	period_end: string;
	scheduled_amount: number;
	committed_amount: number;
	pending_amount: number;
	discount_eligible_amount: number;
	count: number;
}

export interface CashflowForecast {
	granularity: CashflowGranularity;
	horizon_days: number;
	include_pending: boolean;
	generated_at: string;
	periods: CashflowForecastPeriod[];
	totals: {
		scheduled_amount: number;
		committed_amount: number;
		pending_amount: number;
		discount_eligible_amount: number;
		count: number;
	};
}

export interface WhatIfScenario {
	scenario: 'early' | 'on_time' | 'late';
	total_outflow: number;
	total_discount_captured: number;
	weighted_avg_pay_date_days: number;
	periods: Array<{
		period: string;
		period_start: string;
		period_end: string;
		scheduled_amount: number;
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
	opening: number;
	outflow: number;
	inflow: number;
	closing: number;
	below_threshold: boolean;
}

export interface CashPositionBreach {
	period: string;
	period_start: string | null;
	period_end: string | null;
	closing: number;
	shortfall: number;
}

export interface CashPosition {
	granularity: CashflowGranularity;
	horizon_days: number;
	opening_balance: number;
	opening_balance_source: 'query' | 'settings' | 'none';
	threshold: number | null;
	periods: CashPositionPeriod[];
	breaches: CashPositionBreach[];
}

// CFO metrics dashboard — `GET /api/analytics/cfo`. Unlike most of this file
// (and unlike the by-entity rollup below), money here is plain `number`
// (backend-serialized float), matching the pre-existing convention of the
// forecast/what-if/cash-position endpoints on this same page — a tenant-wide
// roll-up with no per-row currency, rendered via the org default currency
// (`orgCurrency`), same as `CashflowForecast` above.

export interface CfoDpoTrendPoint {
	month: string;
	dpo: number;
}

export interface CfoAccruals {
	open_po_amount: number;
	received_amount: number;
	unposted_invoice_amount: number;
	total_accrual: number;
}

export interface CfoSupplierConcentration {
	total_spend: number;
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
	rebates_total: number;
	total_spend: number;
	yield_pct: number;
	annualised_rebates: number;
}

export interface CfoUnrealizedFxByCurrency {
	currency: string;
	open_original_amount: number;
	booked_reporting_amount: number;
	current_reporting_amount: number;
	unrealized_gain_loss: number;
}

export interface CfoUnrealizedFx {
	reporting_currency: string;
	total_unrealized_gain_loss: number;
	by_currency: CfoUnrealizedFxByCurrency[];
	available: boolean;
}

export interface CfoReportingSpendByCurrency {
	currency: string;
	original_amount: number;
	reporting_amount: number;
	count: number;
	unconverted_count: number;
}

export interface CfoReportingSpend {
	reporting_currency: string;
	total_amount: number;
	total_count: number;
	unconverted_count: number;
	by_currency: CfoReportingSpendByCurrency[];
}

export interface CfoAnalytics {
	period_days: number;
	period_start: string;
	total_spend: number;
	reporting_spend: CfoReportingSpend;
	unrealized_fx: CfoUnrealizedFx;
	accounts_payable_balance: number;
	dpo_current: number;
	dpo_trend: CfoDpoTrendPoint[];
	cash_conversion_cycle: number | null;
	accruals: CfoAccruals;
	working_capital_impact_5_days: number;
	avg_daily_outflow: number;
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
