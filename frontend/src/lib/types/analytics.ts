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
