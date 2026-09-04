// Types for the Dynamic Discounting & Early-Payment Optimization surface.
// Mirrors the JSON the Phase-C `/api/discounts` router returns. Money fields
// arrive as JSON numbers (not string-Decimals); percentages are numbers.

/** A single sliding-scale tier: pay within `days` to earn `percent` off. */
export interface DiscountTier {
	days: number;
	percent: number;
}

export type DiscountScope = 'invoice' | 'vendor';
export type DiscountSource = 'supplier' | 'system' | 'financing';
export type DiscountStatus = 'offered' | 'accepted' | 'captured' | 'declined' | 'expired';

/** A discount offer on an invoice or a vendor-wide standing offer. */
export interface DiscountOffer {
	id: string;
	scope: DiscountScope;
	invoice_id: string | null;
	vendor_id: string | null;
	source: DiscountSource;
	status: DiscountStatus;
	/** Sliding-scale tiers, typically ordered soonest-deadline first. */
	tiers: DiscountTier[];
	/** Invoice (or projected) amount the discount applies to. */
	base_amount: number;
	currency: string;
	valid_from: string | null;
	valid_until: string | null;
	/** The tier the AP team accepted, or null while still `offered`. */
	accepted_tier: DiscountTier | null;
	accepted_at: string | null;
	/** Actual discount captured once paid; null until captured. */
	captured_amount: number | null;
	captured_at: string | null;
	financing_provider: string | null;
	notes: string | null;
	created_at: string;
	updated_at: string;
	/** Denormalised for table display. */
	vendor_name: string | null;
	invoice_number: string | null;
}

/** Paginated offer list — matches the shared `{items,total,page,page_size}` shape. */
export interface DiscountOfferPage {
	items: DiscountOffer[];
	total: number;
	page: number;
	page_size: number;
}

/** Dashboard KPI roll-up. Tenant-wide totals carry one `currency`. */
export interface DiscountDashboard {
	captured_count: number;
	captured_amount: number;
	missed_count: number;
	missed_amount: number;
	capture_rate_pct: number;
	open_offer_count: number;
	projected_savings: number;
	currency: string;
	/** Open offers left OUT of `projected_savings` because they are denominated
	 *  in something other than `currency` and no rate bridges them. The figure
	 *  is honest only because they were excluded, so the page must say so —
	 *  otherwise a multi-currency tenant reads a quietly-low number. */
	unconvertible_offer_count: number;
	/** Captured / declined+expired offers left OUT of `captured_amount` /
	 *  `missed_amount` for being denominated in something other than
	 *  `currency`. Amounts in different currencies are not added together, so
	 *  a non-zero count means those two realised figures describe part of the
	 *  set — the same honesty `unconvertible_offer_count` provides for
	 *  `projected_savings`. */
	excluded_captured_count: number;
	excluded_missed_count: number;
}

/** Per-invoice ROI / cost-of-capital comparison for accepting early payment. */
export interface DiscountRoi {
	base_amount: number;
	discount_percent: number;
	days_accelerated: number;
	savings: number;
	annualized_return_pct: number;
	cost_of_capital_pct: number;
	opportunity_cost: number;
	net_benefit: number;
	worthwhile: boolean;
}

/** One ranked recommendation in an optimization run. */
export interface DiscountRecommendation {
	offer_id: string;
	invoice_id: string | null;
	vendor_id: string | null;
	vendor_name: string | null;
	invoice_number: string | null;
	tier_days: number;
	discount_percent: number;
	pay_by: string | null;
	roi: DiscountRoi;
	/** The currency THIS row's money is in. `roi.savings` is computed from the
	 *  offer's own `base_amount`, so it is the OFFER's currency — equal to the
	 *  response-level `currency` only when `unconvertible` is false. Optional
	 *  because a response predating the field must still render (the client
	 *  falls back to a symbol-free figure rather than guessing a code). */
	currency?: string;
	/** Whether the optimizer selected this offer under the cash budget. */
	selected: boolean;
	/** Running cash outlay through this recommendation in the ranked list. */
	cumulative_outlay: number;
	/** This offer's money is in a currency the totals are NOT in, so it is
	 *  excluded from every total (and from selection when a budget binds). Its
	 *  ROI percentages stay meaningful — a rate is currency-free. */
	unconvertible: boolean;
}

/** Budget-constrained optimization result. */
export interface DiscountOptimization {
	cash_budget: number | null;
	/** The currency EVERY money total below is denominated in (the org's
	 *  reporting currency) — stated by the API rather than assumed, because the
	 *  totals sum across offers that carry their own currencies. */
	currency: string;
	cost_of_capital_pct: number;
	total_savings_available: number;
	total_savings_selected: number;
	total_outlay_selected: number;
	/** Ranked offers left out of the totals because they are in another
	 *  currency. Spelled differently from the dashboard's
	 *  `unconvertible_offer_count` — two responses, two field names. */
	unconvertible_count: number;
	recommendations: DiscountRecommendation[];
}

/** Status-filter keys the dashboard's FilterChips drive. `missed` maps to the
 *  backend `declined` + `expired` statuses (a derived bucket, not a status). */
export type DiscountStatusFilter = 'all' | 'offered' | 'accepted' | 'captured' | 'missed';
