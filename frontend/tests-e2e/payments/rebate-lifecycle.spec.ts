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
 *  2. **A rebate carries no currency on the wire.** `card_rebates` has no
 *     currency column and `RebateResponse` reports none; the envelope's
 *     `currency` + `excluded_rebate_count` only prove the row set is
 *     homogeneous when the count is zero. A mixed list therefore renders bare
 *     figures and says why, rather than stamping a symbol on a figure that is
 *     not in it.
 *
 * The rebate list is driven through `page.route()` — a real response the page
 * parses — because manufacturing a settled card rebate in each lifecycle state
 * in the shared e2e tenant would pin these assertions to whichever rows that
 * tenant happens to hold. The ROLE gate is asserted against the REAL backend at
 * the bottom of the file, where a mock would prove nothing.
 */

const PENDING_ID = '44444444-4444-4444-4444-444444444444';
const CARD_ID = '55555555-5555-5555-5555-555555555555';

function rebate(status: string, id = PENDING_ID, amount: string | number = '125.50') {
	return {
		id,
		virtual_card_id: CARD_ID,
		amount,
		rate: 0.0125,
		status,
		period: '2026-06',
		created_at: '2026-06-01T00:00:00Z'
	};
}

function listBody(items: ReturnType<typeof rebate>[], excluded = 0) {
	return {
		items,
		total: '125.50',
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
	await page.route('**/api/cards/rebates', async (route) => {
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

	test('a mixed-currency list shows bare figures and says why, rather than a symbol that may be wrong', async ({
		page
	}) => {
		await mockRebates(page, listBody([rebate('pending')], 1));
		await openCards(page);

		await expect(page.getByTestId('rebate-mixed-currency')).toContainText(
			"card's own currency"
		);
		// No currency code claimed on the row — just the exact figure the API sent.
		await expect(page.getByTestId('rebate-amount-bare')).toHaveText('125.50');
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
