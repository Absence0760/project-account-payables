import { expect, test } from '../fixtures/helpers';
import type { Page, Route } from '@playwright/test';

/**
 * /cfo — Scheduled Reports panel.
 *
 * The report runner (`backend/app/services/scheduled_reports.py`) shipped
 * complete with NO CRUD surface, so a schedule could only be created by
 * hand-written SQL and `list_due_schedules` returned `[]` forever. This spec
 * covers the panel that fixes that, and specifically the four things a
 * happy-path click-through would not reach:
 *
 *  1. Both selects are driven off the LIST RESPONSE, not a hardcoded copy of
 *     the runner's registries — a report type the backend gains must appear
 *     here (humanised) rather than vanish.
 *  2. `enabled: false` + `failure` + a `[retry 5]` streak is the AUTO-DISABLED
 *     state — a schedule that silently stopped emailing — and must be
 *     unmistakable next to one an admin paused on purpose. Its recovery is ONE
 *     `PATCH {enabled: true}` (which also clears the failure), not two calls.
 *  3. `partial` means some recipients DID receive it; `last_run_error` carries
 *     the counts and must be shown (it never carries an address).
 *  4. Recipients are de-duped SERVER-side, so the table must re-render from the
 *     response — showing the list the user typed would show a list that was
 *     never saved.
 *
 * Plus the graceful-degradation contract: the backend routes land in the same
 * PR, but a deployment without them must render one quiet "not available" line
 * — no error state, no toast storm, no broken layout.
 *
 * The API is stubbed with `page.route` (the established pattern — see
 * `tests-e2e/dashboard/error-state.spec.ts`) because every state above needs a
 * run HISTORY no seed produces. Default storage state signs the worker's admin
 * in, so the admin-only mutate controls are present.
 */

const LIST_URL = '**/api/analytics/scheduled-reports**';

type Row = {
	id: string;
	name: string;
	report_type: string;
	cadence: string;
	recipients: string[];
	period_days: number;
	enabled: boolean;
	next_run_at: string;
	last_run_at: string | null;
	last_run_status: 'success' | 'partial' | 'failure' | null;
	last_run_error: string | null;
};

function row(over: Partial<Row> = {}): Row {
	return {
		id: 'sr-1',
		name: 'Weekly aging',
		report_type: 'aging_snapshot',
		cadence: 'weekly',
		recipients: ['cfo@acme.test'],
		period_days: 30,
		enabled: true,
		next_run_at: '2026-09-01T08:00:00Z',
		last_run_at: null,
		last_run_status: null,
		last_run_error: null,
		...over
	};
}

function envelope(rows: Row[], reportTypes?: string[], cadences?: string[]) {
	return {
		schedules: rows,
		report_types: reportTypes ?? [
			'aging_snapshot',
			'cashflow_forecast',
			'expense_register',
			'invoice_register',
			'payment_register',
			'vendor_spend'
		],
		cadences: cadences ?? ['daily', 'monthly', 'weekly']
	};
}

async function json(route: Route, body: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

/** The panel section, which is the scope for every assertion below. */
function panel(page: Page) {
	return page.getByTestId('scheduled-reports');
}

test.describe('/cfo scheduled reports', () => {
	test('drives both selects off the list response, humanising an unknown type', async ({
		page
	}) => {
		// The backend has a report type this build carries no translation for.
		// It must still be listed and rendered — the whole reason the vocabulary
		// is not hardcoded in the frontend.
		const rows = [row({ id: 'sr-x', name: 'Carbon report', report_type: 'carbon_footprint' })];
		await page.route(LIST_URL, async (route) => {
			if (route.request().method() !== 'GET') return route.continue();
			await json(route, envelope(rows, ['aging_snapshot', 'carbon_footprint'], ['weekly']));
		});

		await page.goto('/cfo');
		await expect(panel(page)).toBeVisible();

		// The row's Report cell shows the humanised key, not the raw identifier.
		const rowLocator = panel(page).locator('tbody tr', { hasText: 'Carbon report' });
		await expect(rowLocator).toContainText('Carbon footprint');
		await expect(rowLocator).not.toContainText('carbon_footprint');

		// The create form offers EXACTLY the response's vocabularies — two report
		// types and one cadence, not the six/three this build knows about.
		await panel(page).getByTestId('new-schedule').click();
		const modal = page.getByRole('dialog', { name: 'New scheduled report' });
		await expect(modal).toBeVisible();
		await expect(modal.getByLabel('Report').locator('option')).toHaveCount(2);
		await expect(modal.getByLabel('Report').locator('option')).toHaveText([
			'AP aging snapshot',
			'Carbon footprint'
		]);
		await expect(modal.getByLabel('Cadence').locator('option')).toHaveCount(1);
	});

	test('auto-disabled reads differently from paused, and one call recovers it', async ({
		page
	}) => {
		const auto = row({
			id: 'sr-auto',
			name: 'Auto stopped',
			enabled: false,
			last_run_at: '2026-08-10T08:00:00Z',
			last_run_status: 'failure',
			last_run_error: '[retry 5] SMTPException: connection refused'
		});
		const paused = row({ id: 'sr-paused', name: 'Hand paused', enabled: false });

		let recovered = false;
		const patches: Array<{ url: string; body: unknown }> = [];
		await page.route(LIST_URL, async (route) => {
			const req = route.request();
			if (req.method() === 'GET') {
				const list = recovered
					? [
							{ ...auto, enabled: true, last_run_status: null, last_run_error: null },
							paused
						]
					: [auto, paused];
				return json(route, envelope(list));
			}
			if (req.method() === 'PATCH') {
				patches.push({ url: req.url(), body: req.postDataJSON() });
				recovered = true;
				return json(route, {
					...auto,
					enabled: true,
					last_run_status: null,
					last_run_error: null
				});
			}
			return route.continue();
		});

		await page.goto('/cfo');
		await expect(panel(page)).toBeVisible();

		const autoRow = panel(page).locator('tr[data-health="auto_disabled"]');
		const pausedRow = panel(page).locator('tr[data-health="disabled"]');
		await expect(autoRow).toHaveCount(1);
		await expect(pausedRow).toHaveCount(1);

		// Different word AND different tone — a paused schedule uses the flat,
		// signal-free chip; one that stopped by itself is the danger tone.
		await expect(autoRow.locator('.badge')).toHaveText('Auto-disabled');
		await expect(autoRow.locator('.badge.danger')).toHaveCount(1);
		await expect(pausedRow.locator('.badge')).toHaveText('Paused');
		await expect(pausedRow.locator('.badge.neutral')).toHaveCount(1);

		// Only the auto-disabled row explains itself.
		await expect(autoRow.getByTestId('auto-disabled-note')).toContainText(
			'Stopped sending after 5 consecutive failures'
		);
		await expect(pausedRow.getByTestId('auto-disabled-note')).toHaveCount(0);

		// Recovery is a single PATCH that also clears the failure — no second
		// "clear the error" request to forget.
		await autoRow.getByRole('button', { name: 'Re-enable schedule Auto stopped' }).click();
		await expect(panel(page).locator('tr[data-health="auto_disabled"]')).toHaveCount(0);
		await expect(
			panel(page).locator('tbody tr', { hasText: 'Auto stopped' }).locator('.badge')
		).toHaveText('Scheduled');
		expect(patches).toHaveLength(1);
		expect(patches[0].url).toContain('/api/analytics/scheduled-reports/sr-auto');
		expect(patches[0].body).toEqual({ enabled: true });
	});

	test('a partial run shows the runner message, which carries no address', async ({ page }) => {
		const partial = row({
			id: 'sr-partial',
			name: 'Partly sent',
			last_run_at: '2026-08-17T08:00:00Z',
			last_run_status: 'partial',
			last_run_error: 'delivered 2 of 3 recipients; SMTPRecipientsRefused'
		});
		await page.route(LIST_URL, async (route) => {
			if (route.request().method() !== 'GET') return route.continue();
			await json(route, envelope([partial]));
		});

		await page.goto('/cfo');
		const partialRow = panel(page).locator('tr[data-health="partial"]');
		await expect(partialRow.locator('.badge')).toHaveText('Partial');

		const runError = partialRow.getByTestId('run-error');
		await expect(runError).toHaveText('delivered 2 of 3 recipients; SMTPRecipientsRefused');
		// Counts + an exception class only — the panel must never surface an
		// address here.
		expect(await runError.innerText()).not.toContain('@');
	});

	test('re-renders recipients from the response after the server de-dupes them', async ({
		page
	}) => {
		const saved = row({
			id: 'sr-new',
			name: 'Daily payments',
			report_type: 'payment_register',
			cadence: 'daily',
			// Three were submitted; the backend kept two (case-insensitive dedupe).
			recipients: ['ap@acme.test', 'cfo@acme.test']
		});

		let created = false;
		let postedRecipients: string[] = [];
		await page.route(LIST_URL, async (route) => {
			const req = route.request();
			if (req.method() === 'GET') return json(route, envelope(created ? [saved] : []));
			if (req.method() === 'POST') {
				postedRecipients = (req.postDataJSON() as { recipients: string[] }).recipients;
				created = true;
				return json(route, saved, 201);
			}
			return route.continue();
		});

		await page.goto('/cfo');
		await expect(panel(page)).toBeVisible();
		await panel(page).getByTestId('new-schedule').click();

		const modal = page.getByRole('dialog', { name: 'New scheduled report' });
		await modal.getByLabel('Name').fill('Daily payments');
		await modal.getByLabel('Report').selectOption('payment_register');
		await modal.getByLabel('Cadence').selectOption('daily');
		await modal.getByLabel('Recipients').fill('ap@acme.test\nAP@ACME.test\ncfo@acme.test');
		await modal.getByRole('button', { name: 'Create' }).click();

		// The frontend does NOT de-dupe: the backend owns that, and swallowing it
		// here would hide the difference from the user.
		await expect.poll(() => postedRecipients).toHaveLength(3);

		// The table shows the SAVED list (2), never the typed one (3).
		const newRow = panel(page).locator('tbody tr', { hasText: 'Daily payments' });
		await expect(newRow).toContainText('2 recipients');
		await expect(newRow).not.toContainText('3 recipients');

		// And it says so, so a shortened list can't read as data loss.
		await expect(
			page.locator('.toast-text', { hasText: 'duplicate recipient was removed' })
		).toBeVisible();
	});

	test('Delete is an armed two-click that un-arms on an outside click', async ({ page }) => {
		let deleted = false;
		const deletes: string[] = [];
		await page.route(LIST_URL, async (route) => {
			const req = route.request();
			if (req.method() === 'GET') {
				return json(route, envelope(deleted ? [] : [row({ id: 'sr-del', name: 'Doomed' })]));
			}
			if (req.method() === 'DELETE') {
				deletes.push(req.url());
				deleted = true;
				return route.fulfill({ status: 204, body: '' });
			}
			return route.continue();
		});

		await page.goto('/cfo');
		const doomed = panel(page).locator('tbody tr', { hasText: 'Doomed' });
		const del = doomed.getByRole('button', { name: 'Delete schedule Doomed' });

		// First click arms rather than deletes.
		await del.click();
		await expect(del).toHaveText('Confirm');
		expect(deletes).toHaveLength(0);

		// A click anywhere outside the row action un-arms it.
		await panel(page).getByRole('heading', { name: 'Scheduled Reports' }).click();
		await expect(del).toHaveText('Delete');
		expect(deletes).toHaveLength(0);

		// Arm again, then confirm.
		await del.click();
		await expect(del).toHaveText('Confirm');
		await del.click();
		await expect(panel(page).locator('tbody tr', { hasText: 'Doomed' })).toHaveCount(0);
		expect(deletes).toHaveLength(1);
		expect(deletes[0]).toContain('/api/analytics/scheduled-reports/sr-del');
	});

	test('a deployment without the endpoints degrades to one quiet line', async ({ page }) => {
		await page.route(LIST_URL, async (route) => {
			if (route.request().method() !== 'GET') return route.continue();
			await json(route, { detail: 'Not Found' }, 404);
		});

		await page.goto('/cfo');
		await expect(panel(page)).toBeVisible();
		await expect(panel(page).getByTestId('scheduled-reports-unavailable')).toBeVisible();

		// Not an error the reader can act on: no error state, no retry, no
		// create button, and no toast about it.
		await expect(panel(page).getByTestId('scheduled-reports-error')).toHaveCount(0);
		await expect(panel(page).getByTestId('new-schedule')).toHaveCount(0);
		await expect(page.locator('.toast-text', { hasText: /schedule/i })).toHaveCount(0);
	});
});
