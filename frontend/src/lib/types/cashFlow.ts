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
