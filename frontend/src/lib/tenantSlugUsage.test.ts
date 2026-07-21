import { describe, expect, it } from 'vitest';
import invoicesListSource from '../routes/invoices/+page.svelte?raw';
import invoiceModalSource from './components/modals/InvoiceModal.svelte?raw';

// Regression coverage for issue #170: two call sites hand-rolled
// `document.location.hostname.split('.')[0]` for the X-Tenant-Slug header on
// a raw-fetch binary-download path (bypassing $lib/api.ts by necessity — a
// blob download needs the raw Response). That duplicated getTenantSlug()'s
// logic without its bare-apex-domain guard, so a deployed apex hostname with
// no tenant subdomain would send the apex's first label as a bogus tenant
// slug instead of no header at all. These are pure source-text assertions
// (not component tests — the frontend's vitest setup targets pure modules
// only, per frontend/CLAUDE.md) proving both sites route through the single
// shared helper instead of re-deriving the subdomain independently.

const SITES: [string, string][] = [
	['routes/invoices/+page.svelte', invoicesListSource],
	['components/modals/InvoiceModal.svelte', invoiceModalSource],
];

describe('tenant-slug header derivation (issue #170 follow-up)', () => {
	it.each(SITES)('%s no longer hand-rolls hostname.split for X-Tenant-Slug', (_name, src) => {
		expect(src).not.toContain("hostname.split('.')[0]");
		expect(src).toContain("import { getTenantSlug } from '$lib/tenant'");
		expect(src).toContain("'X-Tenant-Slug': getTenantSlug() ?? ''");
	});
});
