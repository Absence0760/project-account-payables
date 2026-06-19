<script lang="ts">
	import { portalAuth } from '$lib/stores/portalAuth.svelte';
	import { goto } from '$app/navigation';

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);

	// MFA second-factor step. When login returns a challenge, we swap the
	// password form for the TOTP-code form (stashing the challenge token).
	let mfaChallenge = $state<string | null>(null);
	let mfaCode = $state('');

	function afterAuth() {
		if (portalAuth.user?.must_change_password) {
			goto('/portal/change-password');
		} else {
			goto('/portal/invoices');
		}
	}

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		loading = true;
		try {
			const res = await portalAuth.login(email, password);
			if (res.kind === 'mfa') {
				mfaChallenge = res.challenge;
				mfaCode = '';
			} else {
				afterAuth();
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Sign-in failed';
		} finally {
			loading = false;
		}
	}

	async function handleMfaSubmit(e: Event) {
		e.preventDefault();
		if (!mfaChallenge) return;
		error = '';
		loading = true;
		try {
			await portalAuth.completeMfa(mfaChallenge, mfaCode);
			afterAuth();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Verification failed';
		} finally {
			loading = false;
		}
	}

	function backToPassword() {
		mfaChallenge = null;
		mfaCode = '';
		error = '';
	}
</script>

<div class="login-page">
	{#if mfaChallenge}
		<form class="login-card" onsubmit={handleMfaSubmit}>
			<h1>Two-factor authentication</h1>
			<p class="subtitle">Enter the 6-digit code from your authenticator app</p>

			<div role="alert" aria-live="assertive">
				{#if error}
					<div class="error">{error}</div>
				{/if}
			</div>

			<label>
				<span>Authentication code</span>
				<input
					type="text"
					inputmode="numeric"
					autocomplete="one-time-code"
					bind:value={mfaCode}
					maxlength="8"
					required
				/>
			</label>

			<button type="submit" disabled={loading}>
				{loading ? 'Verifying...' : 'Verify'}
			</button>
			<button type="button" class="link-btn" onclick={backToPassword}>Back to sign in</button>
		</form>
	{:else}
		<form class="login-card" onsubmit={handleSubmit}>
			<h1>Supplier Portal</h1>
			<p class="subtitle">Sign in to submit invoices and view payments</p>

			<div role="alert" aria-live="assertive">
				{#if error}
					<div class="error">{error}</div>
				{/if}
			</div>

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
		</form>
	{/if}
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
	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.link-btn {
		margin-top: 0;
		background: transparent;
		color: var(--text-muted);
		font-weight: 400;
		font-size: 0.82rem;
	}
</style>
