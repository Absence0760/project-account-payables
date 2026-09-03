import { describe, it, expect } from 'vitest';
import { PAYMENT_METHODS, PAYMENT_METHOD_LABELS } from './payment';

/**
 * Drift guard: the frontend payment-method vocabulary must cover every rail the
 * backend can stamp onto `payments.method`, and every listed method must have a
 * non-empty display label (a missing one renders a blank cell in the payments
 * method dropdowns / History column).
 *
 * Sources (backend):
 *   - `app/schemas/payment.py::PaymentMethod` — ach, wire, check, virtual_card,
 *     and the UK domestic rails bacs / faster_payments / chaps (issue #328).
 *   - `services/payment_corridor.CORRIDOR_OVERRIDE_FEES` selects the same rails.
 *
 * The UK rails were added so a same-currency GBP→GB payment routes onto Faster
 * Payments instead of falling through to `international_wire` (SWIFT + a 2.5 %
 * fee anchor).
 */
const BACKEND_PAYMENT_METHODS = [
	'ach',
	'wire',
	'check',
	'virtual_card',
	'bacs',
	'faster_payments',
	'chaps'
] as const;

describe('PAYMENT_METHODS', () => {
	it('covers every rail the backend can persist', () => {
		for (const method of BACKEND_PAYMENT_METHODS) {
			expect(
				PAYMENT_METHODS as readonly string[],
				`${method} is a backend rail but missing from PAYMENT_METHODS`
			).toContain(method);
		}
	});

	it('offers no method the backend never persists', () => {
		const backend = new Set<string>(BACKEND_PAYMENT_METHODS);
		for (const method of PAYMENT_METHODS) {
			expect(backend.has(method), `${method} is not a backend payment rail`).toBe(true);
		}
	});

	it('gives every method a non-empty label', () => {
		for (const method of PAYMENT_METHODS) {
			const label = PAYMENT_METHOD_LABELS[method];
			expect(label, `${method} has no display label`).toBeTruthy();
			expect(label.trim().length).toBeGreaterThan(0);
		}
	});

	it('labels the UK domestic rails with their industry names', () => {
		expect(PAYMENT_METHOD_LABELS.bacs).toBe('BACS');
		expect(PAYMENT_METHOD_LABELS.faster_payments).toBe('Faster Payments');
		expect(PAYMENT_METHOD_LABELS.chaps).toBe('CHAPS');
	});
});
