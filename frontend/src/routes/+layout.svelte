<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import Toast from '$lib/components/Toast.svelte';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { getTenantSlug } from '$lib/tenant';
	import { browser } from '$app/environment';

	let tenant = $state<string | null | undefined>(undefined);

	// Routes that render without a tenant context (signup flow).
	const PUBLIC_PATHS = ['/signup', '/verify'];

	$effect(() => {
		if (browser) {
			tenant = getTenantSlug();
		}
	});

	$effect(() => {
		if (!tenant) return;

		const path = $page.url.pathname;

		if (!auth.loggedIn && path !== '/login') {
			goto('/login');
			return;
		}

		if (auth.loggedIn && !auth.user) {
			auth.fetchUser();
			return;
		}

		if (
			auth.loggedIn &&
			auth.user?.must_change_password &&
			path !== '/change-password'
		) {
			goto('/change-password');
		}
	});
</script>

{#if tenant === undefined}
	<!-- SSR / hydration: tenant not resolved yet, render nothing to avoid flash -->
{:else if PUBLIC_PATHS.includes($page.url.pathname)}
	<slot />
{:else if tenant === null}
	<div class="landing">
		<div class="landing-inner">
			<h1>Better AP</h1>
			<p class="tagline">AP automation — invoices, approvals, payments.</p>
			<div class="cta">
				<a class="primary" href="/signup">Create your workspace</a>
			</div>
			<p class="sub">
				Already have one? Visit <code>your-slug.localhost:7777</code> to sign in.
			</p>
		</div>
	</div>
{:else if $page.url.pathname === '/login' || $page.url.pathname === '/change-password'}
	<slot />
{:else if auth.loggedIn && auth.user && !auth.user.must_change_password}
	<div class="app-shell">
		<Sidebar />
		<main class="main-content" style="margin-left: {sidebar.collapsed ? 60 : 220}px">
			<slot />
		</main>
	</div>
{/if}

<Toast />

<style>
	.app-shell {
		display: flex;
		min-height: 100vh;
	}

	.main-content {
		flex: 1;
		transition: margin-left 0.2s ease;
	}

	.landing {
		min-height: 100vh;
		display: grid;
		place-items: center;
		background: var(--bg);
		padding: 40px 20px;
	}

	.landing-inner {
		max-width: 520px;
		text-align: center;
		color: var(--text);
	}

	.landing h1 {
		font-size: 2.2rem;
		margin: 0 0 12px;
		font-weight: 700;
	}

	.tagline {
		font-size: 1rem;
		color: var(--text-muted);
		margin: 0 0 32px;
	}

	.cta {
		margin: 24px 0;
	}

	.cta .primary {
		display: inline-block;
		background: var(--accent);
		color: #fff;
		padding: 12px 28px;
		border-radius: 6px;
		text-decoration: none;
		font-weight: 500;
		font-size: 0.95rem;
	}

	.cta .primary:hover {
		opacity: 0.9;
	}

	.sub {
		font-size: 0.85rem;
		color: var(--text-muted);
		margin-top: 20px;
	}

	.sub code {
		background: var(--surface);
		padding: 2px 6px;
		border-radius: 3px;
		font-size: 0.82rem;
	}
</style>
