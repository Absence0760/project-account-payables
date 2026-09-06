/**
 * Structured e-invoicing — the standards-compliant document generator and the
 * PEPPOL AS4 transmission, both hung off an invoice.
 *
 * This is NOT the flat ERP-upload dump: it emits real e-invoice dialects (UBL
 * 2.1 / UN-CEFACT CII, plus the four national formats) that a receiver's own
 * AP system can ingest, and every dialect is tax-validated before it is
 * emitted — a document that would be non-compliant is refused with a 422
 * rather than downloaded. See `backend/docs/e-invoicing.md` and
 * `backend/docs/peppol.md`.
 *
 * All requests route through the shared `api` client (Bearer + X-Tenant-Slug +
 * X-Entity-ID + the 401 clear-and-bounce), so this module never hand-rolls a
 * `fetch` and can't drift from the rest of the app's transport.
 */
import { api } from '$lib/api';
import type { MessageKey } from '$lib/i18n/messages';

/** The `?format=` tokens `GET /api/invoices/{id}/einvoice` accepts.
 *  `ubl` / `cii` are the built-in dialects; the rest are national formats
 *  resolved from the backend's own country-format registry
 *  (`services/e_invoice/country_formats/`), which stays the source of truth —
 *  an unknown token is a 400 there, never a silent fallback here. */
export type EInvoiceFormat = 'ubl' | 'cii' | 'fatturapa' | 'cfdi' | 'nfe' | 'dian';

export interface EInvoiceFormatOption {
	format: EInvoiceFormat;
	/** Menu label. Typed so `m()` stays checked (dynamic keys go through a map). */
	labelKey: MessageKey;
}

/** Menu order: the two portable dialects first, then the national ones. Every
 *  registered format emits XML (`CountryEInvoiceFormat.file_extension`
 *  defaults to `xml` and none override it), so the filename extension is not
 *  per-format state — see `einvoiceFilename`. */
export const E_INVOICE_FORMATS: readonly EInvoiceFormatOption[] = [
	{ format: 'ubl', labelKey: 'invoices.modal.einvoice.format.ubl' },
	{ format: 'cii', labelKey: 'invoices.modal.einvoice.format.cii' },
	{ format: 'fatturapa', labelKey: 'invoices.modal.einvoice.format.fatturapa' },
	{ format: 'cfdi', labelKey: 'invoices.modal.einvoice.format.cfdi' },
	{ format: 'nfe', labelKey: 'invoices.modal.einvoice.format.nfe' },
	{ format: 'dian', labelKey: 'invoices.modal.einvoice.format.dian' }
];

/** Mirror of the backend's own download filename (`einvoice-<n>.xml` for UBL,
 *  `einvoice-<n>-<format>.xml` for every other dialect) — `api.downloadBlob`
 *  resolves a `Blob`, not the response headers, so the caller names the file,
 *  exactly as the audit / remittance / bulk-export downloads already do. */
export function einvoiceFilename(format: EInvoiceFormat, invoiceRef: string): string {
	const base = invoiceRef || 'invoice';
	return format === 'ubl' ? `einvoice-${base}.xml` : `einvoice-${base}-${format}.xml`;
}

/** Generate + download one dialect for `invoiceId`.
 *
 * Throws `ApiError` on failure — notably **422** when the invoice is not valid
 * for the requested dialect. That body is the backend's PII-free STRUCTURED
 * error list (`{loc, type, msg}` per field — FastAPI's own validation-error
 * shape), so `api.downloadBlob`'s shared `formatApiDetail` renders it as
 * `"field: message; field: message"` with no special-casing; the thrown
 * message IS the explanation, and `parseEInvoiceIssues` splits it back into
 * per-field rows for rendering.
 */
export function downloadEInvoice(invoiceId: string, format: EInvoiceFormat): Promise<Blob> {
	return api.downloadBlob(
		`/api/invoices/${invoiceId}/einvoice?format=${encodeURIComponent(format)}`
	);
}

export interface PeppolSendRequest {
	receiver_scheme: string;
	receiver_value: string;
	sender_scheme?: string;
	sender_value?: string;
}

export interface PeppolSendResult {
	transmission_id: string;
	/** "sent" | "failed" | the existing transmission's status on a re-send. */
	status: string;
	message_id: string | null;
	direction: string;
	/** True when the idempotency short-circuit hit — the invoice was already
	 *  transmitted and NO second document went onto the network. */
	already_sent: boolean;
}

/** Transmit the invoice over PEPPOL via the org's configured Access Point.
 *  Idempotent at the data layer: a repeat call returns the existing
 *  transmission with `already_sent: true` and transmits nothing. */
export function sendInvoiceOverPeppol(
	invoiceId: string,
	body: PeppolSendRequest
): Promise<PeppolSendResult> {
	return api.post<PeppolSendResult>(`/api/invoices/${invoiceId}/peppol-send`, body);
}

export interface EInvoiceValidationIssue {
	/** Dotted field path, e.g. `seller.tax_id`, `lines`, `taxes[0].rate`. */
	field: string;
	/** The backend's own PII-free sentence for this field. Where the failure is
	 *  an EN 16931 / PEPPOL rule the message leads with the rule id
	 *  (`BR-CO-25: …`) — the identifier a receiving Access Point's validator
	 *  names — so nothing here needs a code→prose map on the client. */
	message: string;
}

// A field path is a dotted/indexed identifier — anything else means the detail
// is not the validation rendering (a different 4xx, a proxy's HTML error page),
// and the caller must render it verbatim instead of pretending it parsed.
const FIELD_PATH = /^[A-Za-z0-9_]+(?:\[\d+\])?(?:\.[A-Za-z0-9_]+(?:\[\d+\])?)*$/;

/**
 * Split the rendered 422 detail into its per-field parts so the UI can list
 * *why* the dialect refused the invoice, one row per field.
 *
 * The backend returns the errors STRUCTURED — `[{loc, type, msg}]` — and the
 * shared `formatApiDetail` (in `utils/apiError.ts`, which every response in the
 * app already goes through) flattens exactly that shape to
 * `"issue_date: Issue date is required; lines: At least one invoice line is
 * required"`. `ApiError` carries only a message, so this re-splits that one
 * rendering rather than duplicating the transport. The message is the SERVER's
 * wording — there is deliberately no code→prose table here.
 *
 * Returns `[]` when the string is not that shape — the caller then shows the
 * raw message. Never throws, never guesses: a partially-parseable detail is
 * treated as unparseable, because dropping half the reasons would be worse
 * than showing the backend's own wording.
 */
export function parseEInvoiceIssues(detail: string): EInvoiceValidationIssue[] {
	// `formatApiDetail` joins with exactly `'; '`. Splitting on the bare `;`
	// would cut a message that legitimately contains one.
	const parts = detail
		.split('; ')
		.map((p) => p.trim())
		.filter(Boolean);
	if (parts.length === 0) return [];
	const issues: EInvoiceValidationIssue[] = [];
	for (const part of parts) {
		// First `': '` only: a rule-id message carries its own colon
		// (`due_date: BR-CO-25: an invoice needs a due date`).
		const at = part.indexOf(': ');
		if (at < 0) return [];
		const field = part.slice(0, at).trim();
		const message = part.slice(at + 2).trim();
		if (!FIELD_PATH.test(field) || !message) return [];
		issues.push({ field, message });
	}
	return issues;
}

/** One row of `GET /api/invoices/{id}/peppol-transmissions` — PII-free by
 *  construction: the counterparty's and our own registered participant ids
 *  live on the backend row and inside the UBL, and never in this response. */
export interface PeppolTransmissionSummary {
	id: string;
	/** "outbound" | "inbound". */
	direction: string;
	/** "sending" | "sent" | "delivered" | "failed". */
	status: string;
	provider: string;
	/** EAS registry code of the counterparty id (e.g. `9930`), never its value. */
	participant_scheme: string;
	message_id: string | null;
	/** PII-free reason code only. */
	failure_reason: string | null;
	transmitted_at: string | null;
	created_at: string;
}

/** Every PEPPOL transmission recorded for the invoice, newest first.
 *
 * The send response's `already_sent` only describes a send the user just made,
 * so without this the UI could report an outcome but could never state the
 * state on OPEN — and deliberately made no "not yet sent" claim it could not
 * support. Read-gated to all four roles, like the e-invoice download. */
export function listPeppolTransmissions(invoiceId: string): Promise<{
	transmissions: PeppolTransmissionSummary[];
}> {
	return api.get(`/api/invoices/${invoiceId}/peppol-transmissions`);
}

/** A transmission still standing on the network — the backend's own
 *  idempotency predicate (`status <> 'failed'` in the partial unique index), so
 *  the UI can't disagree with what a repeat send would do. */
export function livePeppolTransmission(
	rows: PeppolTransmissionSummary[]
): PeppolTransmissionSummary | null {
	return rows.find((r) => r.direction === 'outbound' && r.status !== 'failed') ?? null;
}
