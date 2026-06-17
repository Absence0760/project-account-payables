// Vendor + sanctions-screening / risk types. These mirror the backend
// `VendorResponse`, `SanctionsCheckResponse`, `ScreeningReviewItem`, and the
// `/risk` payloads. The vendor list/detail UI and the typed helpers in
// `$lib/api/vendors.ts` share them.

export type ScreeningStatus = 'unscreened' | 'clear' | 'review' | 'match';
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical' | 'unknown';

export interface VendorBankDetails {
	counterparty_id: string | null;
	account_last4: string | null;
	routing_last4: string | null;
	bank_name: string | null;
}

export interface Vendor {
	id: string;
	name: string;
	code: string | null;
	email: string | null;
	phone: string | null;
	address: string | null;
	tax_id: string | null;
	payment_terms: string | null;
	accepts_virtual_cards: boolean;
	status: string;
	source: string;
	verified_by: string | null;
	erp_vendor_id: string | null;
	erp_synced_at: string | null;
	invoice_count: number;
	created_at: string;
	bank_details: VendorBankDetails | null;
	// Sanctions screening + vendor-risk fields (Sanctions & Vendor Risk Screening).
	screening_status: ScreeningStatus;
	last_screened_at: string | null;
	payments_blocked: boolean;
	payments_blocked_reason: string | null;
	risk_score: string | null;
	risk_level: RiskLevel;
}

// One row of a vendor's sanctions-screening history (newest first).
export interface SanctionsCheck {
	id: string;
	vendor_id: string;
	provider: string;
	check_type: string;
	result: string;
	risk_score: string | null;
	matched_list: string | null;
	checked_at: string;
}

// A vendor surfaced on the screening review queue.
export interface ScreeningReviewItem {
	vendor_id: string;
	vendor_name: string;
	screening_status: ScreeningStatus;
	last_screened_at: string | null;
	payments_blocked: boolean;
	risk_level: RiskLevel;
	risk_score: string | null;
	latest_matched_list: string | null;
	latest_provider: string | null;
}

// Vendor risk detail (GET /risk, POST /risk/recompute).
export interface VendorRisk {
	vendor_id: string;
	risk_score: string | null;
	risk_level: RiskLevel;
	risk_factors: Record<string, unknown> | null;
	risk_scored_at: string | null;
}

// One bucket of GET /risk/summary.
export interface RiskSummaryBucket {
	risk_level: RiskLevel;
	count: number;
}

export const SCREENING_STATUS_LABELS: Record<ScreeningStatus, string> = {
	unscreened: 'Unscreened',
	clear: 'Clear',
	review: 'Review',
	match: 'Match'
};

export const RISK_LEVEL_LABELS: Record<RiskLevel, string> = {
	low: 'Low',
	medium: 'Medium',
	high: 'High',
	critical: 'Critical',
	unknown: 'Unknown'
};
