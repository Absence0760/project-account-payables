import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	signInAndWait,
	tenantBase,
	test
} from '../fixtures/helpers';
import { SERVICES, skipUnlessReachable } from '../fixtures/services';
import {
	createApprovedInvoice,
	deleteInvoice,
	erpReferenceFromAudit,
	resetFakeErp,
	sendToErpAndAwaitTerminal,
	setErpSettings,
	testErpConnection
} from './helpers';

/**
 * netsuite adapter e2e — the REAL NetSuite SuiteTalk REST adapter
 * (backend/app/services/erp_adapters/netsuite.py) against the local fake ERP
 * container (tools/fake-erp, `pnpm erp:up`, host port 12112). The committed
 * backend/.env.development points FEOH_ERP_NETSUITE_API_BASE at the fake
 * (instead of deriving the per-account suitetalk.api.netsuite.com URL from
 * account_id), so the adapter's real OAuth 1.0 TBA header construction +
 * httpx calls run end-to-end with no NetSuite account.
 *
 * Coverage:
 *   1. test_connection — GET /vendor?limit=1 with the full `Authorization:
 *      OAuth ...` TBA header (consumer key/token/nonce/HMAC signature); the
 *      fake 401s any request missing the OAuth params.
 *   2. Full send — an approved invoice posts as a vendorBill through the
 *      async ERP dispatch: the fake answers 204 + a Location header, the
 *      adapter parses the record id out of it (NetSuite's contract), and the
 *      invoice lands `done` with that numeric NetSuite-shaped document id.
 */

// The exact settings.erp shape the adapter reads: get_erp_adapter passes the
// whole dict to the adapter, which reads account_id / consumer_key /
// consumer_secret / token_id / token_secret flat (the same flat shape the
// /organization ERP panel saves). All values are fakes — the fake ERP checks
// OAuth-header shape, not signatures.
const NETSUITE_ERP_CONFIG = {
	type: 'netsuite',
	integration_method: 'direct',
	account_id: 'FAKE123',
	consumer_key: 'fake-consumer-key',
	consumer_secret: 'fake-consumer-secret',
	token_id: 'fake-token-id',
	token_secret: 'fake-token-secret'
};

test.describe('/erp netsuite adapter against fake-erp', () => {
	test.beforeAll(async () => {
		// Deterministic document ids for this run (fake NetSuite ids count up
		// from "1001"). Best-effort — when the fake is down the beforeEach
		// gate skips every test with the hint.
		await resetFakeErp();
	});

	test.beforeEach(async ({ page }) => {
		await skipUnlessReachable(SERVICES.fakeErp);
		await setErpSettings(page, NETSUITE_ERP_CONFIG);
	});

	// Other suites (e.g. payments/execute) assume the seeded org has NO ERP
	// configured — `dispatch_payment_sync` only runs the invoice → paid
	// auto-bump when erp_config is present. Same contract as
	// purchase-orders/sync.spec.ts.
	test.afterAll(async ({ browser }) => {
		const context = await browser.newContext({ baseURL: tenantBase(currentTenantSlug()) });
		const page = await context.newPage();
		await signInAndWait(page);
		const headers = await authedTenantHeaders(page);
		await page.request.patch(`${API_BASE}/api/organization`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { settings: { erp: null } }
		});
		await context.close();
	});

	test('connection test succeeds against the fake NetSuite (TBA header accepted)', async ({
		page
	}) => {
		const result = await testErpConnection(page);
		expect(result.success, result.message).toBe(true);
		expect(result.message).toContain('netsuite');
	});

	test('full send: approved invoice posts as a vendorBill and completes', async ({ page }) => {
		const inv = await createApprovedInvoice(page, { prefix: 'E2E-NS', amount: '2450.75' });
		try {
			const terminal = await sendToErpAndAwaitTerminal(page, inv.id);
			expect(terminal).toBe('done');

			// NetSuite returns 204 + the record URL in the Location header; the
			// adapter extracts the trailing id — the fake mints numeric-string
			// ids ("1001", "1002", ...). A numeric id proves the REAL netsuite
			// adapter (not the mock) parsed the Location contract.
			const erpRef = await erpReferenceFromAudit(page, inv.id);
			expect(erpRef).toMatch(/^\d+$/);
		} finally {
			await deleteInvoice(page, inv.id);
		}
	});
});
