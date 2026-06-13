// Typed helpers for the 1099 reporting endpoints. All requests route
// through the shared `api` client (Bearer + X-Tenant-Slug + 401-bounce).
import { api } from '$lib/api';
import type { Report1099 } from '$lib/types/tax';

// 1099 report for a calendar year. Aggregates completed payments per
// vendor and flags W-9 / TIN / >$600-threshold status.
export function get1099Report(year: number): Promise<Report1099> {
	return api.get<Report1099>(`/api/tax/1099-report?year=${encodeURIComponent(year)}`);
}
