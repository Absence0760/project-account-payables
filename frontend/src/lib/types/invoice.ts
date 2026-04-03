export type InvoiceStatus = 'new' | 'pending' | 'ready_for_review' | 'failed' | 'sent_to_erp';

export const INVOICE_STATUSES: InvoiceStatus[] = [
	'new',
	'pending',
	'ready_for_review',
	'failed',
	'sent_to_erp'
];

export const STATUS_LABELS: Record<InvoiceStatus, string> = {
	new: 'New',
	pending: 'Pending',
	ready_for_review: 'Ready for Review',
	failed: 'Failed',
	sent_to_erp: 'Sent to ERP'
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
	vendor: string;
	invoice_number: string;
	amount: number;
	currency: string;
	due_date: string;
	status: InvoiceStatus;
	po_number: string;
	description: string;
	created_at: string;
	file_url: string | null;
}
