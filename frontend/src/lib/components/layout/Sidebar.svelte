<script lang="ts">
	import { page } from '$app/state';
	import { sidebar } from '$lib/stores/sidebar.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { notificationStore } from '$lib/stores/notifications.svelte';
	import { entityStore } from '$lib/stores/entity.svelte';
	import EntitySwitcher from '$lib/components/layout/EntitySwitcher.svelte';

	let collapsed = $derived(sidebar.collapsed);

	// Load the tenant's entities once so the switcher can render (it hides
	// itself for single-entity tenants — see EntitySwitcher).
	$effect(() => {
		entityStore.ensureLoaded();
	});
	let showProfile = $state(false);

	let unread = $derived(notificationStore.unread);
	let badgeLabel = $derived(unread > 99 ? '99+' : String(unread));

	interface NavItem {
		label: string;
		href: string;
		icon: string;
		/** If set, user must have at least one of these roles to see this item. */
		requiredRoles?: string[];
		/** When true, render the unread-notification badge. */
		badge?: boolean;
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
				{ label: 'Notifications', href: '/notifications', icon: 'bell', badge: true },
			],
		},
		{
			title: 'Payables',
			items: [
				{ label: 'Invoices', href: '/invoices', icon: 'invoices' },
				{ label: 'Credit Memos', href: '/credit-memos', icon: 'invoices', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
				{ label: 'Contracts', href: '/contracts', icon: 'invoices', requiredRoles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
				{ label: 'Expenses', href: '/expenses', icon: 'invoices', requiredRoles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
				{ label: 'Payments', href: '/payments', icon: 'payments', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
				{ label: 'Vendors', href: '/vendors', icon: 'vendors', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
				{ label: 'Purchase Orders', href: '/purchase-orders', icon: 'invoices', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
				{ label: 'Goods Receipts', href: '/goods-receipts', icon: 'invoices', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
			],
		},
		{
			title: 'Procurement',
			items: [
				{ label: 'Requisitions', href: '/requisitions', icon: 'invoices', requiredRoles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
				{ label: 'Intake', href: '/intake', icon: 'invoices', requiredRoles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
				{ label: 'Catalogs', href: '/catalogs', icon: 'vendors', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
				{ label: 'Budgets', href: '/budgets', icon: 'payments', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
			],
		},
		{
			title: 'Insights',
			items: [
				{ label: 'Cash Flow', href: '/cfo', icon: 'payments', requiredRoles: ['admin', 'cfo'] },
				{ label: '1099 Reporting', href: '/tax', icon: 'audit', requiredRoles: ['admin', 'ap_manager', 'cfo'] },
			],
		},
		{
			title: 'Processing',
			items: [
				{ label: 'Exceptions', href: '/exceptions', icon: 'exceptions', requiredRoles: ['admin', 'ap_manager'] },
				{ label: 'Workflows', href: '/workflows', icon: 'workflows', requiredRoles: ['admin'] },
			],
		},
		{
			title: 'Settings',
			items: [
				{ label: 'Audit Trail', href: '/audit', icon: 'audit', requiredRoles: ['admin', 'cfo'] },
				{ label: 'Organization', href: '/organization', icon: 'organization', requiredRoles: ['admin'] },
				{ label: 'Users', href: '/admin', icon: 'admin', requiredRoles: ['admin'] },
				{ label: 'Roles', href: '/admin/roles', icon: 'admin', requiredRoles: ['admin'] },
			],
		},
	];

	function canSeeItem(item: NavItem): boolean {
		if (!item.requiredRoles) return true;
		return auth.hasAnyRole(...item.requiredRoles);
	}

	const allHrefs = navGroups.flatMap((g) => g.items.map((i) => i.href));

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		if (page.url.pathname === href) return true;
		// Prefix match — but only if no sibling href is a more specific
		// prefix (eg. /admin must not light up when on /admin/roles).
		if (!page.url.pathname.startsWith(href + '/')) return false;
		const hasMoreSpecific = allHrefs.some(
			(other) =>
				other !== href &&
				other.startsWith(href + '/') &&
				(page.url.pathname === other || page.url.pathname.startsWith(other + '/'))
		);
		return !hasMoreSpecific;
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

	<EntitySwitcher {collapsed} />

	<nav class="nav-main">
		{#each navGroups as group}
			{@const visibleItems = group.items.filter(canSeeItem)}
			{#if visibleItems.length > 0}
				{#if !collapsed}
					<div class="nav-group-title">{group.title}</div>
				{:else}
					<div class="nav-group-divider"></div>
				{/if}
				{#each visibleItems as item}
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
						{:else if item.icon === 'exceptions'}
						<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
					{:else if item.icon === 'organization'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
						{:else if item.icon === 'admin'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
						{:else if item.icon === 'bell'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
						{:else if item.icon === 'audit'}
							<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
						{/if}
						{#if item.badge && unread > 0 && collapsed}
							<span class="nav-badge-dot" aria-hidden="true"></span>
						{/if}
					</span>
					{#if !collapsed}
						<span class="nav-label">{item.label}</span>
						{#if item.badge && unread > 0}
							<span class="nav-badge" aria-label={`${unread} unread notifications`}>{badgeLabel}</span>
						{/if}
					{/if}
				</a>
			{/each}
			{/if}
		{/each}
	</nav>

	<div class="nav-spacer"></div>

	<div class="profile-wrapper">
		{#if showProfile}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="profile-backdrop" onclick={() => (showProfile = false)} onkeydown={() => {}}></div>
			<div class="profile-popover">
				<div class="profile-info">
					<div class="profile-name">{auth.user?.full_name ?? '—'}</div>
					<div class="profile-email">{auth.user?.email ?? '—'}</div>
				</div>
				<a class="profile-action" href="/profile" onclick={() => (showProfile = false)}>
					Profile & Security
				</a>
				<button class="profile-logout" onclick={() => auth.logout()}>Log Out</button>
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
		display: flex;
		flex-direction: column;
		gap: 6px;
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
		display: block;
		box-sizing: border-box;
		width: 100%;
		padding: 7px 12px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 500;
		text-align: center;
		text-decoration: none;
		cursor: pointer;
		font-family: inherit;
	}

	.profile-action:hover {
		border-color: var(--accent);
		color: var(--accent);
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
		position: relative;
	}

	/* Collapsed sidebar — a small dot over the bell instead of the pill count. */
	.nav-badge-dot {
		position: absolute;
		top: -2px;
		right: -2px;
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: #e04040;
		border: 1px solid var(--surface);
	}

	/* Expanded sidebar — pill count pushed to the row's right edge. */
	.nav-badge {
		margin-left: auto;
		min-width: 18px;
		padding: 0 6px;
		height: 18px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		border-radius: 9px;
		background: #e04040;
		color: #fff;
		font-size: 0.7rem;
		font-weight: 700;
		line-height: 1;
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
