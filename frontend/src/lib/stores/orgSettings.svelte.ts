import { api } from '$lib/api';
import { DEFAULT_CURRENCY } from '$lib/utils/money';

/**
 * Tenant-wide display currency for *aggregate* figures that don't carry
 * their own per-row currency code — dashboard KPIs, payment-summary
 * totals, aging buckets, the CFO forecast. Per-row amounts (an invoice,
 * a credit memo) always render with *their own* `currency` field via
 * `<Money currency={row.currency} />`; this store only backs the
 * roll-ups where there is no single row to read from.
 *
 * Sourced from `Organization.settings.invoice_defaults.currency`
 * (`GET /api/organization`). That route is open to any authenticated org
 * user, but the settings it returns are projected by role: a non-admin gets
 * an allow-list that deliberately keeps `invoice_defaults` (this store is
 * one of the consumers it exists for) and drops the tenant's third-party
 * credentials. See `backend/app/services/org_settings_view.py` — if a
 * future field is needed here, it has to be added to that allow-list.
 * Resilient by design: any failure degrades to {@link DEFAULT_CURRENCY}
 * rather than breaking a dashboard render.
 *
 * Cached for the session after the first successful load; `reset()`
 * clears it (e.g. on logout / tenant switch).
 */

interface OrgResponse {
	settings?: {
		invoice_defaults?: { currency?: string | null } | null;
	} | null;
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
				const ccy = org?.settings?.invoice_defaults?.currency;
				if (ccy && ccy.trim().length === 3) {
					this.currency = ccy.trim().toUpperCase();
				}
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
