// Types for the autonomous exception-agent surfaces (decision log + stats).
// Mirrors the backend Pydantic shapes in
// `backend/app/schemas/exception_agent.py`. Confidence is a display-only float
// (stored exact as Numeric(5,4) server-side).

export interface AgentDecision {
	id: string;
	exception_id: string;
	invoice_id: string;
	exception_type: string;
	action_taken: 'auto_resolved' | 'escalated' | 'no_action';
	confidence: number;
	rationale: string | null;
	changes: Record<string, { old: string; new: string }> | null;
	autonomy_level: string;
	agent_type: string;
	created_at: string;
}

export interface AgentDecisionList {
	items: AgentDecision[];
	total: number;
	page: number;
	page_size: number;
}

export interface AgentStats {
	total_decisions: number;
	auto_resolved: number;
	escalated: number;
	no_action: number;
	resolution_rate: number;
	escalation_rate: number;
	// Accuracy is a placeholder pending a human-overturn signal — `null` until
	// the platform tracks whether an auto-resolution was later reversed.
	accuracy: number | null;
}

export const ACTION_LABELS: Record<AgentDecision['action_taken'], string> = {
	auto_resolved: 'Auto-resolved',
	escalated: 'Escalated',
	no_action: 'No action'
};

/**
 * What `POST /api/exceptions/{id}/agent-resolve` returns — the exception's new
 * status plus the single append-only `AgentDecision` the coordinator recorded.
 *
 * There is always a decision, whatever the outcome. `escalated` is a NORMAL
 * result, not a failure: the coordinator resolves the org's autonomy threshold,
 * and a recommendation whose confidence doesn't clear it is handed to a human
 * instead of applied. `no_action` likewise means the resolver found nothing it
 * could safely change. Rendering either as an error would teach operators that
 * the safe path is the broken one.
 * See `backend/docs/exception-agents.md`.
 */
export interface AgentResolveResult {
	exception: { id: string; status: string };
	decision: AgentDecision;
}

/**
 * One exception an agent can be run on — the `GET /api/exceptions` row, narrowed
 * to what the runner panel shows and to what the endpoint will actually accept.
 *
 * `invoice_id` is nullable on the wire and the backend 422s an invoice-less
 * exception (a Positive Pay `not_on_file` fraud return has no invoice for an
 * agent to act on — human triage only), so the panel filters those out rather
 * than offering a Run that can only fail.
 */
export interface AgentCandidateException {
	id: string;
	invoice_id: string | null;
	invoice_number: string | null;
	vendor_name: string | null;
	exception_type: string;
	type_label: string;
	severity: string;
	status: string;
	created_at: string;
}

/** The statuses `POST .../agent-resolve` accepts; anything else 409s. */
export const AGENT_RUNNABLE_STATUSES = ['open', 'escalated'] as const;
