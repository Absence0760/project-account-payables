<script lang="ts">
	import { page } from '$app/state';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/Toast.svelte';

	let collapsed = $derived(sidebar.collapsed);
	let showProfile = $state(false);
	let editingProfile = $state(false);
	let profileName = $state('');
	let currentPassword = $state('');
	let newPassword = $state('');
	let profileSaving = $state(false);

	function openEditProfile() {
		profileName = auth.user?.full_name ?? '';
		currentPassword = '';
		newPassword = '';
		editingProfile = true;
	}

	async function saveProfile() {
		profileSaving = true;
		try {
			const changes: Record<string, string> = {};
			if (profileName.trim() && profileName !== auth.user?.full_name) {
				changes.full_name = profileName.trim();
			}
			if (newPassword.trim()) {
				changes.password = newPassword;
				changes.current_password = currentPassword;
			}
			if (Object.keys(changes).length === 0) {
				editingProfile = false;
				return;
			}
			await api.patch('/api/auth/me', changes);
			await auth.fetchUser();
			toast('Profile updated', 'success');
			editingProfile = false;
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to update profile', 'error');
		} finally {
			profileSaving = false;
		}
	}

	interface NavItem {
		label: string;
		href: string;
		icon: string;
	}

	interface NavGroup {
		title: string;
		items: NavItem[];
	}

	const navGroups: NavGroup[] = [
		{
			title: 'Overview',
			items: [
				{ label: 'Dashboard', href: '/', icon: 'dashboard' },
			],
		},
		{
			title: 'Payables',
			items: [
				{ label: 'Invoices', href: '/invoices', icon: 'invoices' },
				{ label: 'Payments', href: '/payments', icon: 'payments' },
				{ label: 'Vendors', href: '/vendors', icon: 'vendors' },
			],
		},
		{
			title: 'Processing',
			items: [
				{ label: 'Workflows', href: '/workflows', icon: 'workflows' },
			],
		},
		{
			title: 'Settings',
			items: [
				{ label: 'Organization', href: '/organization', icon: 'organization' },
				{ label: 'Admin', href: '/admin', icon: 'admin' },
			],
		},
	];

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname.startsWith(href);
	}
</script>

<aside class="sidebar" class:collapsed>
	<div class="logo">
		{#if collapsed}
			<span class="logo-mark">AP</span>
		{:else}
			<span class="logo-mark">AP</span>
			<span class="logo-text">Account Payables</span>
		{/if}
	</div>

	<nav class="nav-main">
		{#each navGroups as group}
			{#if !collapsed}
				<div class="nav-group-title">{group.title}</div>
			{:else}
				<div class="nav-group-divider"></div>
			{/if}
			{#each group.items as item}
				<a href={item.href} class="nav-item" class:active={isActive(item.href)} title={collapsed ? item.label : ''}>
					<span class="nav-icon">
						{#if item.icon === 'dashboard'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
						{:else if item.icon === 'invoices'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
						{:else if item.icon === 'workflows'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
						{:else if item.icon === 'payments'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
						{:else if item.icon === 'vendors'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
						{:else if item.icon === 'organization'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
						{:else if item.icon === 'admin'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
						{/if}
					</span>
					{#if !collapsed}
						<span class="nav-label">{item.label}</span>
					{/if}
				</a>
			{/each}
		{/each}
	</nav>

	<div class="nav-spacer"></div>

	<div class="profile-wrapper">
		{#if showProfile}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="profile-backdrop" onclick={() => (showProfile = false)} onkeydown={() => {}}></div>
			<div class="profile-popover">
				{#if editingProfile}
					<form class="profile-edit-form" onsubmit={(e) => { e.preventDefault(); saveProfile(); }}>
						<label>
							<span class="field-label">Name</span>
							<input type="text" bind:value={profileName} class="profile-input" />
						</label>
						<label>
							<span class="field-label">Current Password</span>
							<input type="password" bind:value={currentPassword} class="profile-input" placeholder="Required to change password" />
						</label>
						<label>
							<span class="field-label">New Password</span>
							<input type="password" bind:value={newPassword} class="profile-input" minlength="6" placeholder="Leave blank to keep current" />
						</label>
						<div class="profile-edit-actions">
							<button type="button" class="profile-edit-cancel" onclick={() => (editingProfile = false)}>Cancel</button>
							<button type="submit" class="profile-edit-save" disabled={profileSaving}>
								{profileSaving ? 'Saving...' : 'Save'}
							</button>
						</div>
					</form>
				{:else}
					<div class="profile-info">
						<div class="profile-name">{auth.user?.full_name ?? '—'}</div>
						<div class="profile-email">{auth.user?.email ?? '—'}</div>
					</div>
					<button class="profile-action" onclick={openEditProfile}>Edit Profile</button>
					<button class="profile-logout" onclick={() => auth.logout()}>Log Out</button>
				{/if}
			</div>
		{/if}
		<button class="profile-btn" class:collapsed title={collapsed ? 'Profile' : ''} onclick={() => (showProfile = !showProfile)}>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
			{#if !collapsed}
				<span class="profile-label">{auth.user?.full_name ?? 'Profile'}</span>
			{/if}
		</button>
	</div>

	<button class="collapse-btn" class:collapsed onclick={() => sidebar.toggle()} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
		<span class="nav-icon">
			<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class:flipped={collapsed}>
				<polyline points="15 18 9 12 15 6" />
			</svg>
		</span>
		{#if !collapsed}
			<span class="collapse-label">Collapse</span>
		{/if}
	</button>
</aside>

<style>
	.sidebar {
		position: fixed;
		top: 0;
		left: 0;
		bottom: 0;
		width: 220px;
		background: var(--surface);
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		padding: 12px 8px;
		z-index: 50;
		transition: width 0.2s ease;
	}

	.sidebar.collapsed {
		width: 60px;
	}

	.logo {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 10px 20px;
		white-space: nowrap;
		overflow: hidden;
	}

	.logo-mark {
		font-size: 1.1rem;
		font-weight: 800;
		color: var(--accent);
		flex-shrink: 0;
	}

	.logo-text {
		font-size: 1.05rem;
		font-weight: 600;
		color: var(--text);
	}

	.nav-main {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.profile-wrapper {
		position: relative;
		margin-bottom: 4px;
	}

	.profile-btn {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 9px 10px;
		border-radius: 6px;
		border: none;
		background: none;
		color: var(--text-muted);
		cursor: pointer;
		font-family: inherit;
		transition: all 0.12s;
	}

	.profile-btn.collapsed {
		justify-content: center;
		padding: 9px 0;
	}

	.profile-btn:hover {
		background: rgba(99, 140, 255, 0.08);
		color: var(--text);
	}

	.profile-label {
		font-size: 0.88rem;
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.profile-backdrop {
		position: fixed;
		inset: 0;
		z-index: 60;
	}

	.profile-popover {
		position: absolute;
		bottom: 100%;
		left: 8px;
		margin-bottom: 8px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
		padding: 12px;
		min-width: 200px;
		z-index: 61;
	}

	.profile-info {
		padding-bottom: 10px;
		border-bottom: 1px solid var(--border);
		margin-bottom: 10px;
	}

	.profile-name {
		font-size: 0.88rem;
		font-weight: 600;
		color: var(--text);
	}

	.profile-email {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin-top: 2px;
	}

	.profile-logout {
		width: 100%;
		padding: 7px 12px;
		border-radius: 5px;
		border: 1px solid #e04040;
		background: rgba(224, 64, 64, 0.1);
		color: #e04040;
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		transition: background 0.15s;
	}

	.profile-logout:hover {
		background: rgba(224, 64, 64, 0.2);
	}

	.profile-action {
		width: 100%;
		padding: 7px 12px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		margin-bottom: 6px;
	}

	.profile-action:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.profile-edit-form {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.profile-edit-form label {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}

	.field-label {
		font-size: 0.72rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.profile-input {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 6px 8px;
		font-size: 0.82rem;
		color: var(--text);
		font-family: inherit;
		width: 100%;
		box-sizing: border-box;
	}

	.profile-input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	.profile-edit-actions {
		display: flex;
		gap: 6px;
		justify-content: flex-end;
		padding-top: 4px;
		border-top: 1px solid var(--border);
	}

	.profile-edit-cancel {
		padding: 5px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.8rem;
		cursor: pointer;
		font-family: inherit;
	}

	.profile-edit-cancel:hover {
		background: var(--bg);
	}

	.profile-edit-save {
		padding: 5px 12px;
		border-radius: 4px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.8rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.profile-edit-save:hover:not(:disabled) {
		opacity: 0.85;
	}

	.profile-edit-save:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.nav-group-title {
		font-size: 0.68rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-muted);
		padding: 14px 10px 4px;
		opacity: 0.6;
	}

	.nav-group-title:first-child {
		padding-top: 0;
	}

	.nav-group-divider {
		height: 1px;
		background: var(--border);
		margin: 8px 10px;
		opacity: 0.5;
	}

	.nav-group-divider:first-child {
		display: none;
	}

	.nav-spacer {
		flex: 1;
	}

	.nav-item {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 9px 10px;
		border-radius: 6px;
		color: var(--text-muted);
		text-decoration: none;
		font-size: 0.88rem;
		font-weight: 500;
		white-space: nowrap;
		overflow: hidden;
		transition: all 0.12s;
	}

	.nav-item:hover {
		background: rgba(99, 140, 255, 0.08);
		color: var(--text);
	}

	.nav-item.active {
		background: rgba(99, 140, 255, 0.12);
		color: var(--accent);
	}

	.nav-icon {
		display: grid;
		place-items: center;
		flex-shrink: 0;
		width: 20px;
		height: 20px;
	}

	.nav-label {
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.collapse-btn {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		margin-top: 8px;
		padding: 9px 10px;
		border-radius: 6px;
		border: none;
		background: none;
		color: var(--text-muted);
		cursor: pointer;
		font-family: inherit;
		transition: all 0.12s;
	}

	.collapse-btn.collapsed {
		justify-content: center;
		padding: 9px 0;
	}

	.collapse-btn:hover {
		background: rgba(99, 140, 255, 0.08);
		color: var(--text);
	}

	.collapse-label {
		font-size: 0.88rem;
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.collapse-btn svg {
		transition: transform 0.2s ease;
	}

	.collapse-btn svg.flipped {
		transform: rotate(180deg);
	}
</style>
