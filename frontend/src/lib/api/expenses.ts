// Typed helpers for the expense + expense-report endpoints. All requests route
// through the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID +
// 401-bounce). Mirrors the pattern of `src/lib/api/contracts.ts`.
import { api } from '$lib/api';
import { PUBLIC_API_URL } from '$env/static/public';
import { getTenantSlug } from '$lib/tenant';
import { getSelectedEntityId } from '$lib/entity';
import type {
	Expense,
	ExpenseCreate,
	ExpenseListResponse,
	ExpenseReport,
	ExpenseReportCreate,
	ExpenseReportListResponse,
	ExpenseReportSummary,
	ExpensePolicy,
	ExpensePolicyCreate,
	ExpensePreapproval,
	ExpensePreapprovalCreate,
	PolicyViolation
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

// =========================== WF3: Policies ===========================

export function listPolicies(): Promise<ExpensePolicy[]> {
	return api.get<ExpensePolicy[]>('/api/expense-policies');
}

export function createPolicy(body: ExpensePolicyCreate): Promise<ExpensePolicy> {
	return api.post<ExpensePolicy>('/api/expense-policies', body);
}

export function updatePolicy(
	id: string,
	body: Partial<ExpensePolicyCreate>
): Promise<ExpensePolicy> {
	return api.patch<ExpensePolicy>(`/api/expense-policies/${id}`, body);
}

export function deletePolicy(id: string): Promise<void> {
	return api.delete(`/api/expense-policies/${id}`);
}

// ========================= WF3: Pre-approvals =========================

export function listPreapprovals(
	params: { status?: string; requester_user_id?: string } = {}
): Promise<ExpensePreapproval[]> {
	const qs = new URLSearchParams();
	if (params.status) qs.set('status', params.status);
	if (params.requester_user_id) qs.set('requester_user_id', params.requester_user_id);
	return api.get<ExpensePreapproval[]>(`/api/expense-preapprovals?${qs}`);
}

export function createPreapproval(body: ExpensePreapprovalCreate): Promise<ExpensePreapproval> {
	return api.post<ExpensePreapproval>('/api/expense-preapprovals', body);
}

export function getPreapproval(id: string): Promise<ExpensePreapproval> {
	return api.get<ExpensePreapproval>(`/api/expense-preapprovals/${id}`);
}

export function approvePreapproval(id: string): Promise<ExpensePreapproval> {
	return api.post<ExpensePreapproval>(`/api/expense-preapprovals/${id}/approve`, {});
}

export function rejectPreapproval(id: string, reason?: string): Promise<ExpensePreapproval> {
	return api.post<ExpensePreapproval>(`/api/expense-preapprovals/${id}/reject`, {
		reason: reason ?? null
	});
}

// ===================== WF3: Report transitions =====================

/**
 * Discriminated result for the report `submit` action. The submit route can
 * 422 with a structured violation list (blocking policy violations) — the
 * shared `api` client collapses every non-OK body into `Error(body.detail)`,
 * losing a list-shaped `detail`. So submit hand-rolls its own `fetch` (the
 * `api.ts::downloadBlob` private-fetch idiom: Bearer + tenant + entity headers)
 * to read the raw 422 JSON and surface the violations to the caller intact.
 */
export type SubmitReportResult =
	| { ok: true; report: ExpenseReport }
	| { ok: false; violations: PolicyViolation[]; message: string };

/** Normalise whatever the backend put in the 422 body into a violation list.
 *  Supports `detail` = list-of-violations OR `detail` = {message, violations}
 *  OR a plain string `detail`. */
function parseSubmitViolations(body: unknown): { violations: PolicyViolation[]; message: string } {
	const detail = (body as { detail?: unknown } | null)?.detail;
	if (Array.isArray(detail)) {
		const violations = detail as PolicyViolation[];
		return { violations, message: violations.map((v) => v.message).join('; ') };
	}
	if (detail && typeof detail === 'object') {
		const obj = detail as { message?: string; violations?: PolicyViolation[] };
		const violations = Array.isArray(obj.violations) ? obj.violations : [];
		return {
			violations,
			message: obj.message ?? violations.map((v) => v.message).join('; ') ?? 'Submit blocked'
		};
	}
	if (typeof detail === 'string') {
		return { violations: [], message: detail };
	}
	return { violations: [], message: 'Submit blocked by policy.' };
}

export async function submitReport(id: string): Promise<SubmitReportResult> {
	const base = PUBLIC_API_URL.replace(/\/+$/, '');
	const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
	const headers: Record<string, string> = { 'Content-Type': 'application/json' };
	if (token) headers['Authorization'] = `Bearer ${token}`;
	const tenant = getTenantSlug();
	if (tenant) headers['X-Tenant-Slug'] = tenant;
	const entity = getSelectedEntityId();
	if (entity) headers['X-Entity-ID'] = entity;

	// Sanctioned api-layer exception (downloadBlob idiom): the shared api.ts
	// collapses a list-shaped 422 `detail` into Error("[object Object]"),
	// destroying the policy-violation list. We hand-roll the same Bearer +
	// X-Tenant-Slug + X-Entity-ID headers + 401-bounce so the raw 422 JSON
	// survives. Not a component; not a bypass.
	const res = await fetch(`${base}/api/expense-reports/${id}/submit`, { // noqa: raw-fetch-in-component
		method: 'POST',
		headers,
		body: JSON.stringify({})
	});

	if (res.status === 401) {
		// Mirror the api-client stale-session bounce.
		if (token) {
			localStorage.removeItem('auth_token');
			window.location.href = '/login';
		}
		throw new Error('Unauthorized');
	}

	if (res.status === 422) {
		const body = await res.json().catch(() => ({}));
		return { ok: false, ...parseSubmitViolations(body) };
	}

	if (!res.ok) {
		const body = (await res.json().catch(() => ({}))) as { detail?: string };
		throw new Error(body.detail || `API error ${res.status}`);
	}

	const report = (await res.json()) as ExpenseReport;
	return { ok: true, report };
}

export function approveReport(id: string): Promise<ExpenseReport> {
	return api.post<ExpenseReport>(`/api/expense-reports/${id}/approve`, {});
}

export function rejectReport(id: string, reason: string): Promise<ExpenseReport> {
	return api.post<ExpenseReport>(`/api/expense-reports/${id}/reject`, { reason });
}
