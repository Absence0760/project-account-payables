// Typed helpers for the virtual-card REBATE lifecycle — the three endpoints
// that let AP record what the card processor did out-of-band. Everything routes
// through the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID +
// 401-bounce), like every other `$lib/api/*` module.
//
// All three are `require_roles(admin, ap_manager, cfo)` on the server — a ROLE
// gate, not one of the granular `payment.*` permissions the recovery exits in
// `./payments.ts` use, because a rebate is bookkeeping rather than a money
// movement. The UI mirrors that with `auth.hasAnyRole(...)` so a clerk never
// sees a control that can only 403.
//
// **None of these moves money.** The card settlement webhook creates every
// rebate at `pending`; Lithic/Nium confirm and pay rebates on a periodic
// statement, not on an event we ingest, so `confirm` / `mark-paid` record a
// confirmation and a payout that already happened elsewhere. Neither requests a
// payout, and neither pays anyone.
//
// See `backend/docs/virtual-cards.md` § Rebate status lifecycle.
import { api } from '$lib/api';
import type { CardRebate, RebateListResponse } from '$lib/types/cardRebate';

/**
 * List the tenant's card rebates, newest first.
 *
 * Entity-scoped server-side through the `VirtualCard` join (the `X-Entity-ID`
 * header the shared client already sends), and NOT paginated — the route
 * returns every rebate matching the optional `period` filter.
 */
export function listCardRebates(period?: string): Promise<RebateListResponse> {
	const qs = period ? `?period=${encodeURIComponent(period)}` : '';
	return api.get<RebateListResponse>(`/api/cards/rebates${qs}`);
}

/**
 * Record the processor's confirmation — `pending` → `confirmed`.
 *
 * 409s from any other status (the lifecycle has no skips and no reversals) and
 * 404s on an unknown rebate. Writes an append-only `card_rebate.confirmed`
 * audit row. Moves no money.
 */
export function confirmCardRebate(rebateId: string): Promise<CardRebate> {
	return api.post<CardRebate>(`/api/cards/rebates/${rebateId}/confirm`, {});
}

/**
 * Record that the processor's payout landed — `confirmed` → `paid_out`.
 *
 * Requires `confirmed` first: a rebate cannot be recorded paid before it was
 * confirmed to exist. 409s otherwise. Writes an append-only
 * `card_rebate.paid_out` audit row. Moves no money — the payout it records
 * happened at the processor.
 */
export function markCardRebatePaid(rebateId: string): Promise<CardRebate> {
	return api.post<CardRebate>(`/api/cards/rebates/${rebateId}/mark-paid`, {});
}
