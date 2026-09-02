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

// Every value the `purchase_requisitions.status` column can hold — the full
// mirror of the backend `RequisitionStatus` enum
// (`backend/app/models/procurement.py`). Kept COMPLETE on purpose: `submitted`
// is still a legal *source* state for legacy rows
// (`services/requisition_service.VALID_TRANSITIONS[submitted]` allows
// pending_approval + cancelled, and `services/budget_service.
// OPEN_COMMITMENT_REQ_STATUSES` counts it as committed spend), so such a row
// must still render a badge and still offer Cancel. This is NOT the filter-chip
// list — see `REQUISITION_FILTER_STATUSES`.
export const REQUISITION_STATUSES: RequisitionStatus[] = [
	'draft',
	'submitted',
	'pending_approval',
	'approved',
	'rejected',
	'converted',
	'cancelled'
];

// Statuses no backend transition ever stamps, so a filter chip for them is a
// control that can never return a row. Every writer of
// `PurchaseRequisition.status` in the backend:
//   - `draft`            — the column default on insert
//                          (`models/procurement.py:262`), `create_requisition`
//                          (`api/requisitions.py:208`), intake conversion
//                          (`services/intake_service.py:117`) and punch-out
//                          cart conversion (`api/catalogs.py:518`).
//   - `pending_approval` — `submit_requisition` (`api/requisitions.py:352`).
//                          Submit jumps STRAIGHT here; it never passes through
//                          `submitted` (the module docstring at
//                          `api/requisitions.py:335` still advertises the old
//                          draft → submitted → pending_approval graph).
//   - `approved`         — `approve_requisition` (`api/requisitions.py:379`).
//   - `rejected`         — `reject_requisition` (`api/requisitions.py:398`).
//   - `cancelled`        — `cancel_requisition` (`api/requisitions.py:424`).
//   - `converted`        — `convert_to_po` (`api/requisitions.py:485`).
//
// That is the complete list — `submitted` appears nowhere as a destination.
// Delete the entry below the moment `submit_requisition` starts stamping it
// (i.e. if the two-step draft → submitted → pending_approval graph the docstring
// describes is actually implemented).
const UNREACHABLE_REQUISITION_STATUSES: RequisitionStatus[] = ['submitted'];

// The subset offered as *filter chips* — derived by EXCLUSION from the full
// mirror, so a genuinely new status added above joins the chips by default and
// only a deliberate, justified entry in UNREACHABLE_REQUISITION_STATUSES keeps
// one out. The excluded value still lives in the union and in
// REQUISITION_STATUS_LABELS (a legacy row must still render its badge and still
// offer Cancel), and the page appends whatever status is *actively* filtered to
// the chip row, so an explicit `?status=submitted` is never an invisible filter.
export const REQUISITION_FILTER_STATUSES: RequisitionStatus[] = REQUISITION_STATUSES.filter(
	(s) => !UNREACHABLE_REQUISITION_STATUSES.includes(s)
);

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

/** One currency's slice of `GET /api/requisitions/summary`. */
export interface RequisitionCurrencyTotal {
	currency: string;
	/** Exact decimal string — never parse into a float for arithmetic. */
	total: string;
	count: number;
}

/**
 * Whole-set KPI rollup from `GET /api/requisitions/summary`, over the SAME
 * status/search filters the list ran with.
 *
 * The page's `pendingCount` filtered the LOADED page for `pending_approval`
 * and `periodTotal` reduced over it — so both contradicted the whole-set row
 * count beside them, and `periodTotal` added values across currencies.
 * `by_currency` is grouped, never summed (see `$lib/utils/currencyGroups`).
 */
export interface RequisitionSummary {
	total: number;
	by_status: Record<string, number>;
	by_currency: RequisitionCurrencyTotal[];
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
