<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	$effect(() => {
		if (!auth.loggedIn && $page.url.pathname !== '/login') {
			goto('/login');
		}
		if (auth.loggedIn && !auth.user) {
			auth.fetchUser();
		}
	});
</script>

{#if $page.url.pathname === '/login'}
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
</style>
