/**
 * Extract the tenant slug from the current subdomain.
 *
 * acme.localhost:7777      → "acme"
 * techflow.localhost:7777  → "techflow"
 * acme.app.com             → "acme"
 * localhost:7777            → null (no tenant)
 */
export function getTenantSlug(): string | null {
	if (typeof window === 'undefined') return null;
	const parts = window.location.hostname.split('.');
	if (parts.length >= 2) return parts[0];
	return null;
}
