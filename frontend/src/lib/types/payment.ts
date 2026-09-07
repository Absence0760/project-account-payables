import type { BadgeTone } from '$lib/components/ui/Badge.svelte';
import type { MoneyAmount } from '$lib/utils/money';

// Mirrors the statuses the backend actually persists on `payments.status`.
// `pending_compliance` is the parking state the sanctions/KYC gate
// (`services/compliance.check_payment_compliance`) leaves a payment in — it
// must be listed here or the History badge renders blank (no label) and the
// status has no filter chip, which is how a held payment stayed invisible.
// Its two exits are `POST /api/payments/{id}/compliance/{release,dismiss}`.
export type PaymentStatus =
	| 'pending'
	| 'pending_compliance'
	| 'submitted'
	| 'processing'
	| 'completed'
	| 'failed'
	| 'cancelled'
	| 'voided';

export const PAYMENT_STATUSES: PaymentStatus[] = [
	'pending',
	'pending_compliance',
	'submitted',
	'processing',
	'completed',
	'failed',
	'cancelled',
	'voided'
];

export const PAYMENT_STATUS_LABELS: Record<PaymentStatus, string> = {
	pending: 'Pending',
	pending_compliance: 'Compliance Hold',
	submitted: 'Submitted',
	processing: 'Processing',
	completed: 'Completed',
	failed: 'Failed',
	cancelled: 'Cancelled',
	voided: 'Voided'
};

/**
 * Badge tone per payment status.
 *
 * Lives here rather than on `/payments` because the same union is badged in
 * more than one place — the History table, the queue, and `RunDetailModal`'s
 * per-payment column — and the two used to disagree: the modal tinted
 * `pending` amber but had no rule at all for `submitted`, `cancelled`,
 * `voided` or `pending_compliance`, so half the union rendered untinted there
 * while `/payments` painted it. That is the drift a shared map exists to stop
 * (`frontend/CLAUDE.md` § Badge).
 *
 * `Record<PaymentStatus, …>` on purpose: the old per-status CSS rules were a
 * list you had to remember to extend, and `voided` — a real member of
 * `PAYMENT_STATUSES` — never got one, so a voided payment rendered an
 * untinted pill. A total record makes the compiler ask the question.
 *
 * `cancelled` and `voided` share `muted` (a grey tint) while a run's `draft`
 * is `neutral` (flat): "abandoned" and "reversed" are both inert money states,
 * whereas draft is money that has not been attempted yet.
 *
 * No `?? 'neutral'` at the call sites, unlike {@link RUN_STATUS_TONES} below:
 * this record is total over the union, and a status off the union lands on
 * `Badge`'s own `tone` default — which is `neutral` — rather than on a
 * fallback restating it.
 */
export const PAYMENT_STATUS_TONES: Record<PaymentStatus, BadgeTone> = {
	pending: 'warning',
	// Same tone as `pending` — both are waiting. What separates "a human must
	// clear this" from "waiting its turn" is the ring the History cell draws
	// around it (`/payments` `.compliance-ring`), a caller-owned wrapper
	// rather than a sixth tone — decisions.md §52.
	pending_compliance: 'warning',
	submitted: 'accent',
	processing: 'accent',
	completed: 'success',
	failed: 'danger',
	cancelled: 'muted',
	voided: 'muted'
};

/**
 * Badge tone per payment-RUN status — `services/payment_runs`' three claim
 * states (`draft` / `executing` / `cancelled`) plus the four its rollup
 * derives (decisions.md §41).
 *
 * `PaymentRun.status` is a bare string (the backend derives it on read), so
 * this can't be a total record and an unknown value falls back to the flat
 * `neutral` chip rather than to nothing — use {@link runStatusTone}.
 * `partial` and `executing` had no rule at all on either surface and rendered
 * untinted — `partial` especially, which is the one run status meaning "some
 * of this money failed".
 *
 * `draft` is `neutral`, not amber: `RunDetailModal` tinted it `warning` while
 * `/payments` rendered it flat, for the same run, one click apart. Flat wins —
 * a draft run is money that has not been attempted yet, which is the absence
 * of a signal rather than a weak one.
 */
export const RUN_STATUS_TONES: Record<string, BadgeTone> = {
	draft: 'neutral',
	executing: 'accent',
	submitted: 'accent',
	completed: 'success',
	partial: 'warning',
	failed: 'danger',
	cancelled: 'muted'
};

export function runStatusTone(status: string): BadgeTone {
	return RUN_STATUS_TONES[status] ?? 'neutral';
}

// UK domestic bank rails (bacs / faster_payments / chaps). A same-currency
// GBP payment to a GB vendor stays inside the UK banking system (sort code +
// account number — no IBAN, no SWIFT, no FX); the backend corridor selector
// (`payment_corridor.pick_corridor`) auto-selects `faster_payments` and honours
// an explicit `bacs` / `chaps` override. See issue #328.
export type PaymentMethod =
	| 'ach'
	| 'wire'
	| 'check'
	| 'virtual_card'
	| 'bacs'
	| 'faster_payments'
	| 'chaps';

export const PAYMENT_METHODS: PaymentMethod[] = [
	'ach',
	'wire',
	'check',
	'virtual_card',
	'bacs',
	'faster_payments',
	'chaps'
];

export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = {
	ach: 'ACH',
	wire: 'Wire',
	check: 'Check',
	virtual_card: 'Virtual Card',
	bacs: 'BACS',
	faster_payments: 'Faster Payments',
	chaps: 'CHAPS'
};

export interface Payment {
	id: string;
	correlation_id: string | null;
	invoice_id: string;
	payment_run_id: string | null;
	/** `schemas/payment.py::PaymentResponse.amount` is `MoneyAmount` — a JSON number on the wire. */
	amount: MoneyAmount;
	method: PaymentMethod | null;
	status: PaymentStatus;
	reference: string | null;
	created_at: string;
	updated_at: string | null;
	/**
	 * What the PROCESSOR says it settled, beside `amount` — what AP AUTHORIZED
	 * (`schemas/payment.py::PaymentResponse`, migration 0083). Both have been on
	 * the read surface since the settlement-verification work; nothing rendered
	 * them, which is how an invoice held at `payment_scheduled` for an
	 * under-settlement was invisible in the app.
	 *
	 * `null` means no rail ever reported a figure — NOT zero. The backend fails
	 * OPEN on that case (`payment_settlement.settlement_coverage`), so absence
	 * is never evidence of a shortfall. Whether a reported figure actually
	 * covers the invoice is the SERVER's call: never subtract or compare these
	 * two client-side (frontend/CLAUDE.md § Money formatting).
	 */
	settled_amount?: MoneyAmount;
	settled_currency?: string | null;
	/**
	 * What `amount` — the AUTHORIZED figure — is denominated in
	 * (`schemas/payment.py::PaymentResponse.currency`), off the invoice row the
	 * response already joins. `payments` has no currency column; a payment
	 * settles in its invoice's currency.
	 *
	 * Without it, six `/payments` call sites rendered the authorized amount
	 * under the ORG's default code. The sharpest was the Accept-settlement
	 * dialog, whose whole job is to put "Authorized" beside "Settled": the
	 * settled half had `settled_currency` and the authorized half had nothing,
	 * so a EUR payment showed a fabricated `$1,200.00` above a real `€1,150.00`
	 * on the screen built to catch a `currency_mismatch`.
	 *
	 * `null` means the server could not establish it — render the bare figure,
	 * never a substituted default (`docs/decisions.md` §79/§82).
	 */
	currency?: string | null;
	vendor_name: string | null;
	invoice_number: string | null;
	card_last_four: string | null;
	card_provider: string | null;
	card_id: string | null;
}

export interface PaymentRun {
	id: string;
	status: string;
	total_amount: MoneyAmount;
	/**
	 * What `total_amount` is denominated in
	 * (`schemas/payment.py::PaymentRunResponse.currency`). `payment_runs` has no
	 * currency column either — the total is one bare `Numeric`, kept meaningful
	 * by `create_payment_run_for_invoices` refusing a run whose invoices span
	 * more than one currency.
	 *
	 * `null` when the server could not PROVE one: a run with no payments, or a
	 * legacy run predating that guard whose legs disagree — in which case the
	 * total is denominated in nothing real and a code would be worse than none.
	 * Render the bare figure (`docs/decisions.md` §79/§82).
	 */
	currency?: string | null;
	initiated_by: string | null;
	executed_at: string | null;
	created_at: string;
	payment_count: number;
}
