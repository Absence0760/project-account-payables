/**
 * Types for the Conversational AP Assistant.
 *
 * Money fields are serialised by the backend as strings (Decimal) — never
 * parse with `parseFloat` for display; pass straight to `formatMoney`, which
 * accepts a `string`-Decimal. Numeric coercion is only done where the value
 * drives a chart bar width (and even then on a copy, never for display).
 */

/** One tool invocation — same shape as `/chat`'s `tool_invocations[*]` and the
 *  SSE `tool` frame. `result` holds the full structured tool output (charts). */
export interface ToolInvocation {
	tool: string;
	args: Record<string, unknown>;
	result: Record<string, unknown> | null;
	error: string | null;
}

export interface UsageDelta {
	input_tokens: number;
	output_tokens: number;
}

/** Authoritative `/chat` (and SSE `done`) payload. */
export interface ChatResponse {
	conversation_id: string;
	answer: string;
	tool_invocations: ToolInvocation[];
	usage: UsageDelta;
}

export interface ConversationSummary {
	id: string;
	title: string | null;
	created_at: string;
	updated_at: string;
	message_count: number;
}

export interface AssistantMessageOut {
	id: string;
	role: string;
	content: string;
	tool_calls: ToolInvocation[];
	created_at: string;
}

export interface ConversationDetail {
	conversation: ConversationSummary;
	messages: AssistantMessageOut[];
}

export interface ConversationListResponse {
	items: ConversationSummary[];
	total: number;
}

export interface UsageResponse {
	period: string;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	budget: number;
	remaining: number;
	request_count: number;
}

// --- Structured tool result shapes (for chart / table rendering) ---

export interface VendorSpendRow {
	vendor_id?: string | null;
	vendor_name: string;
	amount: string;
	share_pct: string;
}

export interface VendorSpendResult {
	period_label: string;
	currency: string;
	total_spend: string;
	vendors: VendorSpendRow[];
}

export interface ForecastBucket {
	period: string;
	amount: string;
	count: number;
}

export interface ForecastResult {
	currency: string;
	horizon_label: string;
	buckets: ForecastBucket[];
	total: string;
}

export interface InvoiceSummaryRow {
	id: string;
	invoice_number: string;
	vendor_name: string;
	amount: string;
	currency: string;
	status: string;
	invoice_date?: string | null;
	due_date?: string | null;
}

export interface InvoiceListResult {
	items: InvoiceSummaryRow[];
	total: number;
	applied_filters: Record<string, unknown>;
}

export interface PendingApprovalRow {
	invoice_id: string;
	invoice_number: string;
	vendor_name: string;
	amount: string;
	currency: string;
	assigned_to_id?: string | null;
	waiting_since?: string | null;
}

export interface PendingApprovalsResult {
	items: PendingApprovalRow[];
	total: number;
}

export interface TextSearchMatch {
	invoice_id: string;
	vendor_name?: string | null;
	similarity: number;
	snippet: string;
}

export interface TextSearchResult {
	matches: TextSearchMatch[];
}

/** A chat message as rendered in the UI. The in-progress assistant message
 *  accumulates `content` from `delta` frames and `tools` from `tool` frames. */
export interface UiMessage {
	role: 'user' | 'assistant';
	content: string;
	tools: ToolInvocation[];
	/** True while a streaming assistant reply is still arriving. */
	streaming?: boolean;
	/** Set when a turn failed (renders an inline error instead of prose). */
	error?: string | null;
}

/** The three built-in example prompts (verbatim from docs/roadmap.md). */
export const EXAMPLE_PROMPTS: readonly string[] = [
	'which approvals have I been sitting on > 5 days?',
	'which vendors are we paying the most this quarter?',
	'show me invoices with PO mismatches over $10k'
] as const;
