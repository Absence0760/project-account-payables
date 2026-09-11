// The exception-queue `exception_type` taxonomy, with the i18n key carrying
// each label rather than the English string itself.
//
// `Exception.exception_type` is a plain `String(50)` server-side (no DB enum),
// so the roster the backend declares is the contract: `EXCEPTION_TYPES` in
// `backend/app/services/exception_lifecycle.py`, labelled for the queue by
// `backend/app/api/exceptions.py::EXCEPTION_TYPE_LABELS`. `exception.test.ts`
// reads that Python source and fails if either drifts.
//
// Why this module exists: the agent decision log rendered its type cell as
// `exception_type.replace(/_/g, ' ')`, an English-only derivation that printed
// `po mismatch` inside an otherwise-translated table — and printed it in a
// DIFFERENT wording from the queue one tab away, which labels the same row
// `PO Mismatch` from the server's map. A de-underscored raw key cannot be
// translated at all (`decisions.md` §149 is the same defect on the screening
// verdict), so the taxonomy needed a real label map.
//
// The lifecycle `status` vocabulary deliberately does NOT live here yet: the
// queue's own badge and this panel both render the raw wire value, and keying
// one without the other is how two surfaces end up naming one status
// differently. That is filed as its own slice in `docs/followups.md`.

import type { MessageKey } from '$lib/i18n/messages';

/**
 * Every `Exception.exception_type` the platform raises, in the backend's
 * declaration order.
 *
 * `amount_exceeded` is on the roster but nothing raises it any more — the
 * backend keeps it, and its label, because historical rows still carry it and
 * must not regress to rendering a raw key. The same reasoning keeps it here.
 */
export const EXCEPTION_TYPES = [
	'duplicate',
	'po_mismatch',
	'fraud_flag',
	'extraction_failed',
	'unverified_vendor',
	'review_rejected',
	'amount_exceeded',
	'missing_data',
	'quality_hold',
	'price_variance',
	'contract_noncompliant',
	'erp_reconciliation',
	'line_total_mismatch',
	'payment_compliance_hold',
	'payment_reconciliation'
] as const;

export type ExceptionType = (typeof EXCEPTION_TYPES)[number];

/**
 * The i18n key carrying each type's label — never the English string itself.
 *
 * Each ENGLISH value is byte-identical to the matching entry in
 * `EXCEPTION_TYPE_LABELS`, which is what the queue still renders through the
 * wire's `type_label`. `exception.test.ts` asserts that equality, so the two
 * surfaces cannot disagree in English while only one of them is translated.
 */
export const EXCEPTION_TYPE_LABEL_KEYS: Record<ExceptionType, MessageKey> = {
	duplicate: 'exceptions.type.duplicate',
	po_mismatch: 'exceptions.type.poMismatch',
	fraud_flag: 'exceptions.type.fraudFlag',
	extraction_failed: 'exceptions.type.extractionFailed',
	unverified_vendor: 'exceptions.type.unverifiedVendor',
	review_rejected: 'exceptions.type.reviewRejected',
	amount_exceeded: 'exceptions.type.amountExceeded',
	missing_data: 'exceptions.type.missingData',
	quality_hold: 'exceptions.type.qualityHold',
	price_variance: 'exceptions.type.priceVariance',
	contract_noncompliant: 'exceptions.type.contractNoncompliant',
	erp_reconciliation: 'exceptions.type.erpReconciliation',
	line_total_mismatch: 'exceptions.type.lineTotalMismatch',
	payment_compliance_hold: 'exceptions.type.paymentComplianceHold',
	payment_reconciliation: 'exceptions.type.paymentReconciliation'
};

/**
 * The message key for an exception type, or `null` for one this build has no
 * wording for.
 *
 * Tolerant on purpose — the same rule `screeningCategoryLabelKey` states. The
 * column is a plain `String(50)` and a historical row can carry a type this
 * frontend predates, so the caller renders the server's own label, or
 * {@link exceptionTypeFallback}, rather than dropping the cell.
 */
export function exceptionTypeLabelKey(type: string): MessageKey | null {
	return EXCEPTION_TYPE_LABEL_KEYS[type as ExceptionType] ?? null;
}

/** Readable stand-in for an unrecognised type: the raw key, de-underscored. */
export function exceptionTypeFallback(type: string): string {
	return type.replace(/_/g, ' ');
}
