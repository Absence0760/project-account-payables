import { api, setToken, clearToken, hasToken } from '$lib/api';

interface User {
	id: string;
	email: string;
	full_name: string;
	organization_id: string;
	roles: string[];
}

interface TokenResponse {
	access_token: string;
	token_type: string;
}

function createAuthStore() {
	let user = $state<User | null>(null);
	let loggedIn = $state(hasToken());

	async function login(email: string, password: string) {
		const res = await api.post<TokenResponse>('/api/auth/login', { email, password });
		setToken(res.access_token);
		loggedIn = true;
		await fetchUser();
	}

	async function fetchUser() {
		try {
			user = await api.get<User>('/api/auth/me');
		} catch {
			user = null;
		}
	}

	async function logout() {
		try {
			await api.post('/api/auth/logout', {});
		} catch {
			// Proceed with client-side logout even if server call fails
		}
		clearToken();
		user = null;
		loggedIn = false;
		window.location.href = '/login';
	}

	function hasRole(role: string): boolean {
		return user?.roles.includes(role) ?? false;
	}

	function hasAnyRole(...roles: string[]): boolean {
		return roles.some((r) => hasRole(r));
	}

	return {
		get user() { return user; },
		get loggedIn() { return loggedIn; },
		get isAdmin() { return hasRole('admin'); },
		get isManager() { return hasAnyRole('admin', 'ap_manager'); },
		get isCfo() { return hasAnyRole('admin', 'cfo'); },
		get isClerkOnly() { return user?.roles.length === 1 && hasRole('ap_clerk'); },
		login,
		logout,
		fetchUser,
		hasRole,
		hasAnyRole,
	};
}

export const auth = createAuthStore();
