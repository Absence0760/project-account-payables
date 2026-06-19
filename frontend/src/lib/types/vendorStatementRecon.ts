// Types for the Vendor Statement Reconciliation surface. Mirrors the JSON
// returned by the `/api/vendor-statements` endpoints (the Pydantic contract in
// `backend/app/schemas/vendor_statement_recon.py`). Money fields arrive as
// numbers (or null); date/time fields are ISO strings (or null).

// --- Run status -----------------------------------------------------------

export type ReconStatus = 'open' | 'resolved';

export const RECON_STATUSES: ReconStatus[] = ['open', 'resolved'];

// StatusBadge-style label map (Title Case) for the run status pill.
export const RECON_STATUS_LABELS: Record<ReconStatus, string> = {
	open: 'Open',
	resolved: 'Resolved'
};

// --- Line classification --------------------------------------------------

export type ReconClassification =
	| 'matched'
	| 'amount_mismatch'
	| 'missing_on_our_side'
	| 'missing_on_their_side';

export const RECON_CLASSIFICATION_LABELS: Record<ReconClassification, string> = {
	matched: 'Matched',
	amount_mismatch: 'Amount mismatch',
	missing_on_our_side: 'Missing (our side)',
	missing_on_their_side: 'Missing (their side)'
};

// --- Line resolution ------------------------------------------------------

export type ReconResolutionStatus = 'unresolved' | 'resolved' | 'ignored';

export const RECON_RESOLUTION_LABELS: Record<ReconResolutionStatus, string> = {
	unresolved: 'Unresolved',
	resolved: 'Resolved',
	ignored: 'Ignored'
};

// --- Response shapes ------------------------------------------------------

export interface ReconLine {
	id: string;
	classification: ReconClassification;
	resolution_status: ReconResolutionStatus;
	statement_invoice_number: string | null;
	statement_date: string | null;
	statement_amount: number | null;
	statement_status: string | null;
	matched_invoice_id: string | null;
	matched_invoice_number: string | null;
	ledger_amount: number | null;
	amount_difference: number | null;
	match_method: string | null;
	resolution_note: string | null;
	resolved_at: string | null;
}

export interface ReconSummary {
	line_count: number;
	matched_count: number;
	amount_mismatch_count: number;
	missing_our_side_count: number;
	missing_their_side_count: number;
	statement_total: number | null;
	ledger_total: number | null;
}

export interface Reconciliation {
	id: string;
	vendor_id: string | null;
	vendor_name: string | null;
	statement_date: string;
	statement_reference: string | null;
	currency: string;
	source_format: string;
	file_key: string | null;
	status: ReconStatus;
	notes: string | null;
	summary: ReconSummary;
	created_at: string;
	updated_at: string | null;
	// Lines are included on the detail response only (the list omits them).
	lines?: ReconLine[] | null;
}

export interface ReconciliationListResponse {
	items: Reconciliation[];
	total: number;
	page: number;
	page_size: number;
}

// --- Create payloads ------------------------------------------------------

// One supplier-statement line for the manual / pasted-lines intake path.
export interface StatementLineInput {
	invoice_number?: string | null;
	invoice_date?: string | null;
	amount?: number | null;
	status?: string | null;
}

// POST /api/vendor-statements body (JSON, pasted-lines path).
export interface ReconciliationCreate {
	vendor_id: string;
	statement_date: string;
	statement_reference?: string | null;
	currency?: string;
	notes?: string | null;
	lines: StatementLineInput[];
}

// --- Line resolve ---------------------------------------------------------

export interface LineResolveRequest {
	resolution_status: ReconResolutionStatus;
	resolution_note?: string | null;
}

// --- Close-readiness ------------------------------------------------------

export interface CloseReadinessVendor {
	vendor_id: string | null;
	vendor_name: string | null;
	reconciliation_id: string;
	statement_date: string;
	currency: string;
	unreconciled_amount: number;
	missing_our_side_count: number;
	amount_mismatch_count: number;
}

export interface CloseReadinessResponse {
	materiality_threshold: number;
	blocking_vendors: CloseReadinessVendor[];
	is_close_ready: boolean;
}
