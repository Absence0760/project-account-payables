/**
 * Supplier-portal HTTP client. Parallel to $lib/api.ts but scoped to a
 * separate token key so the AP app and the portal can't stomp on each other's
 * localStorage (opening both in the same browser would otherwise clobber one).
 */
import { PUBLIC_API_URL } from '$env/static/public';
import { getTenantSlug } from '$lib/tenant';

const BASE = PUBLIC_API_URL.replace(/\/+$/, '');
const TOKEN_KEY = 'portal_auth_token';

function getToken(): string | null {
	if (typeof window === 'undefined') return null;
	return localStorage.getItem(TOKEN_KEY);
}

export function setPortalToken(token: string) {
	localStorage.setItem(TOKEN_KEY, token);
}

export function clearPortalToken() {
	localStorage.removeItem(TOKEN_KEY);
}

export function hasPortalToken(): boolean {
	return !!getToken();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const token = getToken();
	const inHeaders = (init?.headers ?? {}) as Record<string, string>;
	const headers: Record<string, string> = {
		...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
		...inHeaders,
	};
	if (token) headers['Authorization'] = `Bearer ${token}`;
	const tenant = getTenantSlug();
	if (tenant) headers['X-Tenant-Slug'] = tenant;

	const res = await fetch(`${BASE}${path}`, { ...init, headers });

	if (res.status === 401) {
		// Only treat a 401 as a session expiry — clear the token and bounce to
		// the portal login — when we actually sent one. A 401 on an
		// unauthenticated request (the login POST with bad credentials) must
		// surface to the caller so the login page can render the error, not
		// silently full-page-reload it away.
		if (token) {
			clearPortalToken();
			if (typeof window !== 'undefined') window.location.href = '/portal/login';
			throw new Error('Unauthorized');
		}
		const body = await res.json().catch(() => ({}));
		throw new Error(body.detail || 'Invalid credentials');
	}

	if (!res.ok) {
		const body = await res.json().catch(() => ({}));
		throw new Error(body.detail || `API error ${res.status}`);
	}

	if (res.status === 204) return undefined as T;
	return res.json();
}

export const portalApi = {
	get: <T>(path: string) => request<T>(path),
	post: <T>(path: string, body: unknown) =>
		request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
	delete: (path: string) => request<void>(path, { method: 'DELETE' }),
	upload: <T>(path: string, file: File) => {
		const form = new FormData();
		form.append('file', file);
		return request<T>(path, { method: 'POST', body: form, headers: {} });
	},
};
