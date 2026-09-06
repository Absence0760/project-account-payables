import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	test
} from '../fixtures/helpers';
import { expectNoA11yViolations } from '../a11y/axe-helper';

/**
 * /payments — the Cards tab's card-rebate lifecycle.
 *
 * `CardRebate.status` runs `pending` → `confirmed` → `paid_out`, and nothing
 * advances it automatically: Lithic/Nium confirm and pay rebates on a periodic
 * statement rather than on a webhook we ingest, so the card-settlement handler
 * can only ever create a rebate at `pending`. `POST /api/cards/rebates/{id}/
 * confirm` and `.../mark-paid` exist for AP to RECORD that out-of-band
 * confirmation and payout — and both shipped API-only, so the Cards tab
 * rendered a muted "+{amount} pending confirmation" hint for a status the
 * product gave no way to advance. The pending bucket could only grow.
 *
 * Two things these specs pin that are easy to get wrong:
 *
 *  1. **Neither transition moves money.** They record what the processor
 *     already did. "Mark paid out" is otherwise very easy to read as a button
 *     that pays someone, so the dialog says so before it can be pressed.
 *  2. **A rebate's currency comes from the card that earned it.**
 *     `card_rebates` has no currency column, so `RebateResponse.currency` is
 *     resolved server-side through the `virtual_cards` join. Each ROW is
 *     therefore labelled with its own code — a mixed-currency programme renders
 *     correctly rather than as bare figures. `excluded_rebate_count` survives
 *     with a narrower job: the envelope's `total_amount` is a SINGLE-currency
 *     figure, and it says how many listed rows are not in it.
 *  3. **The list is a page.** `total` is the row COUNT (canonical envelope) and
 *     `total_amount` is the money over the WHOLE filtered set — never a sum of
 *     the rows on screen, which is the defect family `decisions.md` §79/§82
 *     records.
 *
 * The rebate list is driven through `page.route()` — a real response the page
 * parses — because manufacturing a settled card rebate in each lifecycle state
 * in the shared e2e tenant would pin these assertions to whichever rows that
 * tenant happens to hold. The ROLE gate is asserted against the REAL backend at
 * the bottom of the file, where a mock would prove nothing.
 */

const PENDING_ID = '44444444-4444-4444-4444-444444444444';
const CARD_ID = '55555555-5555-5555-5555-555555555555';

/** The rebate LIST route only — never its `/{id}/confirm` siblings. */
const REBATE_LIST = (url: URL) => url.pathname === '/api/cards/rebates';

function rebate(
	status: string,
	id = PENDING_ID,
	amount: string | number = '125.50',
	currency = 'USD'
) {
	return {
		id,
		virtual_card_id: CARD_ID,
		amount,
		rate: 0.0125,
		currency,
		status,
		period: '2026-06',
		created_at: '2026-06-01T00:00:00Z'
	};
}

function listBody(
	items: ReturnType<typeof rebate>[],
	{ excluded = 0, total = items.length, totalAmount = '125.50' } = {}
) {
	return {
		items,
		// Row COUNT of the whole filtered set, not the money — `total_amount` is
		// the money, and it spans that same whole set.
		total,
		page: 1,
		page_size: 20,
		total_amount: totalAmount,
		currency: 'USD',
		excluded_rebate_count: excluded
	};
}

/**
 * Pin `GET /api/cards/rebates` to a body the test can SWAP.
 *
 * A swap rather than a per-call script: the page refetches the list after a
 * successful transition, and the cards tab's own load effect may fetch more
 * than once, so indexing responses by call count would make the assertions
 * depend on how many times the page happened to read. The returned setter is
 * called from the transition's route handler — the same moment the server would
 * have advanced the row.
 */
async function mockRebates(
	page: import('@playwright/test').Page,
	initial: ReturnType<typeof listBody>
): Promise<(next: ReturnType<typeof listBody>) => void> {
	let body = initial;
	// A PATHNAME predicate, not a glob: the client now sends `?page=&page_size=`,
	// so the old exact-path glob would silently stop matching (letting the real
	// endpoint answer and making every assertion below depend on the shared
	// tenant's own rows) — and a `**` glob loose enough to catch the query string
	// would also swallow `/rebates/{id}/confirm`.
	await page.route(REBATE_LIST, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(body)
		});
	});
	return (next) => {
		body = next;
	};
}

async function openCards(page: import('@playwright/test').Page) {
	// `?tab=` is URL-backed state on this page, so the tab can be deep-linked —
	// no click needed, and no ambiguity with the "Cards" section heading.
	await page.goto('/payments?tab=cards');
}

// Located by ACCESSIBLE NAME, which is each row action's `aria-label` — a
// per-row name so a screen reader hears which rebate the button acts on. WCAG
// 2.5.3 (Label in Name) is why the visible label is a PREFIX of it ("Mark paid
// out — rebate 44444444"), which is also why matching on the visible text works.
const confirmAction = (page: import('@playwright/test').Page) =>
	page.getByRole('button', { name: 'Confirm — rebate' });

const markPaidAction = (page: import('@playwright/test').Page) =>
	page.getByRole('button', { name: 'Mark paid out — rebate' });

test.describe('/payments — card-rebate lifecycle', () => {
	test('a pending rebate can be advanced to confirmed, and the row moves on', async ({ page }) => {
		const setRebates = await mockRebates(page, listBody([rebate('pending')]));
		let posted = 0;
		await page.route(`**/api/cards/rebates/${PENDING_ID}/confirm`, async (route) => {
			posted += 1;
			// Advance the list exactly when the server would have.
			setRebates(listBody([rebate('confirmed')]));
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(rebate('confirmed'))
			});
		});

		await openCards(page);

		// The amount renders through the shared money formatter with the
		// currency the envelope proves the row is in.
		await expect(page.getByTestId('rebate-amount')).toContainText('125.50');
		await confirmAction(page).click();

		// Confirm-then-act: the dialog names the figure and states plainly that
		// this records a processor statement rather than paying anyone.
		await expect(page.getByTestId('rebate-dialog-amount')).toContainText('125.50');
		const warning = page.getByTestId('rebate-warning');
		await expect(warning).toContainText('moves no money');
		await expect(warning).toContainText('nothing is paid to anyone');

		await page.getByTestId('rebate-confirm').click();

		await expect.poll(() => posted).toBe(1);
		// The row advanced: the next step is now the only one offered.
		await expect(markPaidAction(page)).toBeVisible();
		await expect(confirmAction(page)).toHaveCount(0);
	});

	test('a confirmed rebate can be advanced to paid out, and the copy still refuses to claim a payment', async ({
		page
	}) => {
		const setRebates = await mockRebates(page, listBody([rebate('confirmed')]));
		let posted = 0;
		await page.route(`**/api/cards/rebates/${PENDING_ID}/mark-paid`, async (route) => {
			posted += 1;
			setRebates(listBody([rebate('paid_out')]));
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(rebate('paid_out'))
			});
		});

		await openCards(page);
		await markPaidAction(page).click();

		// The most misreadable button in the feature: it records a payout that
		// already landed at the processor, it does not make one.
		const warning = page.getByTestId('rebate-warning');
		await expect(warning).toContainText('already landed');
		await expect(warning).toContainText('moves no money');

		await page.getByTestId('rebate-confirm').click();

		await expect.poll(() => posted).toBe(1);
		// `paid_out` is terminal — no skip, no reversal, so no action at all.
		await expect(markPaidAction(page)).toHaveCount(0);
		await expect(confirmAction(page)).toHaveCount(0);
	});

	test('a terminal rebate is offered no transition rather than one that can only 409', async ({
		page
	}) => {
		await mockRebates(page, listBody([rebate('paid_out')]));
		await openCards(page);

		await expect(page.getByTestId('rebate-amount')).toBeVisible();
		await expect(confirmAction(page)).toHaveCount(0);
		await expect(markPaidAction(page)).toHaveCount(0);
	});

	test('an out-of-order transition surfaces the backend refusal, persistently', async ({
		page
	}) => {
		// The row went stale under a concurrent update: the UI offered `confirm`
		// off a `pending` row that another user has already confirmed. The
		// backend is the authority and its 409 detail is the actionable half of
		// the refusal, so it stays on screen instead of fading in a toast.
		await mockRebates(page, listBody([rebate('pending')]));
		await page.route(`**/api/cards/rebates/${PENDING_ID}/confirm`, (r) =>
			r.fulfill({
				status: 409,
				contentType: 'application/json',
				body: JSON.stringify({ detail: "Cannot confirm a rebate in 'confirmed' status" })
			})
		);

		await openCards(page);
		await confirmAction(page).click();
		await page.getByTestId('rebate-confirm').click();

		const error = page.getByTestId('rebate-error');
		await expect(error).toContainText('Cannot confirm a rebate');
		// Still on screen a moment later — a toast would already be gone.
		await expect(error).toBeVisible();
	});

	test('a mixed-currency list labels every row in its own currency, and says what the total leaves out', async ({
		page
	}) => {
		// The follow-up round 22 opened: with no per-row currency the honest
		// rendering was a bare figure with no code. Now each row states its own.
		await mockRebates(
			page,
			listBody(
				[
					rebate('pending', PENDING_ID, '125.50', 'USD'),
					rebate('paid_out', '66666666-6666-6666-6666-666666666666', '90.00', 'EUR')
				],
				{ excluded: 1, totalAmount: '125.50' }
			)
		);
		await openCards(page);

		const amounts = page.getByTestId('rebate-amount');
		await expect(amounts).toHaveCount(2);
		// Both labelled, each under its OWN code — never one stamped on the other.
		await expect(amounts.nth(0)).toContainText('125.50');
		await expect(amounts.nth(1)).toContainText('90.00');
		await expect(amounts.nth(1)).toContainText('€');

		// The advisory's job is now narrower: the total below is single-currency
		// and this row is not in it.
		await expect(page.getByTestId('rebate-mixed-currency')).toContainText(
			'not included in the total'
		);
		await expect(page.getByTestId('rebate-total')).toContainText('125.50');
	});

	test('the money total describes the whole set, not the page on screen', async ({ page }) => {
		// One row loaded out of five, and a total that is the sum of all five.
		// Reducing over the loaded rows would read 125.50 — the exact defect the
		// whole-set rollup guard exists for.
		await mockRebates(
			page,
			listBody([rebate('pending')], { total: 5, totalAmount: '627.50' })
		);
		await openCards(page);

		await expect(page.getByTestId('rebate-amount')).toHaveCount(1);
		await expect(page.getByTestId('rebate-total')).toContainText('627.50');
	});

	test('Load-more counts against the whole set and appends the next page', async ({ page }) => {
		const SECOND_ID = '77777777-7777-7777-7777-777777777777';
		await page.route(REBATE_LIST, async (route) => {
			const page2 = new URL(route.request().url()).searchParams.get('page') === '2';
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(
					page2
						? listBody([rebate('confirmed', SECOND_ID, '75.00')], {
								total: 2,
								totalAmount: '200.50'
							})
						: listBody([rebate('pending')], { total: 2, totalAmount: '200.50' })
				)
			});
		});
		await openCards(page);

		// By TESTID, not accessible name: the Cards table beneath this one has a
		// Load-more of its own, and a name-based match would be ambiguous.
		const loadMore = page.getByTestId('load-more-rebates');
		await expect(loadMore).toHaveText(/1 of 2/);
		await loadMore.click();

		await expect(page.getByTestId('rebate-amount')).toHaveCount(2);
		// Exhausted — the control is replaced by the end-of-list marker.
		await expect(page.getByTestId('load-more-rebates')).toHaveCount(0);
	});

	test('a role without the rebate role gate sees the rebates but no transition', async ({
		page,
		tenantClerk
	}) => {
		await signInAndWait(page, tenantClerk);
		await mockRebates(page, listBody([rebate('pending')]));
		await openCards(page);

		// Wait for the mocked row before asserting an absence.
		await expect(page.getByTestId('rebate-amount')).toBeVisible();
		await expect(confirmAction(page)).toHaveCount(0);
		await expect(markPaidAction(page)).toHaveCount(0);
	});
});

test.describe('/payments — the rebate lifecycle is accessible', () => {
	// The a11y suite's route list visits `/payments` on its default (Queue) tab,
	// so neither the Rebates table nor its dialog is covered there. Both ship
	// new structure — a second `<h2>` section under the page `<h1>`, a data
	// table, tinted status badges, and a modal — so they get their own axe pass
	// here rather than being the one surface nothing scans.
	test('the Cards tab, and the open transition dialog, are axe-clean', async ({ page }) => {
		await mockRebates(page, listBody([rebate('pending')]));
		await openCards(page);
		await expect(page.getByTestId('rebate-amount')).toBeVisible();
		await expectNoA11yViolations(page);

		await confirmAction(page).click();
		await expect(page.getByTestId('rebate-warning')).toBeVisible();
		await expectNoA11yViolations(page);
	});
});

test.describe('/payments — the rebate lifecycle is gated server-side too', () => {
	// The UI gate is a courtesy; these assert the REAL backend refuses a clerk,
	// so hiding the control is never the only lock. All three rebate routes are
	// `require_roles(admin, ap_manager, cfo)`.
	const UNKNOWN = '00000000-0000-0000-0000-000000000000';

	test('clerk gets 403 listing rebates', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const resp = await page.request.get(`${API_BASE}/api/cards/rebates`, {
			headers: await authedTenantHeaders(page)
		});
		expect(resp.status()).toBe(403);
	});

	test('clerk gets 403 confirming a rebate', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const resp = await page.request.post(`${API_BASE}/api/cards/rebates/${UNKNOWN}/confirm`, {
			headers: await authedTenantHeaders(page),
			data: {}
		});
		expect(resp.status()).toBe(403);
	});

	test('clerk gets 403 marking a rebate paid out', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const resp = await page.request.post(`${API_BASE}/api/cards/rebates/${UNKNOWN}/mark-paid`, {
			headers: await authedTenantHeaders(page),
			data: {}
		});
		expect(resp.status()).toBe(403);
	});
});
