import { expect, test } from '../fixtures/helpers';
import type { Route } from '@playwright/test';

/**
 * /audit — the signature drill-down must answer about the finding it names.
 *
 * The per-invoice re-check (`GET /api/audit/invoice/{id}/verify-signatures`) had
 * no request-identity guard: click finding A, then finding B, and A's response
 * resolved into `invoiceReport` afterwards, so B's heading sat above A's
 * approval verdicts. In an evidence panel an auditor exports from, a report
 * attributed to the wrong invoice is worse than no report.
 *
 * Both responses are stubbed and their ORDER is controlled — invoice A's is
 * parked on a promise this spec resolves by hand. No sleeps, no inflated
 * timeouts: the race is driven by a real readiness gate.
 */

const INVOICE_A = '11111111-1111-4111-8111-111111111111';
const INVOICE_B = '22222222-2222-4222-8222-222222222222';

/** The period sweep's two findings, one per invoice. */
const PERIOD_REPORT = {
	start: '2020-01-01',
	end: '2030-01-01',
	signing_configured: true,
	approvals_checked: 2,
	invoices_covered: 2,
	valid: 0,
	invalid: 2,
	unsigned: 0,
	findings_truncated: false,
	findings: [
		{
			invoice_id: INVOICE_A,
			invoice_number: 'DRILL-A',
			actor: 'alpha@example.com',
			signed_at: '2024-01-01T00:00:00Z',
			audit_row_id: 'aaaaaaaa-0000-4000-8000-000000000001',
			verdict: 'invalid'
		},
		{
			invoice_id: INVOICE_B,
			invoice_number: 'DRILL-B',
			actor: 'bravo@example.com',
			signed_at: '2024-02-02T00:00:00Z',
			audit_row_id: 'bbbbbbbb-0000-4000-8000-000000000002',
			verdict: 'invalid'
		}
	]
};

function invoiceReport(invoiceId: string, actor: string, rowId: string) {
	return {
		invoice_id: invoiceId,
		signing_configured: true,
		approvals: [
			{
				audit_row_id: rowId,
				actor,
				signed_at: '2024-03-03T00:00:00Z',
				signed: true,
				valid: false
			}
		]
	};
}

/** The invoice id a per-invoice verification request is for, else null. */
function drillInvoiceId(url: string): string | null {
	const m = new URL(url).pathname.match(/^\/api\/audit\/invoice\/([^/]+)\/verify-signatures$/);
	return m ? m[1] : null;
}

test.describe('/audit signature drill-down identity', () => {
	test("a late drill response cannot land under a second finding's heading", async ({ page }) => {
		let releaseA!: () => void;
		const heldA = new Promise<void>((resolve) => {
			releaseA = resolve;
		});
		let aRequested = false;

		await page.route('**/api/audit/**', async (route: Route) => {
			const url = new URL(route.request().url());
			if (url.pathname === '/api/audit/verify-signatures') {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(PERIOD_REPORT)
				});
				return;
			}
			const invoiceId = drillInvoiceId(route.request().url());
			if (invoiceId === INVOICE_A) {
				aRequested = true;
				await heldA;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(
						invoiceReport(INVOICE_A, 'alpha@example.com', 'aaaaaaaa-0000-4000-8000-000000000001')
					)
				});
				return;
			}
			if (invoiceId === INVOICE_B) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(
						invoiceReport(INVOICE_B, 'bravo@example.com', 'bbbbbbbb-0000-4000-8000-000000000002')
					)
				});
				return;
			}
			await route.continue();
		});

		try {
			await page.goto('/audit');

			// Run the period sweep so the two findings render.
			const runVerify = page.getByTestId('run-verification');
			await expect(runVerify).toBeVisible({ timeout: 15_000 });
			await runVerify.click();

			// `RowLink` renders a <button> carrying the row's aria-label, so select
			// the finding row and then its primary-cell control.
			const findingA = page
				.getByTestId('verify-finding')
				.filter({ hasText: 'DRILL-A' })
				.locator('.row-link');
			const findingB = page
				.getByTestId('verify-finding')
				.filter({ hasText: 'DRILL-B' })
				.locator('.row-link');
			await expect(findingA).toBeVisible({ timeout: 15_000 });

			// 1. Drill into A — its request is issued and then held.
			await findingA.click();
			const drill = page.getByTestId('verify-drill');
			await expect(drill).toHaveAttribute('data-invoice-id', INVOICE_A);
			await expect.poll(() => aRequested).toBe(true);

			// 2. Drill into B. B's response resolves normally.
			await findingB.click();
			await expect(drill).toHaveAttribute('data-invoice-id', INVOICE_B);
			await expect(drill).toContainText('bravo@example.com', { timeout: 15_000 });

			// 3. Release A's response LAST and wait for it to actually arrive.
			const aLanded = page.waitForResponse((r) => drillInvoiceId(r.url()) === INVOICE_A, {
				timeout: 15_000
			});
			releaseA();
			await aLanded;

			// An ordering barrier that touches the page but NOT the drill state: the
			// auditor export's own "Run query" round trip. It completes strictly
			// after A's response reached the page, so anything A's handler was going
			// to write has already been written by the time these assertions run.
			const exported = page.waitForResponse(
				(r) => new URL(r.url()).pathname === '/api/audit/export',
				{ timeout: 15_000 }
			);
			await page.getByRole('button', { name: 'Run query' }).click();
			await exported;

			// The drill-down is still B's.
			await expect(drill).toHaveAttribute('data-invoice-id', INVOICE_B);
			await expect(drill).toContainText('bravo@example.com');
			await expect(drill).not.toContainText('alpha@example.com');

			// And a FRESH period sweep clears the panel rather than letting a
			// still-in-flight drill repopulate a finding that is no longer shown.
			await runVerify.click();
			await expect(page.getByTestId('verify-drill')).toHaveCount(0, { timeout: 15_000 });
		} finally {
			releaseA();
			await page.unroute('**/api/audit/**').catch(() => {});
		}
	});
});
