<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { getTenantSlug } from '$lib/tenant';

	const tenant = getTenantSlug();

	$effect(() => {
		if (tenant && !auth.loggedIn && $page.url.pathname !== '/login') {
			goto('/login');
		}
		if (tenant && auth.loggedIn && !auth.user) {
			auth.fetchUser();
		}
	});
</script>

{#if !tenant}
	<div class="no-tenant">
		<h1>No tenant found</h1>
		<p>Access the app via a subdomain, e.g.:</p>
		<ul>
			<li><a href="http://acme.localhost:7777">acme.localhost:7777</a></li>
			<li><a href="http://techflow.localhost:7777">techflow.localhost:7777</a></li>
		</ul>
	</div>
{:else if $page.url.pathname === '/login'}
	<slot />
{:else if auth.loggedIn}
	<div class="app-shell">
		<Sidebar />
		<main class="main-content" style="margin-left: {sidebar.collapsed ? 60 : 220}px">
			<slot />
		</main>
	</div>
{/if}

<style>
	.app-shell {
		display: flex;
		min-height: 100vh;
	}

	.main-content {
		flex: 1;
		transition: margin-left 0.2s ease;
	}

	.no-tenant {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: 100vh;
		color: var(--text);
		font-family: inherit;
	}

	.no-tenant h1 {
		font-size: 1.3rem;
		margin-bottom: 8px;
	}

	.no-tenant p {
		color: var(--text-muted);
		margin-bottom: 12px;
	}

	.no-tenant ul {
		list-style: none;
		padding: 0;
	}

	.no-tenant li {
		margin: 6px 0;
	}

	.no-tenant a {
		color: var(--accent);
		text-decoration: none;
	}

	.no-tenant a:hover {
		text-decoration: underline;
	}
</style>
