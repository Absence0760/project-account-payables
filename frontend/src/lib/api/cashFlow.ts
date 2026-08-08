// Typed helpers for the AI Cash-Flow Copilot's Phase 3 enact endpoints
// (`POST /api/cash-flow/plans/{plan_id}/{draft-run,capture-discounts}`). Both
// re-derive the plan server-side from the SAME defining parameters the
// `propose_payment_plan` tool result already carries (granularity,
// horizon_days, min_balance_threshold, cash_budget, cost_of_capital_pct) —
// callers must echo those back verbatim, never invent their own. See
// docs/cash-flow-copilot.md §6.
import { api } from '$lib/api';
import type { PaymentPlanResult } from '$lib/types/cashFlow';

export interface PlanReplayParams {
	granularity: string;
	horizon_days: number;
	min_balance_threshold: string | null;
	cash_budget: string | null;
	cost_of_capital_pct: string;
}

export interface DraftRunResult {
	plan_id: string;
	/** False when this call returned an ALREADY-EXISTING draft run for this
	 *  plan_id (idempotent replay) rather than staging a new one. */
	created: boolean;
	run_id: string;
	status: string;
	total_amount: string;
	payment_count: number;
	requires_cfo_approval: boolean;
}

export interface CaptureDiscountsResult {
	plan_id: string;
	accepted_offer_ids: string[];
	accepted_count: number;
	skipped_count: number;
	total_savings_selected: string;
}

/** Build the replay body from a `propose_payment_plan` tool result — the
 *  single place that decides which fields round-trip back to the enact
 *  endpoints, so a future field added to the plan can't accidentally get
 *  echoed somewhere it shouldn't (or forgotten somewhere it should). */
export function planReplayParams(plan: PaymentPlanResult): PlanReplayParams {
	return {
		granularity: plan.granularity,
		horizon_days: plan.horizon_days,
		min_balance_threshold: plan.min_balance_threshold,
		cash_budget: plan.cash_budget,
		cost_of_capital_pct: plan.cost_of_capital_pct
	};
}

/** Tier 1 — stage a DRAFT payment run over the plan's currently-payable
 *  commitments. Never executes; the CFO-gated `/execute` path is unchanged.
 *  Idempotent on `plan.plan_id`. */
export function createDraftRunFromPlan(plan: PaymentPlanResult): Promise<DraftRunResult> {
	return api.post<DraftRunResult>(
		`/api/cash-flow/plans/${plan.plan_id}/draft-run`,
		planReplayParams(plan)
	);
}

/** Tier 2 — accept every discount offer the SAME optimizer pass selected.
 *  Status-only; never moves money. A repeat call is a clean no-op. */
export function captureDiscountsFromPlan(
	plan: PaymentPlanResult
): Promise<CaptureDiscountsResult> {
	return api.post<CaptureDiscountsResult>(
		`/api/cash-flow/plans/${plan.plan_id}/capture-discounts`,
		planReplayParams(plan)
	);
}
