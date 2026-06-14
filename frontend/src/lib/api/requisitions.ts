// Typed helpers for the Procurement / Requisitions endpoints. All requests
// route through the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID +
// 401-bounce). Mirrors the pattern of `src/lib/api/expenses.ts`.
import { api } from '$lib/api';
import type {
	Requisition,
	RequisitionListResponse,
	RequisitionCreate,
	RequisitionUpdate,
	ConvertToPoResult
} from '$lib/types/requisition';

export interface RequisitionListParams {
	status?: string;
	search?: string;
	page?: number;
	page_size?: number;
}

export function listRequisitions(
	params: RequisitionListParams = {}
): Promise<RequisitionListResponse> {
	const qs = new URLSearchParams();
	if (params.status) qs.set('status', params.status);
	if (params.search) qs.set('search', params.search);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<RequisitionListResponse>(`/api/requisitions?${qs}`);
}

export function getRequisition(id: string): Promise<Requisition> {
	return api.get<Requisition>(`/api/requisitions/${id}`);
}

export function createRequisition(body: RequisitionCreate): Promise<Requisition> {
	return api.post<Requisition>('/api/requisitions', body);
}

export function updateRequisition(id: string, body: RequisitionUpdate): Promise<Requisition> {
	return api.patch<Requisition>(`/api/requisitions/${id}`, body);
}

export function deleteRequisition(id: string): Promise<void> {
	return api.delete(`/api/requisitions/${id}`);
}

// --- Approval state machine ---

export function submitRequisition(id: string): Promise<Requisition> {
	return api.post<Requisition>(`/api/requisitions/${id}/submit`, {});
}

export function approveRequisition(id: string): Promise<Requisition> {
	return api.post<Requisition>(`/api/requisitions/${id}/approve`, {});
}

export function rejectRequisition(id: string, reason?: string): Promise<Requisition> {
	return api.post<Requisition>(`/api/requisitions/${id}/reject`, { reason: reason ?? null });
}

export function cancelRequisition(id: string, reason?: string): Promise<Requisition> {
	return api.post<Requisition>(`/api/requisitions/${id}/cancel`, { reason: reason ?? null });
}

// --- Convert to PO (idempotent) ---

export function convertRequisitionToPo(id: string): Promise<ConvertToPoResult> {
	return api.post<ConvertToPoResult>(`/api/requisitions/${id}/convert-to-po`, {});
}
