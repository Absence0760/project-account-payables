import { test, expect } from 'vitest';
import { en } from './locales/en';

// Regression test for issue #131 part 2: the main dashboard's "Total Amount"
// KPI (routes/+page.svelte, `data.total_amount` from `GET /api/dashboard` —
// a naive sum across EVERY invoice regardless of status or date) and the CFO
// analytics "total spend" figure (`GET /api/analytics/cfo` — a trailing
// `period_days` window that excludes only rejected invoices) measure
// different populations under similar-sounding names. The label itself must
// say "All Invoices" so it can't be misread as the CFO's filtered spend
// total. See backend/docs/analytics.md and
// backend/tests/test_analytics_rejected_exclusion.py for the backend-side
// population contrast.
test('dashboard "Total Amount" KPI label states its population (all invoices)', () => {
	expect(en['dashboard.kpi.totalAmount']).toBe('Total Amount (All Invoices)');
	expect(en['dashboard.kpi.totalAmount']).not.toBe('Total Amount');
});
