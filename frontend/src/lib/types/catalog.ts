// Types for the Catalogs surface. Mirrors the JSON returned by the
// `/api/catalogs` endpoints (backend `CatalogResponse` / `CatalogItemResponse`
// / `GuidedBuyingSuggestion`). Money fields arrive as numbers (backend
// `float(...)`); date/datetime fields are ISO strings.

import type { BadgeTone } from '$lib/components/ui/Badge.svelte';

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

// ===================== Punch-out (live cXML/OCI round-trip) =====================

export type PunchoutSessionStatus =
	| 'pending'
	| 'returned'
	| 'converted'
	| 'expired'
	| 'cancelled';

export const PUNCHOUT_STATUS_LABELS: Record<string, string> = {
	pending: 'Awaiting cart',
	returned: 'Cart returned',
	converted: 'Converted',
	expired: 'Expired',
	cancelled: 'Cancelled'
};

// Badge tone per session status. Only `returned` and `converted` ever carried a
// colour of their own; everything else shared one grey tint, and still does.
export const PUNCHOUT_STATUS_TONES: Record<string, BadgeTone> = {
	pending: 'muted',
	returned: 'success',
	converted: 'accent',
	expired: 'muted',
	cancelled: 'muted'
};

/**
 * `PunchoutSession.status` is a string off the wire (the adapter may report a
 * provider status this union doesn't name), so the tone is read through a
 * tolerant accessor — mirroring how the label is read. An unrecognised status
 * gets the same grey the pill's base rule always gave it.
 */
export function punchoutStatusTone(status: string): BadgeTone {
	return PUNCHOUT_STATUS_TONES[status] ?? 'muted';
}

export interface PunchoutStartResponse {
	session_id: string;
	buyer_cookie: string;
	start_url: string;
	status: string;
	provider: string;
}

export interface PunchoutCartItem {
	description: string;
	sku: string | null;
	quantity: number | null;
	unit_price: number | null;
	uom: string | null;
	currency: string;
}

export interface PunchoutSession {
	id: string;
	catalog_id: string;
	buyer_cookie: string;
	status: string;
	requested_by_user_id: string;
	start_url: string | null;
	provider: string | null;
	cart_items: PunchoutCartItem[];
	cart_total: number | null;
	currency: string;
	returned_at: string | null;
	converted_requisition_id: string | null;
	created_at: string;
	updated_at: string;
}

export interface PunchoutConvertResponse {
	session_id: string;
	requisition_id: string;
	requisition_number: string;
	total: number;
	created: boolean;
}
