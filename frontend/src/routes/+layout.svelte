<script lang="ts">
	import '../app.css';
	import Landing from '$lib/components/Landing.svelte';
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

		if (!auth.loggedIn && !path.startsWith('/login')) {
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
	<Landing />
{:else if $page.url.pathname.startsWith('/login') || $page.url.pathname === '/change-password'}
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
</style>
