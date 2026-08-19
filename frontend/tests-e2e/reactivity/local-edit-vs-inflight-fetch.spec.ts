import { expect, test } from '../fixtures/helpers';

/**
 * Regression guard for the second half of `createRequestSequencer`:
 * `supersedeInFlight()`.
 *
 * A list page that edits one row in place — a pause, an approve, a lifted
 * payment block — issues no list request of its own, so the "is there a newer
 * request?" counter never moves. A GET that was ALREADY in flight when the
 * edit landed carries a pre-edit server snapshot, and resolving afterwards it
 * silently reverts the edit: the user watches their own action undo itself,
 * with no error and nothing to retry.
 *
 * `/recurring` is the representative surface — its Pause row action POSTs to
 * the template and then upserts the returned row into the list with no
 * refetch, and its search box issues a real server-side list request, so the
 * two can be interleaved deterministically. The same wiring now guards every
 * other list store and route (see `frontend/CLAUDE.md` § Sequencing list
 * fetches).
 *
 * The second describe covers the OTHER half of the same sequencer — the plain
 * read-vs-read ordering (`canCommit`) — on `/admin/webhooks`' delivery log,
 * which had a status filter and no sequencer at all. The equivalent guards for
 * the other newly-wired surfaces live beside their own areas
 * (`credit-memos/load-sequencing`, `exceptions/load-sequencing`,
 * `goods-receipts/load-sequencing`).
 *
 * Every API surface here is mocked so the interleaving is exact and the specs
 * never depend on seeded tenant data.
 */

const TEMPLATE_ID = '11111111-1111-4111-8111-111111111111';

function template(overrides: { name?: string; status?: 'active' | 'paused' } = {}) {
	return {
		id: TEMPLATE_ID,
		name: overrides.name ?? 'SEQ Guard Template',
		vendor_id: null,
		vendor_name: 'Sequencer Vendor',
		description: null,
		amount: 100,
		currency: 'USD',
		gl_account: null,
		cost_center: null,
		department: null,
		project: null,
		po_number: null,
		payment_terms: null,
		cadence: 'monthly',
		day_of_period: 1,
		start_date: '2026-01-01',
		end_date: null,
		next_run_on: '2026-09-01',
		last_period_key: null,
		last_generated_at: null,
		generated_count: 0,
		status: overrides.status ?? 'active',
		variance_tolerance_pct: null,
		notes: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null
	};
}

function listBody(overrides: { name?: string; status?: 'active' | 'paused' } = {}) {
	return JSON.stringify({
		items: [template(overrides)],
		total: 1,
		page: 1,
		page_size: 20
	});
}

test.describe('a local row edit survives a list fetch that was already in flight', () => {
	test('/recurring: pausing while a search load hangs is not reverted when it lands', async ({
		page
	}) => {
		// Hold the SECOND list request open. The first (mount) load resolves
		// normally so there is a row on screen to act on.
		let listRequests = 0;
		let releaseStale: () => void = () => {};
		const staleGate = new Promise<void>((resolve) => (releaseStale = resolve));

		await page.route('**/api/recurring?*', async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== '/api/recurring') {
				await route.continue();
				return;
			}
			listRequests += 1;
			if (listRequests === 1) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: listBody()
				});
				return;
			}
			await staleGate;
			// The held response carries the PRE-EDIT snapshot — the row still
			// active — under a name that only this response can produce. The
			// server genuinely read the row before the pause landed, so
			// committing this response would revert it; the distinct name makes
			// a revert unmistakable rather than merely absent.
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: listBody({ name: 'SEQ Stale Snapshot' })
			});
		});

		await page.route(`**/api/recurring/${TEMPLATE_ID}/pause`, async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(template({ status: 'paused' }))
			});
		});

		await page.goto('/recurring');

		const row = page.getByRole('row').filter({ hasText: 'SEQ Guard Template' });
		await expect(row).toBeVisible();
		await expect(row.getByText('Active')).toBeVisible();

		// Fire the second list request (server-side `?search=`) and let it hang.
		await page.getByPlaceholder('Search templates...').fill('SEQ');
		await expect
			.poll(() => listRequests, { message: 'the debounced search load was issued' })
			.toBe(2);

		// Pause the template while that load is still out.
		await row.getByRole('button', { name: 'Pause template SEQ Guard Template' }).click();
		await expect(
			row.getByRole('button', { name: 'Resume template SEQ Guard Template' })
		).toBeVisible();
		await expect(row.getByText('Paused')).toBeVisible();

		// Release the stale load and wait for the page to actually receive it —
		// a real signal, not a sleep.
		const staleResponse = page.waitForResponse(
			(r) => r.request().method() === 'GET' && new URL(r.url()).pathname === '/api/recurring'
		);
		releaseStale();
		await staleResponse;
		// One animation frame past the response guarantees the fetch
		// continuation (and any state write it would have made) has run:
		// microtasks drain before a frame paints.
		await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => r())));

		// Without `supersedeInFlight()` the pre-edit snapshot lands last and
		// replaces the list — the row flips back to Active under the stale name.
		await expect(page.getByText('SEQ Stale Snapshot')).toHaveCount(0);
		await expect(row.getByText('Paused')).toBeVisible();
		await expect(
			row.getByRole('button', { name: 'Resume template SEQ Guard Template' })
		).toBeVisible();
	});
});

function delivery(id: string, eventId: string, status: string) {
	return {
		id,
		subscription_id: '22222222-2222-4222-8222-222222222222',
		event_id: eventId,
		event_type: 'invoice.approved',
		status,
		attempt_count: 1,
		response_code: status === 'delivered' ? 200 : 500,
		next_attempt_at: null,
		last_attempt_at: '2026-01-01T00:00:00Z',
		created_at: '2026-01-01T00:00:00Z'
	};
}

test.describe('a stale list response cannot land after a newer one', () => {
	test('/admin/webhooks: a held delivery filter cannot repaint the table under a newer chip', async ({
		page
	}) => {
		await page.route('**/api/webhooks', async (route) => {
			if (route.request().method() !== 'GET') {
				await route.fallback();
				return;
			}
			await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
		});

		let releaseFailed: () => void = () => {};
		const failedGate = new Promise<void>((resolve) => (releaseFailed = resolve));

		// Registered after the subscriptions route so it is matched first for its
		// own URL.
		await page.route('**/api/webhooks/deliveries*', async (route) => {
			const status = new URL(route.request().url()).searchParams.get('status');
			if (status === 'failed') {
				// The earlier request: held until the newer one has landed.
				await failedGate;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify([delivery('d-1', 'evt-STALE-failed', 'failed')])
				});
				return;
			}
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(
					status === 'dead' ? [delivery('d-2', 'evt-LIVE-dead', 'dead')] : []
				)
			});
		});

		await page.goto('/admin/webhooks');

		// Failed (slow, held) then Dead (fast) — the second chip's answer is the
		// one on screen.
		await page.getByRole('button', { name: 'Failed', exact: true }).click();
		await page.getByRole('button', { name: 'Dead', exact: true }).click();
		await expect(page.getByText('evt-LIVE-dead')).toBeVisible();

		// Release the stale response and wait for the page to actually receive it —
		// a real signal, not a sleep.
		const staleResponse = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/webhooks/deliveries' &&
				new URL(r.url()).searchParams.get('status') === 'failed'
		);
		releaseFailed();
		await staleResponse;
		// One animation frame past the response guarantees the fetch continuation
		// (and any state write it would have made) has run.
		await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => r())));

		// The Failed rows must not appear under the Dead chip.
		await expect(page.getByText('evt-STALE-failed')).toHaveCount(0);
		await expect(page.getByText('evt-LIVE-dead')).toBeVisible();
	});
});
