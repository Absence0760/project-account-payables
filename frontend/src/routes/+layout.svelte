<script lang="ts">
	import '../app.css';
	import Landing from '$lib/components/marketing/Landing.svelte';
	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import Toast from '$lib/components/ui/Toast.svelte';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { getTenantSlug } from '$lib/tenant';
	import { browser } from '$app/environment';

	let tenant = $state<string | null | undefined>(undefined);

	// Routes that render without a tenant context (signup flow).
	const PUBLIC_PATHS = ['/signup', '/verify'];

	// The supplier portal runs on the tenant subdomain but uses a separate
	// auth surface (VendorUser, not User). Bypass the root-layout's
	// employee-auth logic for any /portal path — `/portal/+layout.svelte`
	// handles portal-specific routing.
	const PORTAL_PREFIX = '/portal';

	$effect(() => {
		if (browser) {
			tenant = getTenantSlug();
		}
	});

	$effect(() => {
		if (!tenant) return;

		const path = $page.url.pathname;

		// Portal has its own auth tree — don't interleave the two.
		if (path.startsWith(PORTAL_PREFIX)) return;

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
{:else if $page.url.pathname.startsWith(PORTAL_PREFIX)}
	<slot />
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
		/* Without min-width: 0, a flex item's intrinsic minimum is its content
		   width — wide tables/grids would push the page wider than the
		   viewport, breaking the sidebar and clipping headers/buttons. */
		min-width: 0;
		transition: margin-left 0.2s ease;
	}
</style>
