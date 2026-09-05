/**
 * Goods-receipt status semantics.
 *
 * `GoodsReceipt.status` is a free-form `String(30)` on the backend and nothing
 * normalises it on the way in, so the frontend cannot rely on an enum. What it
 * CAN rely on is the set the backend itself treats as "this delivery did not
 * happen": `services/po_matching.CANCELLED_GR_STATUSES`, which excludes those
 * receipts from the 3-way quantity leg.
 *
 * The page used to badge every status `success`, so a reversed receipt read as
 * a successful delivery — the same row the matcher was deliberately ignoring.
 */

/** Mirrors `backend/app/services/po_matching.py::CANCELLED_GR_STATUSES`.
 *
 *  Both spellings of "cancelled" are listed because the column is free-form and
 *  neither is normalised. Compared case-insensitively, as the backend does.
 *  `goodsReceipt.test.ts` fails if this drifts from the backend set. */
export const CANCELLED_GR_STATUSES = new Set([
	'cancelled',
	'canceled',
	'void',
	'voided',
	'reversed'
]);

/** True when the backend would exclude this receipt from PO matching. */
export function isCancelledGoodsReceipt(status: string | null | undefined): boolean {
	return CANCELLED_GR_STATUSES.has((status ?? '').trim().toLowerCase());
}

/** The `<Badge>` tone for a goods-receipt status.
 *
 *  `muted` rather than `danger` for a cancellation: a reversed receipt is a
 *  decision someone made, not a failure — the same distinction `/purchase-orders`
 *  draws between `cancelled` and a status nobody told us about. */
export function goodsReceiptTone(status: string | null | undefined): 'success' | 'muted' {
	return isCancelledGoodsReceipt(status) ? 'muted' : 'success';
}
