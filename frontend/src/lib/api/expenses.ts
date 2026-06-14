// Typed helpers for the expense + expense-report endpoints. All requests route
// through the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID +
// 401-bounce). Mirrors the pattern of `src/lib/api/contracts.ts`.
import { api } from '$lib/api';
import type {
	Expense,
	ExpenseCreate,
	ExpenseListResponse,
	ExpenseReport,
	ExpenseReportCreate,
	ExpenseReportListResponse,
	ExpenseReportSummary
} from '$lib/types/expense';

export interface ExpenseListParams {
	status?: string;
	category?: string;
	search?: string;
	date_from?: string;
	date_to?: string;
	report_id?: string;
	page?: number;
	page_size?: number;
}

/** GL account option from `GET /api/gl-accounts` — the picker value is the uuid
 *  `id` (matches `Expense.gl_account_id` + the `bulk-gl-code` body). */
export interface GlAccountOption {
	id: string;
	code: string;
	name: string;
	account_type?: string;
}

function expenseQuery(params: ExpenseListParams): URLSearchParams {
	const qs = new URLSearchParams();
	if (params.status) qs.set('status', params.status);
	if (params.category) qs.set('category', params.category);
	if (params.search) qs.set('search', params.search);
	if (params.date_from) qs.set('date_from', params.date_from);
	if (params.date_to) qs.set('date_to', params.date_to);
	if (params.report_id) qs.set('report_id', params.report_id);
	return qs;
}

export function listExpenses(params: ExpenseListParams = {}): Promise<ExpenseListResponse> {
	const qs = expenseQuery(params);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<ExpenseListResponse>(`/api/expenses?${qs}`);
}

export function getExpense(id: string): Promise<Expense> {
	return api.get<Expense>(`/api/expenses/${id}`);
}

export function createExpense(body: ExpenseCreate): Promise<Expense> {
	return api.post<Expense>('/api/expenses', body);
}

export function updateExpense(id: string, body: Partial<ExpenseCreate>): Promise<Expense> {
	return api.patch<Expense>(`/api/expenses/${id}`, body);
}

export function deleteExpense(id: string): Promise<void> {
	return api.delete(`/api/expenses/${id}`);
}

export function uploadReceipt(id: string, file: File): Promise<Expense> {
	return api.upload<Expense>(`/api/expenses/${id}/receipt`, file);
}

// Bytes for an expense receipt. `<img>`/`<a download>` can't reach the endpoint
// directly (no Bearer header), so fetch through the auth'd client and render
// via a blob URL. Caller revokes the returned URL.
export function receiptUrl(fileKey: string): Promise<string> {
	return api.fetchBlob(`/api/expenses/receipt/${fileKey}`);
}

// --- Reports ---

export function listExpenseReports(
	params: { status?: string; page?: number; page_size?: number } = {}
): Promise<ExpenseReportListResponse> {
	const qs = new URLSearchParams();
	if (params.status) qs.set('status', params.status);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<ExpenseReportListResponse>(`/api/expense-reports?${qs}`);
}

export function getExpenseReport(id: string): Promise<ExpenseReport> {
	return api.get<ExpenseReport>(`/api/expense-reports/${id}`);
}

export function createExpenseReport(body: ExpenseReportCreate): Promise<ExpenseReport> {
	return api.post<ExpenseReport>('/api/expense-reports', body);
}

export function updateExpenseReport(
	id: string,
	body: { report_number?: string; title?: string | null; currency?: string; notes?: string | null }
): Promise<ExpenseReport> {
	return api.patch<ExpenseReport>(`/api/expense-reports/${id}`, body);
}

export function attachExpenses(
	reportId: string,
	expenseIds: string[],
	detach = false
): Promise<ExpenseReport> {
	return api.post<ExpenseReport>(`/api/expense-reports/${reportId}/expenses`, {
		expense_ids: expenseIds,
		detach
	});
}

export function expenseReportSummary(id: string): Promise<ExpenseReportSummary> {
	return api.get<ExpenseReportSummary>(`/api/expense-reports/${id}/summary`);
}

// --- Bulk GL code (WF2) ---

export function bulkGlCode(
	expenseIds: string[],
	glAccountId: string | null
): Promise<{ updated: number }> {
	return api.post<{ updated: number }>('/api/expenses/bulk-gl-code', {
		expense_ids: expenseIds,
		gl_account_id: glAccountId
	});
}

// --- GL accounts (reused from the existing chart-of-accounts endpoint) ---

export function listGlAccounts(): Promise<GlAccountOption[]> {
	return api.get<GlAccountOption[]>('/api/gl-accounts');
}

// --- CSV export ---

function triggerDownload(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = filename;
	a.click();
	URL.revokeObjectURL(url);
}

/** Streamed `text/csv` expense register; the backend stamps the same filename
 *  via Content-Disposition. Goes through `downloadBlob` so the Bearer + tenant
 *  + entity headers ride along (a bare `<a href>` can't carry the JWT). */
export async function exportExpensesCsv(params: ExpenseListParams = {}): Promise<void> {
	const qs = expenseQuery(params);
	const blob = await api.downloadBlob(`/api/expenses/export?${qs}`);
	const today = new Date().toISOString().slice(0, 10);
	triggerDownload(blob, `expenses_${today}.csv`);
}
