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

// ---------------------------------------------------------------------------
// External vendor enrichment (firmographics from D&B / Clearbit / mock).
// Mirrors the backend `VendorEnrichmentResponse` / `VendorEnrichmentApply*`.
// Advisory / suggestion-only: the enrich call NEVER writes back — a steward
// reviews the per-field diff and explicitly applies a chosen subset. `tax_id`
// is intentionally NOT applyable here (only `name` / `address` / `website`);
// a tax-id change goes through the bank/tax change-request gate.
// ---------------------------------------------------------------------------

// Vendor columns the enrichment apply endpoint can write. Kept in lock-step
// with the backend `APPLYABLE_FIELDS` tuple (name / address / website).
export type EnrichableField = 'name' | 'address' | 'website';

// Normalised firmographics from the provider (raw tax_id is never echoed —
// only `tax_id_masked` of the form `***<last4>`).
export interface VendorFirmographics {
	provider: string;
	matched: boolean;
	legal_name: string | null;
	address: string | null;
	country: string | null;
	industry: string | null;
	sic_code: string | null;
	naics_code: string | null;
	employee_count: number | null;
	annual_revenue: string | null;
	website: string | null;
	duns_number: string | null;
	year_founded: number | null;
	tax_id_masked: string | null;
	confidence: number | null;
	extra: Record<string, unknown>;
}

// One advisory change a steward may choose to apply (current → suggested).
export interface EnrichmentFieldSuggestion {
	field: EnrichableField;
	current_value: string | null;
	suggested_value: string | null;
}

export interface VendorEnrichmentResponse {
	vendor_id: string;
	vendor_name: string;
	firmographics: VendorFirmographics;
	suggestions: EnrichmentFieldSuggestion[];
	generated_at: string;
}

// The steward's selection of fields to write onto the vendor.
export interface EnrichmentApplyField {
	field: EnrichableField;
	value: string | null;
}

export interface VendorEnrichmentApplyResponse {
	vendor_id: string;
	// Field-level before/after diff actually written (empty when a no-op).
	applied: Record<string, { old: string | null; new: string | null }>;
	vendor: Vendor;
	applied_at: string;
}

// Human labels for the applyable fields (used in the enrich diff UI).
export const ENRICHABLE_FIELD_LABELS: Record<EnrichableField, string> = {
	name: 'Legal name',
	address: 'Address',
	website: 'Website'
};

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
