import { api, setToken, clearToken, hasToken } from '$lib/api';
import { performAuthentication, performRegistration } from '$lib/webauthn';

export interface Passkey {
	id: string;
	name: string;
	transports: string | null;
	created_at: string | null;
	last_used_at: string | null;
}

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
	methods: string[]; // "totp" | "passkey" | "email"
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

	// --- WebAuthn / passkey LOGIN (the passkey factor of the MFA step) -----

	async function completePasskey(challengeToken: string) {
		// 1. Ask the server for an authentication challenge scoped to this user.
		const start = await api.post<{ options: any }>('/api/auth/mfa/passkey/authenticate', {
			challenge_token: challengeToken,
		});
		// 2. Run the browser ceremony (prompts Touch ID / security key).
		const credential = await performAuthentication(start.options);
		// 3. Hand the signed assertion back for verification + a real token.
		const res = await api.post<TokenResponse>('/api/auth/mfa/passkey/authenticate/verify', {
			challenge_token: challengeToken,
			credential,
		});
		setToken(res.access_token);
		loggedIn = true;
		await fetchUser();
	}

	// --- WebAuthn / passkey ENROLLMENT (authenticated, on the profile) -----

	async function listPasskeys(): Promise<Passkey[]> {
		return api.get<Passkey[]>('/api/auth/mfa/passkey');
	}

	async function registerPasskey(name: string): Promise<Passkey> {
		const start = await api.post<{ options: any }>('/api/auth/mfa/passkey/register', {});
		const credential = await performRegistration(start.options);
		const saved = await api.post<Passkey>('/api/auth/mfa/passkey/register/verify', {
			credential,
			name,
		});
		// A new factor may change the displayed MFA state — refresh the user.
		await fetchUser();
		return saved;
	}

	async function deletePasskey(id: string): Promise<void> {
		await api.delete(`/api/auth/mfa/passkey/${id}`);
		await fetchUser();
	}

	async function fetchUser() {
		try {
			user = await api.get<User>('/api/auth/me');
			// A successful /me means the token is good — keep `loggedIn` in sync.
			// The SSO callback (and any flow that obtains a token without going
			// through login()/completeMfa()) relies on this to flip the flag,
			// otherwise the layout sees `!loggedIn` and bounces back to /login.
			loggedIn = true;
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
		completePasskey,
		listPasskeys,
		registerPasskey,
		deletePasskey,
		logout,
		fetchUser,
		hasRole,
		hasAnyRole,
	};
}

export const auth = createAuthStore();
