<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { getTenantSlug } from '$lib/tenant';
	import { PUBLIC_API_URL } from '$env/static/public';

	interface SSOConfigPublic {
		enabled: boolean;
		provider: string | null;
		sso_only?: boolean;
	}

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);
	let ssoEnabled = $state(false);
	let ssoProviderLabel = $state<string>('');
	let samlEnabled = $state(false);
	let samlProviderLabel = $state<string>('');
	// When the tenant requires SSO, hide the password form entirely. Only ever
	// true alongside an enabled config, so a broken IdP can't lock everyone out.
	let ssoOnly = $state(false);

	const PROVIDER_LABELS: Record<string, string> = {
		okta: 'Okta',
		entra: 'Microsoft',
		oidc: 'SSO',
		saml: 'SSO',
		adfs: 'ADFS',
		onelogin: 'OneLogin',
	};

	onMount(async () => {
		const slug = getTenantSlug();
		if (!slug) return;
		// A tenant is configured for at most one protocol; query both and render
		// whichever is enabled. Both are non-fatal — password login still works.
		try {
			const cfg = await api.get<SSOConfigPublic>(
				`/api/auth/sso/config?slug=${encodeURIComponent(slug)}`
			);
			ssoEnabled = cfg.enabled;
			ssoProviderLabel = PROVIDER_LABELS[cfg.provider ?? 'oidc'] ?? 'SSO';
			if (cfg.enabled && cfg.sso_only) ssoOnly = true;
		} catch {
			// Non-fatal
		}
		try {
			const cfg = await api.get<SSOConfigPublic>(
				`/api/auth/saml/config?slug=${encodeURIComponent(slug)}`
			);
			samlEnabled = cfg.enabled;
			samlProviderLabel = PROVIDER_LABELS[cfg.provider ?? 'saml'] ?? 'SSO';
			if (cfg.enabled && cfg.sso_only) ssoOnly = true;
		} catch {
			// Non-fatal
		}
	});

	function signInWithSSO() {
		const slug = getTenantSlug();
		if (!slug) return;
		// 302 directly to the backend authorize endpoint — it builds the IdP
		// URL and redirects the browser onward. Full page nav, not fetch,
		// because we need the browser to follow the IdP's redirects.
		const base = PUBLIC_API_URL.replace(/\/+$/, '');
		window.location.href = `${base}/api/auth/sso/authorize?slug=${encodeURIComponent(slug)}`;
	}

	function signInWithSAML() {
		const slug = getTenantSlug();
		if (!slug) return;
		// Full page nav to the backend SAML login endpoint — it builds the
		// AuthnRequest and 302s onward to the IdP (same reason as OIDC).
		const base = PUBLIC_API_URL.replace(/\/+$/, '');
		window.location.href = `${base}/api/auth/saml/login?slug=${encodeURIComponent(slug)}`;
	}

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		loading = true;
		try {
			const result = await auth.login(email, password);
			if (result.kind === 'mfa') {
				// Stash the challenge in sessionStorage so the verify page can
				// pick it up. sessionStorage clears on tab close — won't outlive
				// the login attempt.
				sessionStorage.setItem('mfa_challenge', JSON.stringify(result.challenge));
				goto('/login/mfa');
				return;
			}
			goto('/');
		} catch (err) {
			error = err instanceof Error ? err.message : 'Login failed';
		} finally {
			loading = false;
		}
	}
</script>

<div class="login-page">
	<form class="login-card" onsubmit={handleSubmit}>
		<h1>Account Payables</h1>
		<p class="subtitle">Sign in to continue</p>

		<div role="alert" aria-live="assertive">
			{#if error}
				<div class="error">{error}</div>
			{/if}
		</div>

		{#if !ssoOnly}
			<label>
				<span>Email</span>
				<input type="email" bind:value={email} required autocomplete="email" />
			</label>
			<label>
				<span>Password</span>
				<input type="password" bind:value={password} required autocomplete="current-password" />
			</label>

			<button type="submit" disabled={loading}>
				{loading ? 'Signing in...' : 'Sign in'}
			</button>
		{:else}
			<p class="sso-only-note">This workspace uses single sign-on.</p>
		{/if}

		{#if !ssoOnly && (ssoEnabled || samlEnabled)}
			<div class="divider"><span>or</span></div>
		{/if}
		{#if ssoEnabled}
			<button type="button" class="sso-btn" onclick={signInWithSSO}>
				Sign in with {ssoProviderLabel}
			</button>
		{/if}
		{#if samlEnabled}
			<button type="button" class="sso-btn" onclick={signInWithSAML}>
				Sign in with {samlProviderLabel}
			</button>
		{/if}
	</form>
</div>

<style>
	.login-page {
		min-height: 100vh;
		display: grid;
		place-items: center;
		background: var(--bg);
	}

	.login-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 40px 36px;
		width: min(400px, 90vw);
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	h1 {
		margin: 0;
		font-size: 1.3rem;
		font-weight: 700;
		color: var(--text);
	}

	.subtitle {
		margin: -8px 0 8px;
		font-size: 0.88rem;
		color: var(--text-muted);
	}

	.sso-only-note {
		margin: 4px 0;
		font-size: 0.9rem;
		color: var(--text-muted);
		text-align: center;
	}

	.error {
		background: rgba(224, 64, 64, 0.1);
		border: 1px solid rgba(224, 64, 64, 0.3);
		color: #e04040;
		padding: 10px 14px;
		border-radius: 4px;
		font-size: 0.85rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	input {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 10px 12px;
		font-size: 0.9rem;
		color: var(--text);
		font-family: inherit;
	}

	input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	button {
		margin-top: 8px;
		padding: 10px;
		border-radius: 4px;
		border: none;
		background: var(--accent-strong);
		color: #fff;
		font-size: 0.9rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	button:hover:not(:disabled) {
		opacity: 0.9;
	}

	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.divider {
		display: flex;
		align-items: center;
		gap: 10px;
		color: var(--text-muted);
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		margin: 4px 0;
	}
	.divider::before,
	.divider::after {
		content: '';
		flex: 1;
		height: 1px;
		background: var(--border);
	}

	.sso-btn {
		background: transparent;
		border: 1px solid var(--border);
		color: var(--text);
	}
	.sso-btn:hover:not(:disabled) {
		border-color: var(--text-muted);
		opacity: 1;
	}
</style>
