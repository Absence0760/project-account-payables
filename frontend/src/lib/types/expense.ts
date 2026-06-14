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
	policy_violations: unknown[] | null;
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
