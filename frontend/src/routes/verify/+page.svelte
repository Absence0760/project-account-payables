<script lang="ts">
	import { api } from '$lib/api';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { m } from '$lib/i18n/store.svelte';

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
			errorMessage = m('auth.verify.noToken');
			return;
		}
		try {
			result = await api.post<CompleteResponse>('/api/signup/complete', { token });
			phase = 'success';
		} catch (err) {
			phase = 'error';
			errorMessage = err instanceof Error ? err.message : m('auth.verify.failed');
		}
	});
</script>

<svelte:head>
	<title>{m('auth.verify.pageTitle')}</title>
</svelte:head>

<div class="page">
	<div class="card" aria-live="polite">
		{#if phase === 'pending'}
			<h1>{m('auth.verify.pendingHeading')}</h1>
			<p class="sub">
				{m('auth.verify.pendingSub')}
			</p>
			<div class="spinner"></div>
		{:else if phase === 'success' && result}
			<h1>{m('auth.verify.successHeading')}</h1>
			<p class="sub">
				{m('auth.verify.successSubPre')}<strong>{result.admin_email}</strong>{m('auth.verify.successSubPost')}
			</p>
			<ol class="steps">
				<li>{m('auth.verify.step1')}</li>
				<li>{m('auth.verify.step2')}</li>
				<li>{m('auth.verify.step3')}</li>
			</ol>
			<div class="next">
				<a class="primary" href={result.tenant_url}>{m('auth.verify.continueTo', { slug: result.slug })}</a>
			</div>
		{:else}
			<h1>{m('auth.verify.errorHeading')}</h1>
			<p class="error">{errorMessage}</p>
			<a href="/signup">{m('auth.verify.startOver')}</a>
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
		color: var(--danger);
		font-size: 0.9rem;
		margin: 0 0 16px;
	}
	.steps {
		text-align: left;
		margin: 0 auto 4px;
		max-width: 320px;
		padding-left: 20px;
		color: var(--text-muted);
		font-size: 0.85rem;
		line-height: 1.6;
	}
	.next {
		margin-top: 20px;
	}
	.next .primary {
		display: inline-block;
		background: var(--accent-strong);
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
	@media (prefers-reduced-motion: reduce) {
		.spinner {
			animation-duration: 0.01ms;
			animation-iteration-count: 1;
		}
	}
</style>
