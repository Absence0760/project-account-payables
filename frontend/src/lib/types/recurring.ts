// Types for the Recurring / Subscription Invoices surface. Mirrors the JSON
// returned by the `/api/recurring` endpoints. Money fields arrive as numbers
// (or null); date fields are ISO date strings (or null).

export type RecurringCadence = 'monthly' | 'quarterly' | 'annual';

export const RECURRING_CADENCES: RecurringCadence[] = ['monthly', 'quarterly', 'annual'];

export const CADENCE_LABELS: Record<RecurringCadence, string> = {
	monthly: 'Monthly',
	quarterly: 'Quarterly',
	annual: 'Annual'
};

export type RecurringStatus = 'active' | 'paused' | 'ended';

export const RECURRING_STATUSES: RecurringStatus[] = ['active', 'paused', 'ended'];

// StatusBadge-style label map (Title Case) for the template status pill.
export const STATUS_LABELS: Record<RecurringStatus, string> = {
	active: 'Active',
	paused: 'Paused',
	ended: 'Ended'
};

// A due period the background sweep could NOT generate an invoice for. The
// backend persists it on the template (`meta.generation_skip`) and surfaces it
// here so "nothing raised for months" is distinguishable from "nothing due
// yet". PII-free: a reason code, the period, a count and a timestamp.
// See `backend/docs/recurring-invoices.md` § A skipped period is never silent.
export type GenerationSkipReason =
	| 'missing_amount'
	| 'missing_vendor'
	| 'missing_amount_and_vendor';

export interface GenerationSkip {
	reason: GenerationSkipReason | string;
	period_key: string | null;
	consecutive: number;
	last_skipped_at: string | null;
}

// Every reason code the backend sweep can emit
// (`services/recurring_invoices.SKIP_MISSING_*`). Kept here, beside the
// message-key map, so a code added on the backend fails the unit test rather
// than silently rendering as a raw string in the UI.
export const GENERATION_SKIP_REASONS: GenerationSkipReason[] = [
	'missing_amount',
	'missing_vendor',
	'missing_amount_and_vendor'
];

const SKIP_REASON_KEYS: Record<string, string> = {
	missing_amount: 'recurring.skip.reason.missingAmount',
	missing_vendor: 'recurring.skip.reason.missingVendor',
	missing_amount_and_vendor: 'recurring.skip.reason.missingBoth'
};

/**
 * The i18n key describing a skip reason code, or `null` for a code this
 * frontend doesn't know (the caller renders the raw code rather than a blank).
 * Pure — the typed `MessageKey` cast happens at the call site so this module
 * stays free of the i18n runtime.
 */
export function skipReasonKey(code: string): string | null {
	return SKIP_REASON_KEYS[code] ?? null;
}

export interface RecurringTemplate {
	id: string;
	name: string;
	vendor_id: string | null;
	vendor_name: string | null;
	description: string | null;
	amount: number | null;
	currency: string;
	gl_account: string | null;
	cost_center: string | null;
	department: string | null;
	project: string | null;
	po_number: string | null;
	payment_terms: string | null;
	cadence: RecurringCadence;
	day_of_period: number;
	start_date: string;
	end_date: string | null;
	next_run_on: string | null;
	last_period_key: string | null;
	last_generated_at: string | null;
	generated_count: number;
	status: RecurringStatus;
	variance_tolerance_pct: number | null;
	notes: string | null;
	last_skip: GenerationSkip | null;
	created_at: string;
	updated_at: string | null;
}

// POST /api/recurring body. name + start_date are required.
export interface RecurringTemplateCreate {
	name: string;
	vendor_id?: string | null;
	description?: string | null;
	amount?: number | null;
	currency?: string;
	gl_account?: string | null;
	cost_center?: string | null;
	department?: string | null;
	project?: string | null;
	po_number?: string | null;
	payment_terms?: string | null;
	cadence?: RecurringCadence;
	day_of_period?: number;
	start_date: string;
	end_date?: string | null;
	variance_tolerance_pct?: number | null;
	notes?: string | null;
}

export interface RecurringListResponse {
	items: RecurringTemplate[];
	total: number;
	page: number;
	page_size: number;
}

// GET /api/recurring/{id}/upcoming-schedule
export interface RecurringOccurrence {
	period_key: string;
	run_on: string;
	amount: number | null;
	currency: string;
}

export interface UpcomingSchedule {
	template_id: string;
	occurrences: RecurringOccurrence[];
}

// GET /api/recurring/{id}/history
export interface RecurringHistoryItem {
	invoice_id: string;
	invoice_number: string | null;
	period_key: string;
	amount: number | null;
	currency: string;
	status: string;
	created_at: string;
}

export interface RecurringHistory {
	template_id: string;
	items: RecurringHistoryItem[];
	total: number;
}
