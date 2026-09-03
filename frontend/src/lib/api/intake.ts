// Typed helpers for the procurement intake endpoints. All requests route
// through the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID +
// 401-bounce). Mirrors the pattern of `src/lib/api/expenses.ts`.
import { api } from '$lib/api';
import type {
	IntakeRequest,
	IntakeCreate,
	IntakeUpdate,
	IntakeListResponse,
	IntakeSummary,
	IntakeConvertResponse
} from '$lib/types/intake';

export interface IntakeListParams {
	status?: string;
	type?: string;
	search?: string;
	page?: number;
	page_size?: number;
}

export function listIntake(params: IntakeListParams = {}): Promise<IntakeListResponse> {
	const qs = new URLSearchParams();
	if (params.status) qs.set('status', params.status);
	if (params.type) qs.set('type', params.type);
	if (params.search) qs.set('search', params.search);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<IntakeListResponse>(`/api/intake?${qs}`);
}

// Whole-set KPI rollup — status counts over the SAME status/type/search filters
// as `listIntake`, so `openCount` / `reviewCount` stop describing only the
// loaded page.
export function getIntakeSummary(
	params: Pick<IntakeListParams, 'status' | 'type' | 'search'> = {}
): Promise<IntakeSummary> {
	const qs = new URLSearchParams();
	if (params.status) qs.set('status', params.status);
	if (params.type) qs.set('type', params.type);
	if (params.search) qs.set('search', params.search);
	const suffix = qs.toString() ? `?${qs}` : '';
	return api.get<IntakeSummary>(`/api/intake/summary${suffix}`);
}

export function getIntake(id: string): Promise<IntakeRequest> {
	return api.get<IntakeRequest>(`/api/intake/${id}`);
}

export function createIntake(body: IntakeCreate): Promise<IntakeRequest> {
	return api.post<IntakeRequest>('/api/intake', body);
}

export function updateIntake(id: string, body: IntakeUpdate): Promise<IntakeRequest> {
	return api.patch<IntakeRequest>(`/api/intake/${id}`, body);
}

export function deleteIntake(id: string): Promise<void> {
	return api.delete(`/api/intake/${id}`);
}

// --- Status transitions ---

export function submitIntake(id: string): Promise<IntakeRequest> {
	return api.post<IntakeRequest>(`/api/intake/${id}/submit`, {});
}

export function approveIntake(id: string, reason?: string): Promise<IntakeRequest> {
	return api.post<IntakeRequest>(`/api/intake/${id}/approve`, { reason: reason ?? null });
}

export function rejectIntake(id: string, reason?: string): Promise<IntakeRequest> {
	return api.post<IntakeRequest>(`/api/intake/${id}/reject`, { reason: reason ?? null });
}

export function cancelIntake(id: string, reason?: string): Promise<IntakeRequest> {
	return api.post<IntakeRequest>(`/api/intake/${id}/cancel`, { reason: reason ?? null });
}

// --- Convert to requisition (idempotent on the backend) ---

export function convertIntakeToRequisition(
	id: string,
	body: { department?: string | null; needed_by?: string | null } = {}
): Promise<IntakeConvertResponse> {
	return api.post<IntakeConvertResponse>(`/api/intake/${id}/convert-to-requisition`, body);
}
