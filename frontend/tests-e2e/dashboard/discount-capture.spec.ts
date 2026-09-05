import { expect, test } from '../fixtures/helpers';

/**
 * Dashboard — early-payment discount capture.
 *
 * `GET /api/dashboard`'s `discount_capture` block has carried a three-way
 * captured / missed / PENDING fold plus `*_amount_reporting` +
 * `unconverted_count` since round 16 with NO frontend consumer at all. These
 * specs pin the two things that make surfacing it honest rather than harmful:
 *
 *   1. a still-open discount window renders as capturable, never as a miss;
 *   2. a figure that could not convert some rows SAYS so where it is read.
 *
 * The dashboard response is stubbed so both states are actually on screen —
 * a seeded tenant reliably produces neither.
 */

const AGING = { current: 0, days_30: 0, days_60: 0, days_90: 0, days_90_plus: 0 };

function dashboard(discount: Record<string, unknown>) {
	return {
		total_invoices: 4,
		total_amount: 1000,
		reporting: {
			reporting_currency: 'USD',
			total_amount: 1000,
			total_count: 4,
			unconverted_count: 0
		},
		total_paid: 0,
		total_pending: 1000,
		total_paid_reporting: 0,
		total_pending_reporting: 1000,
		total_paid_unconverted_count: 0,
		total_pending_unconverted_count: 0,
		total_rebates: 0,
		excluded_rebate_count: 0,
		open_exceptions: 0,
		touchless_rate: 0,
		stale_approvals: 0,
		pipeline: { new: 4 },
		vendor_spend: [],
		aging: AGING,
		aging_reporting: AGING,
		monthly_trend: [],
		upcoming_payments: [],
		upcoming_total_amount: 0,
		upcoming_total_amount_reporting: 0,
		upcoming_unconverted_count: 0,
		processing_time: {},
		approval_bottleneck: [],
		discount_capture: {
			eligible_count: 3,
			captured_count: 1,
			missed_count: 1,
			pending_count: 1,
			captured_amount: 100,
			missed_amount: 50,
			pending_amount: 25,
			reporting_currency: 'USD',
			captured_amount_reporting: 100,
			missed_amount_reporting: 50,
			pending_amount_reporting: 25,
			unconverted_count: 0,
			capture_rate_pct: 50,
			insufficient_data: false,
			...discount
		}
	};
}

async function stubDashboard(page: import('@playwright/test').Page, discount: Record<string, unknown>) {
	await page.route('**/api/dashboard*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(dashboard(discount))
		})
	);
}

test('renders the three-way fold — a still-open window is capturable, not missed', async ({
	page
}) => {
	await stubDashboard(page, {});
	await page.goto('/');

	const card = page.getByTestId('discount-capture');
	await expect(card).toBeVisible({ timeout: 15_000 });

	// Pending is its OWN bucket. Folding it into "missed" would report live
	// opportunity as forgone savings.
	await expect(card).toContainText('Captured');
	await expect(card).toContainText('Missed');
	await expect(card).toContainText('Still capturable');
	await expect(card.locator('.discount-row')).toHaveCount(3);
	await expect(page.getByTestId('discount-capture-rate')).toContainText('50%');
});

test('a partial figure says it is partial, at the point of reading', async ({ page }) => {
	await stubDashboard(page, { unconverted_count: 2 });
	await page.goto('/');

	const notice = page.getByTestId('discount-capture-unconverted');
	await expect(notice).toBeVisible({ timeout: 15_000 });
	await expect(notice).toHaveAttribute('role', 'alert');
	await expect(notice).toContainText('2');
	await expect(notice).toContainText('USD');

	// The KPI card's qualifier line carries the same disclosure — it outranks
	// the capture rate there, because a rate is context while an unconverted
	// count means the headline mixes currencies.
	const kpi = page.locator('.kpi', { hasText: 'Discounts captured' });
	await expect(kpi.locator('.kpi-sub')).toContainText('face value');
});

test('nothing decided yet reports no rate, never 0%', async ({ page }) => {
	await stubDashboard(page, {
		captured_count: 0,
		missed_count: 0,
		pending_count: 3,
		captured_amount_reporting: 0,
		missed_amount_reporting: 0,
		capture_rate_pct: null,
		insufficient_data: true
	});
	await page.goto('/');

	// "We have not missed a discount yet" and "we captured none of the
	// discounts we could have" are opposite facts; 0% reads as the bad one.
	const rate = page.getByTestId('discount-capture-rate');
	await expect(rate).toBeVisible({ timeout: 15_000 });
	await expect(rate).toContainText('No discount window has closed yet');
	await expect(rate).not.toContainText('0%');
});

test('no eligible invoices renders the empty state, not a row of zeros', async ({ page }) => {
	await stubDashboard(page, {
		eligible_count: 0,
		captured_count: 0,
		missed_count: 0,
		pending_count: 0,
		captured_amount_reporting: 0,
		missed_amount_reporting: 0,
		pending_amount_reporting: 0,
		capture_rate_pct: null,
		insufficient_data: true
	});
	await page.goto('/');

	const card = page.getByTestId('discount-capture');
	await expect(card).toContainText('No invoices carried an early-payment discount.', {
		timeout: 15_000
	});
	await expect(card.locator('.discount-row')).toHaveCount(0);
	// And no KPI card claiming a captured figure of zero.
	await expect(page.locator('.kpi', { hasText: 'Discounts captured' })).toHaveCount(0);
});
