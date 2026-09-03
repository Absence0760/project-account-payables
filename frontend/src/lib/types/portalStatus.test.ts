import { describe, it, expect } from 'vitest';
import { INVOICE_STATUSES } from './invoice';
import { PAYMENT_STATUSES } from './payment';
import {
	PORTAL_INVOICE_STATUS_LABELS,
	PORTAL_PAYMENT_STATUS_LABELS,
	PORTAL_INVOICE_PHASE_ORDER,
	PORTAL_INVOICE_PHASES,
	PORTAL_PAYMENT_PHASE_ORDER,
	PORTAL_PAYMENT_PHASES,
	portalInvoiceStatusLabel,
	portalPaymentStatusLabel
} from './portalStatus';

/**
 * Drift guard + regression test for the persona-supplier audit finding
 * (issue #328): the supplier portal used to render `inv.status` / `p.status`
 * verbatim — a vendor would see raw internal-jargon values like
 * `sending_to_erp`, `posted_in_erp`, `ready_for_review` straight from
 * `backend/app/models/invoice.py::InvoiceStatus`.
 *
 * `PORTAL_INVOICE_STATUS_LABELS` / `PORTAL_PAYMENT_STATUS_LABELS` are typed
 * as `Record<InvoiceStatus, string>` / `Record<PaymentStatus, string>`, so a
 * new backend status fails typechecking (`pnpm check`) here before it can
 * ship — but this file also proves at runtime that every INTERNAL-ONLY
 * status string (the workflow-engine / ERP-pipeline / payment-rail jargon a
 * vendor has no reason to see) is actually collapsed to something else, not
 * merely present in the map with itself as the value.
 */

// Internal enum values that must NEVER be the literal vendor-facing label —
// a portal reader with no AP context has no use for the ERP pipeline's
// internal step names.
const INTERNAL_ONLY_INVOICE_STATUSES = [
	'sending_to_erp',
	'sent_to_erp',
	'posted_in_erp',
	'ready_for_review',
	'failed'
] as const;

const INTERNAL_ONLY_PAYMENT_STATUSES = ['pending_compliance', 'submitted', 'processing'] as const;

describe('PORTAL_INVOICE_STATUS_LABELS', () => {
	it('covers every status InvoiceStatus can hold', () => {
		for (const status of INVOICE_STATUSES) {
			expect(
				PORTAL_INVOICE_STATUS_LABELS[status],
				`${status} has no portal-facing invoice label`
			).toBeTruthy();
		}
	});

	it('never renders an internal-only status as its own raw value', () => {
		for (const status of INTERNAL_ONLY_INVOICE_STATUSES) {
			expect(
				PORTAL_INVOICE_STATUS_LABELS[status],
				`${status} rendered verbatim to the supplier portal`
			).not.toBe(status);
		}
	});

	it('portalInvoiceStatusLabel never leaks a raw enum value', () => {
		for (const status of INVOICE_STATUSES) {
			expect(portalInvoiceStatusLabel(status)).not.toBe(status);
		}
		// Also fail-soft on an unrecognised value, never echoing it back raw.
		expect(portalInvoiceStatusLabel('some_future_internal_status')).not.toBe(
			'some_future_internal_status'
		);
	});
});

describe('PORTAL_PAYMENT_STATUS_LABELS', () => {
	it('covers every status PaymentStatus can hold', () => {
		for (const status of PAYMENT_STATUSES) {
			expect(
				PORTAL_PAYMENT_STATUS_LABELS[status],
				`${status} has no portal-facing payment label`
			).toBeTruthy();
		}
	});

	it('never renders an internal-only status as its own raw value', () => {
		for (const status of INTERNAL_ONLY_PAYMENT_STATUSES) {
			expect(
				PORTAL_PAYMENT_STATUS_LABELS[status],
				`${status} rendered verbatim to the supplier portal`
			).not.toBe(status);
		}
	});

	it('portalPaymentStatusLabel never leaks a raw enum value', () => {
		for (const status of PAYMENT_STATUSES) {
			expect(portalPaymentStatusLabel(status)).not.toBe(status);
		}
		expect(portalPaymentStatusLabel('some_future_internal_status')).not.toBe(
			'some_future_internal_status'
		);
	});
});

describe('PORTAL_INVOICE_PHASES (invoice-filter chips)', () => {
	it('the phase order lists every distinct vendor-facing label', () => {
		const labels = new Set(Object.values(PORTAL_INVOICE_STATUS_LABELS));
		for (const label of labels) {
			expect(
				PORTAL_INVOICE_PHASE_ORDER as readonly string[],
				`"${label}" is a vendor-facing status label with no filter chip`
			).toContain(label);
		}
	});

	it('every InvoiceStatus is reachable through exactly one phase chip', () => {
		for (const status of INVOICE_STATUSES) {
			const owning = PORTAL_INVOICE_PHASES.filter((c) => c.statuses.includes(status));
			expect(owning.length, `${status} is in ${owning.length} phase chips, expected 1`).toBe(1);
		}
	});

	it('each chip groups only statuses that share its label', () => {
		for (const chip of PORTAL_INVOICE_PHASES) {
			for (const status of chip.statuses) {
				expect(PORTAL_INVOICE_STATUS_LABELS[status]).toBe(chip.phase);
			}
		}
	});
});

describe('PORTAL_PAYMENT_PHASES (payment-filter chips)', () => {
	it('the phase order lists every distinct vendor-facing payment label', () => {
		for (const label of new Set(Object.values(PORTAL_PAYMENT_STATUS_LABELS))) {
			expect(
				PORTAL_PAYMENT_PHASE_ORDER as readonly string[],
				`"${label}" is a vendor-facing payment label with no filter chip`
			).toContain(label);
		}
	});

	it('every PaymentStatus is reachable through exactly one phase chip', () => {
		for (const status of PAYMENT_STATUSES) {
			const owning = PORTAL_PAYMENT_PHASES.filter((c) => c.statuses.includes(status));
			expect(owning.length, `${status} is in ${owning.length} phase chips, expected 1`).toBe(1);
		}
	});

	it('each chip groups only statuses that share its label', () => {
		for (const chip of PORTAL_PAYMENT_PHASES) {
			for (const status of chip.statuses) {
				expect(PORTAL_PAYMENT_STATUS_LABELS[status]).toBe(chip.phase);
			}
		}
	});
});
