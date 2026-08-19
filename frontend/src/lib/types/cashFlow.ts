/**
 * Types for the AI Cash-Flow Copilot (`/cash-flow`).
 *
 * The copilot rides the conversational-assistant orchestrator + SSE contract,
 * so its chat surface reuses the assistant's `ToolInvocation` / `ChatResponse`
 * / `UiMessage` types verbatim (re-exported here for a single import site).
 *
 * Money fields are string-Decimal on the wire (never a lossy float) — pass them
 * straight to `formatMoney` / `<Money>`; only coerce to a number to drive a
 * chart's bar/point geometry, never for display.
 */

export type { ChatResponse, ToolInvocation, UiMessage, UsageDelta } from './assistant';

/** One period of the running cash-position curve — from the `get_cash_position`
 *  tool result. `below_threshold` flags a projected breach of the minimum
 *  balance (rendered red on the chart). */
export interface CashPositionPeriod {
	period: string;
	opening: string;
	outflow: string;
	closing: string;
	below_threshold: boolean;
}

/** The `get_cash_position` tool result (`ToolInvocation.result`). All money
 *  fields are exact decimal strings. */
export interface CashPositionResult {
	currency: string;
	granularity: string;
	horizon_days: number;
	opening_balance: string;
	/** Which link of the resolution chain supplied the opening balance —
	 *  "explicit" | "provider" | "settings" | "none". */
	opening_balance_source: string;
	min_balance_threshold: string | null;
	periods: CashPositionPeriod[];
	first_shortfall_period: string | null;
}

/** One period of a proposed payment plan's cash curve — from the Phase 2
 *  `propose_payment_plan` tool result. Same shape as `CashPositionPeriod`
 *  minus `period`'s neighbours; kept as its own type since the two tools can
 *  diverge independently. */
export interface PaymentPlanPeriod {
	period: string;
	opening: string;
	outflow: string;
	closing: string;
	below_threshold: boolean;
}

/** One ranked early-payment discount recommendation — shared shape between
 *  the `optimize_discount_capture` tool and `propose_payment_plan`'s
 *  `discount_recommendations` (both come from the same optimizer pass). */
export interface DiscountRecommendation {
	offer_id: string;
	vendor_name: string | null;
	invoice_number: string | null;
	base_amount: string;
	discount_percent: string;
	annualized_return_pct: string;
	savings: string;
	pay_by: string;
	selected: boolean;
}

/** The `propose_payment_plan` tool result (Phase 2 — advisory; Phase 3 adds
 *  draft-only enactment on top, see docs/cash-flow-copilot.md §5/§6).
 *  Proposing never moves money. All money fields are exact decimal strings.
 */
export interface PaymentPlanResult {
	/** Deterministic correlation key for this plan (same resolved inputs +
	 *  today's date always hash the same). The Phase 3 enact endpoints
	 *  (`POST /api/cash-flow/plans/{plan_id}/{draft-run,capture-discounts}`)
	 *  recompute it server-side from the fields below and refuse (409) if it
	 *  no longer matches — never trust the client for WHAT to act on. */
	plan_id: string;
	currency: string;
	granularity: string;
	horizon_days: number;
	opening_balance: string;
	opening_balance_source: string;
	min_balance_threshold: string | null;
	/** The raw cash-budget input the discount selection was optimized under
	 *  (may be null — unconstrained). Echoed back verbatim so an enact call
	 *  can replay the exact same optimizer inputs. */
	cash_budget: string | null;
	periods: PaymentPlanPeriod[];
	first_shortfall_period: string | null;
	cost_of_capital_pct: string;
	total_savings_selected: string;
	total_outlay_selected: string;
	discount_recommendations: DiscountRecommendation[];
	/** offer_ids the optimizer selected but the plan could not re-time onto
	 *  the curve (a vendor-scoped offer with no single invoice, or an
	 *  invoice outside the forecast horizon) — still counted in the totals
	 *  above, just not reflected in `periods`. */
	unretimed_offer_ids: string[];
}

/* ---------------------------------------------------------------------------
 * Saved plans + plan-vs-actual.
 *
 * A saved plan is a FROZEN snapshot of one proposal, keyed by the same
 * deterministic `plan_id`. Saving does not change how a plan is enacted — it
 * exists so "did our forecast hold?" has a baseline to answer against. See
 * docs/cash-flow-copilot.md §5 (Persistence).
 * ------------------------------------------------------------------------ */

/** One period of a saved plan's frozen curve. Unlike `PaymentPlanPeriod` this
 *  carries its calendar window, because the comparison needs to say whether the
 *  period has closed. */
export interface SavedPlanPeriod {
	period: string;
	period_start: string;
	period_end: string;
	opening: string;
	outflow: string;
	closing: string;
	below_threshold: boolean;
	unconverted_count: number;
}

/** List-row shape — everything a picker needs, without the curve. */
export interface SavedPlanSummary {
	plan_id: string;
	plan_date: string;
	label: string | null;
	currency: string;
	granularity: string;
	horizon_days: number;
	/** Null when the snapshot is CONSOLIDATED (a whole-group plan). */
	entity_id: string | null;
	consolidated: boolean;
	opening_balance: string;
	min_balance_threshold: string | null;
	first_shortfall_period: string | null;
	total_savings_selected: string;
	period_count: number;
	unconverted_count: number;
	created_at: string;
}

export interface SavedPlanDetail extends SavedPlanSummary {
	cash_budget: string | null;
	cost_of_capital_pct: string;
	total_outlay_selected: string;
	periods: SavedPlanPeriod[];
	selected_offer_ids: string[];
	unretimed_offer_ids: string[];
	/** True once this plan has been staged into a draft payment run. */
	has_draft_run: boolean;
}

export interface SaveCashPlanResult {
	/** False when the plan was already saved — the ORIGINAL snapshot is
	 *  returned untouched, never restated against newer data. */
	created: boolean;
	plan: SavedPlanDetail;
}

/** One period of the plan-vs-actual comparison. `status` is
 *  `elapsed` | `in_progress` | `future`; only `elapsed` periods are scored
 *  into the totals. */
export interface PlanVariancePeriod {
	period: string;
	period_start: string;
	period_end: string;
	planned_outflow: string;
	actual_outflow: string;
	/** actual − planned. Positive = more cash left than projected. */
	variance: string;
	status: string;
}

export interface PlanVarianceResult {
	plan_id: string;
	plan_date: string;
	label: string | null;
	currency: string;
	granularity: string;
	consolidated: boolean;
	as_of: string;
	periods: PlanVariancePeriod[];
	planned_total: string;
	actual_total: string;
	variance_total: string;
	elapsed_period_count: number;
	open_period_count: number;
	unmatched_actual_periods: string[];
	unmatched_actual_total: string;
	/** Completed payments the comparison could not place (no settlement date)
	 *  or could not express in the plan's currency. Excluded from every total
	 *  above and reported rather than silently dropped. */
	undated_payment_count: number;
	unconvertible_payment_count: number;
	selected_offer_count: number;
	captured_offer_count: number;
	planned_unconverted_count: number;
}
