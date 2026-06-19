// Types for the Positive Pay / Payment Fraud File surface. Mirrors the JSON
// returned by the `/api/positive-pay` endpoints (the Pydantic contract in
// `backend/app/schemas/positive_pay.py`). Money fields arrive as numbers;
// date/time fields are ISO strings (or null).
//
// PII discipline mirrors the backend: no full account / routing number ever
// crosses this boundary. `account_last4` is the only account detail on a
// response; a `PresentedItem` carries only a check number + amount.

// --- File type ------------------------------------------------------------

export type PositivePayFileType = 'check_issue' | 'ach_authorization';

export const POSITIVE_PAY_FILE_TYPES: PositivePayFileType[] = [
	'check_issue',
	'ach_authorization'
];

export const POSITIVE_PAY_FILE_TYPE_LABELS: Record<PositivePayFileType, string> = {
	check_issue: 'Check issue',
	ach_authorization: 'ACH authorization'
};

// --- File status ----------------------------------------------------------

export type PositivePayStatus = 'generated' | 'returned_processed';

export const POSITIVE_PAY_STATUSES: PositivePayStatus[] = ['generated', 'returned_processed'];

export const POSITIVE_PAY_STATUS_LABELS: Record<PositivePayStatus, string> = {
	generated: 'Generated',
	returned_processed: 'Return processed'
};

// Bank formatter keys the backend ships (`get_positive_pay_formatter` default
// "csv"; unknown falls back to csv). Surfaced as the format picker options.
export const BANK_FORMATS = ['csv', 'fixed_width'] as const;
export type BankFormat = (typeof BANK_FORMATS)[number];

export const BANK_FORMAT_LABELS: Record<BankFormat, string> = {
	csv: 'CSV',
	fixed_width: 'Fixed width'
};

// --- Response shapes ------------------------------------------------------

// The return-processing summary the backend stores under `meta.return_summary`.
export interface ReturnSummary {
	presented_count: number;
	matched_ok: number;
	amount_mismatches: number;
	not_on_file: number;
	exceptions_created: number;
	unmatched: number;
}

// One presented cheque that couldn't be mapped to an invoice (recorded under
// `meta.unmatched_returns` rather than as a fraud Exception).
export interface UnmatchedReturn {
	check_number: string | null;
	amount: string | null;
	classification: string;
}

export interface PositivePayFile {
	id: string;
	file_type: PositivePayFileType;
	bank_format: string;
	status: PositivePayStatus;
	payment_run_id: string | null;
	item_count: number;
	total_amount: number;
	account_last4: string | null;
	file_key: string | null;
	created_at: string;
	updated_at: string | null;
	// Free-form metadata: holds `issued_map`, `return_summary`,
	// `unmatched_returns`. Typed loosely (only the fields the UI reads matter).
	meta?: {
		return_summary?: ReturnSummary;
		unmatched_returns?: UnmatchedReturn[];
		[k: string]: unknown;
	} | null;
}

export interface PositivePayListResponse {
	items: PositivePayFile[];
	total: number;
	page: number;
	page_size: number;
}

// --- Process-return payload + response ------------------------------------

export interface PresentedItemInput {
	check_number?: string | null;
	amount?: number | null;
}

export interface ProcessReturnResponse {
	presented_count: number;
	matched_ok: number;
	amount_mismatches: number;
	not_on_file: number;
	exceptions_created: number;
	unmatched: number;
	file: PositivePayFile;
}
