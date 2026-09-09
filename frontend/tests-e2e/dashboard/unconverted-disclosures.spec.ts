import { expect, test } from '../fixtures/helpers';

/**
 * Dashboard — the three CHART unconverted disclosures.
 *
 * A foreign invoice with no locked rate into the org's reporting currency is
 * summed at FACE value rather than dropped, and each chart says so at the
 * grain it is read at (`docs/decisions.md` §35): `vendor_spend` NAMES the
 * affected vendors (the tile ranks vendors against each other), the five
 * aging bands carry ONE notice for the set, and `monthly_trend` NAMES the
 * affected months (a trend is read bar against bar).
 *
 * The whole point of a disclosure is that it tracks its own count, so each is
 * pinned in BOTH directions — present when that chart folded a row at face
 * value, absent when it did not. The absent case is the one that needs care:
 * an empty series makes the notice vanish for a reason that has nothing to do
 * with the count (`vendor_spend: []` renders no bars, and the whole
 * monthly-trend card is `{#if data.monthly_trend.length > 0}`), so proving the
 * count is what governs means rendering the series POPULATED with zero counts.
 * A stub with empty series would pass either way.
 *
 * `discount-capture.spec.ts` owns the fourth notice on this page (the discount
 * card's) and the KPI row's `unconverted-rollup` banner is a different
 * question again — every count in this fixture that feeds it stays at zero, so
 * a chart notice here can never be the rollup banner misread.
 */

const REPORTING_CURRENCY = 'USD';

/** One `vendor_spend` row. */
function vendor(name: string, amount: number, unconverted: number) {
	return { vendor: name, amount, unconverted_count: unconverted };
}

/** One `monthly_trend` row. */
function month(key: string, amount: number, unconverted: number) {
	return {
		month: key,
		count: 2,
		amount,
		reporting_amount: amount,
		unconverted_count: unconverted
	};
}

interface Series {
	/** Per-vendor face-value counts, in rank order. */
	vendors?: number[];
	/** The ONE count the API returns for the whole aging band set. */
	aging?: number;
	/** Per-month face-value counts, oldest first. */
	trend?: number[];
}

/**
 * A dashboard payload whose three chart series are always POPULATED; only the
 * per-entry `unconverted_count`s vary. Every KPI-row count is pinned at zero
 * so the page-level `unconverted-rollup` banner never fires and can't be
 * confused for a chart notice.
 */
function dashboard({ vendors = [0, 0], aging = 0, trend = [0, 0, 0] }: Series) {
	return {
		total_invoices: 6,
		total_amount: 6000,
		reporting: {
			reporting_currency: REPORTING_CURRENCY,
			total_amount: 6000,
			total_count: 6,
			unconverted_count: 0
		},
		total_paid: 2000,
		total_pending: 4000,
		total_paid_reporting: 2000,
		total_pending_reporting: 4000,
		total_paid_unconverted_count: 0,
		total_pending_unconverted_count: 0,
		total_rebates: 0,
		excluded_rebate_count: 0,
		open_exceptions: 0,
		touchless_rate: 0,
		stale_approvals: 0,
		pipeline: { new: 6 },
		vendor_spend: [
			vendor('Northwind Traders', 3000, vendors[0] ?? 0),
			vendor('Contoso Supplies', 1500, vendors[1] ?? 0)
		],
		aging: { current: 4000, days_30: 1000, days_60: 500, days_90: 300, days_90_plus: 200 },
		aging_reporting: {
			current: 4000,
			days_30: 1000,
			days_60: 500,
			days_90: 300,
			days_90_plus: 200,
			unconverted_count: aging
		},
		monthly_trend: [
			month('2026-01', 1800, trend[0] ?? 0),
			month('2026-02', 2200, trend[1] ?? 0),
			month('2026-03', 2000, trend[2] ?? 0)
		],
		upcoming_payments: [],
		upcoming_total_amount: 0,
		upcoming_total_amount_reporting: 0,
		upcoming_unconverted_count: 0,
		processing_time: {},
		approval_bottleneck: [],
		discount_capture: {
			eligible_count: 0,
			captured_count: 0,
			missed_count: 0,
			pending_count: 0,
			captured_amount: 0,
			missed_amount: 0,
			pending_amount: 0,
			reporting_currency: REPORTING_CURRENCY,
			captured_amount_reporting: 0,
			missed_amount_reporting: 0,
			pending_amount_reporting: 0,
			unconverted_count: 0,
			capture_rate_pct: null,
			insufficient_data: true
		}
	};
}

async function stubDashboard(page: import('@playwright/test').Page, series: Series) {
	await page.route('**/api/dashboard*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(dashboard(series))
		})
	);
}

/** The three chart notices, plus the KPI-row banner none of them may be. */
const VENDOR_NOTICE = 'unconverted-vendor-spend';
const AGING_NOTICE = 'unconverted-aging';
const TREND_NOTICE = 'unconverted-trend';
const ROLLUP_BANNER = 'unconverted-rollup';

/** Wait for the dashboard to have actually rendered its charts, so a
 *  `toHaveCount(0)` can't pass against a page that is still loading. */
async function awaitCharts(page: import('@playwright/test').Page) {
	await expect(page.locator('.charts-grid .chart-card').first()).toBeVisible({
		timeout: 15_000
	});
}

test('no chart folded a row at face value — none of the three notices appears', async ({
	page
}) => {
	// Every series is populated; only the counts are zero. That is the whole
	// point: an empty series would hide these notices for the wrong reason.
	await stubDashboard(page, { vendors: [0, 0], aging: 0, trend: [0, 0, 0] });
	await page.goto('/');
	await awaitCharts(page);

	// The bars they qualify ARE on screen, so absence is about the count.
	await expect(page.locator('.vendor-row')).toHaveCount(2);
	await expect(page.locator('.trend-bar-group')).toHaveCount(3);

	await expect(page.getByTestId(VENDOR_NOTICE)).toHaveCount(0);
	await expect(page.getByTestId(AGING_NOTICE)).toHaveCount(0);
	await expect(page.getByTestId(TREND_NOTICE)).toHaveCount(0);
	await expect(page.getByTestId(ROLLUP_BANNER)).toHaveCount(0);
});

test('a part-converted vendor total names the vendor, and only that chart says so', async ({
	page
}) => {
	// Only the SECOND vendor folded rows at face value.
	await stubDashboard(page, { vendors: [0, 3] });
	await page.goto('/');

	const notice = page.getByTestId(VENDOR_NOTICE);
	await expect(notice).toBeVisible({ timeout: 15_000 });
	await expect(notice).toHaveAttribute('role', 'alert');
	await expect(notice).toContainText('3');
	await expect(notice).toContainText(REPORTING_CURRENCY);
	// Naming the vendor is the point — the tile RANKS vendors against each
	// other, so "which total is not comparable" is what a reader acts on.
	await expect(notice).toContainText('Contoso Supplies');
	await expect(notice).not.toContainText('Northwind Traders');

	// The affected row is marked in the bars too, and only that one.
	await expect(page.locator('.vendor-bar.partial')).toHaveCount(1);
	await expect(page.locator('.vendor-amount.partial-amount')).toHaveCount(1);

	// The other two charts converted everything, so they stay silent.
	await expect(page.getByTestId(AGING_NOTICE)).toHaveCount(0);
	await expect(page.getByTestId(TREND_NOTICE)).toHaveCount(0);
});

test('a part-converted aging set says so once, for the whole band set', async ({ page }) => {
	await stubDashboard(page, { aging: 5 });
	await page.goto('/');

	const notice = page.getByTestId(AGING_NOTICE);
	await expect(notice).toBeVisible({ timeout: 15_000 });
	await expect(notice).toHaveAttribute('role', 'alert');
	await expect(notice).toContainText('5');
	await expect(notice).toContainText(REPORTING_CURRENCY);
	// ONE notice for five bands, matching the single count the API returns —
	// not one per band.
	await expect(notice).toHaveCount(1);

	await expect(page.getByTestId(VENDOR_NOTICE)).toHaveCount(0);
	await expect(page.getByTestId(TREND_NOTICE)).toHaveCount(0);
});

test('a part-converted month names the month, and only that bar is marked', async ({ page }) => {
	// Only February folded rows at face value.
	await stubDashboard(page, { trend: [0, 4, 0] });
	await page.goto('/');

	const notice = page.getByTestId(TREND_NOTICE);
	await expect(notice).toBeVisible({ timeout: 15_000 });
	await expect(notice).toHaveAttribute('role', 'alert');
	await expect(notice).toContainText('4');
	await expect(notice).toContainText(REPORTING_CURRENCY);
	// Naming the month is the point — a trend is read bar against bar, so a
	// whole-series count would not say which step not to trust.
	await expect(notice).toContainText('2026-02');
	await expect(notice).not.toContainText('2026-01');

	await expect(page.locator('.trend-bar.partial')).toHaveCount(1);
	await expect(page.locator('.trend-value.partial-amount')).toHaveCount(1);

	await expect(page.getByTestId(VENDOR_NOTICE)).toHaveCount(0);
	await expect(page.getByTestId(AGING_NOTICE)).toHaveCount(0);
});
