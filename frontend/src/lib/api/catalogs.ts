// Typed helpers for the catalog + guided-buying endpoints. All requests route
// through the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID +
// 401-bounce). Mirrors the pattern of `src/lib/api/expenses.ts`.
import { api } from '$lib/api';
import type {
	Catalog,
	CatalogCreate,
	CatalogItem,
	CatalogItemCreate,
	CatalogListResponse,
	GuidedBuyingSuggestion
} from '$lib/types/catalog';

export interface CatalogListParams {
	catalog_type?: string;
	is_active?: boolean;
	is_preferred?: boolean;
	search?: string;
	page?: number;
	page_size?: number;
}

/** GL account option from `GET /api/gl-accounts` — picker value is the uuid `id`. */
export interface GlAccountOption {
	id: string;
	code: string;
	name: string;
	account_type?: string;
}

/** Vendor option from `GET /api/vendors` — picker value is the uuid `id`. */
export interface VendorOption {
	id: string;
	name: string;
	code?: string | null;
}

export function listCatalogs(params: CatalogListParams = {}): Promise<CatalogListResponse> {
	const qs = new URLSearchParams();
	if (params.catalog_type) qs.set('catalog_type', params.catalog_type);
	if (params.is_active !== undefined) qs.set('is_active', String(params.is_active));
	if (params.is_preferred !== undefined) qs.set('is_preferred', String(params.is_preferred));
	if (params.search) qs.set('search', params.search);
	qs.set('page', String(params.page ?? 1));
	qs.set('page_size', String(params.page_size ?? 20));
	return api.get<CatalogListResponse>(`/api/catalogs?${qs}`);
}

export function getCatalog(id: string): Promise<Catalog> {
	return api.get<Catalog>(`/api/catalogs/${id}`);
}

export function createCatalog(body: CatalogCreate): Promise<Catalog> {
	return api.post<Catalog>('/api/catalogs', body);
}

export function updateCatalog(id: string, body: Partial<CatalogCreate>): Promise<Catalog> {
	return api.patch<Catalog>(`/api/catalogs/${id}`, body);
}

export function deleteCatalog(id: string): Promise<void> {
	return api.delete(`/api/catalogs/${id}`);
}

// --- Nested catalog items ---

export function listCatalogItems(catalogId: string): Promise<CatalogItem[]> {
	return api.get<CatalogItem[]>(`/api/catalogs/${catalogId}/items`);
}

export function createCatalogItem(catalogId: string, body: CatalogItemCreate): Promise<CatalogItem> {
	return api.post<CatalogItem>(`/api/catalogs/${catalogId}/items`, body);
}

export function updateCatalogItem(
	itemId: string,
	body: Partial<CatalogItemCreate>
): Promise<CatalogItem> {
	return api.patch<CatalogItem>(`/api/catalogs/items/${itemId}`, body);
}

export function deleteCatalogItem(itemId: string): Promise<void> {
	return api.delete(`/api/catalogs/items/${itemId}`);
}

// --- Guided buying ---

export interface GuidedBuyingParams {
	category?: string;
	vendor_id?: string;
	q?: string;
}

export function guidedBuying(params: GuidedBuyingParams = {}): Promise<GuidedBuyingSuggestion> {
	const qs = new URLSearchParams();
	if (params.category) qs.set('category', params.category);
	if (params.vendor_id) qs.set('vendor_id', params.vendor_id);
	if (params.q) qs.set('q', params.q);
	return api.get<GuidedBuyingSuggestion>(`/api/catalogs/guided-buying?${qs}`);
}

// --- Lookups reused from existing endpoints ---

export function listGlAccounts(): Promise<GlAccountOption[]> {
	return api.get<GlAccountOption[]>('/api/gl-accounts');
}

/** Vendor picker options. `/api/vendors` returns a paginated envelope and is
 *  gated to admin/ap_manager/cfo — an ap_clerk gets a 403, so we unwrap the
 *  envelope and degrade to an empty list rather than failing the whole page. */
export async function listVendors(): Promise<VendorOption[]> {
	try {
		const res = await api.get<{ items: VendorOption[] }>('/api/vendors?page_size=100');
		return res.items ?? [];
	} catch {
		return [];
	}
}
