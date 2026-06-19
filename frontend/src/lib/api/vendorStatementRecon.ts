// Typed helpers for the vendor statement reconciliation endpoints. All
// requests route through the shared `api` client (Bearer + X-Tenant-Slug +
// 401-bounce). Mirrors the pattern of `src/lib/api/recurring.ts`.
import { api } from '$lib/api';
import type {
	Reconciliation,
	ReconciliationCreate,
	ReconciliationListResponse,
	LineResolveRequest,
	CloseReadinessResponse
} from '$lib/types/vendorStatementRecon';

export interface ReconListParams {
	vendor_id?: string;
	status?: string;
	page?: number;
	page_size?: number;
}

export function listReconciliations(
	params: ReconListParams = {}
): Promise<ReconciliationListResponse> {
	const qs = new URLSearchParams();
	if (params.vendor_id) qs.set('vendor_id', params.vendor_id);
	if (params.status) qs.set('status', params.status);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<ReconciliationListResponse>(`/api/vendor-statements?${qs}`);
}

export function getReconciliation(id: string): Promise<Reconciliation> {
	return api.get<Reconciliation>(`/api/vendor-statements/${id}`);
}

// Create a reconciliation run from a pasted / normalised list of lines (JSON).
export function createReconciliation(body: ReconciliationCreate): Promise<Reconciliation> {
	return api.post<Reconciliation>('/api/vendor-statements', body);
}

// Create a reconciliation run by uploading a statement file (CSV / PDF), with
// the run metadata carried as multipart form fields alongside the file.
export function uploadReconciliation(
	file: File,
	fields: {
		vendor_id: string;
		statement_date: string;
		statement_reference?: string;
		currency?: string;
	}
): Promise<Reconciliation> {
	return api.upload<Reconciliation>('/api/vendor-statements/upload', file, {
		vendor_id: fields.vendor_id,
		statement_date: fields.statement_date,
		statement_reference: fields.statement_reference,
		currency: fields.currency
	});
}

export function deleteReconciliation(id: string): Promise<void> {
	return api.delete(`/api/vendor-statements/${id}`);
}

// Resolve / ignore / un-resolve a single reconciliation line.
export function resolveLine(
	reconciliationId: string,
	lineId: string,
	body: LineResolveRequest
): Promise<Reconciliation> {
	return api.post<Reconciliation>(
		`/api/vendor-statements/${reconciliationId}/lines/${lineId}/resolve`,
		body
	);
}

// Month-end close-readiness: vendors whose latest run still carries a material
// unreconciled balance.
export function getCloseReadiness(materiality?: number): Promise<CloseReadinessResponse> {
	const qs = new URLSearchParams();
	if (materiality !== undefined) qs.set('materiality', String(materiality));
	const suffix = qs.toString() ? `?${qs}` : '';
	return api.get<CloseReadinessResponse>(`/api/vendor-statements/close-readiness${suffix}`);
}
