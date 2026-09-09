import { describe, it, expect } from 'vitest';
import {
	PAYMENT_STATUSES,
	PAYMENT_STATUS_LABEL_KEYS,
	paymentStatusLabelKey,
	type PaymentStatus
} from './payment';
import { en } from '$lib/i18n/locales/en';

/**
 * Drift guard: the frontend payment-status vocabulary must cover every status
 * the backend can persist on `payments.status`.
 *
 * A status missing here is not a cosmetic gap — the History row renders the
 * label behind `PAYMENT_STATUS_LABEL_KEYS[p.status]`, so an unlisted status
 * renders a BLANK badge, gets no filter chip, and becomes invisible in the
 * UI. That is exactly
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

	it('gives every status a label KEY that exists in the catalogue', () => {
		// `Record<PaymentStatus, MessageKey>` already fails `pnpm check` when a
		// status is added with no key — but a key naming nothing in the
		// catalogue typechecks fine and renders the raw key string in the badge
		// (`m()` falls back key → raw). This is the runtime half.
		for (const status of PAYMENT_STATUSES) {
			const key = PAYMENT_STATUS_LABEL_KEYS[status];
			expect(key, `${status} has no label key`).toBeTruthy();
			expect(Object.keys(en), `${status} → "${key}" is not in the catalogue`).toContain(key);
			expect(en[key].trim().length).toBeGreaterThan(0);
		}
	});

	it('never uses the raw enum value as its own label', () => {
		for (const status of PAYMENT_STATUSES) {
			expect(en[PAYMENT_STATUS_LABEL_KEYS[status]]).not.toBe(status);
		}
	});

	it('paymentStatusLabelKey resolves a known status and returns null otherwise', () => {
		for (const status of PAYMENT_STATUSES) {
			expect(paymentStatusLabelKey(status)).toBe(PAYMENT_STATUS_LABEL_KEYS[status]);
		}
		// The caller renders the raw value — visible and searchable — rather
		// than a blank badge, so a status the backend adds first degrades
		// gracefully until this map catches up.
		expect(paymentStatusLabelKey('some_future_status')).toBeNull();
	});

	it('includes the compliance-hold parking state', () => {
		// Called out on its own: this is the status whose absence made a held
		// payment a dead end in the UI, with no chip and no release/dismiss path.
		const held: PaymentStatus = 'pending_compliance';
		expect(PAYMENT_STATUSES).toContain(held);
		expect(en[PAYMENT_STATUS_LABEL_KEYS[held]]).toBe('Compliance Hold');
	});
});
