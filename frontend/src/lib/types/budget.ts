// Types for the Procurement → Budgets surface. Mirrors the JSON returned by the
// `/api/budgets` endpoints (backend `BudgetResponse` / `BudgetSpendResponse` /
// `BudgetCheckResponse`). Money fields arrive as numbers (backend `float(...)`);
// date/datetime fields are ISO strings.

export type BudgetDimension = 'department' | 'project' | 'cost_center' | 'gl_account';

export const BUDGET_DIMENSIONS: BudgetDimension[] = [
	'department',
	'project',
	'cost_center',
	'gl_account'
];

export const BUDGET_DIMENSION_LABELS: Record<BudgetDimension, string> = {
	department: 'Department',
	project: 'Project',
	cost_center: 'Cost Center',
	gl_account: 'GL Account'
};

export interface Budget {
	id: string;
	name: string;
	dimension: string;
	dimension_value: string;
	period: string | null;
	period_start: string | null;
	period_end: string | null;
	amount: number;
	currency: string;
	notes: string | null;
	created_at: string;
	updated_at: string;
}

export interface BudgetListResponse {
	items: Budget[];
	total: number;
	page: number;
	page_size: number;
}

// `GET /api/budgets/{id}/spend` — computed on read from requisitions / POs /
// invoices. `committed` = open requisitions + their converted POs; `actual` =
// realised invoice spend matched to the dimension; `remaining` =
// allocated - committed - actual (negative = overspend).
export interface BudgetSpend {
	budget_id: string;
	name: string;
	dimension: string;
	dimension_value: string;
	currency: string;
	allocated: number;
	committed: number;
	actual: number;
	remaining: number;
	utilization_pct: number;
}

// `GET /api/budgets/check?budget_id=&amount=` — overspend pre-check for the
// requisition flow.
export interface BudgetCheck {
	budget_id: string;
	amount: number;
	allocated: number;
	committed: number;
	actual: number;
	remaining: number;
	remaining_after: number;
	would_overspend: boolean;
	currency: string;
}

// Request shapes (money goes out as a number — the backend coerces to Decimal).
export interface BudgetCreate {
	name: string;
	dimension: BudgetDimension;
	dimension_value: string;
	period: string | null;
	period_start: string | null;
	period_end: string | null;
	amount: number;
	currency: string;
	notes: string | null;
}

export type BudgetUpdate = Partial<BudgetCreate>;
