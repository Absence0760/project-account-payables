import { describe, it, expect } from 'vitest';
import { PAYMENT_STATUSES, PAYMENT_STATUS_LABELS, type PaymentStatus } from './payment';

/**
 * Drift guard: the frontend payment-status vocabulary must cover every status
 * the backend can persist on `payments.status`.
 *
 * A status missing here is not a cosmetic gap — the History row renders
 * `PAYMENT_STATUS_LABELS[p.status]`, so an unlisted status renders a BLANK
 * badge, gets no filter chip, and becomes invisible in the UI. That is exactly
 * what happened to `pending_compliance`: the sanctions/KYC gate parks a payment
 * there, and the payment then had no chip, no label, and no way forward.
 *
 * Sources (backend):
 *   - `services/payment_adapters/base.py::PaymentStatus` — pending, submitted,
 *     processing, completed, failed, cancelled (adapter + webhook vocabulary).
 *   - `api/payments.py` sets two more directly: `voided` (POST .../void) and
 *     `pending_compliance` (the compliance hold).
 */
const BACKEND_PAYMENT_STATUSES = [
	'pending',
	'pending_compliance',
	'submitted',
	'processing',
	'completed',
	'failed',
	'cancelled',
	'voided'
] as const;

describe('PAYMENT_STATUSES', () => {
	it('covers every status the backend can persist', () => {
		for (const status of BACKEND_PAYMENT_STATUSES) {
			expect(
				PAYMENT_STATUSES as readonly string[],
				`${status} is persisted by the backend but missing from PAYMENT_STATUSES`
			).toContain(status);
		}
	});

	it('offers no status the backend never persists', () => {
		const backend = new Set<string>(BACKEND_PAYMENT_STATUSES);
		for (const status of PAYMENT_STATUSES) {
			expect(backend.has(status), `${status} is not a backend payment status`).toBe(true);
		}
	});

	it('gives every status a non-empty label (a missing one renders a blank badge)', () => {
		for (const status of PAYMENT_STATUSES) {
			const label = PAYMENT_STATUS_LABELS[status];
			expect(label, `${status} has no display label`).toBeTruthy();
			expect(label.trim().length).toBeGreaterThan(0);
		}
	});

	it('includes the compliance-hold parking state', () => {
		// Called out on its own: this is the status whose absence made a held
		// payment a dead end in the UI, with no chip and no release/dismiss path.
		const held: PaymentStatus = 'pending_compliance';
		expect(PAYMENT_STATUSES).toContain(held);
		expect(PAYMENT_STATUS_LABELS[held]).toBe('Compliance Hold');
	});
});
