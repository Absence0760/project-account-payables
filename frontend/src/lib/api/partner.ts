// Typed helpers for the partner / reseller multi-tenant admin surface. Routes
// through the shared `api` client (Bearer + X-Tenant-Slug + 401-bounce) — never
// raw fetch. Backend: `backend/app/api/partner.py` (admin + JWT gated, scoped to
// the caller's own child tenants). See `docs/white-label.md` § Partner admin.
import { api } from '$lib/api';
import type { ChildBranding, PartnerOverview } from '$lib/types/partner';

/** The caller's partner overview — its identity + the child tenants it administers. */
export function getPartnerOverview(): Promise<PartnerOverview> {
	return api.get<PartnerOverview>('/api/partner');
}

/** One child tenant's white-label branding. */
export function getChildBranding(childId: string): Promise<ChildBranding> {
	return api.get<ChildBranding>(`/api/partner/children/${childId}/branding`);
}

/** Push branding onto a child tenant (full replace; preserves the child's
 *  custom-domain list server-side). Admin-only; audited into the child's trail. */
export function updateChildBranding(
	childId: string,
	branding: ChildBranding
): Promise<ChildBranding> {
	return api.put<ChildBranding>(`/api/partner/children/${childId}/branding`, branding);
}
