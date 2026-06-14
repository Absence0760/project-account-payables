// Typed helpers for the procurement budgets endpoints. All requests route
// through the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID +
// 401-bounce). Mirrors the pattern of `src/lib/api/expenses.ts`.
import { api } from '$lib/api';
import type {
	Budget,
	BudgetCheck,
	BudgetCreate,
	BudgetListResponse,
	BudgetSpend,
	BudgetUpdate
} from '$lib/types/budget';

export interface BudgetListParams {
	dimension?: string;
	period?: string;
	search?: string;
	page?: number;
	page_size?: number;
}

export function listBudgets(params: BudgetListParams = {}): Promise<BudgetListResponse> {
	const qs = new URLSearchParams();
	if (params.dimension) qs.set('dimension', params.dimension);
	if (params.period) qs.set('period', params.period);
	if (params.search) qs.set('search', params.search);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<BudgetListResponse>(`/api/budgets?${qs}`);
}

export function getBudget(id: string): Promise<Budget> {
	return api.get<Budget>(`/api/budgets/${id}`);
}

export function createBudget(body: BudgetCreate): Promise<Budget> {
	return api.post<Budget>('/api/budgets', body);
}

export function updateBudget(id: string, body: BudgetUpdate): Promise<Budget> {
	return api.patch<Budget>(`/api/budgets/${id}`, body);
}

export function deleteBudget(id: string): Promise<void> {
	return api.delete(`/api/budgets/${id}`);
}

// Computed spend rollup for one budget (allocated / committed / actual /
// remaining / utilization). Computed on read by the backend.
export function getBudgetSpend(id: string): Promise<BudgetSpend> {
	return api.get<BudgetSpend>(`/api/budgets/${id}/spend`);
}

// Overspend pre-check: would committing `amount` against this budget exceed it?
export function checkBudget(budgetId: string, amount: number): Promise<BudgetCheck> {
	const qs = new URLSearchParams({ budget_id: budgetId, amount: String(amount) });
	return api.get<BudgetCheck>(`/api/budgets/check?${qs}`);
}
