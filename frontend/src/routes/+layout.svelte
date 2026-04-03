<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { sidebar } from '$lib/stores/sidebar.svelte';
</script>

<div class="app-shell">
	<Sidebar />
	<main class="main-content" style="margin-left: {sidebar.collapsed ? 60 : 220}px">
		<slot />
	</main>
</div>

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
