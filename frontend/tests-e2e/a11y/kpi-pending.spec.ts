import type { Locator, Page, Route } from '@playwright/test';

import { expect, test } from '../fixtures/helpers';
import { expectNoA11yViolations } from './axe-helper';

/**
 * Accessibility regression guard for the **KPI "no figure yet" affordance**
 * (`$lib/components/ui/KpiCard.svelte` + `$lib/utils/kpiValue.ts`).
 *
 * The convention this guards, in the order the component applies it:
 *
 *   - A figure that does not exist renders `KPI_NO_FIGURE` (an em dash), never
 *     a coerced `0` — `docs/decisions.md` §34 in a display layer.
 *   - **Whether it is missing because it is still arriving is ANNOUNCED, not
 *     drawn.** A pending card is pixel-identical to an unavailable one; it
 *     differs only in `aria-busy="true"`, an `aria-hidden` dash, and a
 *     screen-reader-only "Loading…" in the dash's place. A placeholder must
 *     never be read out as a value.
 *   - A pending or unavailable card drops its highlight tint, because the tint
 *     is a *verdict* and there is no figure to have a verdict about.
 *   - A genuine computed `0` is a figure: it renders as one, keeps its verdict
 *     tint, and is never announced as pending. The classification is nullish,
 *     not falsy.
 *
 * **Why this spec exists separately from `axe.spec.ts`.** That suite scans
 * routes; this scans *states*. Every assertion below needs the KPI response
 * held or shaped, and none of the three routes reaches the pending state for
 * long enough to be scanned incidentally — a route list would report the page
 * clean while the busy affordance went entirely unchecked. It is the same
 * argument `deemphasised-rows.spec.ts` makes for de-emphasised rows.
 *
 * **Why axe alone would not be enough here.** axe cannot see the interesting
 * part: a dash left inside the accessibility tree while the card claims to be
 * busy is valid ARIA and violates no rule — it just reads "em dash" to a
 * screen-reader user where a number belongs. So each test asserts the
 * *announced* content directly, via Playwright's accessibility-tree snapshot
 * (`locator.ariaSnapshot()`), and runs axe on top of that.
 *
 * The pending state is reached by holding the route's own KPI response on a
 * promise released by hand — the pattern
 * `tests-e2e/admin/api-keys.spec.ts`'s usage-identity test establishes. No
 * sleep, no timeout, no race: the readiness gate is the rendered
 * `data-kpi-state`, and the response is released only once the pending
 * assertions have run.
 */

/** `kpiValue.ts::KPI_NO_FIGURE` — U+2014, the glyph a card shows for no figure. */
const NO_FIGURE = '—';

/** `common.loading` — the screen-reader-only text substituted for the dash. */
const LOADING = 'Loading…';

/**
 * Hold every request matching `pathname` until `release()` is called, then let
 * the REAL backend answer it.
 *
 * Passing the real response through (rather than a fixture) is deliberate for
 * the pending→settled tests: the settled half is then the page's genuine data,
 * so the same test covers "the pending state is clean" and "the state the user
 * actually lands on is clean" without inventing a payload.
 *
 * The glob is broader than the path, so the handler re-checks the pathname and
 * lets anything else through — the guard `tests-e2e/requisitions/search-scope`
 * documents.
 */
function holdEndpoint(
	page: Page,
	pathname: string
): { release: () => void; install: () => Promise<void> } {
	let release!: () => void;
	const released = new Promise<void>((resolve) => {
		release = resolve;
	});
	const install = async () => {
		await page.route(`**${pathname}*`, async (route: Route) => {
			if (new URL(route.request().url()).pathname !== pathname) {
				await route.continue();
				return;
			}
			await released;
			const response = await route.fetch();
			await route.fulfill({ response });
		});
	};
	return { release, install };
}

/** Fulfil `pathname` with a fixed body; everything else passes through. */
async function stubEndpoint(
	page: Page,
	pathname: string,
	body: unknown,
	status = 200
): Promise<void> {
	await page.route(`**${pathname}*`, async (route: Route) => {
		if (new URL(route.request().url()).pathname !== pathname) {
			await route.continue();
			return;
		}
		await route.fulfill({
			status,
			contentType: 'application/json',
			body: JSON.stringify(body)
		});
	});
}

/**
 * Every card in `row` is announcing itself as busy, and none of them is
 * announcing its placeholder as a value.
 */
async function expectRowPending(row: Locator, count: number): Promise<void> {
	const cards = row.locator('.kpi');
	await expect(cards).toHaveCount(count);
	await expect(row.locator('.kpi[data-kpi-state="pending"]')).toHaveCount(count);
	await expect(row.locator('.kpi[aria-busy="true"]')).toHaveCount(count);

	for (let i = 0; i < count; i++) {
		const value = cards.nth(i).locator('.kpi-value');
		// Drawn (the row keeps its shape) but hidden from assistive tech.
		await expect(value).toHaveText(NO_FIGURE);
		await expect(value).toHaveAttribute('aria-hidden', 'true');
		// …and the loading text stands in its place.
		await expect(cards.nth(i).locator('.visually-hidden')).toHaveText(LOADING);
	}

	// The tint is a verdict. There is no figure to have a verdict about, so a
	// pending row is uniformly neutral — this is the assertion that would have
	// caught `/bank-reconciliation` reading "nothing unmatched, no
	// discrepancies" on a fraud control while it was still asking.
	await expect(row.locator('.kpi.highlight-green, .kpi.highlight-red')).toHaveCount(0);

	// The accessibility tree itself — the thing axe cannot judge. The dash is
	// out of it entirely; "Loading…" is in it once per card.
	const announced = await row.ariaSnapshot();
	expect(announced, 'a pending card must not announce its placeholder dash').not.toContain(
		NO_FIGURE
	);
	expect(announced.match(new RegExp(LOADING, 'g')) ?? []).toHaveLength(count);
}

/**
 * No card in `row` is claiming to be busy any more.
 *
 * Deliberately does NOT require every card to hold a figure. A settled row can
 * still carry an `unavailable` card — `/discounts`' capture rate is exactly
 * that on a tenant where no offer has been captured or missed, since a ratio
 * over an empty decided population is not `0%`. What must be true of every
 * card once the response has landed is that it has stopped announcing a load,
 * which is what this asserts; the stubbed-figure tests below pin the
 * `value` state where the data makes it determinate.
 */
async function expectRowSettled(row: Locator, count: number): Promise<void> {
	await expect(row.locator('.kpi')).toHaveCount(count);
	await expect(row.locator('.kpi[data-kpi-state="pending"]')).toHaveCount(0);
	// `aria-busy` is ABSENT once settled, not `"false"` — a card that keeps the
	// attribute keeps telling assistive tech the page is still working.
	await expect(row.locator('.kpi[aria-busy]')).toHaveCount(0);
	await expect(row.locator('.kpi-value[aria-hidden]')).toHaveCount(0);
	await expect(row.locator('.visually-hidden')).toHaveCount(0);

	const announced = await row.ariaSnapshot();
	expect(announced, 'a settled card must not still announce a loading state').not.toContain(
		LOADING
	);
}

/** Every card in `row` holds a computed figure — no dash, pending or not. */
async function expectEveryCardIsAFigure(row: Locator, count: number): Promise<void> {
	await expect(row.locator('.kpi[data-kpi-state="value"]')).toHaveCount(count);
	await expect(row.locator('.kpi-value', { hasText: NO_FIGURE })).toHaveCount(0);
}

test.describe('accessibility — KPI pending affordance (WCAG 4.1.2 / 1.3.1)', () => {
	/**
	 * Retire the route handlers before Playwright tears the test down.
	 *
	 * `holdEndpoint` parks a handler inside `await released` and then inside
	 * `route.fetch()`. A page still settling can leave one there when the last
	 * assertion resolves, and the teardown surfaces it as `route.fetch: Test
	 * ended.` against whichever test owned it — the failure mode
	 * `a11y/target-size.spec.ts` and `a11y/deemphasised-rows.spec.ts` both
	 * document. Every test also releases its own hold in a `finally`, so the
	 * handler is never still blocked when this runs.
	 */
	test.afterEach(async ({ page }) => {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	});

	// --- /discounts -------------------------------------------------------

	test('/discounts KPI row: pending announces a load, settled announces figures', async ({
		page
	}) => {
		const held = holdEndpoint(page, '/api/discounts/dashboard');
		await held.install();

		try {
			await page.goto('/discounts');
			const row = page.locator('.kpi-row').first();
			await expect(row.locator('.kpi').first()).toBeVisible();

			// --- pending ---
			await expectRowPending(row, 5);
			await expectNoA11yViolations(page);

			// --- settled (the page's real dashboard response) ---
			held.release();
			await expect(row.locator('.kpi').first()).toHaveAttribute('data-kpi-state', 'value');
			await expectRowSettled(row, 5);
			await expectNoA11yViolations(page);
		} finally {
			held.release();
		}
	});

	test('/discounts: a computed zero is announced as a zero, never as pending', async ({ page }) => {
		// Every figure genuinely zero — the case the nullish (not falsy) rule in
		// `kpiFigureState` exists for. A tenant with nothing captured and nothing
		// open has five real answers, and each must read as one.
		await stubEndpoint(page, '/api/discounts/dashboard', {
			captured_count: 0,
			captured_amount: 0,
			missed_count: 0,
			missed_amount: 0,
			capture_rate_pct: 0,
			insufficient_data: false,
			open_offer_count: 0,
			projected_savings: 0,
			currency: 'USD',
			unconvertible_offer_count: 0,
			excluded_captured_count: 0,
			excluded_missed_count: 0
		});

		await page.goto('/discounts');
		const row = page.locator('.kpi-row').first();
		await expect(row.locator('.kpi').first()).toHaveAttribute('data-kpi-state', 'value');

		// Not pending, not unavailable — a figure.
		await expectRowSettled(row, 5);
		await expectEveryCardIsAFigure(row, 5);

		// The count card is currency-independent, so it can be pinned exactly.
		const openOffers = row.locator('.kpi', { hasText: 'Open offers' });
		await expect(openOffers.locator('.kpi-value')).toHaveText('0');
		await expect(row.locator('.kpi', { hasText: 'Capture rate' }).locator('.kpi-value')).toHaveText(
			'0%'
		);

		// A zero is a figure, so it KEEPS its verdict tint — the difference from
		// a pending card, which drops it. Without this the two states could be
		// collapsed back together by making the tint conditional on truthiness.
		await expect(row.locator('.kpi.highlight-green, .kpi.highlight-red')).toHaveCount(3);

		// And the zero reaches the accessibility tree as a zero.
		const announced = await row.ariaSnapshot();
		expect(announced).toContain('0 Open offers');

		await expectNoA11yViolations(page);
	});

	test('/discounts: an unavailable figure announces its dash, and is not busy', async ({
		page
	}) => {
		// The third state, and the reason `aria-hidden` on the dash has to be
		// CONDITIONAL: "still arriving" and "did not arrive" draw the same glyph,
		// so hiding it unconditionally would leave a failed load announcing an
		// empty card with no explanation at all.
		await stubEndpoint(page, '/api/discounts/dashboard', { detail: 'boom' }, 500);

		await page.goto('/discounts');
		const row = page.locator('.kpi-row').first();
		await expect(row.locator('.kpi').first()).toHaveAttribute('data-kpi-state', 'unavailable');

		await expect(row.locator('.kpi[data-kpi-state="unavailable"]')).toHaveCount(5);
		await expect(row.locator('.kpi[aria-busy]')).toHaveCount(0);
		await expect(row.locator('.kpi-value[aria-hidden]')).toHaveCount(0);
		await expect(row.locator('.visually-hidden')).toHaveCount(0);
		// No verdict on a figure that never arrived, either.
		await expect(row.locator('.kpi.highlight-green, .kpi.highlight-red')).toHaveCount(0);

		const announced = await row.ariaSnapshot();
		expect(announced, 'an unavailable figure must still announce its dash').toContain(NO_FIGURE);
		expect(announced).not.toContain(LOADING);

		await expectNoA11yViolations(page);
	});

	// --- /bank-reconciliation --------------------------------------------

	test('/bank-reconciliation KPI row: pending announces a load, settled announces figures', async ({
		page
	}) => {
		const held = holdEndpoint(page, '/api/bank-reconciliation/outstanding');
		await held.install();

		try {
			await page.goto('/bank-reconciliation');
			const row = page.locator('.kpi-row').first();
			await expect(row.locator('.kpi').first()).toBeVisible();

			await expectRowPending(row, 3);
			await expectNoA11yViolations(page);

			held.release();
			await expect(row.locator('.kpi').first()).toHaveAttribute('data-kpi-state', 'value');
			await expectRowSettled(row, 3);
			await expectNoA11yViolations(page);
		} finally {
			held.release();
		}
	});

	test('/bank-reconciliation: a zero count is a figure, and the tint returns with it', async ({
		page
	}) => {
		// Nothing uncleared, three debits the bank shows that we cannot account
		// for, no amount discrepancies. Two genuine zeros beside one genuine
		// alarm, so this pins BOTH halves of the tint rule in one render: the
		// verdict is withheld only for a missing figure, never for a zero one.
		await stubEndpoint(page, '/api/bank-reconciliation/outstanding', {
			as_of: '2026-01-15T09:00:00Z',
			older_than_days: 0,
			uncleared_payments: [],
			uncleared_count: 0,
			uncleared_totals: [],
			unmatched_debits: [],
			unmatched_debit_count: 3,
			unmatched_debit_totals: [],
			discrepancies: [],
			discrepancy_count: 0,
			amount_mismatch_net_variances: []
		});

		await page.goto('/bank-reconciliation');
		const row = page.locator('.kpi-row').first();
		await expect(row.locator('.kpi').first()).toHaveAttribute('data-kpi-state', 'value');

		await expectRowSettled(row, 3);
		await expectEveryCardIsAFigure(row, 3);

		const uncleared = row.locator('.kpi', { hasText: 'Uncleared payments' });
		await expect(uncleared.locator('.kpi-value')).toHaveText('0');
		const discrepancies = row.locator('.kpi', { hasText: 'Discrepancies' });
		await expect(discrepancies.locator('.kpi-value')).toHaveText('0');

		// The one card with something to be alarmed about, and only that one.
		const unmatched = row.locator('.kpi', { hasText: 'Unmatched bank debits' });
		await expect(unmatched.locator('.kpi-value')).toHaveText('3');
		await expect(row.locator('.kpi.highlight-red')).toHaveCount(1);

		const announced = await row.ariaSnapshot();
		expect(announced).toContain('0 Uncleared payments');
		expect(announced).toContain('3 Unmatched bank debits');

		await expectNoA11yViolations(page);
	});

	// --- /cfo -------------------------------------------------------------

	test('/cfo: the forecast KPI row is withheld while the forecast is in flight', async ({
		page
	}) => {
		// `/cfo` applies the same rule by a different mechanism: rather than
		// rendering busy cards, it renders no KPI row at all until the response
		// lands. That satisfies §34 — no figure nobody computed is displayed —
		// so what this test pins is the invariant, not the mechanism: while the
		// three analytics requests are in flight, NO card on this row reports a
		// figure. If `/cfo` later adopts `KpiCard`'s `pending` prop, replace the
		// count assertion with `expectRowPending`; the invariant is unchanged.
		const forecast = holdEndpoint(page, '/api/analytics/cashflow_forecast');
		const whatif = holdEndpoint(page, '/api/analytics/cashflow_whatif');
		const position = holdEndpoint(page, '/api/analytics/cash_position');
		await forecast.install();
		await whatif.install();
		await position.install();

		try {
			await page.goto('/cfo');
			await expect(page.getByRole('heading', { name: 'Cash Flow', exact: true })).toBeVisible();
			// The page's own in-flight state, not a guess at timing.
			await expect(page.locator('.workspace > .loading')).toBeVisible();

			await expect(page.getByTestId('forecast-kpi-row')).toHaveCount(0);
			await expectNoA11yViolations(page);

			forecast.release();
			whatif.release();
			position.release();
			await expect(page.getByTestId('forecast-kpi-row')).toBeVisible();
		} finally {
			forecast.release();
			whatif.release();
			position.release();
		}
	});

	test('/cfo: a zeroed forecast is announced as a zero and keeps its verdict tint', async ({
		page
	}) => {
		// Rewrite the REAL payload rather than inventing one — the forecast shape
		// is wide and the assertion is about the KPI cards, not the fixture. Same
		// approach `deemphasised-rows.spec.ts` uses for the credit-memo row.
		await page.route('**/api/analytics/cashflow_forecast*', async (route: Route) => {
			const response = await route.fetch();
			const body = (await response.json()) as {
				totals?: Record<string, unknown>;
			};
			if (body.totals) {
				body.totals.scheduled_amount = '0';
				body.totals.committed_amount = '0';
				body.totals.pending_amount = '0';
			}
			await route.fulfill({ response, json: body });
		});
		await page.route('**/api/analytics/cashflow_whatif*', async (route: Route) => {
			const response = await route.fetch();
			const body = (await response.json()) as {
				scenarios?: { early?: Record<string, unknown> };
			};
			if (body.scenarios?.early) body.scenarios.early.total_discount_captured = '0';
			await route.fulfill({ response, json: body });
		});

		await page.goto('/cfo');
		const row = page.getByTestId('forecast-kpi-row');
		await expect(row).toBeVisible();

		await expectRowSettled(row, 4);
		await expectEveryCardIsAFigure(row, 4);

		// Every card carries a real zero — money, so the currency symbol is the
		// tenant's and only the digit can be pinned.
		const values = row.locator('.kpi-value');
		for (let i = 0; i < 4; i++) {
			await expect(values.nth(i)).toHaveText(/0/);
		}

		// "$0 available if you pay early" is a computed answer, so it keeps the
		// green verdict a pending card would have dropped.
		await expect(row.locator('.kpi.highlight-green')).toHaveCount(1);

		await expectNoA11yViolations(page);
	});
});
