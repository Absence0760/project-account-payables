<script lang="ts">
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Tabs from '$lib/components/ui/Tabs.svelte';
	import UsersPanel from '$lib/components/admin/UsersPanel.svelte';
	import RolesPanel from '$lib/components/admin/RolesPanel.svelte';

	type Tab = 'users' | 'roles';
	// Seed from the URL once on mount (deep links / the /admin/roles redirect /
	// refresh all land on the right tab). We deliberately do NOT derive from or
	// reconcile against `$page.url` afterwards: `replaceState` here does not
	// re-flow into `$page.url` reactively, so a URL-driven reconciler would
	// immediately revert a click back to the stale URL's tab. SvelteKit remounts
	// this page on real navigation (re-reading the URL below), and tab switches
	// use `replaceState` (no new history entry), so there's no back-nav desync to
	// reconcile — the local state is the source of truth within a mount.
	let tab = $state<Tab>($page.url.searchParams.get('tab') === 'roles' ? 'roles' : 'users');

	// Panel instance handles — the per-tab primary action lives in the shared
	// PageHeader toolbar (outside the panels), so it reaches into the active
	// panel to open its create modal.
	let usersPanel = $state<UsersPanel>();
	let rolesPanel = $state<RolesPanel>();

	function switchTab(next: Tab) {
		tab = next;
		const url = new URL($page.url);
		// `users` is the default — keep its URL clean (no ?tab). The URL is for
		// deep-link/refresh/bookmark fidelity; `tab` above drives the render.
		if (next === 'roles') url.searchParams.set('tab', 'roles');
		else url.searchParams.delete('tab');
		replaceState(`${url.pathname}${url.search}`, {});
	}
</script>

<PageHeader title="Users & Roles">
	{#snippet actions()}
		{#if tab === 'users'}
			<button class="btn-primary" onclick={() => usersPanel?.openCreate()}>+ Invite User</button>
		{:else}
			<button class="btn-primary" onclick={() => rolesPanel?.openCreate()}>+ Create Role</button>
		{/if}
	{/snippet}

	<Tabs
		tabs={[
			{ key: 'users', label: 'Users' },
			{ key: 'roles', label: 'Roles' },
		]}
		active={tab}
		onchange={(k) => switchTab(k as Tab)}
		ariaLabel="Users and roles"
		idPrefix="admin"
	/>

	<div class="tabpanel" role="tabpanel" id={`admin-panel-${tab}`} aria-labelledby={`admin-tab-${tab}`}>
		{#if tab === 'users'}
			<UsersPanel bind:this={usersPanel} />
		{:else}
			<RolesPanel bind:this={rolesPanel} />
		{/if}
	</div>
</PageHeader>

<style>
	/* The panel content (search row, table, load-more / role sections) relied on
	   the PageHeader `.workspace` flex-column gap when it was a direct child.
	   The tabpanel wrapper re-establishes that vertical rhythm. */
	.tabpanel {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
</style>
