// Typed helpers for the payment-path RECOVERY exits — the two endpoints that
// exist to un-strand money that has already moved. Everything routes through
// the shared `api` client (Bearer + X-Tenant-Slug + X-Entity-ID + 401-bounce).
//
// Both are `payment.execute`-gated on the server; the UI mirrors that with
// `auth.can(PERM_PAYMENT_EXECUTE)` so a holder-less role never sees a control
// that can only 403. Neither moves money — one reports money that already
// moved, the other closes out a payable the rail short-paid.
//
// See `backend/docs/payments.md` § ERP Payment Sync + § Settlement-amount
// verification.
import { api } from '$lib/api';
import type { Payment } from '$lib/types/payment';

/**
 * What `POST /api/payments/runs/{run_id}/sync-erp` returns.
 *
 * Read `transitioned`, NOT `synced`, to answer "did this recover anything".
 * `synced` counts legs whose ERP-facing work completed, which stays true for a
 * settled payment whose invoice was already `paid` — so a repeat call reports
 * the same `synced` and `transitioned: 0`. The route's own docstring says so.
 */
export interface RunErpSyncResult {
	/** The run the pass ran for (echoed back as a string uuid). */
	id: string;
	/** Legs whose ERP-facing work completed. True again on a repeat call. */
	synced: number;
	/** Invoices actually moved `payment_scheduled → paid`. The success number. */
	transitioned: number;
	/** Legs the pass declined to act on (payment not `completed`, invoice past
	 *  `payment_scheduled`) — the idempotency in action, not a failure. */
	skipped: number;
	/** Legs whose settlement doesn't cover the invoice, so it stays held. Those
	 *  exit via `acceptPaymentSettlement` (or a void), not via another sync. */
	held: number;
	/** Legs that raised again. Each keeps its `erp_reconciliation` exception. */
	failed: number;
}

/**
 * Re-run the ERP sync-back for a run whose settled payments never landed.
 *
 * The exit for an invoice stranded at `payment_scheduled` after a
 * `payment_erp_sync` leg failed. Idempotent by construction (the pass skips
 * every non-`completed` payment and every invoice past `payment_scheduled`) and
 * moves no money. 409s when the run has no settled payments at all.
 *
 * Voiding is NOT the exit for that state: it returns the invoice to `approved`
 * and invites a second payment for money that already left.
 */
export function retryRunErpSync(runId: string): Promise<RunErpSyncResult> {
	return api.post<RunErpSyncResult>(`/api/payments/runs/${runId}/sync-erp`, {});
}

/**
 * Declare a short / unverifiable settlement final and release the invoice.
 *
 * The other exit from the under-settlement hold: the rail settled less than AP
 * authorized (or in a currency we never authorized), so `settlement_coverage`
 * holds the invoice at `payment_scheduled` rather than reporting it settled in
 * full. Accepting moves it to `paid` and records `reason` on the immutable
 * trail. Irreversible; the money stays where it landed.
 *
 * 409s when the settlement already covers the invoice ("nothing to accept") and
 * when the invoice is no longer held — the backend is the authority on both, so
 * surface its `detail` rather than pre-judging it client-side.
 */
export function acceptPaymentSettlement(paymentId: string, reason: string): Promise<Payment> {
	return api.post<Payment>(`/api/payments/${paymentId}/settlement/accept`, { reason });
}
