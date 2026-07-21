import { afterEach, describe, expect, it, vi } from 'vitest';
import { getTenantSlug } from './tenant';

// vitest.config.ts runs these under `environment: 'node'` (no jsdom), so
// `window` isn't defined by default — getTenantSlug()'s own
// `typeof window === 'undefined'` guard is exercised for free by that. To
// exercise the hostname-parsing branches we stub a minimal `window.location`
// per test and unstub it afterward so tests can't leak state into each other.
function stubHostname(hostname: string) {
	vi.stubGlobal('window', { location: { hostname } });
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('getTenantSlug', () => {
	it('returns null when window is undefined (SSR / build-time)', () => {
		// No stub — `window` stays undefined under the node test environment.
		expect(getTenantSlug()).toBeNull();
	});

	it('returns null for a bare apex domain (no tenant subdomain)', () => {
		stubHostname('example.com');
		expect(getTenantSlug()).toBeNull();
	});

	it('returns the slug for a genuine tenant subdomain on a deployed apex', () => {
		stubHostname('acme.example.com');
		expect(getTenantSlug()).toBe('acme');
	});

	it('returns the slug for a genuine tenant subdomain with a multi-label apex', () => {
		stubHostname('acme.example.co.uk');
		expect(getTenantSlug()).toBe('acme');
	});

	it('returns null for bare localhost (regression: existing local-dev case)', () => {
		stubHostname('localhost');
		expect(getTenantSlug()).toBeNull();
	});

	it('returns the slug for acme.localhost (regression: existing local-dev case)', () => {
		stubHostname('acme.localhost');
		expect(getTenantSlug()).toBe('acme');
	});

	it('returns the slug for techflow.localhost (regression: existing local-dev case)', () => {
		stubHostname('techflow.localhost');
		expect(getTenantSlug()).toBe('techflow');
	});
});
