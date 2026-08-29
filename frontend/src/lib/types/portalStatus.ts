import type { InvoiceStatus } from './invoice';
import type { PaymentStatus } from './payment';

/**
 * Vendor-facing status labels for the supplier portal.
 *
 * The supplier portal is not the internal AP console: a vendor doesn't need
 * to distinguish `sending_to_erp` from `posted_in_erp`, and the raw
 * workflow-engine / payment-rail enum values (see
 * `backend/app/models/invoice.py::InvoiceStatus` and
 * `backend/app/models/payment.py`) are internal jargon that must never
 * render verbatim in front of a supplier (persona-supplier audit finding,
 * issue #328).
 *
 * These maps collapse the internal state machines into the handful of
 * phases a vendor actually cares about — "Submitted", "Processing",
 * "Approved", "Paid", "Rejected" — while staying keyed on the SAME
 * `InvoiceStatus` / `PaymentStatus` types the internal app uses
 * (`$lib/types/invoice`, `$lib/types/payment`): a `Record<InvoiceStatus, …>`
 * fails typechecking here (and `messages_parity`-style drift guards below)
 * until a vendor label is added for a new backend status, instead of a new
 * status silently falling through to the raw string.
 */
export const PORTAL_INVOICE_STATUS_LABELS: Record<InvoiceStatus, string> = {
	new: 'Submitted',
	pending: 'Processing',
	ready_for_review: 'Under Review',
	approved: 'Approved',
	rejected: 'Rejected',
	sending_to_erp: 'Processing',
	sent_to_erp: 'Processing',
	posted_in_erp: 'Processing',
	payment_scheduled: 'Payment Scheduled',
	paid: 'Paid',
	// `done` is reachable straight from `approved` (a workflow with no ERP
	// step and no scheduled payment) as well as after `paid` — it doesn't
	// always mean "paid", so it gets its own vendor-neutral label rather
	// than reusing "Paid" and asserting something that may not be true.
	done: 'Completed',
	// System-managed retry state (`failed → pending | sending_to_erp`) — not
	// a rejection, and not something a vendor can act on.
	failed: 'Processing'
};

export const PORTAL_PAYMENT_STATUS_LABELS: Record<PaymentStatus, string> = {
	pending: 'Scheduled',
	pending_compliance: 'Processing',
	submitted: 'Processing',
	processing: 'Processing',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	voided: 'Cancelled'
};

/**
 * Fail-soft lookups for portal status rendering: an unrecognised value
 * (a not-yet-classified backend status, stale portal build, or test fixture)
 * never renders raw — falls back to a neutral "Processing" rather than
 * leaking the internal string.
 */
export function portalInvoiceStatusLabel(status: string): string {
	return (
		(PORTAL_INVOICE_STATUS_LABELS as Record<string, string>)[status] ?? 'Processing'
	);
}

export function portalPaymentStatusLabel(status: string): string {
	return (
		(PORTAL_PAYMENT_STATUS_LABELS as Record<string, string>)[status] ?? 'Processing'
	);
}

/**
 * Vendor-facing phase filter for the portal invoice list.
 *
 * A supplier filters by the same collapsed phases they SEE
 * (`PORTAL_INVOICE_STATUS_LABELS`), not by the 12 raw workflow-engine states.
 * Each phase carries the set of internal `InvoiceStatus` values it covers —
 * `derivePhaseStatuses` builds that set straight from the label map, so a new
 * backend status is grouped under whichever phase its label already put it in
 * and can never be silently un-filterable. `PORTAL_INVOICE_PHASE_ORDER` fixes
 * the chip order (roughly lifecycle order); `portalStatus.test.ts` fails if a
 * label is missing from it.
 */
export const PORTAL_INVOICE_PHASE_ORDER = [
	'Submitted',
	'Processing',
	'Under Review',
	'Approved',
	'Payment Scheduled',
	'Paid',
	'Completed',
	'Rejected',
] as const;

export type PortalInvoicePhase = (typeof PORTAL_INVOICE_PHASE_ORDER)[number];

function derivePhaseStatuses(phase: PortalInvoicePhase): InvoiceStatus[] {
	return (Object.entries(PORTAL_INVOICE_STATUS_LABELS) as [InvoiceStatus, string][])
		.filter(([, label]) => label === phase)
		.map(([status]) => status);
}

export interface PortalInvoicePhaseChip {
	phase: PortalInvoicePhase;
	statuses: InvoiceStatus[];
}

/** Ordered phase → internal-status mapping the portal invoice-filter chips
 *  render from. Phases with no backing status are dropped. */
export const PORTAL_INVOICE_PHASES: PortalInvoicePhaseChip[] = PORTAL_INVOICE_PHASE_ORDER.map(
	(phase) => ({ phase, statuses: derivePhaseStatuses(phase) })
).filter((chip) => chip.statuses.length > 0);

/**
 * The same idea for the portal payment-history list — a vendor filters by the
 * collapsed phases they SEE (`PORTAL_PAYMENT_STATUS_LABELS`), and each chip
 * carries the raw `payments.status` values behind that label.
 * `portalStatus.test.ts` fails if a payment label is missing from the order.
 */
export const PORTAL_PAYMENT_PHASE_ORDER = [
	'Scheduled',
	'Processing',
	'Completed',
	'Failed',
	'Cancelled',
] as const;

export type PortalPaymentPhase = (typeof PORTAL_PAYMENT_PHASE_ORDER)[number];

export interface PortalPaymentPhaseChip {
	phase: PortalPaymentPhase;
	statuses: PaymentStatus[];
}

export const PORTAL_PAYMENT_PHASES: PortalPaymentPhaseChip[] = PORTAL_PAYMENT_PHASE_ORDER.map(
	(phase) => ({
		phase,
		statuses: (
			Object.entries(PORTAL_PAYMENT_STATUS_LABELS) as [PaymentStatus, string][]
		)
			.filter(([, label]) => label === phase)
			.map(([status]) => status),
	})
).filter((chip) => chip.statuses.length > 0);
