<script lang="ts">
	import { page } from '$app/state';
	import { sidebar } from '$lib/stores/sidebar.svelte';

	let collapsed = $derived(sidebar.collapsed);

	interface NavItem {
		label: string;
		href: string;
		icon: string;
	}

	const mainNav: NavItem[] = [
		{ label: 'Dashboard', href: '/', icon: 'dashboard' },
		{ label: 'Invoices', href: '/invoices', icon: 'invoices' },
		{ label: 'Workflows', href: '/workflows', icon: 'workflows' },
		{ label: 'Payments', href: '/payments', icon: 'payments' },
		{ label: 'Vendors', href: '/vendors', icon: 'vendors' },
		{ label: 'Exceptions', href: '/exceptions', icon: 'exceptions' },
	];

	const bottomNav: NavItem[] = [
		{ label: 'Admin', href: '/admin', icon: 'admin' },
		{ label: 'Profile', href: '/profile', icon: 'profile' },
		{ label: 'Organization', href: '/organization', icon: 'organization' },
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
			<span class="logo-text">Payables</span>
		{/if}
	</div>

	<nav class="nav-main">
		{#each mainNav as item}
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
					{/if}
				</span>
				{#if !collapsed}
					<span class="nav-label">{item.label}</span>
				{/if}
			</a>
		{/each}
	</nav>

	<div class="nav-spacer"></div>

	<nav class="nav-bottom">
		{#each bottomNav as item}
			<a href={item.href} class="nav-item" class:active={isActive(item.href)} title={collapsed ? item.label : ''}>
				<span class="nav-icon">
					{#if item.icon === 'admin'}
						<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
					{:else if item.icon === 'profile'}
						<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
					{:else if item.icon === 'organization'}
						<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
					{/if}
				</span>
				{#if !collapsed}
					<span class="nav-label">{item.label}</span>
				{/if}
			</a>
		{/each}
	</nav>

	<button class="collapse-btn" onclick={() => sidebar.toggle()} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
		<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class:flipped={collapsed}>
			<polyline points="15 18 9 12 15 6" />
		</svg>
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

	.nav-main,
	.nav-bottom {
		display: flex;
		flex-direction: column;
		gap: 2px;
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
		display: grid;
		place-items: center;
		margin-top: 8px;
		padding: 8px;
		border-radius: 6px;
		border: none;
		background: none;
		color: var(--text-muted);
		cursor: pointer;
	}

	.collapse-btn:hover {
		background: rgba(99, 140, 255, 0.08);
		color: var(--text);
	}

	.collapse-btn svg {
		transition: transform 0.2s ease;
	}

	.collapse-btn svg.flipped {
		transform: rotate(180deg);
	}
</style>
