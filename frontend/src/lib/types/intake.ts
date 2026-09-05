// Types for the procurement Intake surface. Mirrors the JSON returned by the
// `/api/intake` endpoints (backend `IntakeRequestResponse`). Date/datetime
// fields are ISO strings.
//
// **Money is `MoneyAmount` on the way in, `MoneyString` on the way out.** The
// backend serialises `estimated_amount` as a JSON number (`float(...)`), so
// `MoneyAmount` is the honest read type — and it makes `a - b` / `Math.max()`
// on the figure a type error rather than a convention. Outbound it is the exact
// decimal string the user typed (`utils/moneyInput.ts`), because a fractional
// JSON number is already a float by the time pydantic sees it. The `total` on
// the list/summary envelopes stays `number`: it is a ROW COUNT, not money.
// See `frontend/CLAUDE.md` § Money formatting.

import type { BadgeTone } from '$lib/components/ui/Badge.svelte';
import type { MoneyAmount, MoneyString } from '$lib/utils/money';

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

// Badge tone per status, so the list page and the modal can't tint the same
// status two different shades — which is exactly what they did (.12 alpha on
// the list, .15 in the modal, and `open` already token-based in only one).
export const INTAKE_STATUS_TONES: Record<IntakeStatus, BadgeTone> = {
	open: 'accent',
	in_review: 'warning',
	approved: 'success',
	rejected: 'danger',
	// Converted means the ask left intake and became a requisition — a
	// mid-pipeline handoff, which is exactly what the `erp` purple marks.
	converted: 'erp',
	// Cancelled is the absence of a signal, not a weak one — flat, not tinted.
	cancelled: 'neutral'
};

/**
 * `IntakeRequest.status` is typed `string` (the API is the source of truth, and
 * the backend may add a status before this union does), so both call sites read
 * the tone through a tolerant accessor — mirroring how they read the label.
 * An unrecognised status renders flat rather than borrowing another tone's
 * meaning.
 */
export function intakeStatusTone(status: string): BadgeTone {
	return INTAKE_STATUS_TONES[status as IntakeStatus] ?? 'neutral';
}

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
	estimated_amount: MoneyAmount;
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
	/** Row count of the whole filtered set — NOT money. */
	total: number;
	page: number;
	page_size: number;
}

/**
 * Whole-set KPI rollup from `GET /api/intake/summary`, over the SAME
 * status/type/search filters the list ran with.
 *
 * `openCount` / `reviewCount` used to filter the LOADED page by status, so both
 * contradicted the whole-set `total` count beside them.
 */
export interface IntakeSummary {
	/** Row count of the whole filtered set — NOT money. */
	total: number;
	by_status: Record<string, number>;
}

// Request side. Money goes out as the EXACT DECIMAL STRING the user typed —
// never a JSON number, which `json.loads` would have already rounded to a
// float before any pydantic `Decimal` annotation could see it.
export interface IntakeCreate {
	request_number?: string | null;
	title: string;
	request_type: string;
	description: string | null;
	estimated_amount: MoneyString | null;
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
	estimated_amount?: MoneyString | null;
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
