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
// only, per frontend/CLAUDE.md) proving neither site re-derives the subdomain.
//
// The invariant is "never re-derive the slug", not "call getTenantSlug here":
// a site that has since moved onto the shared client (`api.downloadBlob`,
// which sets Authorization + X-Tenant-Slug + X-Entity-ID centrally) satisfies
// it without touching the header at all, and is strictly better than the
// hand-built version this test was originally written against. So the
// getTenantSlug requirement applies only to a file that still builds the
// header itself.

const SITES: [string, string][] = [
	['routes/invoices/+page.svelte', invoicesListSource],
	['components/modals/InvoiceModal.svelte', invoiceModalSource],
];

describe('tenant-slug header derivation (issue #170 follow-up)', () => {
	it.each(SITES)('%s never re-derives the tenant slug from the hostname', (_name, src) => {
		expect(src).not.toContain("hostname.split('.')[0]");
		expect(src).not.toContain('hostname.split(".")[0]');
	});

	it.each(SITES)('%s builds X-Tenant-Slug via getTenantSlug, or not at all', (_name, src) => {
		// Match the header as an object KEY, not any mention of it — a file that
		// routes through $lib/api names it only in a comment.
		if (!src.includes("'X-Tenant-Slug':")) return;
		expect(src).toContain("import { getTenantSlug } from '$lib/tenant'");
		expect(src).toContain("'X-Tenant-Slug': getTenantSlug() ?? ''");
	});
});
