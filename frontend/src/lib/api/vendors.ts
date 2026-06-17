// Typed helpers for the vendor sanctions-screening + risk endpoints. All
// requests route through the shared `api` client (Bearer + X-Tenant-Slug +
// 401-bounce). Mirrors the pattern of `src/lib/api/contracts.ts` / `tax.ts`.
import { api } from '$lib/api';
import type {
	Vendor,
	SanctionsCheck,
	ScreeningReviewItem,
	VendorRisk,
	RiskSummaryBucket
} from '$lib/types/vendor';

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
