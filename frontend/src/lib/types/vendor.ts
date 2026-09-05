// Vendor + sanctions-screening / risk types. These mirror the backend
// `VendorResponse`, `SanctionsCheckResponse`, `ScreeningReviewItem`, and the
// `/risk` payloads. The vendor list/detail UI and the typed helpers in
// `$lib/api/vendors.ts` share them.

export type ScreeningStatus = 'unscreened' | 'clear' | 'review' | 'match';
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical' | 'unknown';

export interface VendorMailingAddress {
	street: string | null;
	city: string | null;
	state: string | null;
	postal: string | null;
	country: string | null;
}

export interface VendorBankDetails {
	counterparty_id: string | null;
	account_last4: string | null;
	// Last 4 of the ACH / domestic routing number (`bank_details.routing_number`
	// — the original generic key, which every stored row already means ACH by).
	routing_last4: string | null;
	// Last 4 of the SEPARATE Fedwire ABA larger US banks publish for incoming
	// wires (`bank_details.wire_routing_number`). Optional: when it is absent
	// the wire rail falls back to the ACH number, which is what a bank with a
	// single ABA means. Backend: `resolve_routing_number` in
	// `services/payment_adapters/base.py`.
	wire_routing_last4?: string | null;
	bank_name: string | null;
	// ISO 3166-1 alpha-2 destination-bank country. Drives the routing-number
	// vs. sort-code label/validation switch on the AP bank-change modal (a
	// UK vendor uses a sort code, not a US ABA routing number) — mirrors the
	// backend `schemas.vendor.VendorBankDetails.country` field.
	country?: string | null;
	// Where a printed check to this vendor gets mailed — required by the
	// `checkeeper` check-printing payment rail (see `services/payment_adapters/
	// checkeeper.py`); without it that rail refuses with
	// `checkeeper_missing_mailing_address`.
	mailing_address?: VendorMailingAddress | null;
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
	// PII-free taxonomy of WHAT was hit: 'sanctions' | 'pep' | 'adverse_media'
	// | 'high_risk_country'. Empty for a clear screen and for rows written
	// before the taxonomy shipped.
	categories: string[];
	adverse_media: boolean;
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
	latest_categories: string[];
	adverse_media: boolean;
}

/**
 * `GET /api/vendors/counts` — whole-set tallies over the entity-scoped,
 * search/source-filtered vendor population. PII-free (counts only).
 *
 * `payments_blocked` is a SECOND tally over the same population, not a slice
 * of `by_status`: a payment block is an orthogonal axis
 * (`POST /api/vendors/{id}/block` sets `payments_blocked` and never touches
 * `status` or `screening_status`), so it may overlap any bucket and must never
 * be added to `total`.
 *
 * It exists because `/vendors/screening`'s "Payments blocked" KPI used to be
 * counted off the screening REVIEW QUEUE — `screening_status IN
 * ('match','review')` — which structurally cannot see a vendor AP blocked
 * while screening-clear. A tally has to come from a query that asks the
 * tally's own question.
 */
export interface VendorStatusCounts {
	total: number;
	by_status: Record<string, number>;
	payments_blocked: number;
	// Whole-set tally of `Vendor.screening_status`, on the same aggregate pass
	// as `by_status` / `payments_blocked`. Drives the `/vendors/screening` KPI
	// row: those figures used to be derived by filtering the LOADED review
	// queue, which stopped being whole-set the moment that queue paginated.
	by_screening_status: Record<string, number>;
}

// `GET /api/vendors/screening/review-queue` — the canonical paginated envelope.
export interface ScreeningReviewQueueResponse {
	items: ScreeningReviewItem[];
	total: number;
	page: number;
	page_size: number;
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

// ---------------------------------------------------------------------------
// Vendor consolidation (duplicate / similar vendor clusters + merge).
// Mirrors the backend `VendorConsolidationResponse` / `VendorMerge*`. The
// suggestions endpoint is advisory (clusters by tax_id / code / fuzzy name,
// deterministic canonical pick, tax_id masked); the merge endpoint executes
// the fold of duplicates into the canonical vendor (gated `vendor.manage`).
// ---------------------------------------------------------------------------

// One member of a consolidation cluster (the canonical pick has is_canonical).
export interface VendorClusterMember {
	vendor_id: string;
	name: string;
	code: string | null;
	tax_id_masked: string | null; // ***6789 — never the full tax id
	status: string | null;
	invoice_count: number;
	is_canonical: boolean;
}

export interface VendorCluster {
	cluster_id: number;
	members: VendorClusterMember[];
	canonical_vendor_id: string;
	score: string; // 0..1 strongest pairwise evidence, string-Decimal
	reasons: string[];
}

export interface VendorConsolidationResponse {
	clusters: VendorCluster[];
	vendor_count: number;
	cluster_count: number;
	truncated: boolean; // tenant exceeded the bound, or clusters were capped
	generated_at: string;
}

// The steward's explicit merge: fold `duplicate_vendor_ids` into the canonical.
export interface VendorMergeRequest {
	canonical_vendor_id: string;
	duplicate_vendor_ids: string[];
}

export interface VendorMergeResponse {
	canonical_vendor_id: string;
	duplicate_vendor_ids: string[];
	// Per-table reassigned row counts (PII-free — table name → rows moved).
	reassigned: Record<string, number>;
	total_reassigned: number;
	// Duplicate ids THIS call flipped active → inactive (empty on idempotent re-run).
	deactivated_vendor_ids: string[];
	merged_at: string;
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

// The screening-hit taxonomy (`SanctionsCheck.categories`). The backend's
// vocabulary is fixed but open-ended — a future provider may report a label we
// have no wording for, so `formatScreeningCategories` falls back to a
// de-underscored version of the raw label rather than dropping it.
export const SCREENING_CATEGORY_LABELS: Record<string, string> = {
	sanctions: 'Sanctions list',
	pep: 'Politically exposed person',
	adverse_media: 'Negative news',
	high_risk_country: 'High-risk jurisdiction'
};

export function formatScreeningCategories(categories: string[] | null | undefined): string {
	if (!categories?.length) return '—';
	return categories.map((c) => SCREENING_CATEGORY_LABELS[c] ?? c.replace(/_/g, ' ')).join(', ');
}

// ---------------------------------------------------------------------------
// Vendor change requests — the dual-control (BEC / bank-redirect) gate.
//
// A bank-detail or tax-ID change NEVER applies inline: `POST /api/vendors`,
// `PATCH /api/vendors/{id}`, `POST /api/vendors/{id}/bank-change` and the
// supplier portal all STAGE a `VendorChangeRequest`, and a SECOND user holding
// `vendor.bank_change.approve` applies it. Mirrors the backend
// `VendorChangeRequestResponse`.
//
// `proposed_value` is MASKED on the queue list (last-4 only) and revealed on
// the per-vendor detail endpoint — so the field is deliberately loose here:
// the same key carries `{account_last4, bank_name}` in one view and the full
// `{bank_details: {...}}` in the other.
// ---------------------------------------------------------------------------

export type VendorChangeRequestStatus = 'pending' | 'approved' | 'rejected';

/** The two staged change kinds the backend knows how to apply. */
export type VendorChangeType = 'bank_details' | 'tax_id';

export interface VendorChangeRequest {
	id: string;
	vendor_id: string;
	vendor_name: string | null;
	change_type: string;
	status: string;
	/** Masked on the queue list; full value on `GET /api/vendors/{id}/change-requests`. */
	proposed_value: Record<string, unknown> | null;
	// Exactly one requester is set: the portal VendorUser, or the AP User.
	requested_by_vendor_user_id: string | null;
	requested_by_user_id: string | null;
	reviewed_by_user_id: string | null;
	reviewed_at: string | null;
	review_note: string | null;
	created_at: string;
}

/** `GET /api/vendors/change-requests` — the paginated queue envelope. */
export interface VendorChangeRequestPage {
	items: VendorChangeRequest[];
	total: number;
	page: number;
	page_size: number;
}

/**
 * `GET /api/vendors/change-requests/counts` — whole-set, search-independent
 * tallies for the status chips. PII-free (counts only).
 *
 * Separate from the queue's own `total`, which counts only the ACTIVE filter's
 * result set: the Pending badge has to be the whole set or a queue with 25
 * unreviewed bank changes reads as 20 the moment the first page caps. Same
 * reason `GET /api/vendors/counts` exists for the vendor status chips.
 *
 * `pending` is a convenience duplicate of `by_status.pending`; read whichever,
 * they are the same number.
 */
export interface VendorChangeRequestCounts {
	total: number;
	pending: number;
	by_status: Partial<Record<VendorChangeRequestStatus, number>>;
}

/**
 * One-line summary of a MASKED proposed value, for the queue list.
 *
 * Renders nothing but the backend's own mask: the list payload carries
 * `account_last4` / `tax_id_last4` and never a full account or tax number, so
 * this helper must not invent one. Returns `null` when the change type is
 * unknown or the payload is empty, so the caller renders its own em-dash
 * rather than a blank cell.
 */
export function maskedProposalSummary(
	changeType: string,
	proposed: Record<string, unknown> | null | undefined
): string | null {
	if (!proposed) return null;
	if (changeType === 'bank_details') {
		const bankName = typeof proposed.bank_name === 'string' ? proposed.bank_name : null;
		const last4 = typeof proposed.account_last4 === 'string' ? proposed.account_last4 : null;
		return [bankName, last4 ? `••••${last4}` : null].filter(Boolean).join(' · ') || null;
	}
	if (changeType === 'tax_id') {
		const last4 = typeof proposed.tax_id_last4 === 'string' ? proposed.tax_id_last4 : null;
		return last4 ? `••••${last4}` : null;
	}
	return null;
}

/**
 * The REVEALED proposed value flattened to `{field, value}` rows for the
 * review dialog — what an approver reads back to the supplier on a callback
 * before signing off.
 *
 * Returns `null` whenever it cannot represent the payload **without losing
 * anything** (an unknown change type, a nested object, a non-primitive). The
 * caller then falls back to rendering the raw JSON: on a control whose whole
 * job is "look at the new account number", a prettier view that silently drops
 * a field would be worse than an ugly one that shows all of them.
 */
export function revealedProposalFields(
	changeType: string,
	proposed: Record<string, unknown> | null | undefined
): { field: string; value: string }[] | null {
	if (!proposed) return null;
	const inner =
		changeType === 'bank_details'
			? proposed.bank_details
			: changeType === 'tax_id'
				? proposed
				: null;
	if (!inner || typeof inner !== 'object' || Array.isArray(inner)) return null;
	const rows: { field: string; value: string }[] = [];
	for (const [key, value] of Object.entries(inner as Record<string, unknown>)) {
		if (value === null || value === undefined) continue;
		if (typeof value === 'object') return null; // nested — fall back to JSON
		rows.push({ field: key.replace(/_/g, ' '), value: String(value) });
	}
	return rows.length ? rows : null;
}
