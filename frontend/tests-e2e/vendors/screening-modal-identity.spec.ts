import {
	API_BASE,
	authedTenantHeaders,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';
import type { Page, Route } from '@playwright/test';

/**
 * /vendors/screening — the review modal must answer about the vendor it names.
 *
 * The modal's screening-history fetch was the one request on this page with no
 * request-identity guard, while the list and the counts had one each. Opening
 * vendor A, closing it and opening vendor B left A's response in flight; it
 * resolved into `history` afterwards, so B's modal rendered A's sanctions
 * timeline — with Block/Unblock, bound to `selected`, acting on B. That is a
 * reviewer reading one vendor's evidence and blocking another vendor's
 * payments.
 *
 * The race is driven with CONTROLLED RESPONSES, never a sleep: vendor A's
 * `GET /api/vendors/{id}/screening-history` is parked on a promise this spec
 * resolves by hand, so the ordering is deterministic — A issued first, B
 * issued and resolved second, A released last.
 *
 * Second case: a FAILED history fetch must say "we could not look", not "there
 * is nothing" — the empty message is a claim that the vendor has never been
 * screened, sitting directly above the control whose decision rests on the
 * timeline (`/exceptions` states the rule verbatim).
 */

let H: Record<string, string>;
let SLUG: string;

function slugFromPage(page: Page): string {
	return new URL(page.url()).hostname.split('.')[0];
}

async function createVendor(page: Page, name: string): Promise<{ id: string }> {
	const resp = await page.request.post(`${API_BASE}/api/vendors`, {
		headers: H,
		data: { name }
	});
	expect(resp.status(), `create vendor ${name}`).toBe(201);
	return (await resp.json()) as { id: string };
}

/** Flag a vendor onto the review queue with one history row carrying a
 *  distinctive matched-list name, so whose timeline is on screen is provable. */
function flagForReview(vendorId: string, matchedList: string): void {
	tenantPsql(
		`UPDATE vendors SET screening_status='match', last_screened_at=now(), ` +
			`risk_level='high', risk_score=90 WHERE id='${vendorId}'`,
		SLUG
	);
	tenantPsql(
		`INSERT INTO sanctions_checks ` +
			`(id, vendor_id, organization_id, provider, check_type, result, ` +
			`risk_score, matched_list, checked_at) ` +
			`SELECT gen_random_uuid(), v.id, v.organization_id, 'mock', 'manual', ` +
			`'match', 90, '${matchedList}', now() ` +
			`FROM vendors v WHERE v.id='${vendorId}'`,
		SLUG
	);
}

function deleteVendorCascade(vendorId: string): void {
	try {
		tenantPsql(`DELETE FROM sanctions_checks WHERE vendor_id='${vendorId}'`, SLUG);
		tenantPsql(`DELETE FROM vendors WHERE id='${vendorId}'`, SLUG);
	} catch {
		/* best-effort */
	}
}

/** The vendor id a screening-history request is for, or null if it isn't one. */
function historyVendorId(url: string): string | null {
	const m = new URL(url).pathname.match(/^\/api\/vendors\/([^/]+)\/screening-history$/);
	return m ? m[1] : null;
}

test.describe('/vendors/screening modal identity', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendors');
		SLUG = slugFromPage(page);
		H = await authedTenantHeaders(page, SLUG);
	});

	test("a late history response cannot land in a second vendor's modal", async ({ page }) => {
		const stamp = Date.now();
		const nameA = `Screen-Race A ${stamp}`;
		const nameB = `Screen-Race B ${stamp}`;
		const a = await createVendor(page, nameA);
		const b = await createVendor(page, nameB);
		try {
			flagForReview(a.id, 'RACE_LIST_ALPHA');
			flagForReview(b.id, 'RACE_LIST_BRAVO');

			// Park vendor A's history response on a promise we release by hand.
			// No timer anywhere: the ordering is a real readiness gate.
			let releaseA!: () => void;
			const heldA = new Promise<void>((resolve) => {
				releaseA = resolve;
			});
			let aRequested = false;
			await page.route('**/api/vendors/**', async (route: Route) => {
				const vendorId = historyVendorId(route.request().url());
				if (route.request().method() !== 'GET' || vendorId !== a.id) {
					await route.continue();
					return;
				}
				aRequested = true;
				await heldA;
				await route.continue();
			});

			await page.goto('/vendors/screening');
			// Narrow the queue to this run's two vendors — the shared e2e tenant
			// can hold flagged vendors from other specs, and the queue pages at 20.
			await page
				.getByRole('textbox', { name: 'Search screening review queue' })
				.fill(String(stamp));

			const rowA = page.locator('table tbody tr', { hasText: nameA });
			const rowB = page.locator('table tbody tr', { hasText: nameB });
			await expect(rowA).toBeVisible({ timeout: 15_000 });
			await expect(rowB).toBeVisible();

			const modal = page.getByRole('dialog', { name: 'Vendor screening review' });

			// 1. Open A — its history request is issued and then held.
			await rowA.locator('.row-link').click();
			await expect(modal).toBeVisible();
			await expect(page.getByTestId('screening-history-loading')).toBeVisible();
			await expect.poll(() => aRequested).toBe(true);

			// 2. Close A and open B. B's history resolves normally.
			await modal.getByRole('button', { name: 'Close' }).click();
			await expect(modal).toBeHidden();
			await rowB.locator('.row-link').click();
			await expect(modal).toBeVisible();
			await expect(modal.getByRole('heading', { name: nameB })).toBeVisible();
			await expect(page.getByTestId('screening-history')).toContainText('RACE_LIST_BRAVO', {
				timeout: 15_000
			});

			// 3. Release A's response LAST, and wait for it to actually arrive —
			//    a real network event, not a timer.
			const aLanded = page.waitForResponse(
				(r) => historyVendorId(r.url()) === a.id,
				{ timeout: 15_000 }
			);
			releaseA();
			await aLanded;

			// Act from B's modal. The block round-trip is strictly ordered AFTER
			// A's response reached the page, so anything A's handler was going to
			// write has already been written by the time these assertions run.
			await modal.getByRole('button', { name: 'Block payments' }).click();
			await expect(modal.getByRole('button', { name: 'Unblock payments' })).toBeVisible({
				timeout: 15_000
			});

			// The timeline is still B's, and the actions were bound to B.
			await expect(page.getByTestId('screening-actions')).toHaveAttribute('data-vendor-id', b.id);
			await expect(page.getByTestId('screening-history')).toContainText('RACE_LIST_BRAVO');
			await expect(page.getByTestId('screening-history')).not.toContainText('RACE_LIST_ALPHA');

			const readB = await page.request.get(`${API_BASE}/api/vendors/${b.id}`, { headers: H });
			expect(readB.status()).toBe(200);
			expect((await readB.json()).payments_blocked, 'B is the vendor that got blocked').toBe(true);
			const readA = await page.request.get(`${API_BASE}/api/vendors/${a.id}`, { headers: H });
			expect(readA.status()).toBe(200);
			expect((await readA.json()).payments_blocked, 'A was never touched').toBe(false);
		} finally {
			await page.unroute('**/api/vendors/**').catch(() => {});
			deleteVendorCascade(a.id);
			deleteVendorCascade(b.id);
		}
	});

	test('a FAILED history fetch says "could not load", never "no screening history yet"', async ({
		page
	}) => {
		const name = `Screen-HistErr Co ${Date.now()}`;
		const vendor = await createVendor(page, name);
		try {
			flagForReview(vendor.id, 'ERR_LIST_CHARLIE');

			let attempts = 0;
			await page.route('**/api/vendors/**', async (route: Route) => {
				if (
					route.request().method() !== 'GET' ||
					historyVendorId(route.request().url()) !== vendor.id
				) {
					await route.continue();
					return;
				}
				attempts += 1;
				if (attempts === 1) {
					await route.fulfill({
						status: 500,
						contentType: 'application/json',
						body: JSON.stringify({ detail: 'boom' })
					});
					return;
				}
				await route.continue();
			});

			await page.goto('/vendors/screening');
			await page
				.getByRole('textbox', { name: 'Search screening review queue' })
				.fill(name.split(' ').pop() as string);
			const row = page.locator('table tbody tr', { hasText: name });
			await expect(row).toBeVisible({ timeout: 15_000 });
			await row.locator('.row-link').click();

			const modal = page.getByRole('dialog', { name: 'Vendor screening review' });
			await expect(modal).toBeVisible();

			// "We could not look" outranks "there is nothing".
			const error = page.getByTestId('screening-history-error');
			await expect(error).toBeVisible({ timeout: 15_000 });
			await expect(page.getByTestId('screening-history-empty')).toHaveCount(0);

			// And the state is recoverable in place.
			await error.getByRole('button', { name: 'Retry' }).click();
			await expect(page.getByTestId('screening-history')).toContainText('ERR_LIST_CHARLIE', {
				timeout: 15_000
			});
			await expect(page.getByTestId('screening-history-error')).toHaveCount(0);
			expect(attempts).toBeGreaterThan(1);
		} finally {
			await page.unroute('**/api/vendors/**').catch(() => {});
			deleteVendorCascade(vendor.id);
		}
	});
});
