// Types for the Catalogs surface. Mirrors the JSON returned by the
// `/api/catalogs` endpoints (backend `CatalogResponse` / `CatalogItemResponse`
// / `GuidedBuyingSuggestion`). Money fields arrive as numbers (backend
// `float(...)`); date/datetime fields are ISO strings.

export type CatalogType = 'internal' | 'punchout';

export const CATALOG_TYPES: CatalogType[] = ['internal', 'punchout'];

export const CATALOG_TYPE_LABELS: Record<CatalogType, string> = {
	internal: 'Internal',
	punchout: 'Punch-out'
};

export interface CatalogItem {
	id: string;
	catalog_id: string;
	sku: string | null;
	name: string;
	description: string | null;
	unit_price: number | null;
	currency: string;
	uom: string | null;
	vendor_id: string | null;
	gl_account_id: string | null;
	category: string | null;
	is_active: boolean;
	created_at: string;
	updated_at: string;
}

export interface Catalog {
	id: string;
	name: string;
	catalog_type: string;
	vendor_id: string | null;
	punchout_url: string | null;
	is_active: boolean;
	is_preferred: boolean;
	description: string | null;
	item_count: number;
	items: CatalogItem[];
	created_at: string;
	updated_at: string;
}

export interface CatalogListResponse {
	items: Catalog[];
	total: number;
	page: number;
	page_size: number;
}

// Payload shapes for create / update (request side).
export interface CatalogCreate {
	name: string;
	catalog_type: CatalogType;
	vendor_id: string | null;
	punchout_url: string | null;
	is_active: boolean;
	is_preferred: boolean;
	description: string | null;
}

export interface CatalogItemCreate {
	sku: string | null;
	name: string;
	description: string | null;
	unit_price: number | null;
	currency: string;
	uom: string | null;
	vendor_id: string | null;
	gl_account_id: string | null;
	category: string | null;
	is_active: boolean;
}

// ===================== Guided buying =====================

export interface GuidedBuyingVendor {
	vendor_id: string;
	vendor_name: string;
	reasons: string[]; // 'preferred_catalog' | 'active_contract'
	contract_id: string | null;
	contract_number: string | null;
	catalog_id: string | null;
	catalog_name: string | null;
}

export interface GuidedBuyingItem {
	catalog_item_id: string;
	catalog_id: string;
	catalog_name: string;
	sku: string | null;
	name: string;
	unit_price: number | null;
	currency: string;
	uom: string | null;
	vendor_id: string | null;
	category: string | null;
	is_preferred: boolean;
}

export interface GuidedBuyingSuggestion {
	preferred_vendors: GuidedBuyingVendor[];
	in_contract_vendors: GuidedBuyingVendor[];
	items: GuidedBuyingItem[];
}

export const GUIDED_BUYING_REASON_LABELS: Record<string, string> = {
	preferred_catalog: 'Preferred catalog',
	active_contract: 'Active contract'
};
