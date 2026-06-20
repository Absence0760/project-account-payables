// Typed helpers for the autonomous exception-agent surfaces. All requests route
// through the shared `api` client (Bearer + X-Tenant-Slug + 401-bounce). Backend
// endpoints are admin/ap_manager-gated (see `app/api/exception_agents.py`).
import { api } from '$lib/api';
import type { AgentDecisionList, AgentStats } from '$lib/types/exceptionAgents';

export interface AgentDecisionParams {
	exceptionType?: string;
	actionTaken?: string;
	page?: number;
	pageSize?: number;
}

export function getAgentStats(): Promise<AgentStats> {
	return api.get<AgentStats>('/api/exceptions/agent-stats');
}

export function getAgentDecisions(params: AgentDecisionParams = {}): Promise<AgentDecisionList> {
	const q = new URLSearchParams();
	if (params.exceptionType) q.set('exception_type', params.exceptionType);
	if (params.actionTaken) q.set('action_taken', params.actionTaken);
	q.set('page', String(params.page ?? 1));
	q.set('page_size', String(params.pageSize ?? 20));
	return api.get<AgentDecisionList>(`/api/exceptions/agent-decisions?${q}`);
}
