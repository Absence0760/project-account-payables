import { portalApi, setPortalToken, clearPortalToken, hasPortalToken } from '$lib/portalApi';

interface PortalUser {
	id: string;
	email: string;
	full_name: string;
	must_change_password: boolean;
	mfa_enabled: boolean;
	vendor_id: string;
	vendor_name: string;
	vendor_status: string;
}

interface PortalTokenResponse {
	access_token: string;
	token_type: string;
	must_change_password?: boolean;
}

interface PortalMFAChallengeResponse {
	mfa_required: boolean;
	mfa_challenge_token: string;
	methods: string[];
}

interface PortalMFAEnrollStart {
	secret: string;
	provisioning_uri: string;
	qr_code_data_url: string;
}

// Discriminated login result — mirrors the employee auth store. `'ok'` means a
// real access token was minted; `'mfa'` means the password checked out but the
// vendor still owes a TOTP code (the login page routes to the MFA step).
type LoginResult = { kind: 'ok' } | { kind: 'mfa'; challenge: string };

function isChallenge(
	res: PortalTokenResponse | PortalMFAChallengeResponse
): res is PortalMFAChallengeResponse {
	return (res as PortalMFAChallengeResponse).mfa_challenge_token !== undefined;
}

function createPortalAuth() {
	let user = $state<PortalUser | null>(null);
	let loggedIn = $state(hasPortalToken());

	async function login(email: string, password: string): Promise<LoginResult> {
		const res = await portalApi.post<PortalTokenResponse | PortalMFAChallengeResponse>(
			'/api/portal/auth/login',
			{ email, password }
		);
		if (isChallenge(res)) {
			return { kind: 'mfa', challenge: res.mfa_challenge_token };
		}
		setPortalToken(res.access_token);
		loggedIn = true;
		await fetchUser();
		return { kind: 'ok' };
	}

	// Second factor: trade the login-issued challenge token + a TOTP code for a
	// real access token.
	async function completeMfa(challenge_token: string, code: string) {
		const res = await portalApi.post<PortalTokenResponse>('/api/portal/auth/mfa/challenge', {
			challenge_token,
			code,
		});
		setPortalToken(res.access_token);
		loggedIn = true;
		await fetchUser();
	}

	async function fetchUser() {
		try {
			user = await portalApi.get<PortalUser>('/api/portal/auth/me');
		} catch {
			user = null;
		}
	}

	async function changePassword(current_password: string, new_password: string) {
		user = await portalApi.post<PortalUser>('/api/portal/auth/change-password', {
			current_password,
			new_password,
		});
	}

	// --- MFA enrollment management (authenticated portal session) ---

	async function startMfaEnrollment(): Promise<PortalMFAEnrollStart> {
		return portalApi.post<PortalMFAEnrollStart>('/api/portal/auth/mfa/enroll', {});
	}

	async function verifyMfaEnrollment(code: string) {
		user = await portalApi.post<PortalUser>('/api/portal/auth/mfa/verify', { code });
	}

	async function disableMfa(code: string) {
		user = await portalApi.post<PortalUser>('/api/portal/auth/mfa/disable', { code });
	}

	async function logout() {
		try {
			await portalApi.post('/api/portal/auth/logout', {});
		} catch {
			// client-side logout regardless
		}
		clearPortalToken();
		user = null;
		loggedIn = false;
		if (typeof window !== 'undefined') window.location.href = '/portal/login';
	}

	return {
		get user() {
			return user;
		},
		get loggedIn() {
			return loggedIn;
		},
		login,
		completeMfa,
		logout,
		fetchUser,
		changePassword,
		startMfaEnrollment,
		verifyMfaEnrollment,
		disableMfa,
	};
}

export const portalAuth = createPortalAuth();
