// Typed helpers for the Dynamic Discounting & Early-Payment endpoints. All
// requests route through the shared `api` client (Bearer + X-Tenant-Slug +
// 401-bounce). See `$lib/types/discounts` for the response contracts.
import { api } from '$lib/api';
import type {
	DiscountDashboard,
	DiscountOffer,
	DiscountOfferPage,
	DiscountOptimization,
	DiscountRoi,
	DiscountStatusFilter
} from '$lib/types/discounts';

/** KPI roll-up for the dashboard header (captured / missed / open / projected). */
export function getDiscountDashboard(): Promise<DiscountDashboard> {
	return api.get<DiscountDashboard>('/api/discounts/dashboard');
}

export interface OfferListParams {
	/** Maps the UI `missed` bucket to the backend status param; `all`/`missed`
	 *  are sent as-is and the backend resolves `missed` → declined+expired. */
	status?: DiscountStatusFilter;
	scope?: 'invoice' | 'vendor';
	vendorId?: string;
	page?: number;
	pageSize?: number;
}

/** Paginated offer list. Omits `status` when `all`. */
export function listDiscountOffers(params: OfferListParams = {}): Promise<DiscountOfferPage> {
	const qs = new URLSearchParams();
	if (params.status && params.status !== 'all') qs.set('status', params.status);
	if (params.scope) qs.set('scope', params.scope);
	if (params.vendorId) qs.set('vendor_id', params.vendorId);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.pageSize ?? 20));
	return api.get<DiscountOfferPage>(`/api/discounts/offers?${qs.toString()}`);
}

/** Accept an offer, optionally pinning a specific tier by its `days`. Returns
 *  the updated offer (status → accepted). */
export function acceptDiscountOffer(id: string, tierDays?: number): Promise<DiscountOffer> {
	const body = tierDays === undefined ? {} : { tier_days: tierDays };
	return api.post<DiscountOffer>(`/api/discounts/offers/${id}/accept`, body);
}

/** Decline an offer (status → declined). Returns the updated offer. */
export function declineDiscountOffer(id: string): Promise<DiscountOffer> {
	return api.post<DiscountOffer>(`/api/discounts/offers/${id}/decline`, {});
}

/** Per-invoice ROI comparison (savings vs cost-of-capital opportunity cost). */
export function getInvoiceRoi(invoiceId: string): Promise<DiscountRoi> {
	return api.get<DiscountRoi>(`/api/discounts/invoices/${invoiceId}/roi`);
}

/** Budget-constrained optimization: ranks open offers by ROI and greedily
 *  selects within the optional cash budget. */
export function optimizeDiscounts(cashBudget?: number): Promise<DiscountOptimization> {
	const body = cashBudget === undefined ? {} : { cash_budget: cashBudget };
	return api.post<DiscountOptimization>('/api/discounts/optimize', body);
}
