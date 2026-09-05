import type { Page } from '@playwright/test';

import { API_BASE, authedTenantHeaders, expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * /bank-reconciliation — the Statements tab's search box.
 *
 * The tab shipped with NO search at all, on purpose: the endpoint offered only
 * an EXACT `account_identifier` match, so a free-text box over it would have
 * returned nothing for a partial term, and a chip set built from page one
 * would have omitted every account further down. Both are the "filter that
 * quietly hides rows" class `frontend/CLAUDE.md` § Search forbids.
 *
 * This spec pins the server-side `search` leg that replaced that gap and the
 * three properties that make it honest:
 *
 *  1. the term goes to the SERVER (`?search=`), so a match on page 2 is found;
 *  2. it is debounced and URL-backed on its OWN key (`statement_search`), kept
 *     apart from the Outstanding tab's `search` — the two tabs query different
 *     endpoints over different columns;
 *  3. "nothing matched" is rendered as a SEARCH empty state, never as the
 *     first-run "no statements imported yet" onboarding block (which would
 *     offer to import a file nobody asked to import).
 *
 * Statements are imported through the real API (a CSV import moves no money)
 * and torn down afterwards, exactly as `bank-reconciliation.spec.ts` does.
 */

const LIST_PATH = '/api/bank-reconciliation';

/** Unique per run, so the import-idempotency slot never collides across runs. */
function uniqueTag(): string {
	return `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

/** A debit far outside the matcher's window and at an amount no seeded payment
 *  carries — the row exists to be listed, never to reconcile. */
function unmatchableCsv(marker: string): string {
	return [
		'Date,Amount,Description,Reference,Counterparty',
		`2019-03-07,-13579.24,${marker},REF-${marker},E2E Recon Counterparty`
	].join('\n');
}

async function importStatement(page: Page, account: string, marker: string) {
	const headers = await authedTenantHeaders(page);
	return page.request.post(`${API_BASE}${LIST_PATH}/upload`, {
		headers,
		multipart: {
			file: {
				name: 'statement.csv',
				mimeType: 'text/csv',
				buffer: Buffer.from(unmatchableCsv(marker))
			},
			account_identifier: account,
			period_start: '2019-03-01',
			period_end: '2019-03-31',
			currency: 'USD'
		}
	});
}

async function deleteStatement(page: Page, id: string) {
	await page.request.delete(`${API_BASE}${LIST_PATH}/${id}`, {
		headers: await authedTenantHeaders(page)
	});
}

/** Only the paginated statement LIST — not `/outstanding`, not a detail read. */
function isListRequest(url: URL): boolean {
	return url.pathname === LIST_PATH;
}

test.describe('/bank-reconciliation — Statements search', () => {
	test('a partial term is sent to the server and narrows the list', async ({ page }) => {
		const tag = uniqueTag();
		const needleAccount = `E2E-Operating-${tag}`;
		const otherAccount = `E2E-Payroll-${tag}`;
		const ids: string[] = [];
		try {
			await page.goto('/bank-reconciliation?tab=statements');
			await page.waitForLoadState('networkidle');

			for (const [account, marker] of [
				[needleAccount, `op-${tag}`],
				[otherAccount, `pr-${tag}`]
			]) {
				const resp = await importStatement(page, account, marker);
				expect(resp.status()).toBe(201);
				ids.push(((await resp.json()) as { id: string }).id);
			}

			await page.reload();
			await page.waitForLoadState('networkidle');

			const needleRow = page.getByRole('button', {
				name: new RegExp(`Open bank statement ${needleAccount}`)
			});
			const otherRow = page.getByRole('button', {
				name: new RegExp(`Open bank statement ${otherAccount}`)
			});
			await expect(needleRow).toBeVisible({ timeout: 10_000 });
			await expect(otherRow).toBeVisible();

			// The term must reach the SERVER — a client-side filter could never
			// see a statement living past the loaded page.
			const listResponse = page.waitForResponse(
				(r) =>
					isListRequest(new URL(r.url())) &&
					new URL(r.url()).searchParams.get('search') === needleAccount
			);
			await page.getByLabel('Search bank statements').fill(needleAccount);
			await listResponse;

			await expect(needleRow).toBeVisible();
			await expect(otherRow).toHaveCount(0);

			// …and the footer's whole-set claim narrows with the rows, rather than
			// heading a filtered table with the unfiltered total.
			await expect(page.getByText('Showing all 1 statement')).toBeVisible();

			// URL-backed on its own key.
			await expect(page).toHaveURL(new RegExp(`statement_search=${needleAccount}`));

			// A reload restores the filter from the URL — the term, the rows and
			// the footer all come back together.
			await page.reload();
			await page.waitForLoadState('networkidle');
			await expect(page.getByLabel('Search bank statements')).toHaveValue(needleAccount);
			await expect(needleRow).toBeVisible({ timeout: 10_000 });
			await expect(otherRow).toHaveCount(0);
		} finally {
			for (const id of ids) await deleteStatement(page, id);
		}
	});

	test('typing is debounced into exactly one request for the final term', async ({ page }) => {
		// The list is STUBBED here: this test measures when requests are issued,
		// not what comes back, and the shape mirrors the canonical #168 guard in
		// `tests-e2e/reactivity/search-debounce-race.spec.ts`. It is kept local
		// rather than added to that table because this page holds TWO search
		// boxes on two URL keys, which that table's one-box-per-route shape
		// cannot express.
		const searchTerms: string[] = [];
		await page.route(`**${LIST_PATH}?*`, async (route) => {
			const url = new URL(route.request().url());
			if (!isListRequest(url)) {
				await route.continue();
				return;
			}
			searchTerms.push(url.searchParams.get('search') ?? '');
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 })
			});
		});

		await page.goto('/bank-reconciliation?tab=statements');
		const box = page.getByLabel('Search bank statements');
		await expect(box).toBeVisible();

		// Let the mount fetch(es) fire and settle before measuring.
		await page.waitForTimeout(400);
		searchTerms.length = 0;

		// One keystroke at a time, well inside the 300ms window. `fill()` writes
		// one state change and would pass whether or not the loader depends on
		// the term reactively — this page carries the `appliedStatementSearch`
		// guard, so an untracked-read slip shows up as one request PER keystroke
		// and no debounced one, which only typing can distinguish.
		await box.pressSequentially('acme', { delay: 30 });

		// Comfortably under 300ms since the last keystroke: nothing yet.
		await page.waitForTimeout(150);
		expect(searchTerms, 'no immediate un-debounced request per keystroke').toEqual([]);

		// Past the debounce: exactly one coalesced request, for the FINAL value
		// — not one each for "a", "ac", "acm", "acme".
		await page.waitForTimeout(300);
		expect(searchTerms, 'exactly one debounced request for the final term').toEqual(['acme']);
	});

	test('a term matching nothing shows the search empty state, not the onboarding block', async ({
		page
	}) => {
		const tag = uniqueTag();
		const account = `E2E-Empty-${tag}`;
		let id: string | null = null;
		try {
			await page.goto('/bank-reconciliation?tab=statements');
			await page.waitForLoadState('networkidle');

			const resp = await importStatement(page, account, `em-${tag}`);
			expect(resp.status()).toBe(201);
			id = ((await resp.json()) as { id: string }).id;

			const noSuchTerm = `no-such-account-${tag}`;
			const listResponse = page.waitForResponse(
				(r) =>
					isListRequest(new URL(r.url())) &&
					new URL(r.url()).searchParams.get('search') === noSuchTerm
			);
			await page.getByLabel('Search bank statements').fill(noSuchTerm);
			await listResponse;

			// The table's own empty row says the SEARCH matched nothing…
			await expect(page.getByTestId('table-empty')).toHaveText(
				'No statements match your search.'
			);
			// …and the first-run onboarding block — which offers to import a file
			// — is deliberately NOT the answer to "your search matched nothing".
			await expect(page.getByTestId('statements-empty')).toHaveCount(0);
			// Nothing claims a whole-set total above an empty table.
			await expect(page.getByText(/Showing all/)).toHaveCount(0);

			// Clearing the box re-fires WITHOUT the term and the row comes back —
			// a visual-only clear would strand the user on an empty table.
			const clearedResponse = page.waitForResponse(
				(r) => isListRequest(new URL(r.url())) && !new URL(r.url()).searchParams.has('search')
			);
			await page.getByLabel('Search bank statements').fill('');
			await clearedResponse;
			await expect(
				page.getByRole('button', { name: new RegExp(`Open bank statement ${account}`) })
			).toBeVisible({ timeout: 10_000 });
			await expect(page).not.toHaveURL(/statement_search=/);
		} finally {
			if (id) await deleteStatement(page, id);
		}
	});

	test('the two tabs keep separate terms on separate URL keys', async ({ page }) => {
		const tag = uniqueTag();
		const account = `E2E-Tabs-${tag}`;
		let id: string | null = null;
		try {
			await page.goto('/bank-reconciliation?tab=statements');
			await page.waitForLoadState('networkidle');

			const resp = await importStatement(page, account, `tb-${tag}`);
			expect(resp.status()).toBe(201);
			id = ((await resp.json()) as { id: string }).id;

			const listResponse = page.waitForResponse(
				(r) =>
					isListRequest(new URL(r.url())) &&
					new URL(r.url()).searchParams.get('search') === account
			);
			await page.getByLabel('Search bank statements').fill(account);
			await listResponse;
			await expect(page).toHaveURL(new RegExp(`statement_search=${account}`));

			// Switching to Outstanding must not carry an ACCOUNT term into a
			// filter that searches vendors and references — its box is empty and
			// its own URL key is untouched.
			await page.getByRole('tab', { name: 'Outstanding' }).click();
			await expect(page.getByLabel('Filter outstanding reconciliation items')).toHaveValue('');
			await expect(page).not.toHaveURL(/[?&]search=/);
			// …and the statements term survives the switch.
			await expect(page).toHaveURL(new RegExp(`statement_search=${account}`));

			// The reverse direction too: the Outstanding term stays on `search=`.
			const outstandingResponse = page.waitForResponse(
				(r) =>
					new URL(r.url()).pathname === `${LIST_PATH}/outstanding` &&
					new URL(r.url()).searchParams.get('search') === 'E2E Recon Counterparty'
			);
			await page
				.getByLabel('Filter outstanding reconciliation items')
				.fill('E2E Recon Counterparty');
			await outstandingResponse;
			await expect(page).toHaveURL(/[?&]search=E2E\+Recon\+Counterparty/);
			await expect(page).toHaveURL(new RegExp(`statement_search=${account}`));
		} finally {
			if (id) await deleteStatement(page, id);
		}
	});
});

test.describe('/bank-reconciliation — Statements search (clerk)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk can search — read is all four roles', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/bank-reconciliation?tab=statements');
		await page.waitForLoadState('networkidle');

		const box = page.getByLabel('Search bank statements');
		await expect(box).toBeVisible();

		const listResponse = page.waitForResponse(
			(r) =>
				isListRequest(new URL(r.url())) &&
				new URL(r.url()).searchParams.get('search') === 'clerk-can-read'
		);
		await box.fill('clerk-can-read');
		const resp = await listResponse;
		expect(resp.status()).toBe(200);
	});
});
