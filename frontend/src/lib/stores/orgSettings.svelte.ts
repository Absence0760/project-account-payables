import { api } from '$lib/api';
import { DEFAULT_CURRENCY } from '$lib/utils/money';
import {
	resolveReportingCurrency,
	type ReportingCurrencySettings
} from '$lib/utils/reportingCurrency';

/**
 * Tenant-wide display currency for *aggregate* figures that don't carry
 * their own per-row currency code — dashboard KPIs, payment-summary
 * totals, aging buckets, the CFO forecast. Per-row amounts (an invoice,
 * a credit memo) always render with *their own* `currency` field via
 * `<Money currency={row.currency} />`; this store only backs the
 * roll-ups where there is no single row to read from.
 *
 * Resolved from `GET /api/organization` in the SAME order the backend uses —
 * `settings.reporting_currency` → `settings.payments.home_currency` →
 * `settings.invoice_defaults.currency` → {@link DEFAULT_CURRENCY}. That order
 * is not a preference; it is `currency_conversion.resolve_reporting_currency`,
 * the function that decides what currency the API's cross-currency rollups are
 * *actually denominated in* (`/api/payments/summary`, the CFO forecast + cash
 * position, the dashboard `reporting` block, the discount dashboard).
 *
 * Reading only `invoice_defaults.currency` — as this store did — was therefore
 * a mislabel, not a fallback: an org reporting in GBP while its invoice default
 * stayed USD had its converted GBP totals rendered with a `$`, on every
 * aggregate figure in the app. The two keys agree in the common case, which is
 * exactly why it went unnoticed.
 *
 * `GET /api/organization` is open to any authenticated org user, but the
 * settings it returns are projected by role: a non-admin gets an allow-list
 * that keeps the three keys above (this store is the consumer they are listed
 * for) and drops the tenant's third-party credentials — `payments` is admitted
 * for `home_currency` ONLY, never the processor credentials beside it. See
 * `backend/app/services/org_settings_view.py`; a future field needed here has
 * to be added there on purpose.
 *
 * Resilient by design: any failure degrades to {@link DEFAULT_CURRENCY} rather
 * than breaking a dashboard render.
 *
 * Cached for the session after the first successful load; `reset()`
 * clears it (e.g. on logout / tenant switch).
 */

interface OrgResponse {
	settings?: ReportingCurrencySettings | null;
}

class OrgSettingsStore {
	currency = $state(DEFAULT_CURRENCY);
	#loaded = false;
	#inflight: Promise<void> | null = null;

	/**
	 * Lazy-load the tenant default currency once per session. Safe to
	 * call from any page's `$effect`/`onMount`; concurrent callers share
	 * one in-flight request and a non-admin 403 is swallowed.
	 */
	async ensureLoaded(): Promise<void> {
		if (this.#loaded) return;
		if (this.#inflight) return this.#inflight;
		this.#inflight = (async () => {
			try {
				const org = await api.get<OrgResponse>('/api/organization');
				const ccy = resolveReportingCurrency(org?.settings);
				if (ccy) this.currency = ccy;
				this.#loaded = true;
			} catch {
				// Transient error (or a signed-out race): keep the default and
				// don't mark loaded, so a later navigation can still resolve it.
			} finally {
				this.#inflight = null;
			}
		})();
		return this.#inflight;
	}

	reset(): void {
		this.currency = DEFAULT_CURRENCY;
		this.#loaded = false;
		this.#inflight = null;
	}
}

export const orgCurrency = new OrgSettingsStore();
