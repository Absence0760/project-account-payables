/**
 * Single source of truth for the app's primary navigation.
 *
 * Two kinds of entries:
 *  - `link`  — a high-traffic destination that gets its own sidebar row.
 *  - `group` — a cluster of lower-traffic routes folded behind ONE sidebar row.
 *              Clicking it lands on its first accessible child; the page then
 *              shows the group's children as a sub-tab bar (see
 *              `components/layout/SectionTabs.svelte`).
 *
 * Both the sidebar (`components/layout/Sidebar.svelte`) and the section sub-tab
 * bar read from `NAV` so the structure + RBAC live in exactly one place.
 *
 * `roles` is the at-least-one-of gate (omitted = visible to every role). A
 * group is visible when at least one child is; its sidebar link points at the
 * first child the current role can see. Child order = sub-tab order.
 */

export interface NavLink {
	kind: 'link';
	label: string;
	href: string;
	icon: string;
	roles?: string[];
}

export interface NavChild {
	label: string;
	href: string;
	roles?: string[];
}

export interface NavGroup {
	kind: 'group';
	label: string;
	icon: string;
	children: NavChild[];
}

export type NavEntry = NavLink | NavGroup;

/** Matches the signature of `auth.hasAnyRole`. */
export type RoleCheck = (...roles: string[]) => boolean;

export const NAV: NavEntry[] = [
	{ kind: 'link', label: 'Dashboard', href: '/', icon: 'dashboard' },
	{ kind: 'link', label: 'Invoices', href: '/invoices', icon: 'invoices' },
	{ kind: 'link', label: 'Payments', href: '/payments', icon: 'payments', roles: ['admin', 'ap_manager', 'cfo'] },
	{ kind: 'link', label: 'Vendors', href: '/vendors', icon: 'vendors', roles: ['admin', 'ap_manager', 'cfo'] },
	{ kind: 'link', label: 'Exceptions', href: '/exceptions', icon: 'exceptions', roles: ['admin', 'ap_manager'] },
	{
		kind: 'group',
		label: 'Procurement',
		icon: 'cart',
		children: [
			{ label: 'Purchase Orders', href: '/purchase-orders', roles: ['admin', 'ap_manager', 'cfo'] },
			{ label: 'Goods Receipts', href: '/goods-receipts', roles: ['admin', 'ap_manager', 'cfo'] },
			{ label: 'Requisitions', href: '/requisitions', roles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
			{ label: 'Intake', href: '/intake', roles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
			{ label: 'Catalogs', href: '/catalogs', roles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
			{ label: 'Budgets', href: '/budgets', roles: ['admin', 'ap_manager', 'cfo'] },
		],
	},
	{
		kind: 'group',
		label: 'Billing',
		icon: 'receipt',
		children: [
			{ label: 'Contracts', href: '/contracts', roles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
			{ label: 'Expenses', href: '/expenses', roles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
			{ label: 'Credit Memos', href: '/credit-memos', roles: ['admin', 'ap_manager', 'cfo'] },
			{ label: 'Discounts', href: '/discounts', roles: ['admin', 'ap_manager', 'cfo'] },
		],
	},
	{
		kind: 'group',
		label: 'Insights',
		icon: 'assistant',
		children: [
			{ label: 'AI Assistant', href: '/assistant', roles: ['admin', 'ap_manager', 'ap_clerk', 'cfo'] },
			{ label: 'Cash Flow', href: '/cfo', roles: ['admin', 'cfo'] },
			{ label: '1099 Reporting', href: '/tax', roles: ['admin', 'ap_manager', 'cfo'] },
		],
	},
	{
		kind: 'group',
		label: 'Settings',
		icon: 'settings',
		children: [
			{ label: 'Organization', href: '/organization', roles: ['admin'] },
			// Users + Roles share the /admin route via ?tab=; they're surfaced as
			// peer section tabs (not a second tab row inside the page).
			{ label: 'Users', href: '/admin?tab=users', roles: ['admin'] },
			{ label: 'Roles', href: '/admin?tab=roles', roles: ['admin'] },
			{ label: 'Audit Trail', href: '/audit', roles: ['admin', 'cfo'] },
			{ label: 'Workflows', href: '/workflows', roles: ['admin'] },
		],
	},
];

export function canSee(roles: string[] | undefined, has: RoleCheck): boolean {
	return !roles || has(...roles);
}

/** The children of a group the current role is allowed to see, in order. */
export function visibleChildren(group: NavGroup, has: RoleCheck): NavChild[] {
	return group.children.filter((c) => canSee(c.roles, has));
}

/** A link is visible by its own gate; a group is visible if any child is. */
export function isEntryVisible(entry: NavEntry, has: RoleCheck): boolean {
	return entry.kind === 'link' ? canSee(entry.roles, has) : visibleChildren(entry, has).length > 0;
}

/** Where a group's sidebar row navigates to — its first accessible child. */
export function groupHref(group: NavGroup, has: RoleCheck): string | null {
	return visibleChildren(group, has)[0]?.href ?? null;
}

/**
 * `/foo` is active on `/foo` and `/foo/bar`; `/` only on exactly `/`. Any query
 * string / hash on `href` is ignored here — path comparison only. Query-aware
 * active state (e.g. `/admin?tab=roles`) is handled by {@link sectionTabActive}.
 */
export function pathMatches(href: string, pathname: string): boolean {
	const path = href.split(/[?#]/)[0];
	if (path === '/') return pathname === '/';
	return pathname === path || pathname.startsWith(path + '/');
}

/**
 * Whether a section tab is the active one for the current URL. Handles tabs that
 * differ only by query (Users/Roles both live at `/admin`):
 *  - exact match on every query param the tab specifies → active;
 *  - if the URL carries none of those params, the FIRST sibling sharing that
 *    path is the default and wins (so bare `/admin` lights up Users).
 */
export function sectionTabActive(child: NavChild, siblings: NavChild[], url: URL): boolean {
	const [path, qs] = child.href.split('?');
	if (!pathMatches(path, url.pathname)) return false;
	if (!qs) return true;

	const want = new URLSearchParams(qs);
	const keys = [...want.keys()];
	if (keys.every((k) => url.searchParams.get(k) === want.get(k))) return true;

	// URL specifies none of this tab's params → the first sibling on this path
	// is the default selection.
	if (keys.every((k) => url.searchParams.get(k) === null)) {
		const firstOnPath = siblings.find((s) => s.href.split('?')[0] === path);
		return firstOnPath?.href === child.href;
	}
	return false;
}

/** True when the current path belongs to this entry (link or any group child). */
export function isEntryActive(entry: NavEntry, pathname: string): boolean {
	return entry.kind === 'link'
		? pathMatches(entry.href, pathname)
		: entry.children.some((c) => pathMatches(c.href, pathname));
}

/**
 * The group that owns `pathname`, or null if the path is a top-level link (or
 * unknown). Structural only — does NOT apply RBAC, so the caller still filters
 * the returned group's children by role before rendering the sub-tab bar.
 */
export function groupForPath(pathname: string): NavGroup | null {
	for (const e of NAV) {
		if (e.kind === 'group' && e.children.some((c) => pathMatches(c.href, pathname))) return e;
	}
	return null;
}
