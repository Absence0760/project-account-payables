// Typed helpers for the platform billing surface. Routes through the shared
// `api` client (Bearer + X-Tenant-Slug + 401-bounce). See
// `$lib/types/billing` for the response contracts and `backend/docs/billing.md`
// for the backend slice this consumes.
import { api } from '$lib/api';
import type { BillingInvoicesResponse, BillingSubscriptionResponse } from '$lib/types/billing';

/** Current plan + subscription status + usage-to-date for the active period.
 *  `plan`/`subscription` are null when the org has no live subscription. */
export function getBillingSubscription(): Promise<BillingSubscriptionResponse> {
	return api.get<BillingSubscriptionResponse>('/api/billing/subscription');
}

/** The org's past platform-billing invoices / receipts (newest first). admin/cfo
 *  only (the backend 403s everyone else). An org never provisioned with the
 *  provider — or an unconfigured / unavailable one — yields an empty list. */
export function getBillingInvoices(): Promise<BillingInvoicesResponse> {
	return api.get<BillingInvoicesResponse>('/api/billing/invoices');
}
