// Types for the admin API-key management surface (`/admin/api-keys`).
// Mirrors the backend `ApiKeyResponse` / `ApiKeyCreatedResponse` /
// `ApiKeyUsageResponse` shapes in `backend/app/api/api_keys.py`. The plaintext
// `key` is ONLY ever present on the mint response and is shown exactly once —
// it is never stored, echoed, or re-fetchable.

export interface ApiKey {
	id: string;
	name: string;
	key_prefix: string;
	scopes: string[];
	created_at: string | null;
	last_used_at: string | null;
	revoked_at: string | null;
}

/** The mint response — the ONLY place the plaintext key is ever returned. */
export interface ApiKeyCreated {
	api_key: ApiKey;
	// Shown once; copy it now. Never persisted client-side after the modal closes.
	key: string;
}

export interface ApiKeyUsageDay {
	usage_date: string;
	request_count: number;
}

export interface ApiKeyUsage {
	api_key_id: string;
	key_prefix: string;
	total_requests: number;
	window_days: number;
	window_requests: number;
	last_used_at: string | null;
	daily: ApiKeyUsageDay[];
}
