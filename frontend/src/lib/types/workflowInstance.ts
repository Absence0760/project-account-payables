// Runtime workflow-instance shapes returned by `GET /api/invoices/{id}/workflow`
// (backend/app/schemas/workflow.py::WorkflowInstanceResponse). Distinct from
// `workflow.ts`, which types the DEFINITION/config side (`ApprovalLevelConfig`
// etc.) — this file types the live per-invoice STATE, in particular the
// multi-level approval-chain progress persisted on
// `WorkflowInstance.state_data.approval_levels`
// (backend/app/services/approval_chain.py::init_chain_state /
// advance_approval_chain). See backend/docs/workflow-design.md
// § Multi-Level Approval Chains.

export interface ChainApprovalRecord {
	user_id: string;
	approved_at: string;
}

export interface ChainLevelState {
	level: number;
	name: string;
	required: number;
	approver_ids: string[];
	approvals: ChainApprovalRecord[];
	parallel_mode: 'any' | 'all';
	escalation_hours: number | null;
	escalation_to_user_ids: string[];
	entered_at: string | null;
	escalations: unknown[];
}

export interface ApprovalChainState {
	levels: ChainLevelState[];
	current_level: number;
}

export interface WorkflowStepInstance {
	id: string;
	step_number: number;
	step_type: string;
	assigned_to: string | null;
	action: string | null;
	completed_at: string | null;
	created_at: string;
}

export interface WorkflowInstanceDetail {
	id: string;
	correlation_id: string;
	definition_id: string;
	invoice_id: string;
	current_step: number;
	state: string;
	// JSONB — `approval_levels` is the only key this frontend reads today; the
	// rest of state_data (ERP references, rejection counters, …) is opaque here.
	state_data: { approval_levels?: ApprovalChainState } | null;
	steps: WorkflowStepInstance[];
	created_at: string;
}

/** Distinct-approver count so far at a level (mirrors the backend's own
 *  `_level_satisfied` dedup — the same user approving twice must not count
 *  twice). */
export function distinctApprovedCount(level: ChainLevelState): number {
	return new Set(level.approvals.map((a) => a.user_id)).size;
}

/** How many MORE approvals this level needs before it is satisfied — the
 *  same rule `approval_chain._level_satisfied` enforces server-side:
 *  `all` mode needs every named approver to have approved at least once;
 *  `any` mode (or an unrestricted level) needs `required` distinct approvers.
 *  Returns 0 once satisfied, never negative. */
export function chainLevelRemaining(level: ChainLevelState): number {
	const approved = new Set(level.approvals.map((a) => a.user_id));
	if (level.parallel_mode === 'all' && level.approver_ids.length > 0) {
		return level.approver_ids.filter((id) => !approved.has(id)).length;
	}
	return Math.max(0, (level.required ?? 1) - approved.size);
}

/** Whether every level in the chain has been satisfied. */
export function chainIsComplete(state: ApprovalChainState): boolean {
	return state.current_level >= state.levels.length;
}
