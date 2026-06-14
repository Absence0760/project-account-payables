// Types for the Expenses surface. Mirrors the JSON returned by the
// `/api/expenses` + `/api/expense-reports` endpoints (backend
// `ExpenseResponse` / `ExpenseReportResponse`). Money fields arrive as
// numbers (backend `float(...)`); date/datetime fields are ISO strings.

export type ExpenseStatus = 'draft' | 'submitted' | 'approved' | 'rejected' | 'reimbursed';

export const EXPENSE_STATUSES: ExpenseStatus[] = [
	'draft',
	'submitted',
	'approved',
	'rejected',
	'reimbursed'
];

export const EXPENSE_STATUS_LABELS: Record<ExpenseStatus, string> = {
	draft: 'Draft',
	submitted: 'Submitted',
	approved: 'Approved',
	rejected: 'Rejected',
	reimbursed: 'Reimbursed'
};

export type ExpensePaymentMethod = 'out_of_pocket' | 'corporate_card' | 'virtual_card';

export const EXPENSE_PAYMENT_METHODS: ExpensePaymentMethod[] = [
	'out_of_pocket',
	'corporate_card',
	'virtual_card'
];

export const EXPENSE_PAYMENT_METHOD_LABELS: Record<ExpensePaymentMethod, string> = {
	out_of_pocket: 'Out of pocket',
	corporate_card: 'Corporate card',
	virtual_card: 'Virtual card'
};

export type ExpenseReportStatus =
	| 'draft'
	| 'submitted'
	| 'pending_approval'
	| 'approved'
	| 'rejected'
	| 'reimbursed'
	| 'cancelled';

export const EXPENSE_REPORT_STATUSES: ExpenseReportStatus[] = [
	'draft',
	'submitted',
	'pending_approval',
	'approved',
	'rejected',
	'reimbursed',
	'cancelled'
];

export const EXPENSE_REPORT_STATUS_LABELS: Record<ExpenseReportStatus, string> = {
	draft: 'Draft',
	submitted: 'Submitted',
	pending_approval: 'Pending Approval',
	approved: 'Approved',
	rejected: 'Rejected',
	reimbursed: 'Reimbursed',
	cancelled: 'Cancelled'
};

// A single policy-engine finding stamped onto `Expense.policy_violations` by the
// WF3 backend engine (`evaluate_expense`). `code` is a stable machine key
// (`category_limit`, `receipt_required`, `preapproval_required`, `per_diem`),
// `message` is the human string the badge tooltip renders.
export interface PolicyViolation {
	code: string;
	message: string;
	policy_id?: string;
	limit?: number;
	actual?: number;
}

export interface Expense {
	id: string;
	report_id: string | null;
	expense_date: string;
	merchant: string | null;
	category: string | null;
	description: string | null;
	amount: number;
	currency: string;
	gl_account_id: string | null;
	receipt_file_key: string | null;
	receipt_url: string | null;
	payment_method: string;
	card_transaction_id: string | null;
	policy_violations: PolicyViolation[] | null;
	status: string;
	reimbursable: boolean;
	mileage_miles: number | null;
	created_at: string;
	updated_at: string;
}

export interface ExpenseListResponse {
	items: Expense[];
	total: number;
	page: number;
	page_size: number;
}

export interface ExpenseReport {
	id: string;
	report_number: string;
	title: string | null;
	employee_user_id: string;
	status: string;
	submitted_at: string | null;
	approved_at: string | null;
	approved_by: string | null;
	total_amount: number;
	currency: string;
	notes: string | null;
	expenses: Expense[];
	created_at: string;
	updated_at: string;
}

export interface ExpenseReportListResponse {
	items: ExpenseReport[];
	total: number;
	page: number;
	page_size: number;
}

// `GET /api/expense-reports/{id}/summary` — computed from the report's expenses.
export interface ExpenseSummaryBucket {
	category?: string | null;
	status?: string | null;
	total: number;
	count: number;
}

export interface ExpenseReportSummary {
	total: number;
	count: number;
	by_category: ExpenseSummaryBucket[];
	by_status: ExpenseSummaryBucket[];
}

// Payload shapes for create / update (request side). Money goes out as a
// number — the backend coerces to Decimal. Optional fields default server-side.
export interface ExpenseCreate {
	expense_date: string | null;
	merchant: string | null;
	category: string | null;
	description: string | null;
	amount: number;
	currency: string;
	gl_account_id: string | null;
	payment_method: string;
	reimbursable: boolean;
	report_id?: string | null;
}

export interface ExpenseReportCreate {
	report_number: string;
	title: string | null;
	currency: string;
	notes: string | null;
}

// ============================ WF3: Policies ============================

export interface ExpensePolicy {
	id: string;
	name: string;
	active: boolean;
	category: string | null;
	per_diem_amount: number | null;
	per_diem_currency: string | null;
	mileage_rate: number | null;
	category_limit: number | null;
	requires_preapproval_above: number | null;
	requires_receipt_above: number | null;
	rules: unknown | null;
	created_at: string;
	updated_at: string;
}

export interface ExpensePolicyCreate {
	name: string;
	active: boolean;
	category: string | null;
	category_limit: number | null;
	requires_receipt_above: number | null;
	requires_preapproval_above: number | null;
	per_diem_amount: number | null;
	mileage_rate: number | null;
}

// ========================= WF3: Pre-approvals =========================

export type ExpensePreapprovalStatus = 'pending' | 'approved' | 'rejected';

export const EXPENSE_PREAPPROVAL_STATUSES: ExpensePreapprovalStatus[] = [
	'pending',
	'approved',
	'rejected'
];

export const EXPENSE_PREAPPROVAL_STATUS_LABELS: Record<ExpensePreapprovalStatus, string> = {
	pending: 'Pending',
	approved: 'Approved',
	rejected: 'Rejected'
};

export interface ExpensePreapproval {
	id: string;
	requester_user_id: string;
	title: string;
	estimated_amount: number;
	currency: string;
	category: string | null;
	justification: string | null;
	status: string;
	decided_by: string | null;
	decided_at: string | null;
	expense_report_id: string | null;
	created_at: string;
	updated_at: string;
}

export interface ExpensePreapprovalCreate {
	title: string;
	estimated_amount: number;
	currency: string;
	category: string | null;
	justification: string | null;
}

// ===================== WF4: Corporate-card transactions =====================

export type ReconciliationStatus = 'unmatched' | 'matched' | 'ignored';

export const RECONCILIATION_STATUSES: ReconciliationStatus[] = [
	'unmatched',
	'matched',
	'ignored'
];

export const RECONCILIATION_STATUS_LABELS: Record<ReconciliationStatus, string> = {
	unmatched: 'Unmatched',
	matched: 'Matched',
	ignored: 'Ignored'
};

// Mirrors the backend `CorporateCardTransactionResponse`. Money fields arrive as
// numbers (backend `float(...)` on the `Numeric(15,2)` column); date fields are
// ISO date strings. PII: only `card_last_four` is ever surfaced — never a PAN.
export interface CorporateCardTransaction {
	id: string;
	card_ref: string | null;
	card_last_four: string | null;
	virtual_card_id: string | null;
	txn_date: string;
	posted_date: string | null;
	merchant: string | null;
	amount: number;
	currency: string;
	external_txn_id: string | null;
	matched_expense_id: string | null;
	reconciliation_status: string;
	import_batch: string | null;
	created_at: string;
	updated_at: string;
}

export interface CardTransactionListResponse {
	items: CorporateCardTransaction[];
	total: number;
	page: number;
	page_size: number;
}

// A ranked candidate expense for reconciliation (from
// GET /{id}/match-suggestions) — the Expense plus a match score.
export interface CardMatchSuggestion {
	expense: Expense;
	score: number;
}

// POST /import-csv returns the shared `ImportResult.to_dict()` — `imported` /
// `skipped` / `errors`.
export interface CardImportResult {
	imported: number;
	skipped: number;
	errors: { row: number; message: string }[];
}

// POST /sync-virtual-cards return shape — created vs already-imported.
export interface SyncVirtualCardsResult {
	created: number;
	skipped: number;
}
