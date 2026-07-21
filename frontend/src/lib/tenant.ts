/**
 * Extract the tenant slug from the current subdomain.
 *
 * acme.localhost:7777      → "acme"
 * techflow.localhost:7777  → "techflow"
 * acme.example.com         → "acme"
 * localhost:7777           → null (no tenant)
 * example.com              → null (bare apex — no tenant)
 *
 * A bare registrable apex (exactly 2 dot-separated labels, e.g.
 * "example.com") is the marketing/landing host, not a tenant — it must NOT
 * be treated as a subdomain. `*.localhost` is the one exception: it's also
 * 2 labels, but the local-dev convention puts the tenant slug directly in
 * front of the `localhost` TLD (there's no third label to spare), so a
 * genuine tenant subdomain there is 2 labels, not 3+.
 */
export function getTenantSlug(): string | null {
	if (typeof window === 'undefined') return null;
	const parts = window.location.hostname.split('.');

	if (parts.length === 2 && parts[1] === 'localhost') return parts[0];
	if (parts.length >= 3) return parts[0];
	return null;
}
