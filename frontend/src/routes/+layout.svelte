<script lang="ts">
	import '../app.css';
	import Landing from '$lib/components/marketing/Landing.svelte';
	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import SectionTabs from '$lib/components/layout/SectionTabs.svelte';
	import Toast from '$lib/components/ui/Toast.svelte';
	import ConsentBanner from '$lib/components/ConsentBanner.svelte';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { notificationStore } from '$lib/stores/notifications.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { getTenantSlug } from '$lib/tenant';
	import { browser } from '$app/environment';
	import { initLocale, m } from '$lib/i18n/store.svelte';

	let tenant = $state<string | null | undefined>(undefined);

	// Detect + apply the visitor's UI language once on first client mount
	// (stored choice → navigator.languages → English). This also sets the
	// <html lang/dir> attributes and the active Intl format locale.
	$effect(() => {
		if (browser) initLocale();
	});

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

	// WCAG 1.4.10 Reflow: on narrow viewports collapse the sidebar to its icon
	// rail so the 220px panel doesn't force the page wider than the screen. Nav
	// stays reachable (icon rail), the user can still expand it. Desktop is
	// unaffected — the breakpoint only fires below 700px.
	$effect(() => {
		if (!browser) return;
		const mq = window.matchMedia('(max-width: 700px)');
		const apply = () => {
			if (mq.matches && !sidebar.collapsed) sidebar.toggle();
		};
		apply();
		mq.addEventListener('change', apply);
		return () => mq.removeEventListener('change', apply);
	});

	// Drive the sidebar's unread-notification badge. Start the 60s poll once the
	// user is fully signed in (and past the change-password gate); stop it on
	// logout so a stale timer doesn't fire 401s after the token is cleared.
	$effect(() => {
		const active =
			!!tenant &&
			auth.loggedIn &&
			!!auth.user &&
			!auth.user.must_change_password &&
			!$page.url.pathname.startsWith(PORTAL_PREFIX);
		if (active) {
			notificationStore.startPolling();
		} else {
			notificationStore.stopPolling();
		}
	});
</script>

<svelte:head>
	<title>Accounts Payable</title>
</svelte:head>

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
		<!-- WCAG 2.4.1 Bypass Blocks: first focusable element jumps past the
		     sidebar nav straight to the page content. -->
		<a href="#main-content" class="skip-link">{m('shell.skipToMain')}</a>
		<Sidebar />
		<main
			id="main-content"
			tabindex="-1"
			class="main-content"
			style="margin-left: {sidebar.collapsed ? 60 : 220}px"
		>
			<SectionTabs />
			<slot />
		</main>
	</div>
{/if}

<Toast />

<!--
	Consent banner is mounted here, outside the routed `<slot />`, so it renders
	on every surface — the app shell, the no-tenant marketing landing, the
	signup/verify flow, and the supplier portal (whose `/portal/+layout.svelte`
	only owns the slot content; this root layout still wraps it). It governs
	non-essential/analytics storage only — essential JWT auth is exempt.
-->
<ConsentBanner />

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
