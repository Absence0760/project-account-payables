<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { auth } from '$lib/stores/auth.svelte';
	import { m } from '$lib/i18n/store.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import UsersPanel from '$lib/components/admin/UsersPanel.svelte';
	import RolesPanel from '$lib/components/admin/RolesPanel.svelte';

	// RBAC: `/api/admin/users` + `/api/admin/roles` are admin-only and 403 the
	// rest (`$lib/nav.ts` gates the sidebar Users/Roles rows to `admin`
	// accordingly). Mirror the guard every other admin-only page uses
	// (`/admin/api-keys`, `/admin/partner`, `/admin/webhooks`): wait for
	// `auth.user` to resolve before redirecting so we don't bounce before /me
	// lands, then send a non-admin home instead of letting the panels mount
	// and throw on the guaranteed 403.
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isAdmin);

	$effect(() => {
		if (userLoaded && !allowed) goto('/');
	});

	type Tab = 'users' | 'roles';
	// Users + Roles are peer tabs in the sidebar's Settings section bar
	// (`SectionTabs`), which navigates here with `?tab=`. We derive the active
	// panel straight from the URL — those are real navigations (anchor hrefs), so
	// `$page.url` updates reactively and the panel follows. `users` is the
	// default for a bare `/admin` (deep link or the /admin/roles redirect target).
	let tab = $derived<Tab>($page.url.searchParams.get('tab') === 'roles' ? 'roles' : 'users');

	// Panel instance handles — the per-tab primary action lives in the shared
	// PageHeader toolbar (outside the panels), so it reaches into the active
	// panel to open its create modal.
	let usersPanel = $state<UsersPanel>();
	let rolesPanel = $state<RolesPanel>();
</script>

<PageHeader title={m('admin.usersRoles.title')}>
	{#snippet actions()}
		{#if tab === 'users'}
			<button class="btn-primary" onclick={() => usersPanel?.openCreate()}>{m('admin.usersRoles.inviteUser')}</button>
		{:else}
			<button class="btn-primary" onclick={() => rolesPanel?.openCreate()}>{m('admin.usersRoles.createRole')}</button>
		{/if}
	{/snippet}

	<div class="tabpanel">
		{#if allowed}
			{#if tab === 'users'}
				<UsersPanel bind:this={usersPanel} />
			{:else}
				<RolesPanel bind:this={rolesPanel} />
			{/if}
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
