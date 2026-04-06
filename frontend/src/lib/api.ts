import { PUBLIC_API_URL } from '$env/static/public';
import { getTenantSlug } from '$lib/tenant';

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

	const res = await fetch(`${BASE}${path}`, { ...init, headers });

	if (res.status === 401) {
		clearToken();
		window.location.href = '/login';
		throw new Error('Unauthorized');
	}

	if (!res.ok) {
		const body = await res.json().catch(() => ({}));
		throw new Error(body.detail || `API error ${res.status}`);
	}

	if (res.status === 204) return undefined as T;
	return res.json();
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
};
