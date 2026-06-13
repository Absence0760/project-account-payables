// Typed helpers for the contract endpoints. All requests route through the
// shared `api` client (Bearer + X-Tenant-Slug + 401-bounce). Mirrors the
// pattern of `src/lib/api/tax.ts` / `audit.ts`.
import { api } from '$lib/api';
import type {
	Contract,
	ContractCreate,
	ContractListResponse
} from '$lib/types/contract';

export interface ContractListParams {
	status?: string;
	contract_type?: string;
	vendor_id?: string;
	search?: string;
	page?: number;
	page_size?: number;
}

export function listContracts(params: ContractListParams = {}): Promise<ContractListResponse> {
	const qs = new URLSearchParams();
	if (params.status) qs.set('status', params.status);
	if (params.contract_type) qs.set('contract_type', params.contract_type);
	if (params.vendor_id) qs.set('vendor_id', params.vendor_id);
	if (params.search) qs.set('search', params.search);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<ContractListResponse>(`/api/contracts?${qs}`);
}

export function getContract(id: string): Promise<Contract> {
	return api.get<Contract>(`/api/contracts/${id}`);
}

export function createContract(body: ContractCreate): Promise<Contract> {
	return api.post<Contract>('/api/contracts', body);
}

export function updateContract(id: string, body: Partial<ContractCreate>): Promise<Contract> {
	return api.patch<Contract>(`/api/contracts/${id}`, body);
}

export function deleteContract(id: string): Promise<void> {
	return api.delete(`/api/contracts/${id}`);
}

export function uploadContractFile(id: string, file: File): Promise<Contract> {
	return api.upload<Contract>(`/api/contracts/${id}/upload`, file);
}

// Bytes for the attached document. `<iframe>`/`<a download>` can't reach the
// endpoint directly (no Bearer header), so fetch through the auth'd client and
// render via a blob URL. Caller revokes the returned URL.
export function fetchContractFile(fileKey: string): Promise<string> {
	return api.fetchBlob(`/api/contracts/file/${fileKey}`);
}

// --- Lifecycle ---

export function activateContract(id: string): Promise<Contract> {
	return api.post<Contract>(`/api/contracts/${id}/activate`, {});
}

export function terminateContract(id: string): Promise<Contract> {
	return api.post<Contract>(`/api/contracts/${id}/terminate`, {});
}

export function cancelContract(id: string): Promise<Contract> {
	return api.post<Contract>(`/api/contracts/${id}/cancel`, {});
}

export interface RenewContractBody {
	end_date: string;
	total_value?: number | null;
	spend_limit?: number | null;
}

export function renewContract(id: string, body: RenewContractBody): Promise<Contract> {
	return api.post<Contract>(`/api/contracts/${id}/renew`, body);
}

export interface CreatePoBody {
	po_number?: string;
	total?: number | null;
}

export interface CreatedPo {
	id: string;
	po_number: string;
}

export function createPoFromContract(id: string, body: CreatePoBody = {}): Promise<CreatedPo> {
	return api.post<CreatedPo>(`/api/contracts/${id}/create-po`, body);
}
