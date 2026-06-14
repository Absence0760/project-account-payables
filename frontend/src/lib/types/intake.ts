// Types for the procurement Intake surface. Mirrors the JSON returned by the
// `/api/intake` endpoints (backend `IntakeRequestResponse`). Money fields arrive
// as numbers (backend `float(...)`); date/datetime fields are ISO strings.

export type IntakeStatus =
	| 'open'
	| 'in_review'
	| 'approved'
	| 'rejected'
	| 'converted'
	| 'cancelled';

export const INTAKE_STATUSES: IntakeStatus[] = [
	'open',
	'in_review',
	'approved',
	'rejected',
	'converted',
	'cancelled'
];

export const INTAKE_STATUS_LABELS: Record<IntakeStatus, string> = {
	open: 'Open',
	in_review: 'In Review',
	approved: 'Approved',
	rejected: 'Rejected',
	converted: 'Converted',
	cancelled: 'Cancelled'
};

export type IntakeType = 'software' | 'services' | 'hardware' | 'other';

export const INTAKE_TYPES: IntakeType[] = ['software', 'services', 'hardware', 'other'];

export const INTAKE_TYPE_LABELS: Record<IntakeType, string> = {
	software: 'Software',
	services: 'Services',
	hardware: 'Hardware',
	other: 'Other'
};

// Flexible questionnaire payload — free-form key/value answers, shape varies by
// request type. Persisted verbatim as JSONB on the backend.
export type IntakeFormData = Record<string, unknown>;

export interface IntakeRequest {
	id: string;
	request_number: string;
	title: string;
	request_type: string;
	requester_user_id: string;
	description: string | null;
	estimated_amount: number | null;
	currency: string;
	vendor_name: string | null;
	vendor_id: string | null;
	status: string;
	form_data: IntakeFormData | null;
	needed_by: string | null;
	justification: string | null;
	converted_requisition_id: string | null;
	converted_po_id: string | null;
	created_at: string;
	updated_at: string;
}

export interface IntakeListResponse {
	items: IntakeRequest[];
	total: number;
	page: number;
	page_size: number;
}

// Request side. Money goes out as a number — the backend coerces to Decimal.
export interface IntakeCreate {
	request_number?: string | null;
	title: string;
	request_type: string;
	description: string | null;
	estimated_amount: number | null;
	currency: string;
	vendor_name: string | null;
	form_data: IntakeFormData | null;
	needed_by: string | null;
	justification: string | null;
}

export interface IntakeUpdate {
	title?: string;
	request_type?: string;
	description?: string | null;
	estimated_amount?: number | null;
	currency?: string;
	vendor_name?: string | null;
	form_data?: IntakeFormData | null;
	needed_by?: string | null;
	justification?: string | null;
}

export interface IntakeConvertResponse {
	intake: IntakeRequest;
	requisition_id: string;
	requisition_number: string;
	created: boolean;
}

// Per-request-type questionnaire fields shown in the create modal. The labels
// drive a small dynamic form; answers are collected into `form_data`.
export const INTAKE_FORM_FIELDS: Record<IntakeType, { key: string; label: string }[]> = {
	software: [
		{ key: 'seats', label: 'Number of seats' },
		{ key: 'renewal', label: 'Renewal cadence (monthly/annual)' },
		{ key: 'data_residency', label: 'Data residency requirement' }
	],
	services: [
		{ key: 'scope', label: 'Scope of work' },
		{ key: 'duration', label: 'Engagement duration' },
		{ key: 'sow_ref', label: 'SOW reference' }
	],
	hardware: [
		{ key: 'quantity', label: 'Quantity' },
		{ key: 'model', label: 'Model / spec' },
		{ key: 'ship_to', label: 'Ship-to location' }
	],
	other: [{ key: 'details', label: 'Details' }]
};
