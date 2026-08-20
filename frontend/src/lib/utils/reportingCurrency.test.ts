import { describe, it, expect } from 'vitest';
import { resolveReportingCurrency } from './reportingCurrency';

describe('resolveReportingCurrency', () => {
	it('prefers the explicit reporting currency over every other key', () => {
		// The bug: the store read only `invoice_defaults.currency`, so this org's
		// GBP-denominated rollups were rendered with a `$`.
		expect(
			resolveReportingCurrency({
				reporting_currency: 'GBP',
				payments: { home_currency: 'EUR' },
				invoice_defaults: { currency: 'USD' }
			})
		).toBe('GBP');
	});

	it('falls to the payments home currency when no reporting currency is set', () => {
		expect(
			resolveReportingCurrency({
				payments: { home_currency: 'EUR' },
				invoice_defaults: { currency: 'USD' }
			})
		).toBe('EUR');
	});

	it('falls to the invoice default last — the key the store used to read first', () => {
		expect(resolveReportingCurrency({ invoice_defaults: { currency: 'ZAR' } })).toBe('ZAR');
	});

	it('returns null when the org declares nothing usable, so the caller keeps its default', () => {
		expect(resolveReportingCurrency(null)).toBeNull();
		expect(resolveReportingCurrency(undefined)).toBeNull();
		expect(resolveReportingCurrency({})).toBeNull();
		expect(resolveReportingCurrency({ reporting_currency: null, payments: null })).toBeNull();
	});

	it('skips a malformed code rather than letting it win the resolution', () => {
		// A blank or wrong-length value must not shadow the next candidate —
		// otherwise a half-saved settings blob silently downgrades the label.
		expect(
			resolveReportingCurrency({
				reporting_currency: '  ',
				payments: { home_currency: 'US' },
				invoice_defaults: { currency: 'gbp' }
			})
		).toBe('GBP');
	});

	it('normalises to upper case and trims', () => {
		expect(resolveReportingCurrency({ reporting_currency: ' eur ' })).toBe('EUR');
	});
});
