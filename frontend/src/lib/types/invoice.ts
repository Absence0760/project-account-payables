export type InvoiceStatus =
	| 'new'
	| 'pending'
	| 'ready_for_review'
	| 'approved'
	| 'rejected'
	| 'sending_to_erp'
	| 'sent_to_erp'
	| 'posted_in_erp'
	| 'payment_scheduled'
	| 'paid'
	| 'done'
	| 'failed';

export const INVOICE_STATUSES: InvoiceStatus[] = [
	'new',
	'pending',
	'ready_for_review',
	'approved',
	'rejected',
	'sending_to_erp',
	'sent_to_erp',
	'posted_in_erp',
	'payment_scheduled',
	'paid',
	'done',
	'failed'
];

export const STATUS_LABELS: Record<InvoiceStatus, string> = {
	new: 'New',
	pending: 'Extracting',
	ready_for_review: 'Ready for Review',
	approved: 'Approved',
	rejected: 'Rejected',
	sending_to_erp: 'Sending to ERP',
	sent_to_erp: 'Sent to ERP',
	posted_in_erp: 'Posted in ERP',
	payment_scheduled: 'Payment Scheduled',
	paid: 'Paid',
	done: 'Done',
	failed: 'Failed'
};

/** Statuses managed by the system — users should not select or bulk-act on these. */
export const SYSTEM_MANAGED_STATUSES: Set<InvoiceStatus> = new Set([
	'pending',
	'sending_to_erp',
	'sent_to_erp',
	'posted_in_erp',
	'payment_scheduled',
	'paid',
	'done'
]);

/** Valid manual status transitions per source status. */
export const VALID_TRANSITIONS: Record<InvoiceStatus, InvoiceStatus[]> = {
	new: ['ready_for_review', 'rejected', 'done'],
	pending: [],
	ready_for_review: ['approved', 'rejected'],
	approved: ['rejected', 'done'],
	rejected: ['new', 'ready_for_review'],
	sending_to_erp: [],
	sent_to_erp: [],
	posted_in_erp: [],
	payment_scheduled: [],
	paid: [],
	done: [],
	failed: ['new', 'pending'],
};

/**
 * Given a set of source statuses, return the status targets valid for ALL of them.
 */
export function commonTransitions(statuses: InvoiceStatus[]): InvoiceStatus[] {
	if (statuses.length === 0) return [];
	const sets = statuses.map((s) => new Set(VALID_TRANSITIONS[s]));
	const first = sets[0];
	return [...first].filter((t) => sets.every((s) => s.has(t)));
}

export interface AdvancedSearchFilters {
	vendor: string;
	invoice_number: string;
	po_number: string;
	description: string;
	amount_min: string;
	amount_max: string;
	due_date_from: string;
	due_date_to: string;
	statuses: InvoiceStatus[];
}

export const EMPTY_ADVANCED_FILTERS: AdvancedSearchFilters = {
	vendor: '',
	invoice_number: '',
	po_number: '',
	description: '',
	amount_min: '',
	amount_max: '',
	due_date_from: '',
	due_date_to: '',
	statuses: [],
};

export interface InvoiceWarning {
	type: string;
	severity: 'error' | 'warning' | 'info';
	message: string;
}

export interface PoMatch {
	status: 'no_po' | 'matched' | 'mismatch' | 'partial';
	match_type: 'none' | '2-way' | '3-way' | '4-way';
	po_id: string | null;
	po_number: string | null;
	po_total: number | null;
	gr_id: string | null;
	amount_variance: number;
	amount_variance_pct: number;
	within_tolerance: boolean;
	inspection_id: string | null;
	inspection_result: 'pass' | 'fail' | 'partial' | null;
	inspection_accepted_quantity: number | null;
	inspection_required: boolean;
	issues: string[];
	details: Record<string, unknown>;
}

export interface Invoice {
	id: string;
	correlation_id: string;
	vendor: string;
	invoice_number: string;
	amount: number;
	currency: string;
	invoice_date: string | null;
	received_date: string | null;
	due_date: string | null;
	payment_terms: string | null;
	status: InvoiceStatus;
	po_number: string;
	subtotal: number | null;
	tax_amount: number | null;
	discount_amount: number | null;
	shipping_amount: number | null;
	remit_to_address: string | null;
	bill_to_address: string | null;
	vendor_address: string | null;
	vendor_tax_id: string | null;
	ship_to_address: string | null;
	tax_rate: number | null;
	payment_method: string | null;
	reference_number: string | null;
	description: string;
	notes: string | null;
	approval_date: string | null;
	approved_by: string | null;
	rejected_by: string | null;
	assigned_to_id: string | null;
	assigned_to: string | null;
	gl_account: string | null;
	cost_center: string | null;
	contract_id: string | null;
	created_at: string;
	file_url: string | null;
	warnings: InvoiceWarning[] | null;
	po_match: PoMatch | null;
	priors_summary: { cache: number; rag: number } | null;
}

/**
 * One-paragraph audit-log summary shown at the top of the invoice detail
 * modal. Mirrors the backend `AuditSummaryResponse`. Fetched lazily from
 * `GET /api/invoices/{id}/summary`.
 */
export interface AuditSummary {
	text: string;
	confidence_context: string | null;
	generated_at: string | null;
	stale: boolean;
}
