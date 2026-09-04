import { expect, test } from '../fixtures/helpers';

/**
 * `/vendors/screening` — the "Payments blocked" KPI comes from a query that
 * asks the KPI's own question.
 *
 * Regression: the figure was `items.filter((it) => it.payments_blocked).length`
 * over the SCREENING REVIEW QUEUE, and the queue is selected on
 * `screening_status IN ('match','review')`. `POST /api/vendors/{id}/block`
 * sets `payments_blocked` and deliberately never touches `screening_status`,
 * so a vendor AP blocked while screening-clear was invisible to a headline
 * claiming to count blocked payments — at any queue size, not just past a page
 * boundary. The KPI now reads `payments_blocked` off GET /api/vendors/counts.
 *
 * Both cases below mock a queue in which NOTHING is blocked, so the pre-fix
 * page could only ever render 0.
 */

const QUEUE_ROW = {
	vendor_id: '00000000-0000-0000-0000-0000000000a1',
	vendor_name: 'Flagged But Not Blocked Co',
	screening_status: 'review',
	last_screened_at: '2026-01-05T10:00:00Z',
	payments_blocked: false,
	risk_level: 'medium',
	risk_score: '55.00',
	latest_matched_list: 'OFAC SDN',
	latest_provider: 'mock',
	latest_categories: ['sanctions'],
	adverse_media: false
};

const blockedKpi = (page: import('@playwright/test').Page) =>
	page.locator('.kpi', { hasText: 'Payments blocked' }).locator('.kpi-value');

test.describe('screening review queue — payments-blocked KPI', () => {
	test('counts blocked vendors the review queue cannot see', async ({ page }) => {
		await page.route('**/api/vendors/screening/review-queue**', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify([QUEUE_ROW])
			});
		});
		// Three blocked vendors tenant-wide; none of them is in the queue above.
		await page.route('**/api/vendors/counts**', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 40,
					by_status: { active: 39, unverified: 1 },
					payments_blocked: 3
				})
			});
		});

		await page.goto('/vendors/screening');

		// The queue-derived tally would be 0 — every mocked row is unblocked.
		await expect(blockedKpi(page)).toHaveText('3');
		// The sibling KPIs still describe the queue itself.
		await expect(page.locator('.kpi', { hasText: 'Needs review' }).locator('.kpi-value')).toHaveText(
			'1'
		);
	});

	test('says so when the tally is unavailable rather than showing a wrong number', async ({
		page
	}) => {
		await page.route('**/api/vendors/screening/review-queue**', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify([QUEUE_ROW])
			});
		});
		// `ap_clerk` reaches this queue but not the vendor list (and so not its
		// tally). An em-dash is honest; latching back to the queue-derived count
		// would reinstate the bug for exactly that caller.
		await page.route('**/api/vendors/counts**', async (route) => {
			await route.fulfill({
				status: 403,
				contentType: 'application/json',
				body: JSON.stringify({ detail: 'Your role does not permit this action.' })
			});
		});

		await page.goto('/vendors/screening');

		await expect(blockedKpi(page)).toHaveText('—');
		await expect(page.locator('.kpi', { hasText: 'Payments blocked' })).toContainText(
			'Count unavailable'
		);
	});
});
