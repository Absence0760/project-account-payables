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
 * (`GET /api/organization`). That route is admin-gated, so for non-admin
 * roles the fetch 403s and we fall back to {@link DEFAULT_CURRENCY} —
 * which is exactly the pre-existing behaviour (everything was hardcoded
 * USD), so nothing regresses. Resilient by design: any failure degrades
 * to the default rather than breaking a dashboard render.
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
				// Non-admin role (403) or transient error: keep the default.
				// Don't mark loaded so an admin navigating later can still resolve it.
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
