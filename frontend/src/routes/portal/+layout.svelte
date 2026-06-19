<script lang="ts">
	import { portalAuth } from '$lib/stores/portalAuth.svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { browser } from '$app/environment';
	import { getTenantSlug } from '$lib/tenant';

	let tenant = $state<string | null | undefined>(undefined);

	$effect(() => {
		if (browser) tenant = getTenantSlug();
	});

	$effect(() => {
		if (!tenant) return;
		const path = $page.url.pathname;

		// `/portal/cards/<token>` is the email-emitted single-use reveal
		// link. The URL token is the credential — vendors don't need a
		// portal login to use it. Skip the auth redirect for this path.
		const isPublicCardReveal = path.startsWith('/portal/cards/');

		if (!portalAuth.loggedIn && path !== '/portal/login' && !isPublicCardReveal) {
			goto('/portal/login');
			return;
		}
		if (portalAuth.loggedIn && !portalAuth.user) {
			portalAuth.fetchUser();
			return;
		}
		if (
			portalAuth.loggedIn &&
			portalAuth.user?.must_change_password &&
			path !== '/portal/change-password'
		) {
			goto('/portal/change-password');
		}
	});

	async function handleLogout() {
		await portalAuth.logout();
	}
</script>

{#if tenant === undefined}
	<!-- hydrating -->
{:else if tenant === null}
	<div class="no-tenant">
		<p>The supplier portal is accessed through your customer's tenant URL.</p>
	</div>
{:else if $page.url.pathname === '/portal/login' || $page.url.pathname === '/portal/change-password' || $page.url.pathname.startsWith('/portal/cards/')}
	<slot />
{:else if portalAuth.loggedIn && portalAuth.user && !portalAuth.user.must_change_password}
	<div class="portal-shell">
		<!-- WCAG 2.4.1 Bypass Blocks. -->
		<a href="#main-content" class="skip-link">Skip to main content</a>
		<header class="portal-header">
			<div class="brand">
				<strong>Supplier Portal</strong>
				<span class="vendor">{portalAuth.user.vendor_name}</span>
			</div>
			<nav aria-label="Supplier portal">
				<a href="/portal/invoices" class:active={$page.url.pathname.startsWith('/portal/invoices')}
					>Invoices</a
				>
				<a
					href="/portal/purchase-orders"
					class:active={$page.url.pathname.startsWith('/portal/purchase-orders')}>Purchase Orders</a
				>
				<a href="/portal/payments" class:active={$page.url.pathname.startsWith('/portal/payments')}
					>Payments</a
				>
				<a
					href="/portal/discount-offers"
					class:active={$page.url.pathname.startsWith('/portal/discount-offers')}>Discounts</a
				>
				<a href="/portal/company" class:active={$page.url.pathname.startsWith('/portal/company')}
					>Company</a
				>
				<a
					href="/portal/notifications"
					class:active={$page.url.pathname.startsWith('/portal/notifications')}>Notifications</a
				>
			</nav>
			<div class="user">
				<span>{portalAuth.user.full_name}</span>
				<button type="button" onclick={handleLogout}>Log out</button>
			</div>
		</header>
		<main id="main-content" tabindex="-1" class="portal-main">
			<slot />
		</main>
	</div>
{/if}

<style>
	.no-tenant {
		min-height: 100vh;
		display: grid;
		place-items: center;
		color: var(--text-muted);
	}

	.portal-shell {
		min-height: 100vh;
		display: flex;
		flex-direction: column;
	}

	.portal-header {
		display: flex;
		align-items: center;
		gap: 24px;
		padding: 12px 24px;
		background: var(--surface);
		border-bottom: 1px solid var(--border);
	}

	.brand {
		display: flex;
		flex-direction: column;
		line-height: 1.2;
	}

	.brand strong {
		font-size: 0.95rem;
	}

	.vendor {
		font-size: 0.75rem;
		color: var(--text-muted);
	}

	nav {
		display: flex;
		gap: 16px;
		flex: 1;
		margin-left: 24px;
	}

	nav a {
		color: var(--text-muted);
		text-decoration: none;
		padding: 6px 10px;
		border-radius: 4px;
		font-size: 0.9rem;
	}

	nav a.active,
	nav a:hover {
		color: var(--text);
		background: var(--bg);
	}

	.user {
		display: flex;
		align-items: center;
		gap: 12px;
		font-size: 0.85rem;
		color: var(--text-muted);
	}

	.user button {
		background: transparent;
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 5px 10px;
		color: var(--text);
		font-size: 0.8rem;
		cursor: pointer;
	}

	.portal-main {
		flex: 1;
		padding: 24px;
	}
</style>
