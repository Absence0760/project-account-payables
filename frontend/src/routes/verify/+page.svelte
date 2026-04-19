<script lang="ts">
	import { api } from '$lib/api';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';

	interface CompleteResponse {
		status: string;
		slug: string;
		tenant_url: string;
		admin_email: string;
	}

	let phase = $state<'pending' | 'success' | 'error'>('pending');
	let result = $state<CompleteResponse | null>(null);
	let errorMessage = $state<string>('');

	onMount(async () => {
		const token = $page.url.searchParams.get('token');
		if (!token) {
			phase = 'error';
			errorMessage = 'No verification token in the URL.';
			return;
		}
		try {
			result = await api.post<CompleteResponse>('/api/signup/complete', { token });
			phase = 'success';
		} catch (err) {
			phase = 'error';
			errorMessage = err instanceof Error ? err.message : 'Verification failed.';
		}
	});
</script>

<svelte:head>
	<title>Verifying — Better AP</title>
</svelte:head>

<div class="page">
	<div class="card">
		{#if phase === 'pending'}
			<h1>Creating your workspace…</h1>
			<p class="sub">
				This usually takes a few seconds — we're provisioning your database and sending
				your credentials.
			</p>
			<div class="spinner"></div>
		{:else if phase === 'success' && result}
			<h1>You're in.</h1>
			<p class="sub">
				We sent the tenant URL and your temporary password to <strong>{result.admin_email}</strong>.
				You'll be asked to change your password on first login.
			</p>
			<div class="next">
				<a class="primary" href={result.tenant_url}>Go to {result.slug}</a>
			</div>
		{:else}
			<h1>Something went wrong</h1>
			<p class="error">{errorMessage}</p>
			<a href="/signup">Start over</a>
		{/if}
	</div>
</div>

<style>
	.page {
		min-height: 100vh;
		display: grid;
		place-items: center;
		background: var(--bg);
		padding: 40px 20px;
	}
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 40px 36px;
		width: min(480px, 92vw);
		text-align: center;
		color: var(--text);
	}
	h1 {
		margin: 0 0 12px;
		font-size: 1.3rem;
		font-weight: 700;
	}
	.sub {
		color: var(--text-muted);
		font-size: 0.9rem;
		margin: 0 0 16px;
	}
	.error {
		color: #e04040;
		font-size: 0.9rem;
		margin: 0 0 16px;
	}
	.next {
		margin-top: 20px;
	}
	.next .primary {
		display: inline-block;
		background: var(--accent);
		color: #fff;
		padding: 12px 28px;
		border-radius: 6px;
		text-decoration: none;
		font-weight: 500;
	}
	.spinner {
		width: 24px;
		height: 24px;
		border: 3px solid var(--border);
		border-top-color: var(--accent);
		border-radius: 50%;
		margin: 16px auto 0;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to { transform: rotate(360deg); }
	}
</style>
