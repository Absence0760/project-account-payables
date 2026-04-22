import { portalApi, setPortalToken, clearPortalToken, hasPortalToken } from '$lib/portalApi';

interface PortalUser {
	id: string;
	email: string;
	full_name: string;
	must_change_password: boolean;
	vendor_id: string;
	vendor_name: string;
	vendor_status: string;
}

interface PortalTokenResponse {
	access_token: string;
	token_type: string;
	must_change_password?: boolean;
}

function createPortalAuth() {
	let user = $state<PortalUser | null>(null);
	let loggedIn = $state(hasPortalToken());

	async function login(email: string, password: string) {
		const res = await portalApi.post<PortalTokenResponse>('/api/portal/auth/login', {
			email,
			password,
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
		logout,
		fetchUser,
		changePassword,
	};
}

export const portalAuth = createPortalAuth();
