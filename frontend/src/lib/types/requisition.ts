// Types for the Procurement / Requisitions surface. Mirrors the JSON returned
// by the `/api/requisitions` endpoints (backend `RequisitionResponse` /
// `RequisitionLineItemResponse`). Money fields arrive as numbers (backend
// `float(...)`); date/datetime fields are ISO strings.

export type RequisitionStatus =
	| 'draft'
	| 'submitted'
	| 'pending_approval'
	| 'approved'
	| 'rejected'
	| 'converted'
	| 'cancelled';

export const REQUISITION_STATUSES: RequisitionStatus[] = [
	'draft',
	'submitted',
	'pending_approval',
	'approved',
	'rejected',
	'converted',
	'cancelled'
];

export const REQUISITION_STATUS_LABELS: Record<RequisitionStatus, string> = {
	draft: 'Draft',
	submitted: 'Submitted',
	pending_approval: 'Pending Approval',
	approved: 'Approved',
	rejected: 'Rejected',
	converted: 'Converted',
	cancelled: 'Cancelled'
};

export interface RequisitionLineItem {
	id: string;
	line_number: number | null;
	catalog_item_id: string | null;
	item_code: string | null;
	description: string | null;
	quantity: number | null;
	unit_price: number | null;
	total: number | null;
	gl_account_id: string | null;
	uom: string | null;
}

export interface Requisition {
	id: string;
	requisition_number: string;
	title: string | null;
	requester_user_id: string;
	department: string | null;
	status: string;
	needed_by: string | null;
	justification: string | null;
	vendor_id: string | null;
	contract_id: string | null;
	budget_id: string | null;
	total: number;
	currency: string;
	notes: string | null;
	submitted_at: string | null;
	approved_at: string | null;
	approved_by: string | null;
	rejection_reason: string | null;
	converted_po_id: string | null;
	line_items: RequisitionLineItem[];
	created_at: string;
	updated_at: string;
}

export interface RequisitionListResponse {
	items: Requisition[];
	total: number;
	page: number;
	page_size: number;
}

// `POST /api/requisitions/{id}/convert-to-po`. `created` is false on the
// idempotent replay path (requisition already converted).
export interface ConvertToPoResult {
	requisition_id: string;
	po_id: string;
	po_number: string;
	total: number;
	created: boolean;
}

// --- Request (create / update) payload shapes ---

export interface RequisitionLineItemInput {
	line_number?: number | null;
	catalog_item_id?: string | null;
	item_code?: string | null;
	description?: string | null;
	quantity?: number | null;
	unit_price?: number | null;
	gl_account_id?: string | null;
	uom?: string | null;
}

export interface RequisitionCreate {
	requisition_number: string;
	title: string | null;
	department: string | null;
	needed_by: string | null;
	justification: string | null;
	vendor_id?: string | null;
	contract_id?: string | null;
	budget_id?: string | null;
	currency: string;
	notes: string | null;
	line_items: RequisitionLineItemInput[];
}

export interface RequisitionUpdate {
	requisition_number?: string;
	title?: string | null;
	department?: string | null;
	needed_by?: string | null;
	justification?: string | null;
	vendor_id?: string | null;
	contract_id?: string | null;
	budget_id?: string | null;
	currency?: string;
	notes?: string | null;
	line_items?: RequisitionLineItemInput[];
}
