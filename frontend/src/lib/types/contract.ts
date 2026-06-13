// Types for the Contracts surface. Mirrors the JSON returned by the
// `/api/contracts` endpoints (backend `Contract.to_dict()`). Money fields
// arrive as numbers (or null); date fields are ISO date strings (or null).

export type ContractType =
	| 'purchase'
	| 'service'
	| 'subscription'
	| 'lease'
	| 'sla'
	| 'msa'
	| 'sow'
	| 'other';

export const CONTRACT_TYPES: ContractType[] = [
	'purchase',
	'service',
	'subscription',
	'lease',
	'sla',
	'msa',
	'sow',
	'other'
];

export const CONTRACT_TYPE_LABELS: Record<ContractType, string> = {
	purchase: 'Purchase',
	service: 'Service',
	subscription: 'Subscription',
	lease: 'Lease',
	sla: 'SLA',
	msa: 'MSA',
	sow: 'SOW',
	other: 'Other'
};

export type ContractStatus =
	| 'draft'
	| 'active'
	| 'expired'
	| 'terminated'
	| 'cancelled';

export const CONTRACT_STATUSES: ContractStatus[] = [
	'draft',
	'active',
	'expired',
	'terminated',
	'cancelled'
];

// StatusBadge-style label map (Title Case) for the contract status pill.
export const STATUS_LABELS: Record<ContractStatus, string> = {
	draft: 'Draft',
	active: 'Active',
	expired: 'Expired',
	terminated: 'Terminated',
	cancelled: 'Cancelled'
};

export interface ContractLineItem {
	id: string;
	line_number: number | null;
	item_code: string | null;
	description: string | null;
	quantity: number | null;
	unit_price: number | null;
	total: number | null;
	gl_account: string | null;
}

export interface ContractSpend {
	invoiced_total: number;
	invoice_count: number;
	spend_limit: number | null;
	remaining: number | null;
	over_limit: boolean;
}

export interface Contract {
	id: string;
	contract_number: string;
	title: string | null;
	description: string | null;
	contract_type: ContractType;
	status: ContractStatus;
	vendor_id: string;
	vendor_name: string | null;
	currency: string;
	total_value: number | null;
	spend_limit: number | null;
	not_to_exceed: boolean;
	start_date: string | null;
	end_date: string | null;
	signed_date: string | null;
	auto_renew: boolean;
	renewal_term_months: number | null;
	renewal_notice_days: number;
	renewal_alert_sent_at: string | null;
	payment_terms: string | null;
	owner_user_id: string | null;
	file_url: string | null;
	file_key: string | null;
	terms: Record<string, unknown> | null;
	line_items: ContractLineItem[];
	spend: ContractSpend | null;
	created_at: string;
	updated_at: string;
}

// Writable line-item shape for ContractCreate / line-item edits. All fields
// optional — the backend fills line_number when omitted.
export interface ContractLineItemInput {
	line_number?: number;
	item_code?: string | null;
	description?: string | null;
	quantity?: number | null;
	unit_price?: number | null;
	total?: number | null;
	gl_account?: string | null;
}

// POST /api/contracts body. vendor_id + contract_number are required.
export interface ContractCreate {
	contract_number: string;
	vendor_id: string;
	title?: string | null;
	description?: string | null;
	contract_type?: ContractType;
	currency?: string;
	total_value?: number | null;
	spend_limit?: number | null;
	not_to_exceed?: boolean;
	start_date?: string | null;
	end_date?: string | null;
	signed_date?: string | null;
	auto_renew?: boolean;
	renewal_term_months?: number | null;
	renewal_notice_days?: number;
	payment_terms?: string | null;
	owner_user_id?: string | null;
	line_items?: ContractLineItemInput[];
}

export interface ContractListResponse {
	items: Contract[];
	total: number;
	page: number;
	page_size: number;
}
