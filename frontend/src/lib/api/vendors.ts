// Typed helpers for the vendor sanctions-screening + risk endpoints. All
// requests route through the shared `api` client (Bearer + X-Tenant-Slug +
// 401-bounce). Mirrors the pattern of `src/lib/api/contracts.ts` / `tax.ts`.
import { api } from '$lib/api';
import type { MatchingIdsResponse } from '$lib/utils/pagination';
import { triggerDownload } from '$lib/utils/download';
import type {
	Vendor,
	SanctionsCheck,
	ScreeningReviewItem,
	VendorRisk,
	RiskSummaryBucket,
	EnrichmentApplyField,
	VendorEnrichmentResponse,
	VendorEnrichmentApplyResponse,
	VendorConsolidationResponse,
	VendorMergeResponse
} from '$lib/types/vendor';

// --- List / select-all-matching / sort ---

export interface VendorListParams {
	search?: string;
	status?: string;
	source?: string;
	sort?: string;
	order?: 'asc' | 'desc';
	page?: number;
	page_size?: number;
}

function vendorQuery(params: VendorListParams): URLSearchParams {
	const qs = new URLSearchParams();
	if (params.search) qs.set('search', params.search);
	if (params.status) qs.set('status', params.status);
	if (params.source) qs.set('source', params.source);
	if (params.sort) qs.set('sort', params.sort);
	if (params.order) qs.set('order', params.order);
	return qs;
}

/**
 * Every vendor id matching the current list filters — `GET /api/vendors/ids`.
 * Backs "select all N matching" the same way `getExpenseIds` /
 * `GET /api/invoices/ids` do: the header checkbox only ever covers the
 * currently-loaded page, so a bulk action over "select all" would otherwise
 * silently skip every row past it.
 */
export function getVendorIds(params: VendorListParams = {}): Promise<MatchingIdsResponse> {
	const qs = vendorQuery(params);
	const query = qs.toString();
	return api.get<MatchingIdsResponse>(`/api/vendors/ids${query ? `?${query}` : ''}`);
}

// --- Bulk operations ---

export interface BulkSkip {
	id: string;
	reason: string;
}

export interface VendorBulkStatusResponse {
	updated: number;
	skipped: BulkSkip[];
}

/** Bulk verify (`status: 'active'`) / reject over a hand-picked set of
 * vendors — the bulk counterpart of the single-row verify/reject row
 * actions. `vendor.manage`-gated on the backend. */
export function bulkVendorStatus(
	ids: string[],
	status: 'active' | 'rejected'
): Promise<VendorBulkStatusResponse> {
	return api.post<VendorBulkStatusResponse>('/api/vendors/bulk/status', { ids, status });
}

export interface VendorBulkScreenResponse {
	screened: number;
	skipped: BulkSkip[];
}

/** Bulk re-screen against the configured sanctions provider. admin / ap_manager. */
export function bulkScreenVendors(ids: string[]): Promise<VendorBulkScreenResponse> {
	return api.post<VendorBulkScreenResponse>('/api/vendors/bulk/screen', { ids });
}

/** CSV export of a hand-picked set of vendors (name/code/email/phone/status/
 * source/created_at only — never bank details or the raw tax id). */
export async function exportVendorsCsv(ids: string[]): Promise<void> {
	const blob = await api.downloadBlobPost('/api/vendors/bulk/export', { ids });
	triggerDownload(blob, `vendors-export.csv`);
}

// Manual re-screen of a vendor against the sanctions provider. admin / ap_manager.
export function screenVendor(id: string): Promise<Vendor> {
	return api.post<Vendor>(`/api/vendors/${id}/screen`, {});
}

// Sanctions-screening history for a vendor, newest first.
export function getScreeningHistory(id: string): Promise<SanctionsCheck[]> {
	return api.get<SanctionsCheck[]>(`/api/vendors/${id}/screening-history`);
}

// Vendors needing screening review (potential matches / blocked / high risk).
export function getScreeningReviewQueue(): Promise<ScreeningReviewItem[]> {
	return api.get<ScreeningReviewItem[]>('/api/vendors/screening/review-queue');
}

// Block / unblock vendor payments. admin / ap_manager.
export function blockVendor(id: string, reason?: string): Promise<Vendor> {
	return api.post<Vendor>(`/api/vendors/${id}/block`, reason ? { reason } : {});
}

export function unblockVendor(id: string): Promise<Vendor> {
	return api.post<Vendor>(`/api/vendors/${id}/unblock`, {});
}

// Vendor risk detail + recompute. recompute is admin / ap_manager.
export function getVendorRisk(id: string): Promise<VendorRisk> {
	return api.get<VendorRisk>(`/api/vendors/${id}/risk`);
}

export function recomputeVendorRisk(id: string): Promise<VendorRisk> {
	return api.post<VendorRisk>(`/api/vendors/${id}/risk/recompute`, {});
}

// Risk-level distribution across vendors.
export function getRiskSummary(): Promise<RiskSummaryBucket[]> {
	return api.get<RiskSummaryBucket[]>('/api/vendors/risk/summary');
}

// External firmographics enrichment (D&B / Clearbit / mock). Advisory only —
// returns the looked-up firmographics + a per-field suggestion diff; nothing is
// written back. admin / ap_manager / cfo (the backend `_ENRICH_ROLES`).
export function enrichVendor(id: string): Promise<VendorEnrichmentResponse> {
	return api.post<VendorEnrichmentResponse>(`/api/enrichment/vendors/${id}/enrich`, {});
}

// Apply a steward-selected subset of enrichment suggestions onto the vendor.
// Non-destructive (only the named fields change) + idempotent + audited.
// `tax_id` is never applyable here — only `name` / `address` / `website`.
export function applyVendorEnrichment(
	id: string,
	fields: EnrichmentApplyField[]
): Promise<VendorEnrichmentApplyResponse> {
	return api.post<VendorEnrichmentApplyResponse>(`/api/enrichment/vendors/${id}/apply`, {
		fields
	});
}

// Advisory clusters of likely-duplicate / similar vendors (by tax_id / code /
// fuzzy name), each with a deterministic canonical pick (tax_id masked).
// Read-only, compute-on-read, no mutation. admin / ap_manager / cfo.
export function getVendorConsolidationSuggestions(): Promise<VendorConsolidationResponse> {
	return api.get<VendorConsolidationResponse>('/api/enrichment/vendors/consolidation-suggestions');
}

// Execute a consolidation: fold `duplicateVendorIds` into `canonicalVendorId`.
// Reassigns every vendor FK to the canonical vendor + soft-retires the
// duplicates (status=inactive); idempotent + audited (`vendor.merged`). Gated
// on the granular `vendor.manage` permission. Refuses self-merge / cross-entity
// / unknown vendor (surfaced as the backend's 4xx detail).
export function mergeVendorConsolidation(
	canonicalVendorId: string,
	duplicateVendorIds: string[]
): Promise<VendorMergeResponse> {
	return api.post<VendorMergeResponse>('/api/enrichment/vendors/consolidation/merge', {
		canonical_vendor_id: canonicalVendorId,
		duplicate_vendor_ids: duplicateVendorIds
	});
}
