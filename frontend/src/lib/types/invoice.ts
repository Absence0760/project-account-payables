export type InvoiceStatus =
	| 'new'
	| 'pending'
	| 'ready_for_review'
	| 'approved'
	| 'rejected'
	| 'sending_to_erp'
	| 'sent_to_erp'
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
	'done',
	'failed'
];

export const STATUS_LABELS: Record<InvoiceStatus, string> = {
	new: 'New',
	pending: 'Pending',
	ready_for_review: 'Ready for Review',
	approved: 'Approved',
	rejected: 'Rejected',
	sending_to_erp: 'Sending to ERP',
	sent_to_erp: 'Sent to ERP',
	done: 'Done',
	failed: 'Failed'
};

export interface AdvancedSearchFilters {
	vendor: string;
	invoice_number: string;
	po_number: string;
	description: string;
	amount_min: string;
	amount_max: string;
	due_date_from: string;
	due_date_to: string;
	statuses: string[];
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
	description: string;
	notes: string | null;
	approval_date: string | null;
	approved_by: string | null;
	gl_account: string | null;
	cost_center: string | null;
	created_at: string;
	file_url: string | null;
}
