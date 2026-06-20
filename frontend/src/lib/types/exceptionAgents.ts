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
