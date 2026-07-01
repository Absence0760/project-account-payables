import { test, expect } from 'vitest';
import { NAV, isEntryActive, sectionTabActive, type NavLink, type NavGroup } from './nav';

const links = NAV.filter((e): e is NavLink => e.kind === 'link');
const link = (href: string): NavLink => links.find((l) => l.href === href)!;

const settings = NAV.find(
	(e): e is NavGroup => e.kind === 'group' && e.label === 'Settings'
)!;
const kids = settings.children;
const kid = (href: string) => kids.find((c) => c.href === href)!;
const url = (p: string) => new URL(`http://acme.localhost:7777${p}`);

test('isEntryActive: a plain link is active on its path and sub-paths', () => {
	const vendors = link('/vendors');
	expect(isEntryActive(vendors, '/vendors')).toBe(true);
	expect(isEntryActive(vendors, '/vendors/abc-123')).toBe(true);
	expect(isEntryActive(vendors, '/payments')).toBe(false);
});

test('isEntryActive: the most specific nested link wins (Vendors vs Screening)', () => {
	const vendors = link('/vendors');
	const screening = link('/vendors/screening');
	// On the screening sub-route, ONLY Screening lights up — not its parent.
	expect(isEntryActive(vendors, '/vendors/screening')).toBe(false);
	expect(isEntryActive(screening, '/vendors/screening')).toBe(true);
	// And the deeper link is not active on the plain list page.
	expect(isEntryActive(screening, '/vendors')).toBe(false);
	expect(isEntryActive(vendors, '/vendors')).toBe(true);
});

test('sectionTabActive: bare /admin defaults to the first same-path tab (Users)', () => {
	expect(sectionTabActive(kid('/admin?tab=users'), kids, url('/admin'))).toBe(true);
	expect(sectionTabActive(kid('/admin?tab=roles'), kids, url('/admin'))).toBe(false);
});

test('sectionTabActive: an explicit ?tab= selects exactly that tab', () => {
	expect(sectionTabActive(kid('/admin?tab=roles'), kids, url('/admin?tab=roles'))).toBe(true);
	expect(sectionTabActive(kid('/admin?tab=users'), kids, url('/admin?tab=roles'))).toBe(false);
});

test('sectionTabActive: a deeper sibling path does not light up the query tab', () => {
	// On /admin/api-keys, the API Keys tab is active and the /admin?tab=users
	// default tab is NOT (the prefix-overmatch bug this fix closes).
	expect(sectionTabActive(kid('/admin/api-keys'), kids, url('/admin/api-keys'))).toBe(true);
	expect(sectionTabActive(kid('/admin?tab=users'), kids, url('/admin/api-keys'))).toBe(false);
	expect(sectionTabActive(kid('/admin?tab=roles'), kids, url('/admin/api-keys'))).toBe(false);
});
