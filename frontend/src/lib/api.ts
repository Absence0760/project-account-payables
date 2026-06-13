import { PUBLIC_API_URL } from '$env/static/public';
import { getTenantSlug } from '$lib/tenant';
import { getSelectedEntityId } from '$lib/entity';

const BASE = PUBLIC_API_URL.replace(/\/+$/, '');

function getToken(): string | null {
	if (typeof window === 'undefined') return null;
	return localStorage.getItem('auth_token');
}

export function setToken(token: string) {
	localStorage.setItem('auth_token', token);
}

export function clearToken() {
	localStorage.removeItem('auth_token');
}

export function hasToken(): boolean {
	return !!getToken();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const token = getToken();
	const inHeaders = (init?.headers ?? {}) as Record<string, string>;
	const headers: Record<string, string> = {
		...( init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
		...inHeaders,
	};
	if (token) {
		headers['Authorization'] = `Bearer ${token}`;
	}
	const tenant = getTenantSlug();
	if (tenant) {
		headers['X-Tenant-Slug'] = tenant;
	}
	// Multi-entity: scope to the selected subsidiary. Absent = consolidated.
	const entity = getSelectedEntityId();
	if (entity) {
		headers['X-Entity-ID'] = entity;
	}

	const res = await fetch(`${BASE}${path}`, { ...init, headers });

	if (res.status === 401) {
		// Auto-redirect only fires when an existing session went stale.
		// For anonymous requests (e.g. /api/auth/login itself), 401
		// means "wrong credentials" — let the caller's catch handle it
		// so forms can render error banners instead of being torn down
		// mid-render by a navigation.
		if (token) {
			clearToken();
			window.location.href = '/login';
		}
		const body = await res.json().catch(() => ({}));
		throw new Error(body.detail || 'Unauthorized');
	}

	if (!res.ok) {
		const body = await res.json().catch(() => ({}));
		throw new Error(body.detail || `API error ${res.status}`);
	}

	if (res.status === 204) return undefined as T;
	return res.json();
}

async function fetchBlob(path: string): Promise<string> {
	// For binary endpoints (image / PDF) that <img src> and <iframe src>
	// can't reach because they don't carry the Bearer token. Caller is
	// responsible for `URL.revokeObjectURL` on the returned URL.
	const blob = await downloadBlob(path);
	return URL.createObjectURL(blob);
}

async function downloadBlob(path: string): Promise<Blob> {
	const token = getToken();
	const headers: Record<string, string> = {};
	if (token) headers['Authorization'] = `Bearer ${token}`;
	const tenant = getTenantSlug();
	if (tenant) headers['X-Tenant-Slug'] = tenant;
	const entity = getSelectedEntityId();
	if (entity) headers['X-Entity-ID'] = entity;

	const res = await fetch(`${BASE}${path}`, { headers });
	if (res.status === 401) {
		clearToken();
		window.location.href = '/login';
		throw new Error('Unauthorized');
	}
	if (!res.ok) {
		throw new Error(`Failed to load file: ${res.status}`);
	}
	return res.blob();
}

export const api = {
	get: <T>(path: string) => request<T>(path),
	post: <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
	patch: <T>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
	put: <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
	delete: (path: string) => request<void>(path, { method: 'DELETE' }),
	upload: <T>(path: string, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return request<T>(path, {
			method: 'POST',
			body: form,
			headers: {},  // let browser set Content-Type with boundary
		});
	},
	fetchBlob,
	downloadBlob,
};
