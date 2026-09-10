/**
 * The client half of the e-invoice refusal contract.
 *
 * `decisions.md` §95 moved the 422 to `[{loc, type, msg}]` and deleted the
 * hand-written code→prose map that had covered 4 codes out of dozens. The
 * replacement is generated from the backend's rule set, and these tests pin the
 * three things that make it work: the rule id survives the flattening the
 * shared `formatApiDetail` performs, the generated catalogue names only keys
 * that exist, and an unrecognised code degrades to the server's own sentence
 * rather than to nothing.
 */
import { describe, expect, it } from 'vitest';
import { einvoiceRuleMessageKey, parseEInvoiceIssues } from './einvoiceIssues';
import {
	E_INVOICE_OPAQUE_CODES,
	E_INVOICE_RULE_MESSAGE_KEYS
} from './einvoiceRuleMessages.generated';
import { en } from '../i18n/locales/en';

describe('parseEInvoiceIssues', () => {
	it('splits the flattened detail into one row per field', () => {
		const issues = parseEInvoiceIssues(
			'issue_date: Issue date is required; lines: At least one invoice line is required'
		);
		expect(issues).toEqual([
			{ field: 'issue_date', message: 'Issue date is required', code: null },
			{ field: 'lines', message: 'At least one invoice line is required', code: null }
		]);
	});

	it('recovers the rule id a rule-backed message leads with', () => {
		const [issue] = parseEInvoiceIssues(
			'due_date: BR-CO-25: An invoice with an amount due requires a due date or payment terms'
		);
		expect(issue.field).toBe('due_date');
		expect(issue.code).toBe('BR-CO-25');
		// The raw sentence is KEPT verbatim — it is the fallback rendering, and it
		// still carries the rule id a receiving Access Point's validator names.
		expect(issue.message).toBe(
			'BR-CO-25: An invoice with an amount due requires a due date or payment terms'
		);
	});

	it('reads the indexed field paths the per-line rules emit', () => {
		const [issue] = parseEInvoiceIssues(
			'lines[2].line_total: PEPPOL-EN16931-R120: Invoice line net amount must equal quantity times item net price'
		);
		expect(issue.field).toBe('lines[2].line_total');
		expect(issue.code).toBe('PEPPOL-EN16931-R120');
	});

	it('leaves a generic-kind message uncoded — the 422 does not fold those in', () => {
		const [issue] = parseEInvoiceIssues('seller.tax_id: Seller tax id format is invalid for country');
		expect(issue.code).toBeNull();
	});

	it('does not mistake a sentence-internal colon for a rule id', () => {
		const [issue] = parseEInvoiceIssues('currency: Not one of: USD, EUR');
		expect(issue.code).toBeNull();
		expect(issue.message).toBe('Not one of: USD, EUR');
	});

	it('treats a partially-parseable detail as unparseable', () => {
		// Half the reasons is worse than the backend's own wording rendered whole.
		expect(parseEInvoiceIssues('seller.tax_id: missing; something went wrong')).toEqual([]);
		expect(parseEInvoiceIssues('<html>502 Bad Gateway</html>')).toEqual([]);
		expect(parseEInvoiceIssues('')).toEqual([]);
	});
});

describe('einvoiceRuleMessageKey', () => {
	const issue = (code: string | null) => ({ field: 'due_date', message: 'x', code });

	it('resolves a code the generated catalogue knows', () => {
		expect(einvoiceRuleMessageKey(issue('BR-CO-25'))).toBe(
			'invoices.modal.einvoice.rule.brCo25'
		);
	});

	it('collapses the per-VAT-category families onto one key each', () => {
		// BR-Z-08 and BR-S-08 are the same sentence about different categories.
		expect(einvoiceRuleMessageKey(issue('BR-Z-08'))).toBe(
			einvoiceRuleMessageKey(issue('BR-S-08'))
		);
		// …but BR-S-05 is the MIRROR of the family (a rate ABOVE zero), so it keeps
		// its own key. Collapsing it would state the opposite rule.
		expect(einvoiceRuleMessageKey(issue('BR-S-05'))).not.toBe(
			einvoiceRuleMessageKey(issue('BR-Z-05'))
		);
	});

	it('degrades to null — never a blank row — for a code this build predates', () => {
		expect(einvoiceRuleMessageKey(issue('BR-NEW-99'))).toBeNull();
		expect(einvoiceRuleMessageKey(issue(null))).toBeNull();
	});
});

describe('the generated catalogue', () => {
	const catalogue: Record<string, string> = E_INVOICE_RULE_MESSAGE_KEYS;
	const messages: Record<string, string> = en;

	it('is non-empty and names only keys the English catalogue defines', () => {
		// The `satisfies Record<string, MessageKey>` on the generated file makes this
		// a compile error too; asserted at runtime so weakening the type is still loud.
		const codes = Object.keys(catalogue);
		expect(codes.length).toBeGreaterThan(0);
		for (const code of codes) {
			expect(messages[catalogue[code]], `no message for ${code}`).toBeTruthy();
		}
	});

	it('keys only rule ids — the codes the 422 actually folds into `msg`', () => {
		for (const code of Object.keys(catalogue)) {
			expect(code, `${code} is not rule-id shaped`).toBe(code.toUpperCase());
			expect(/[A-Z]/.test(code)).toBe(true);
		}
	});

	it('excludes the opaque generic kinds, and they really are unrecoverable', () => {
		for (const code of E_INVOICE_OPAQUE_CODES) {
			// Lower-case, so `parseEInvoiceIssues` cannot mistake one for a rule id —
			// which is exactly why mapping them would produce dead keys.
			expect(code).not.toBe(code.toUpperCase());
			expect(catalogue[code]).toBeUndefined();
		}
	});
});
