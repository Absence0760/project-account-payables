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
 * for the requested dialect, whose `detail` is the backend's PII-free
 * `"field: code; field: code"` join. `api.downloadBlob` already runs that
 * detail through `formatApiDetail`, so the thrown message IS the explanation;
 * `parseEInvoiceIssues` turns it back into per-field rows for rendering.
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
	/** `missing` | `malformed` | `inconsistent` | `implausible` from the
	 *  structural + tax pass, or an EN 16931 rule id (`BR-CO-25`) from the
	 *  PEPPOL BIS Billing 3.0 conformance pass. */
	code: string;
}

// A field path is a dotted/indexed identifier and a code is a bare token —
// anything else means the detail is not the validation join (a different 4xx,
// a proxy's HTML error page), and the caller must render it verbatim instead
// of pretending it parsed. The code alphabet allows upper case, digits and
// hyphens because the PEPPOL/EN 16931 conformance pass reports the rule id
// itself (`BR-CO-25`) where the structural pass reports a word (`missing`).
const FIELD_PATH = /^[A-Za-z0-9_]+(?:\[\d+\])?(?:\.[A-Za-z0-9_]+(?:\[\d+\])?)*$/;
const ISSUE_CODE = /^[A-Za-z0-9_-]+$/;

/**
 * Split the 422 detail (`"issue_date: missing; lines: missing"`) into its
 * per-field parts so the UI can say *why* the dialect refused the invoice.
 *
 * Returns `[]` when the string is not that shape — the caller then shows the
 * raw message. Never throws, never guesses: a partially-parseable detail is
 * treated as unparseable, because dropping half the reasons would be worse
 * than showing the backend's own wording.
 */
export function parseEInvoiceIssues(detail: string): EInvoiceValidationIssue[] {
	const parts = detail.split(';').map((p) => p.trim()).filter(Boolean);
	if (parts.length === 0) return [];
	const issues: EInvoiceValidationIssue[] = [];
	for (const part of parts) {
		const at = part.indexOf(':');
		if (at < 0) return [];
		const field = part.slice(0, at).trim();
		const code = part.slice(at + 1).trim();
		if (!FIELD_PATH.test(field) || !ISSUE_CODE.test(code)) return [];
		issues.push({ field, code });
	}
	return issues;
}

/** Collapse an indexed path (`taxes[0].rate`) onto its label key shape
 *  (`taxes[].rate`) — the index identifies which line, not which rule. */
export function einvoiceIssueFieldKey(field: string): string {
	return field.replace(/\[\d+\]/g, '[]');
}
