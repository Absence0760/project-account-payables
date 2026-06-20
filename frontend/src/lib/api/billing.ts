// Typed helpers for the platform billing surface. Routes through the shared
// `api` client (Bearer + X-Tenant-Slug + 401-bounce). See
// `$lib/types/billing` for the response contracts and `backend/docs/billing.md`
// for the backend slice this consumes.
import { api } from '$lib/api';
import type { BillingSubscriptionResponse } from '$lib/types/billing';

/** Current plan + subscription status + usage-to-date for the active period.
 *  `plan`/`subscription` are null when the org has no live subscription. */
export function getBillingSubscription(): Promise<BillingSubscriptionResponse> {
	return api.get<BillingSubscriptionResponse>('/api/billing/subscription');
}
