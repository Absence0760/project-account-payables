<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		loading = true;
		try {
			await auth.login(email, password);
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

		{#if error}
			<div class="error">{error}</div>
		{/if}

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
		background: var(--accent);
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
</style>
