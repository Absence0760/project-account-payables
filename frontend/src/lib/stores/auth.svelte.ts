import { api, setToken, clearToken, hasToken } from '$lib/api';

interface User {
	id: string;
	email: string;
	full_name: string;
	organization_id: string;
	is_active: boolean;
	must_change_password: boolean;
	mfa_enabled: boolean;
	mfa_required_by_org: boolean;
	roles: string[];
}

interface TokenResponse {
	access_token: string;
	token_type: string;
	must_change_password?: boolean;
}

export interface MFAChallenge {
	mfa_required: true;
	mfa_challenge_token: string;
	methods: string[]; // "totp" | "email"
	must_enroll: boolean;
}

type LoginResult =
	| { kind: 'ok' }
	| { kind: 'mfa'; challenge: MFAChallenge };

function createAuthStore() {
	let user = $state<User | null>(null);
	let loggedIn = $state(hasToken());

	async function login(email: string, password: string): Promise<LoginResult> {
		const res = await api.post<TokenResponse | MFAChallenge>('/api/auth/login', {
			email,
			password,
		});
		// MFA challenge — caller routes to the verify page
		if ('mfa_required' in res && res.mfa_required) {
			return { kind: 'mfa', challenge: res };
		}
		const tok = res as TokenResponse;
		setToken(tok.access_token);
		loggedIn = true;
		await fetchUser();
		return { kind: 'ok' };
	}

	async function completeMfa(challengeToken: string, code: string, method: 'totp' | 'email') {
		const res = await api.post<TokenResponse>('/api/auth/mfa/verify', {
			challenge_token: challengeToken,
			code,
			method,
		});
		setToken(res.access_token);
		loggedIn = true;
		await fetchUser();
	}

	async function requestEmailMfa(challengeToken: string) {
		await api.post('/api/auth/mfa/challenge/email', { challenge_token: challengeToken });
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
		completeMfa,
		requestEmailMfa,
		logout,
		fetchUser,
		hasRole,
		hasAnyRole,
	};
}

export const auth = createAuthStore();
